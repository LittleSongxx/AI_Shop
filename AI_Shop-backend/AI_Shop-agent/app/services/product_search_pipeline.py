"""Shared product retrieval plan used by production and Search evaluations."""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.rag.rrf import rrf_score_at_rank
from app.services.product_constraint_evidence import evaluate_excluded_terms
from app.services.product_search_query import (
    comparison_target_terms,
    exact_model_tokens,
    extract_query_hard_constraints,
    filter_products_by_query_relevance,
    infer_product_category,
    is_comparison_query,
    is_managed_search_keyword,
    normalize_product_search_query,
    primary_product_request,
    runtime_surface_contracts,
    verified_qualifier_contracts,
)
from app.services.shopping_profile_service import shopping_profile_service
from evaluation.core.fault_injection import fault_point


@dataclass(frozen=True)
class ProductRuntimeConstraints:
    category: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    required_brands: tuple[str, ...] = ()
    excluded_brands: tuple[str, ...] = ()
    excluded_terms: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    preferred_features: tuple[str, ...] = ()
    must_terms: tuple[str, ...] = ()
    must_not_terms: tuple[str, ...] = ()
    comparison_targets: tuple[str, ...] = ()
    comparison_required: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "budgetMin": self.budget_min,
            "budgetMax": self.budget_max,
            "requiredBrands": list(self.required_brands),
            "excludedBrands": list(self.excluded_brands),
            "excludedTerms": list(self.excluded_terms),
            "useCases": list(self.use_cases),
            "preferredFeatures": list(self.preferred_features),
            "mustTerms": list(self.must_terms),
            "mustNotTerms": list(self.must_not_terms),
            "comparisonTargets": list(self.comparison_targets),
            "comparisonRequired": self.comparison_required,
        }


@dataclass(frozen=True)
class ProductQueryPlan:
    raw_query: str
    retrieval_variants: tuple[str, ...]
    constraints: ProductRuntimeConstraints
    normalization_rules: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "rawQuery": self.raw_query,
            "retrievalVariants": list(self.retrieval_variants),
            "runtimeConstraints": self.constraints.public(),
            "normalizationRules": list(self.normalization_rules),
        }


@dataclass
class ProductSearchTrace:
    query_plan: dict[str, Any]
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    recall_counts: dict[str, int] = field(default_factory=dict)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    rejection_reasons: list[dict[str, str]] = field(default_factory=list)
    candidate_count: int = 0
    result_count: int = 0
    fallback: bool = False
    provider_calls: dict[str, int] = field(default_factory=dict)
    provider_failures: dict[str, int] = field(default_factory=dict)
    provider_timeouts: dict[str, int] = field(default_factory=dict)
    deadline_cancellations: dict[str, int] = field(default_factory=dict)
    stage_failures: dict[str, int] = field(default_factory=dict)
    stage_timeouts: dict[str, int] = field(default_factory=dict)
    cancelled_count: int = 0
    partial_failure: bool = False
    deadline_exceeded: bool = False
    result_source: str = "none"
    comparison_coverage: dict[str, int] = field(default_factory=dict)
    comparison_complete: bool | None = None
    incomplete_reason: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "queryPlan": self.query_plan,
            "stageLatencyMs": dict(self.stage_latency_ms),
            "recallCounts": dict(self.recall_counts),
            "rejectionCounts": dict(self.rejection_counts),
            "rejectionReasons": list(self.rejection_reasons),
            "candidateCount": self.candidate_count,
            "resultCount": self.result_count,
            "fallback": self.fallback,
            "providerCalls": dict(self.provider_calls),
            "providerFailures": dict(self.provider_failures),
            "providerTimeouts": dict(self.provider_timeouts),
            "deadlineCancellations": dict(self.deadline_cancellations),
            "stageFailures": dict(self.stage_failures),
            "stageTimeouts": dict(self.stage_timeouts),
            "cancelledCount": self.cancelled_count,
            "partialFailure": self.partial_failure,
            "deadlineExceeded": self.deadline_exceeded,
            "resultSource": self.result_source,
            "comparisonCoverage": dict(self.comparison_coverage),
            "comparisonComplete": self.comparison_complete,
            "incompleteReason": self.incomplete_reason,
        }


@dataclass(frozen=True)
class ProductSearchResult:
    products: list[dict[str, Any]]
    ranked_ids: list[str]
    trace: ProductSearchTrace


