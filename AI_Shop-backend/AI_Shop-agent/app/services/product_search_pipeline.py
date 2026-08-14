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
from app.services.product_search_query import (
    filter_products_by_query_relevance,
    infer_product_category,
    is_managed_search_keyword,
    normalize_product_search_query,
    primary_product_request,
)
from app.services.shopping_profile_service import shopping_profile_service


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
    candidate_count: int = 0
    result_count: int = 0
    fallback: bool = False
    provider_calls: dict[str, int] = field(default_factory=dict)
    result_source: str = "none"

    def public(self) -> dict[str, Any]:
        return {
            "queryPlan": self.query_plan,
            "stageLatencyMs": dict(self.stage_latency_ms),
            "recallCounts": dict(self.recall_counts),
            "rejectionCounts": dict(self.rejection_counts),
            "candidateCount": self.candidate_count,
            "resultCount": self.result_count,
            "fallback": self.fallback,
            "providerCalls": dict(self.provider_calls),
            "resultSource": self.result_source,
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

    constraints = ProductRuntimeConstraints(
        category=category,
        budget_min=_number(hard.get("budgetMin")),
        budget_max=_number(hard.get("budgetMax")),
        required_brands=_unique_text(hard.get("requiredBrands") or ()),
        excluded_brands=_unique_text(exclusions.get("brands") or ()),
        excluded_terms=_unique_text(exclusions.get("terms") or ()),
        use_cases=_unique_text(mission.get("useCases") or ()),
        preferred_features=_unique_text(soft.get("features") or ()),
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
    return " ".join(str(value or "") for value in values).casefold()


def _product_price(product: Mapping[str, Any]) -> float | None:
    for key in ("estimated_payable", "price", "min_price", "minPrice"):
        value = _number(product.get(key))
        if value is not None:
            return value
    return None


def filter_products_by_runtime_constraints(
    products: Sequence[Mapping[str, Any]],
    constraints: ProductRuntimeConstraints,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Filter only constraints supported by verified product snapshot fields."""

    required = {value.casefold() for value in constraints.required_brands}
    excluded = {value.casefold() for value in constraints.excluded_brands}
    excluded_terms = {value.casefold() for value in constraints.excluded_terms}
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
        product_text = _product_text(product)
        price = _product_price(product)
        reason: str | None = None
        if constraints.budget_max is not None and price is not None and price > constraints.budget_max:
            reason = "OVER_BUDGET"
        elif constraints.budget_min is not None and price is not None and price < constraints.budget_min:
            reason = "BELOW_BUDGET_RANGE"
        elif required and brand not in required:
            reason = "BRAND_REQUIRED"
        elif brand and brand in excluded:
            reason = "BRAND_EXCLUDED"
        elif excluded_terms and any(term in product_text for term in excluded_terms):
            reason = "TERM_EXCLUDED"
        elif constraints.category:
            category_fields = " ".join(
                str(product.get(key) or "")
                for key in ("category", "categoryName", "category_name", "product_class")
            ).strip().casefold()
            if category_fields and constraints.category.casefold() not in category_fields:
                reason = "CATEGORY_REQUIRED"
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
    constraints.  A conservative surface guard is limited to explicit
    comparison suffixes; applying it to every unknown taxonomy category would
    incorrectly hide valid products newly added to the live catalog.
    """

    candidates = [dict(product) for product in products]
    surface_rejected: list[dict[str, str]] = []
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
    managed_category = infer_product_category(plan.raw_query)
    if managed_category and candidates:
        matched = filter_products_by_query_relevance(candidates, managed_category)
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
    ) -> ProductSearchResult:
        started = time.perf_counter()
        trace = ProductSearchTrace(query_plan=plan.public())
        variants = list(plan.retrieval_variants)
        if not variants:
            trace.stage_latency_ms["total"] = 0.0
            return ProductSearchResult([], [], trace)

        async def measured(kind: str, variant: str, call) -> tuple[str, str, list[str], float]:
            stage_started = time.perf_counter()
            values = await call(variant, candidate_size)
            return kind, variant, list(values), (time.perf_counter() - stage_started) * 1000

        tasks = [
            asyncio.create_task(measured(kind, variant, search))
            for variant in variants
            for kind, search in (("bm25", keyword_search), ("vector", vector_search))
        ]
        recall_rows = await asyncio.gather(*tasks)
        trace.provider_calls["bm25"] = len(variants)
        trace.provider_calls["embeddingVector"] = len(variants)
        rankings: list[list[str]] = []
        for kind, variant, values, latency in recall_rows:
            key = f"{kind}:{variant}"
            rankings.append(values)
            trace.recall_counts[key] = len(values)
            trace.stage_latency_ms[key] = round(latency, 4)

        rrf_started = time.perf_counter()
        ranked_ids = merge_ranked_lists(rankings, candidate_size)
        trace.stage_latency_ms["rrf"] = round((time.perf_counter() - rrf_started) * 1000, 4)
        trace.candidate_count = len(ranked_ids)

        load_started = time.perf_counter()
        products = await load_products(ranked_ids)
        trace.stage_latency_ms["snapshotLoad"] = round(
            (time.perf_counter() - load_started) * 1000, 4
        )

        filter_started = time.perf_counter()
        eligible, rejected = filter_products_for_query_plan(products, plan)
        trace.stage_latency_ms["filter"] = round(
            (time.perf_counter() - filter_started) * 1000, 4
        )
        for row in rejected:
            reason = row["reason"]
            trace.rejection_counts[reason] = trace.rejection_counts.get(reason, 0) + 1

        if eligible:
            rerank_started = time.perf_counter()
            eligible = await rerank(plan.raw_query, eligible, result_size)
            trace.stage_latency_ms["rerank"] = round(
                (time.perf_counter() - rerank_started) * 1000, 4
            )
            trace.fallback = any(
                str(product.get("_search_rerank_source") or "rerank") != "rerank"
                for product in eligible
            )
            trace.provider_calls["rerank"] = 1
            trace.result_source = "rrf_fallback" if trace.fallback else "rerank"
        else:
            trace.stage_latency_ms["rerank"] = 0.0
            trace.provider_calls["rerank"] = 0
        trace.result_count = len(eligible)
        trace.stage_latency_ms["total"] = round((time.perf_counter() - started) * 1000, 4)
        capture = _PRODUCT_SEARCH_EVALUATION_CAPTURE.get()
        if capture is not None:
            capture.traces.append(trace)
        return ProductSearchResult(eligible, ranked_ids, trace)


product_search_pipeline = ProductSearchPipeline()
