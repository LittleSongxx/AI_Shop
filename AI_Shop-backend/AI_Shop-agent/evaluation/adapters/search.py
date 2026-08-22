from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from app.rag.embedding import embedding_evaluation_scope
from app.rag.retriever import rerank_evaluation_scope
from app.services.java_internal_client import delegated_user_scope
from app.services.product_constraint_evidence import evaluate_excluded_terms
from app.services.product_search_pipeline import product_search_evaluation_scope
from app.services.product_service import product_service
from evaluation.adapters.common import assertion, provider_complete
from evaluation.core.catalog import load_catalog_fixture
from evaluation.core.contracts import (
    CaseResult,
    CaseStatus,
    Domain,
    EvaluationCase,
)
from evaluation.core.io import utc_now
from evaluation.core.metrics import ndcg_at_k, recall_at_k, reciprocal_rank_at_k
from evaluation.core.slices import evaluate_case_metamorphic_contract
from evaluation.core.usage import merge_usage, normalize_usage


def _product_id(product: Mapping[str, Any]) -> str:
    return str(product.get("product_id") or product.get("productId") or product.get("id") or "")


def _product_text(product: Mapping[str, Any]) -> str:
    return " ".join(
        str(product.get(key) or "")
        for key in (
            "product_name",
            "productName",
            "product_desc",
            "productDesc",
            "brand",
            "category",
            "categoryName",
        )
    ).casefold()


def _price(product: Mapping[str, Any]) -> float | None:
    for key in ("estimated_payable", "price", "min_price", "minPrice"):
        try:
            if product.get(key) is not None:
                return float(product[key])
        except (TypeError, ValueError):
            continue
    return None


