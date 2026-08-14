"""Evaluation adapter for the real ProductService request path."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.evaluation.ranking import (
    aggregate_ranking_cases,
    aggregate_stage_latency,
    ranking_case_metrics,
)
from app.rag.embedding import embedding_evaluation_scope
from app.rag.retriever import rerank_evaluation_scope
from app.services.java_internal_client import java_internal_client
from app.services.product_search_pipeline import product_search_evaluation_scope
from app.services.product_service import ProductService
from benchmarks.mature_eval.common import atomic_write_json, sha256_file, write_gzip_json


def _product_id(product: Mapping[str, Any]) -> str:
    return str(product.get("product_id") or product.get("productId") or product.get("id") or "")


async def _load_authoritative_availability(
    cases: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, bool | None], dict[str, Any]]:
    relevant_ids = sorted(
        {
            str(product_id)
            for case in cases
            for product_id, grade in (case.get("relevanceGrades") or {}).items()
            if float(grade) >= 2
        }
    )
    if not relevant_ids:
        return {}, {"status": "NOT_APPLICABLE", "relevantProductCount": 0}
    try:
        batch = await java_internal_client.snapshot_batch(relevant_ids)
    except Exception as exc:
        return {}, {
            "status": "ERROR",
            "errorType": type(exc).__name__,
            "relevantProductCount": len(relevant_ids),
        }
    products = {
        _product_id(product): product
        for product in (batch or {}).get("products") or []
        if _product_id(product)
    }
    stocks = {
        str(product_id): value
        for product_id, value in ((batch or {}).get("total_stocks") or {}).items()
    }
    availability: dict[str, bool | None] = {}
    for product_id in relevant_ids:
        product = products.get(product_id)
        if product is None:
            availability[product_id] = None
            continue
        status = product.get("status")
        on_sale = status is None or str(status) == "1"
        stock_value = stocks.get(product_id)
        try:
            in_stock = None if stock_value is None else float(stock_value) > 0
        except (TypeError, ValueError):
            in_stock = None
        availability[product_id] = (
            False if not on_sale or in_stock is False else True if in_stock is True else None
        )
    return availability, {
        "status": "EXECUTED",
        "source": "java-product-snapshot",
        "relevantProductCount": len(relevant_ids),
        "availableCount": sum(value is True for value in availability.values()),
        "unavailableCount": sum(value is False for value in availability.values()),
        "unknownCount": sum(value is None for value in availability.values()),
        "unavailableProductIds": sorted(
            product_id for product_id, value in availability.items() if value is False
        ),
    }


async def run_product_service_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    output_path: Path,
    service: ProductService | None = None,
    top_k: int = 10,
    evaluate_authoritative_availability: bool = False,
    authoritative_availability: Mapping[str, bool | None] | None = None,
) -> dict[str, Any]:
    """Call ProductService without gold constraints or dataset-aware shortcuts."""

    service = service or ProductService()
    availability_facts: dict[str, Any] = {"status": "NOT_REQUESTED"}
    availability = dict(authoritative_availability or {})
    if authoritative_availability is not None:
        availability_facts = {
            "status": "PROVIDED",
            "source": "test-or-preloaded",
            "relevantProductCount": len(availability),
        }
    elif evaluate_authoritative_availability:
        availability, availability_facts = await _load_authoritative_availability(cases)
    rows: list[dict[str, Any]] = []
    with embedding_evaluation_scope(bypass_cache=True) as embedding_stats:
        with rerank_evaluation_scope() as rerank_stats:
            for case in cases:
                case_id = str(case.get("id") or "")
                query = str(case.get("query") or "")
                started = time.perf_counter()
                status = "ERROR"
                error_type: str | None = None
                products: list[dict[str, Any]] = []
                source = "none"
                response_type = "unknown"
                trace: dict[str, Any] | None = None
                try:
                    with product_search_evaluation_scope() as capture:
                        _assistant, _biz_data, response_type, products, source = (
                            await service.search_products(
                                user_id=f"search-v2-eval-{case_id}",
                                keyword=query,
                                user_text=query,
                            )
                        )
                    trace = capture.traces[-1].public() if capture.traces else None
                    status = "EXECUTED"
                except Exception as exc:  # retained as a failed evidence case
                    error_type = type(exc).__name__
                ranked_ids = [_product_id(product) for product in products]
                ranked_ids = [value for value in ranked_ids if value]
                metrics = ranking_case_metrics(
                    ranked_ids,
                    case.get("relevanceGrades") or {},
                    k_values=(1, 2, 3, 5, 10, 20),
                    relevant_threshold=2,
                    expected_no_results=bool(case.get("expectedNoResults")),
                )
                relevant_grades = {
                    str(product_id): grade
                    for product_id, grade in (case.get("relevanceGrades") or {}).items()
                    if float(grade) >= 2
                }
                unavailable_relevant = sorted(
                    product_id
                    for product_id in relevant_grades
                    if availability.get(product_id) is False
                )
                purchasable_grades = {
                    product_id: grade
                    for product_id, grade in (case.get("relevanceGrades") or {}).items()
                    if availability.get(str(product_id)) is not False
                }
                has_purchasable_relevant = any(
                    float(grade) >= 2 for grade in purchasable_grades.values()
                )
                availability_metrics = (
                    ranking_case_metrics(
                        ranked_ids,
                        purchasable_grades,
                        k_values=(1, 2, 3, 5, 10, 20),
                        relevant_threshold=2,
                        expected_no_results=bool(case.get("expectedNoResults"))
                        or (bool(relevant_grades) and not has_purchasable_relevant),
                    )
                    if availability_facts.get("status") in {"EXECUTED", "PROVIDED"}
                    else None
                )
                stage_latency = dict((trace or {}).get("stageLatencyMs") or {})
                stage_latency["runnerTotal"] = round(
                    (time.perf_counter() - started) * 1000, 4
                )
                rows.append(
                    {
                        "caseId": case_id,
                        "split": case.get("split"),
                        "status": status,
                        "errorType": error_type,
                        "route": source,
                        "responseType": response_type,
                        "rankedIds": ranked_ids[:top_k],
                        "metrics": metrics,
                        "availabilityAdjustedMetrics": availability_metrics,
                        "unavailableRelevantIds": unavailable_relevant,
                        "runtimeTrace": trace,
                        "stageLatencyMs": stage_latency,
                        "goldUsedByRuntime": False,
                    }
                )

    provider_facts = {
        "embedding": embedding_stats.snapshot(),
        "rerank": rerank_stats.snapshot(),
    }
    payload = {
        "schemaVersion": 2,
        "kind": "product-service-runtime-search",
        "caseCount": len(rows),
        "executedCount": sum(row["status"] == "EXECUTED" for row in rows),
        "routeCounts": {
            route: sum(row["route"] == route for row in rows)
            for route in sorted({str(row["route"]) for row in rows})
        },
        "metrics": aggregate_ranking_cases([row["metrics"] for row in rows]),
        "availabilityAdjustedMetrics": (
            aggregate_ranking_cases(
                [
                    row["availabilityAdjustedMetrics"]
                    for row in rows
                    if row.get("availabilityAdjustedMetrics") is not None
                ]
            )
            if any(row.get("availabilityAdjustedMetrics") is not None for row in rows)
            else None
        ),
        "availabilityFacts": availability_facts,
        "stageLatency": aggregate_stage_latency(rows),
        "providerFacts": provider_facts,
        "cases": rows,
    }
    write_gzip_json(output_path, payload)
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".sha256.json"),
        {"path": output_path.name, "sha256": sha256_file(output_path)},
    )
    return payload