KeywordSearch = Callable[[str, int], Awaitable[list[str]]]
VectorSearch = Callable[[str, int], Awaitable[list[str]]]
ProductLoader = Callable[[list[str]], Awaitable[list[dict[str, Any]]]]
ProductReranker = Callable[[str, list[dict[str, Any]], int], Awaitable[list[dict[str, Any]]]]


@dataclass
class ProductSearchEvaluationCapture:
    traces: list[ProductSearchTrace] = field(default_factory=list)


_PRODUCT_SEARCH_EVALUATION_CAPTURE: contextvars.ContextVar[
    ProductSearchEvaluationCapture | None
] = contextvars.ContextVar("product_search_evaluation_capture", default=None)


@contextmanager
def product_search_evaluation_scope() -> Iterator[ProductSearchEvaluationCapture]:
    """Capture production-path traces without adding fields to user responses."""

    capture = ProductSearchEvaluationCapture()
    token = _PRODUCT_SEARCH_EVALUATION_CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _PRODUCT_SEARCH_EVALUATION_CAPTURE.reset(token)


def _unique_text(values: Sequence[Any]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        value = " ".join(str(raw or "").strip().split())
        if value and value.casefold() not in {item.casefold() for item in result}:
            result.append(value)
    return tuple(result)


def _text_values(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _merge_exclusion_terms(*groups: Any) -> tuple[str, ...]:
    """Merge exclusions while removing longer terms covered by a shorter one."""

    merged = _unique_text(
        tuple(item for group in groups for item in _text_values(group))
    )
    result: list[str] = []
    for value in sorted(merged, key=lambda item: (len(item), item.casefold())):
        folded = value.casefold()
        if any(existing.casefold() in folded for existing in result):
            continue
        result = [item for item in result if folded not in item.casefold()]
        result.append(value)
    return tuple(result)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_product_query_plan(
    raw_query: str | None,
    mission: Mapping[str, Any] | None,
    *,
    max_variants: int = 3,
) -> ProductQueryPlan:
    """Build additive retrieval variants without replacing the user's request."""

    raw = " ".join(str(raw_query or "").strip().split())
    mission = mission or {}
    hard = mission.get("hardConstraints") if isinstance(mission, Mapping) else {}
    soft = mission.get("softPreferences") if isinstance(mission, Mapping) else {}
    exclusions = mission.get("exclusions") if isinstance(mission, Mapping) else {}
    hard = hard if isinstance(hard, Mapping) else {}
    soft = soft if isinstance(soft, Mapping) else {}
    exclusions = exclusions if isinstance(exclusions, Mapping) else {}

    primary_request = primary_product_request(raw)
    comparison_suffix = bool(primary_request and primary_request != raw)
    mission_category = str(mission.get("category") or "").strip() or None
    inferred_category = infer_product_category(raw)
    comparison_target_only = comparison_suffix and inferred_category is None
    normalized = normalize_product_search_query(
        primary_request if comparison_target_only else raw
    )
    broad_categories = {
        "电脑",
        "平板",
        "家电",
        "箱包",
        "包",
        "鞋子",
        "服饰",
        "美妆",
        "玩具",
        "乐器",
        "手表",
    }
    if inferred_category and not is_managed_search_keyword(normalized):
        normalized = inferred_category
    category = None if comparison_target_only else mission_category
    if not comparison_target_only and inferred_category and (
        not mission_category
        or mission_category in broad_categories
        or inferred_category.casefold() in mission_category.casefold()
    ):
        category = inferred_category
    variants = _unique_text((raw, normalized, category))[: max(1, max_variants)]
    rules: list[str] = []
    if normalized and normalized.casefold() != raw.casefold():
        rules.append("managed_taxonomy_additive_variant")
    if category and category.casefold() not in {value.casefold() for value in variants[:2]}:
        rules.append("shopping_mission_category_variant")
    if category and category != mission_category:
        rules.append("managed_taxonomy_runtime_category")
    if comparison_target_only:
        rules.append("comparison_target_excluded_from_requested_category")

    raw_hard = extract_query_hard_constraints(raw)
    mission_budget_min = _number(hard.get("budgetMin", hard.get("budget_min")))
    mission_budget_max = _number(hard.get("budgetMax", hard.get("budget_max")))
    raw_budget_min = _number(raw_hard.get("budget_min"))
    raw_budget_max = _number(raw_hard.get("budget_max"))
    budget_mins = [value for value in (mission_budget_min, raw_budget_min) if value is not None]
    budget_maxes = [value for value in (mission_budget_max, raw_budget_max) if value is not None]
    mission_must_terms = hard.get("mustTerms", hard.get("must_terms")) or hard.get("requiredTerms", ())
    mission_must_not_terms = exclusions.get("terms") or ()
    mission_targets = hard.get("comparisonTargets", hard.get("comparison_targets")) or ()
    must_terms = _unique_text(
        (*_text_values(mission_must_terms), *_text_values(raw_hard.get("must_terms")))
    )
    must_not_terms = _merge_exclusion_terms(
        mission_must_not_terms, raw_hard.get("must_not_terms")
    )
    comparison_targets = _unique_text(
        (*_text_values(mission_targets), *_text_values(raw_hard.get("comparison_targets")))
    )
    comparison_required = bool(
        _boolean(hard.get("comparisonRequired", hard.get("comparison_required", False)))
        or raw_hard.get("comparison_required")
    )
    constraints = ProductRuntimeConstraints(
        category=category,
        budget_min=max(budget_mins) if budget_mins else None,
        budget_max=min(budget_maxes) if budget_maxes else None,
        required_brands=_unique_text(_text_values(hard.get("requiredBrands"))),
        excluded_brands=_unique_text(_text_values(exclusions.get("brands"))),
        excluded_terms=must_not_terms,
        use_cases=_unique_text(_text_values(mission.get("useCases"))),
        preferred_features=_unique_text(_text_values(soft.get("features"))),
        must_terms=must_terms,
        must_not_terms=must_not_terms,
        comparison_targets=comparison_targets,
        comparison_required=comparison_required,
    )
    return ProductQueryPlan(
        raw_query=raw,
        retrieval_variants=variants or ((raw,) if raw else ()),
        constraints=constraints,
        normalization_rules=tuple(rules),
    )


def merge_ranked_lists(rankings: Sequence[Sequence[str]], limit: int) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in rankings:
        for rank, product_id in enumerate(ranked, 1):
            value = str(product_id or "")
            if value:
                scores[value] = scores.get(value, 0.0) + rrf_score_at_rank(rank)
    return [
        product_id
        for product_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            : max(1, limit)
        ]
    ]


def _product_text(product: Mapping[str, Any]) -> str:
    values = (
        product.get("product_name"),
        product.get("productName"),
        product.get("product_desc"),
        product.get("productDesc"),
        product.get("description"),
        product.get("brand"),
        product.get("category"),
        product.get("categoryName"),
        product.get("category_name"),
        product.get("product_class"),
    )
    property_values = product.get("property_values") or product.get("propertyValues") or ()
    property_text = " ".join(
        " ".join(
            str(item.get(key) or "")
            for key in ("property_name", "propertyName", "property_value", "propertyValue")
        )
        for item in property_values
        if isinstance(item, Mapping)
    )
    sku_text = " ".join(
        " ".join(str(item.get(key) or "") for key in ("property_value_ids", "propertyValueIds", "sku_name", "skuName"))
        for item in (product.get("skus") or ())
        if isinstance(item, Mapping)
    )
    return " ".join((*(str(value or "") for value in values), property_text, sku_text)).casefold()


def _product_surface_text(product: Mapping[str, Any]) -> str:
    values = (
        product.get("product_name"),
        product.get("productName"),
        product.get("brand"),
        product.get("category"),
        product.get("categoryName"),
        product.get("category_name"),
        product.get("product_class"),
    )
    return " ".join(str(value or "") for value in values).casefold()


def _product_id(product: Mapping[str, Any]) -> str:
    return str(product.get("product_id") or product.get("productId") or product.get("id") or "")


def _product_price(product: Mapping[str, Any]) -> float | None:
    for key in ("estimated_payable", "price", "min_price", "minPrice"):
        value = _number(product.get(key))
        if value is not None:
            return value
    return None


def _compact_text(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").casefold()
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


def _product_matches_term(product: Mapping[str, Any], term: str) -> bool:
    """Match an explicit hard term against snapshot-backed product text."""

    raw_term = " ".join(str(term or "").strip().split()).casefold()
    if not raw_term:
        return True
    text = _product_text(product)
    if raw_term in text:
        return True
    compact_term = _compact_text(raw_term)
    compact_product = _compact_text(text)
    if compact_term and compact_term in compact_product:
        return True
    return bool(
        len(compact_term) >= 3
        and compact_term[-1:] in {"版", "款"}
        and compact_term[:-1] in compact_product
    )


def comparison_target_coverage(
    products: Sequence[Mapping[str, Any]],
    plan: ProductQueryPlan,
) -> tuple[dict[str, int], bool | None, str | None]:
    """Return per-target evidence coverage for an explicit comparison."""

    if not plan.constraints.comparison_required:
        return {}, None, None
    targets = plan.constraints.comparison_targets or plan.constraints.must_terms
    coverage = {str(target): 0 for target in targets if str(target).strip()}
    if not coverage:
        return {}, False, "COMPARISON_TARGETS_UNRESOLVED"
    for product in products:
        for target in tuple(coverage):
            if _product_matches_term(product, target):
                coverage[target] += 1
    missing = [target for target, count in coverage.items() if count <= 0]
    return coverage, not missing, "MISSING_COMPARISON_TARGETS" if missing else None


def filter_products_by_runtime_constraints(
    products: Sequence[Mapping[str, Any]],
    constraints: ProductRuntimeConstraints,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Filter only constraints supported by verified product snapshot fields."""

    required = {value.casefold() for value in constraints.required_brands}
    excluded = {value.casefold() for value in constraints.excluded_brands}
    excluded_terms = tuple(
        value.casefold()
        for value in constraints.excluded_terms
        if str(value).strip()
    )
    brand_profile = {
        "brands": list(constraints.required_brands),
        "excludedBrands": list(constraints.excluded_brands),
    }
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for raw in products:
        product = dict(raw)
        product_id = str(product.get("product_id") or product.get("productId") or product.get("id") or "")
        brand = str(
            shopping_profile_service.resolve_known_brand(product, brand_profile) or ""
        ).strip().casefold()
        if brand:
            product["brand"] = brand
        price = _product_price(product)
        reason: str | None = None
        if (constraints.budget_max is not None or constraints.budget_min is not None) and price is None:
            reason = "BUDGET_UNVERIFIED"
        elif constraints.budget_max is not None and price > constraints.budget_max:
            reason = "OVER_BUDGET"
        elif constraints.budget_min is not None and price < constraints.budget_min:
            reason = "BELOW_BUDGET_RANGE"
        elif required and brand not in required:
            reason = "BRAND_REQUIRED"
        elif brand and brand in excluded:
            reason = "BRAND_EXCLUDED"
        if constraints.category:
            category_fields = " ".join(
                str(product.get(key) or "")
                for key in ("category", "categoryName", "category_name", "product_class")
            ).strip().casefold()
            if category_fields and constraints.category.casefold() not in category_fields:
                reason = reason or "CATEGORY_REQUIRED"
        if excluded_terms:
            exclusion = evaluate_excluded_terms(product, excluded_terms)
            if exclusion["violates"]:
                reason = reason or "TERM_EXCLUDED"
            elif exclusion["eligibleSkuKeys"]:
                product["constraint_allowed_sku_keys"] = exclusion["eligibleSkuKeys"]
                product["constraint_evidence_contracts"] = exclusion["evidenceContracts"]
        if reason:
            rejected.append({"productId": product_id, "reason": reason})
        else:
            eligible.append(product)
    return eligible, rejected


def filter_products_for_query_plan(
    products: Sequence[Mapping[str, Any]],
    plan: ProductQueryPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Apply the same verified candidate guard in runtime and evaluation.

    Managed and dynamic catalog categories use snapshot-backed hard
    constraints. Explicit product-type contracts are strict: a compact Java
    snapshot without a matching surface cannot justify a cross-category
    substitute. Broad shelves without a concrete type contract retain their
    conservative unknown-category behavior, while comparison suffixes remain
    separately guarded.
    """

    candidates = [dict(product) for product in products]
    surface_rejected: list[dict[str, str]] = []
    excluded_model_tokens = {
        _compact_text(term) for term in plan.constraints.must_not_terms
    }
    model_tokens = tuple(
        token
        for token in exact_model_tokens(plan.raw_query)
        if _compact_text(token) not in excluded_model_tokens
    )
    if model_tokens:
        model_match = any if is_comparison_query(plan.raw_query) else all
        retained_terms = comparison_target_terms(plan.raw_query)

        def matches_model_or_retained_target(product: Mapping[str, Any]) -> bool:
            name = "".join(
                character
                for character in str(
                    product.get("product_name") or product.get("productName") or ""
                ).casefold()
                if character.isalnum()
            )
            model_hit = model_match(
                token in name
                for token in model_tokens
            )
            return model_hit or bool(
                is_comparison_query(plan.raw_query)
                and any(
                    term in name
                    or (len(term) >= 3 and term[:-1] in name)
                    for term in retained_terms
                )
            )

        matched = [
            product
            for product in candidates
            if matches_model_or_retained_target(product)
        ]
        matched_ids = {id(product) for product in matched}
        surface_rejected.extend(
            {
                "productId": _product_id(product),
                "reason": "EXACT_MODEL_MISMATCH",
            }
            for product in candidates
            if id(product) not in matched_ids
        )
        candidates = matched

    type_contracts = runtime_surface_contracts(plan.raw_query)
    if type_contracts and candidates:
        matched = []
        for product in candidates:
            text = _product_surface_text(product)
            contract_matches = [
                bool(contract.get("surfaceTerms"))
                and any(
                    str(term).casefold() in text
                    for term in contract.get("surfaceTerms") or []
                )
                and not any(
                    str(term).casefold() in text
                    for term in contract.get("blockedTerms") or []
                )
                for contract in type_contracts
            ]
            comparison_target_match = bool(
                plan.constraints.comparison_required
                and any(
                    _product_matches_term(product, target)
                    for target in plan.constraints.comparison_targets
                )
            )
            if any(contract_matches) or comparison_target_match:
                matched.append(product)
            else:
                surface_rejected.append(
                    {
                        "productId": _product_id(product),
                        "reason": "MANAGED_CATEGORY_SURFACE_MISMATCH",
                    }
                )
        candidates = matched

    qualifier_contracts = verified_qualifier_contracts(plan.raw_query)
    if qualifier_contracts and candidates:
        matched = []
        for product in candidates:
            text = _product_text(product)
            if all(
                any(
                    str(term).casefold() in text
                    for term in contract.get("evidenceTerms") or []
                )
                for contract in qualifier_contracts
            ):
                matched.append(product)
            else:
                surface_rejected.append(
                    {
                        "productId": _product_id(product),
                        "reason": "UNVERIFIED_REQUIRED_ATTRIBUTE",
                    }
                )
        candidates = matched
    if "comparison_target_excluded_from_requested_category" in plan.normalization_rules:
        matched = filter_products_by_query_relevance(
            candidates, primary_product_request(plan.raw_query)
        )
        matched_ids = {id(product) for product in matched}
        # The relevance helper returns original dict objects for this list.
        for product in candidates:
            if id(product) not in matched_ids:
                surface_rejected.append(
                    {
                        "productId": str(
                            product.get("product_id")
                            or product.get("productId")
                            or product.get("id")
                            or ""
                        ),
                        "reason": "UNKNOWN_CATEGORY_SURFACE_MISMATCH",
                    }
                )
        candidates = matched
    hard_terms = tuple(
        term for term in plan.constraints.must_terms if str(term or "").strip()
    )
    if hard_terms and candidates:
        if plan.constraints.comparison_required:
            target_terms = tuple(
                term
                for term in (plan.constraints.comparison_targets or hard_terms)
                if str(term or "").strip()
            )
            matched = [
                product
                for product in candidates
                if any(_product_matches_term(product, term) for term in target_terms)
            ]
            mismatch_reason = "COMPARISON_TARGET_MISSING"
        else:
            matched = [
                product
                for product in candidates
                if all(_product_matches_term(product, term) for term in hard_terms)
            ]
            mismatch_reason = "MUST_TERM_MISSING"
        matched_ids = {id(product) for product in matched}
        surface_rejected.extend(
            {
                "productId": _product_id(product),
                "reason": mismatch_reason,
            }
            for product in candidates
            if id(product) not in matched_ids
        )
        candidates = matched
    managed_category = infer_product_category(plan.raw_query)
    if managed_category and candidates and not type_contracts:
        # A Java offer snapshot may intentionally omit human-readable category
        # fields.  In that case absence of the category word in a title is not
        # evidence that the offer is unrelated; keep the authoritative
        # candidates and let explicit constraints/exclusions decide.
        category_fields_available = any(
            any(
                any(char.isalpha() or "\u4e00" <= char <= "\u9fff" for char in value)
                for value in (
                    str(product.get(key) or "").strip()
                    for key in ("category", "categoryName", "category_name", "product_class")
                )
                if value
            )
            for product in candidates
        )
        matched = filter_products_by_query_relevance(candidates, managed_category)
        if not category_fields_available and plan.constraints.excluded_terms:
            # A negative-constraint query may intentionally use a broad shelf
            # (for example, "零食不要旺旺").  Product titles are not a
            # category authority in this snapshot shape, so retain the pool
            # for the explicit exclusion filter instead of manufacturing a
            # false partial result (a title can mention the shelf alias for the
            # excluded item while valid alternatives do not).
            matched = list(candidates)
        matched_ids = {id(product) for product in matched}
        for product in candidates:
            if id(product) not in matched_ids:
                surface_rejected.append(
                    {
                        "productId": str(
                            product.get("product_id")
                            or product.get("productId")
                            or product.get("id")
                            or ""
                        ),
                        "reason": "MANAGED_CATEGORY_SURFACE_MISMATCH",
                    }
                )
        candidates = matched
    eligible, rejected = filter_products_by_runtime_constraints(
        candidates, plan.constraints
    )
    return eligible, [*surface_rejected, *rejected]


class ProductSearchPipeline:
    async def search(
        self,
        plan: ProductQueryPlan,
        *,
        candidate_size: int,
        result_size: int,
        keyword_search: KeywordSearch,
        vector_search: VectorSearch,
        load_products: ProductLoader,
        rerank: ProductReranker,
        deadline_seconds: float | None = None,
        provider_timeout_seconds: float | None = None,
    ) -> ProductSearchResult:
        started = time.perf_counter()
        trace = ProductSearchTrace(query_plan=plan.public())
        variants = list(plan.retrieval_variants)
        if not variants:
            trace.stage_latency_ms["total"] = 0.0
            return ProductSearchResult([], [], trace)

        search_deadline = float(8.0 if deadline_seconds is None else deadline_seconds)
        provider_timeout = float(
            4.0 if provider_timeout_seconds is None else provider_timeout_seconds
        )
        if search_deadline <= 0 or provider_timeout <= 0:
            raise ValueError("search deadlines must be positive")

        # The deadline is an end-to-end budget for this pipeline, not merely a
        # recall timeout.  Providers receive a shorter individual timeout, while
        # snapshot loading and reranking consume whatever budget remains.
        deadline_at = started + search_deadline

        def remaining_budget() -> float:
            return deadline_at - time.perf_counter()

        async def measured(
            kind: str,
            variant: str,
            call,
        ) -> tuple[str, str, list[str], float, str, str | None]:
            stage_started = time.perf_counter()
            try:
                injected_mode = fault_point(kind)
                if injected_mode == "empty":
                    return (
                        kind,
                        variant,
                        [],
                        (time.perf_counter() - stage_started) * 1000,
                        "EMPTY",
                        "InjectedEmptyResult",
                    )
                async with asyncio.timeout(provider_timeout):
                    values = await call(variant, candidate_size)
                values = list(values)
                if injected_mode == "partial":
                    values = values[: max(1, len(values) // 2)] if values else []
                return (
                    kind,
                    variant,
                    values,
                    (time.perf_counter() - stage_started) * 1000,
                    "OK",
                    None,
                )
            except TimeoutError:
                return (
                    kind,
                    variant,
                    [],
                    (time.perf_counter() - stage_started) * 1000,
                    "TIMEOUT",
                    "TimeoutError",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return (
                    kind,
                    variant,
                    [],
                    (time.perf_counter() - stage_started) * 1000,
                    "ERROR",
                    type(exc).__name__,
                )

        tasks: list[asyncio.Task] = []
        task_metadata: dict[asyncio.Task, tuple[str, str]] = {}
        for variant in variants:
            for kind, search in (("bm25", keyword_search), ("vector", vector_search)):
                task = asyncio.create_task(measured(kind, variant, search))
                tasks.append(task)
                task_metadata[task] = (kind, variant)
        trace.provider_calls["bm25"] = len(variants)
        trace.provider_calls["embeddingVector"] = len(variants)
        recall_started = time.perf_counter()
        done, pending = await asyncio.wait(tasks, timeout=max(0.0, remaining_budget()))
        if pending:
            trace.partial_failure = True
            trace.deadline_exceeded = True
            trace.cancelled_count += len(pending)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        recall_rows: list[tuple[str, str, list[str], float, str, str | None]] = []
        for task in done:
            try:
                recall_rows.append(task.result())
            except asyncio.CancelledError:
                trace.cancelled_count += 1
                kind, _variant = task_metadata.get(task, ("unknown", ""))
                trace.deadline_cancellations[kind] = (
                    trace.deadline_cancellations.get(kind, 0) + 1
                )
            except Exception:
                trace.partial_failure = True
                kind, variant = task_metadata.get(task, ("unknown", ""))
                trace.provider_failures[kind] = trace.provider_failures.get(kind, 0) + 1
                key = f"{kind}:{variant}" if variant else f"unknown:task-{id(task)}"
                trace.recall_counts[key] = 0
                trace.stage_latency_ms[key] = 0.0
        trace.stage_latency_ms["recall"] = round(
            (time.perf_counter() - recall_started) * 1000, 4
        )
        if pending:
            # ``asyncio.wait`` retains task identity but not its payload after
            # cancellation, so use the explicit metadata map for diagnostics.
            for task in pending:
                kind, variant = task_metadata.get(task, ("unknown", ""))
                trace.deadline_cancellations[kind] = (
                    trace.deadline_cancellations.get(kind, 0) + 1
                )
                trace.recall_counts[f"{kind}:{variant}"] = 0
                trace.stage_latency_ms[f"{kind}:{variant}"] = round(
                    (time.perf_counter() - recall_started) * 1000, 4
                )
        rankings: list[list[str]] = []
        for kind, variant, values, latency, status, error_name in recall_rows:
            key = f"{kind}:{variant}"
            if status in {"OK", "EMPTY"}:
                rankings.append(values)
                if status == "EMPTY":
                    trace.partial_failure = True
                    trace.provider_failures[kind] = trace.provider_failures.get(kind, 0) + 1
            else:
                trace.partial_failure = True
                if status == "TIMEOUT":
                    trace.provider_timeouts[kind] = trace.provider_timeouts.get(kind, 0) + 1
                else:
                    trace.provider_failures[kind] = trace.provider_failures.get(kind, 0) + 1
            trace.recall_counts[key] = len(values)
            trace.stage_latency_ms[key] = round(latency, 4)
            if error_name:
                trace.recall_counts[f"{key}:error"] = 1

        if not rankings:
            trace.result_source = "recall_unavailable" if trace.partial_failure else "no_recall"
            trace.stage_latency_ms["total"] = round((time.perf_counter() - started) * 1000, 4)
            capture = _PRODUCT_SEARCH_EVALUATION_CAPTURE.get()
            if capture is not None:
                capture.traces.append(trace)
            return ProductSearchResult([], [], trace)

        rrf_started = time.perf_counter()
        ranked_ids = merge_ranked_lists(rankings, candidate_size)
        trace.stage_latency_ms["rrf"] = round((time.perf_counter() - rrf_started) * 1000, 4)
        trace.candidate_count = len(ranked_ids)

        async def bounded_stage(
            stage: str,
            call: Callable[[], Awaitable[Any]],
        ) -> tuple[Any, str, float]:
            stage_started = time.perf_counter()
            remaining = remaining_budget()
            if remaining <= 0:
                trace.partial_failure = True
                trace.deadline_exceeded = True
                trace.stage_timeouts[stage] = trace.stage_timeouts.get(stage, 0) + 1
                return None, "DEADLINE", (time.perf_counter() - stage_started) * 1000
            try:
                async with asyncio.timeout(remaining):
                    value = await call()
                return value, "OK", (time.perf_counter() - stage_started) * 1000
            except TimeoutError:
                trace.partial_failure = True
                trace.deadline_exceeded = True
                trace.stage_timeouts[stage] = trace.stage_timeouts.get(stage, 0) + 1
                return None, "TIMEOUT", (time.perf_counter() - stage_started) * 1000
            except asyncio.CancelledError:
                raise
            except Exception:
                trace.partial_failure = True
                trace.stage_failures[stage] = trace.stage_failures.get(stage, 0) + 1
                return None, "ERROR", (time.perf_counter() - stage_started) * 1000

        products, load_status, load_latency = await bounded_stage(
            "snapshotLoad", lambda: load_products(ranked_ids)
        )
        trace.stage_latency_ms["snapshotLoad"] = round(load_latency, 4)
        if load_status != "OK":
            trace.result_source = "snapshot_unavailable"
            trace.result_count = 0
            trace.stage_latency_ms["total"] = round((time.perf_counter() - started) * 1000, 4)
            capture = _PRODUCT_SEARCH_EVALUATION_CAPTURE.get()
            if capture is not None:
                capture.traces.append(trace)
            return ProductSearchResult([], ranked_ids, trace)
        products = list(products or [])

        filter_started = time.perf_counter()
        eligible, rejected = filter_products_for_query_plan(products, plan)
        trace.stage_latency_ms["filter"] = round(
            (time.perf_counter() - filter_started) * 1000, 4
        )
        for row in rejected:
            reason = row["reason"]
            trace.rejection_counts[reason] = trace.rejection_counts.get(reason, 0) + 1
        trace.rejection_reasons.extend(rejected)

        if eligible:
            trace.provider_calls["rerank"] = 1
            authoritative_ids = {
                _product_id(product) for product in eligible if _product_id(product)
            }
            rerank_size = min(
                max(1, candidate_size),
                max(result_size, len(plan.constraints.comparison_targets)),
            )
            reranked, rerank_status, rerank_latency = await bounded_stage(
                "rerank", lambda: rerank(plan.raw_query, eligible, rerank_size)
            )
            trace.stage_latency_ms["rerank"] = round(rerank_latency, 4)
            if rerank_status == "OK":
                reranked_rows = list(reranked or [])
                unknown_rerank_rows = [
                    product
                    for product in reranked_rows
                    if authoritative_ids and _product_id(product) not in authoritative_ids
                ]
                if unknown_rerank_rows:
                    trace.rejection_counts["RERANK_UNKNOWN_PRODUCT"] = (
                        trace.rejection_counts.get("RERANK_UNKNOWN_PRODUCT", 0)
                        + len(unknown_rerank_rows)
                    )
                    trace.rejection_reasons.extend(
                        {
                            "productId": _product_id(product),
                            "reason": "RERANK_UNKNOWN_PRODUCT",
                        }
                        for product in unknown_rerank_rows
                    )
                    reranked_rows = [
                        product
                        for product in reranked_rows
                        if not authoritative_ids
                        or _product_id(product) in authoritative_ids
                    ]
                reranked_eligible, rerank_rejected = filter_products_for_query_plan(
                    reranked_rows, plan
                )
                eligible = reranked_eligible
                for row in rerank_rejected:
                    reason = row["reason"]
                    trace.rejection_counts[reason] = trace.rejection_counts.get(reason, 0) + 1
                trace.rejection_reasons.extend(rerank_rejected)
                trace.fallback = any(
                    str(product.get("_search_rerank_source") or "rerank") != "rerank"
                    for product in eligible
                )
                trace.result_source = "rrf_fallback" if trace.fallback else "rerank"
            else:
                # Preserve an eligible, authoritative snapshot when the optional
                # reranker misses its budget.  The trace makes the degradation
                # explicit and provider-completeness gates can fail the case.
                trace.fallback = True
                trace.result_source = "rrf_fallback"
                eligible = eligible[: max(1, result_size)]
        else:
            trace.stage_latency_ms["rerank"] = 0.0
            trace.provider_calls["rerank"] = 0
            if ranked_ids:
                trace.result_source = "constraint_miss"
        coverage, complete, incomplete_reason = comparison_target_coverage(eligible, plan)
        trace.comparison_coverage = coverage
        trace.comparison_complete = complete
        trace.incomplete_reason = incomplete_reason
        if complete is False:
            trace.result_source = "comparison_incomplete"
            eligible = []
        trace.result_count = len(eligible)
        trace.stage_latency_ms["total"] = round((time.perf_counter() - started) * 1000, 4)
        capture = _PRODUCT_SEARCH_EVALUATION_CAPTURE.get()
        if capture is not None:
            capture.traces.append(trace)
        return ProductSearchResult(eligible, ranked_ids, trace)


product_search_pipeline = ProductSearchPipeline()