def constraint_violations(
    products: Sequence[Mapping[str, Any]],
    constraints: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    required_brands = {str(value).casefold() for value in constraints.get("requiredBrands") or []}
    excluded_brands = {str(value).casefold() for value in constraints.get("excludedBrands") or []}
    excluded_terms = {str(value).casefold() for value in constraints.get("excludedTerms") or []}
    for product in products:
        product_id = _product_id(product)
        text = _product_text(product)
        price = _price(product)
        if constraints.get("budgetMin") is not None and (
            price is None or price < float(constraints["budgetMin"])
        ):
            rows.append({"productId": product_id, "rule": "BUDGET_MIN"})
        if constraints.get("budgetMax") is not None and (
            price is None or price > float(constraints["budgetMax"])
        ):
            rows.append({"productId": product_id, "rule": "BUDGET_MAX"})
        if required_brands and not any(brand in text for brand in required_brands):
            rows.append({"productId": product_id, "rule": "REQUIRED_BRAND"})
        if any(brand in text for brand in excluded_brands):
            rows.append({"productId": product_id, "rule": "EXCLUDED_BRAND"})
        if evaluate_excluded_terms(product, tuple(excluded_terms), selected_only=True)[
            "violates"
        ]:
            rows.append({"productId": product_id, "rule": "EXCLUDED_TERM"})
    return rows


async def run_search_case(case: EvaluationCase, *, user_id: str) -> CaseResult:
    started_at = utc_now()
    started = time.perf_counter()
    expected = case.expected
    query = str(case.input["query"])
    constraints = case.input.get("constraints") or {}
    with (
        delegated_user_scope(user_id),
        product_search_evaluation_scope() as search_capture,
        embedding_evaluation_scope(bypass_cache=True) as embedding_stats,
        rerank_evaluation_scope() as rerank_stats,
    ):
        _assistant, _biz_data, _biz_type, products, source = await product_service.search_products(
            user_id=user_id,
            keyword=query,
            user_text=query,
            # Ranking decisions are persisted under a unique request key. A
            # fixed case-only ID made a second evaluation collide with the
            # first run's durable ledger and produced misleading IntegrityError
            # warnings (and potentially stale attribution joins).
            request_id=f"eval-{user_id}-{case.case_id}",
            runtime_constraints=constraints,
        )
    latency_ms = (time.perf_counter() - started) * 1000
    ranking = [_product_id(product) for product in products if _product_id(product)]
    qrels = {str(key): int(value) for key, value in expected["qrels"].items()}
    unjudged: list[str] = []
    metric_ranking = ranking
    if expected["judgmentMode"] == "JUDGED_POOL":
        judged = {str(value) for value in expected["judgedDocumentIds"]}
        unjudged = [doc_id for doc_id in ranking if doc_id not in judged]
        metric_ranking = [doc_id for doc_id in ranking if doc_id in judged]
    no_result_expected = bool(expected.get("noResult"))
    violations = constraint_violations(products, constraints)
    catalog_ids = [
        str(item.get("productId") or "")
        for item in load_catalog_fixture().get("products") or []
        if str(item.get("productId") or "")
    ]
    unknown_product_ids = sorted(set(ranking) - set(catalog_ids))
    metamorphic = evaluate_case_metamorphic_contract(
        relations=tuple(str(value) for value in expected.get("metamorphicRelations") or []),
        expected={**expected, "constraints": constraints, "catalogIds": catalog_ids},
        output={
            "products": products,
            "catalogIds": catalog_ids,
        },
    )
    facts = {
        "embedding": embedding_stats.snapshot(),
        "rerank": rerank_stats.snapshot(),
    }
    effective_required = set(case.required_providers)
    if source == "clarify" and not search_capture.traces:
        # Clarification is a deterministic product decision, not a silent
        # provider miss. Preserve the zero-call ledger and mark only explicitly
        # required providers N/A for this audited short path.
        for provider in effective_required.intersection(facts):
            snapshot = facts[provider]
            request_count = max(
                int(snapshot.get("providerRequests") or 0),
                int(snapshot.get("requests") or 0),
            )
            failure_count = max(
                int(snapshot.get("providerFailures") or 0),
                int(snapshot.get("failures") or 0),
            )
            if request_count == 0 and failure_count == 0:
                snapshot["notApplicable"] = True
                snapshot["notApplicableReason"] = "search_clarification_path"
    # The production reranker intentionally short-circuits a single eligible
    # product. Record this as an explicit N/A rather than a provider failure.
    if (
        "rerank" in effective_required
        and int(facts["rerank"].get("eligibleRequests") or 0) == 0
        and search_capture.traces
        and all(trace.result_count <= 1 for trace in search_capture.traces)
    ):
        facts["rerank"]["notApplicable"] = True
        max_result_count = max(trace.result_count for trace in search_capture.traces)
        facts["rerank"]["notApplicableReason"] = (
            "single_eligible_product"
            if max_result_count == 1
            else "no_eligible_product_after_hard_filter"
        )
    if int(facts["rerank"].get("eligibleRequests") or 0) > 0:
        effective_required.add("rerank")
    complete, provider_facts = provider_complete(sorted(effective_required), facts)
    metrics: dict[str, float | int] = {
        "providerCompleteness": complete,
        "constraintViolationCount": len(violations),
        "hardConstraintBypassCount": len(violations),
        # Search answers are authoritative product records. A returned ID that
        # is absent from the locked catalog is treated as an unsafe fabricated
        # result, including during provider partial-failure recovery.
        "unsafeAnswerCount": len(unknown_product_ids),
        "hardConstraintSatisfaction": int(not violations),
        # Evaluate presence/absence on every case so the denominator cannot
        # be changed by adding or removing no-result examples.
        "noResultAccuracy": int(bool(not ranking) == no_result_expected),
    }
    if no_result_expected:
        quality_passed = not ranking
    else:
        metrics.update(
            {
                "recallAt3": recall_at_k(metric_ranking, qrels, 3),
                "recallAt5": recall_at_k(metric_ranking, qrels, 5),
                "recallAt10": recall_at_k(metric_ranking, qrels, 10),
                "mrrAt10": reciprocal_rank_at_k(metric_ranking, qrels, 10),
                "ndcgAt5": ndcg_at_k(metric_ranking, qrels, 5),
                "ndcgAt10": ndcg_at_k(metric_ranking, qrels, 10),
            }
        )
        quality_passed = metrics["recallAt10"] > 0
    assertions = [
        assertion("provider-complete", complete == 1, provider_facts),
        assertion("no-constraint-violations", not violations, violations),
        assertion("no-unrecognized-products", not unknown_product_ids, unknown_product_ids),
        assertion("case-relevance-contract", quality_passed, ranking),
    ]
    passed = all(row["passed"] for row in assertions)
    usage = merge_usage(
        [
            normalize_usage(
                None,
                provider="embedding",
                model=str(facts["embedding"].get("model") or "unknown"),
                default_calls=int(facts["embedding"].get("providerRequests") or 0),
            ),
            normalize_usage(
                None,
                provider="rerank",
                model=str(facts["rerank"].get("model") or "unknown"),
                default_calls=int(facts["rerank"].get("providerRequests") or 0),
            ),
        ]
    )
    return CaseResult(
        case_id=case.case_id,
        domain=Domain.SEARCH,
        status=CaseStatus.PASSED if passed else CaseStatus.FAILED,
        metrics=metrics,
        latency_ms=latency_ms,
        output={
            "query": query,
            "ranking": ranking,
            "resultSource": source,
            "products": [
                {
                    "productId": _product_id(product),
                    "productName": product.get("product_name") or product.get("productName"),
                    "price": _price(product),
                }
                for product in products
            ],
            "unjudgedDocumentIds": unjudged,
            "trace": [trace.public() for trace in search_capture.traces],
            "sliceTags": list(case.slice_tags),
            "rejectionReasons": [
                reason
                for trace in search_capture.traces
                for reason in (trace.public().get("rejectionReasons") or [])
            ],
            "constraints": constraints,
            "catalogIds": catalog_ids,
            "unknownProductIds": unknown_product_ids,
            "metamorphicChecks": metamorphic,
            "usage": usage,
        },
        providers=provider_facts,
        assertions=assertions,
        started_at=started_at,
        completed_at=utc_now(),
        usage=usage,
        slice=case.slice_tags[0] if case.slice_tags else None,
    )
