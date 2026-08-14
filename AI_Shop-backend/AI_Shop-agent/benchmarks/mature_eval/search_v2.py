"""Search v2 cold collection and honest full-catalog replay."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.evaluation.ranking import (
    aggregate_incomplete_judgment_cases,
    aggregate_ranking_cases,
    aggregate_stage_latency,
    incomplete_judgment_case_metrics,
    paired_ranking_comparison,
    ranking_case_metrics,
)
from app.rag.embedding import embedding_evaluation_scope
from app.services.product_search_pipeline import (
    ProductQueryPlan,
    ProductRuntimeConstraints,
    build_product_query_plan,
    filter_products_by_runtime_constraints,
    filter_products_for_query_plan,
    merge_ranked_lists,
)
from app.services.shopping_mission_service import apply_explicit_turn
from benchmarks.mature_eval.common import (
    atomic_write_json,
    read_gzip_json,
    sha256_file,
    write_gzip_json,
)
from benchmarks.mature_eval.search_pipeline import SearchCollector, provider_audit, relevance_filter

SEARCH_V2_VARIANTS = (
    "raw_bm25",
    "normalized_bm25",
    "vector",
    "rrf",
    "runtime_filter",
    "full_rerank",
    "oracle_gold_filter",
)
WANDS_V2_VARIANTS = ("raw_bm25", "vector", "rrf", "full_rerank")
SEARCH_V2_K_VALUES = (1, 2, 3, 5, 10, 20)


def _is_external_incomplete_qrels(value: Any) -> bool:
    return str(value or "") == "full-catalog-incomplete-qrels"


def _product_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("product_id") or row.get("productId") or "")


def _runtime_plan(query: str, *, external: bool) -> Any:
    mission = (
        {}
        if external
        else apply_explicit_turn(
            None,
            profile={},
            user_text=query,
            message_id=0,
        )
        or {}
    )
    plan = build_product_query_plan(query, mission)
    if external:
        return type(plan)(
            raw_query=plan.raw_query,
            retrieval_variants=(plan.raw_query,),
            constraints=ProductRuntimeConstraints(),
            normalization_rules=(),
        )
    return plan


def _constraints(value: Mapping[str, Any] | None) -> ProductRuntimeConstraints:
    value = value or {}
    return ProductRuntimeConstraints(
        category=str(value.get("category") or "").strip() or None,
        budget_min=_float_or_none(value.get("budgetMin")),
        budget_max=_float_or_none(value.get("budgetMax")),
        required_brands=tuple(str(item) for item in value.get("requiredBrands") or []),
        excluded_brands=tuple(str(item) for item in value.get("excludedBrands") or []),
        excluded_terms=tuple(str(item) for item in value.get("excludedTerms") or []),
        use_cases=tuple(str(item) for item in value.get("useCases") or []),
        preferred_features=tuple(str(item) for item in value.get("preferredFeatures") or []),
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _provider_facts(
    old: Mapping[str, Any],
    audit: Mapping[str, Any],
    embedding: Mapping[str, Any],
) -> dict[str, Any]:
    old_embedding = old.get("embedding") if isinstance(old.get("embedding"), Mapping) else {}
    return {
        "embeddingRequests": int(old.get("embeddingRequests") or 0)
        + int(audit.get("embeddingRequests") or 0),
        "rerankRequests": int(old.get("rerankRequests") or 0)
        + int(audit.get("rerankRequests") or 0),
        "responseFacts": [*(old.get("responseFacts") or []), *(audit.get("responseFacts") or [])],
        "embedding": {
            **{
                key: int(old_embedding.get(key) or 0) + int(embedding.get(key) or 0)
                for key in (
                    "requests",
                    "cacheHits",
                    "providerRequests",
                    "providerSuccesses",
                    "providerFailures",
                    "breakerRejections",
                )
            },
            "bypassCache": True,
            "responseRecords": [
                *(old_embedding.get("responseRecords") or []),
                *(embedding.get("responseRecords") or []),
            ],
        },
    }


async def collect_v2_cases(
    *,
    cases: Sequence[Mapping[str, Any]],
    products: Sequence[Mapping[str, Any]],
    index: str,
    output_path: Path,
    candidate_size: int,
    rerank_pool_size: int = 50,
    rerank_request_char_budget: int = 48_000,
) -> dict[str, Any]:
    """Collect each Provider result once; gold labels never enter retrieval."""

    collector = SearchCollector(index, es_hosts=get_settings().es_hosts)
    products_by_id = {_product_id(row): row for row in products}
    previous = read_gzip_json(output_path) if output_path.is_file() else {}
    if previous and (
        previous.get("kind") != "search-v2-cold-collection"
        or previous.get("index") != index
        or int(previous.get("candidateSize") or 0) != candidate_size
    ):
        raise ValueError("existing Search v2 checkpoint does not match this collection")
    rows = [dict(row) for row in previous.get("cases") or []]
    completed = {str(row.get("caseId") or "") for row in rows}
    old_facts = dict(previous.get("providerFacts") or {})

    def checkpoint(facts: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schemaVersion": 2,
            "kind": "search-v2-cold-collection",
            "index": index,
            "candidateSize": candidate_size,
            "rerankPoolSize": rerank_pool_size,
            "rerankRequestCharBudget": rerank_request_char_budget,
            "providerFacts": dict(facts),
            "cases": rows,
        }
        write_gzip_json(output_path, payload)
        return payload

    expected_embedding_calls = sum(
        len(
            _runtime_plan(
                str(case.get("query") or ""),
                external=_is_external_incomplete_qrels(case.get("labelScope")),
            ).retrieval_variants
        )
        for case in cases
    )
    with provider_audit(allow_calls=True) as audit, embedding_evaluation_scope(
        bypass_cache=True
    ) as embedding_stats:
        for case in cases:
            case_id = str(case.get("id") or case.get("queryId") or "")
            if not case_id:
                raise ValueError("Search v2 case id is required")
            if case_id in completed:
                continue
            query = str(case.get("query") or "")
            external = _is_external_incomplete_qrels(case.get("labelScope"))
            plan = _runtime_plan(query, external=external)

            async def collect_variant(variant: str) -> tuple[str, list[str], list[str], list[float], dict[str, float]]:
                bm25_task = asyncio.create_task(collector.bm25(variant, candidate_size))
                vector_task = asyncio.create_task(collector.vector(variant, candidate_size))
                (bm25, bm25_ms), (vector, embedding, vector_ms) = await asyncio.gather(
                    bm25_task, vector_task
                )
                return variant, bm25, vector, embedding, {"bm25": bm25_ms, **vector_ms}

            collected = await asyncio.gather(
                *(collect_variant(variant) for variant in plan.retrieval_variants)
            )
            bm25_by_variant = {variant: bm25 for variant, bm25, _vector, _embedding, _latency in collected}
            vector_by_variant = {variant: vector for variant, _bm25, vector, _embedding, _latency in collected}
            embeddings = {variant: embedding for variant, _bm25, _vector, embedding, _latency in collected}
            stage_latency: dict[str, float] = {}
            for variant, _bm25, _vector, _embedding, latency in collected:
                for stage, value in latency.items():
                    stage_latency[f"{stage}:{variant}"] = float(value)

            rrf_started = time.perf_counter()
            raw_rankings = [
                *bm25_by_variant.values(),
                *vector_by_variant.values(),
            ]
            rrf = merge_ranked_lists(raw_rankings, candidate_size)
            stage_latency["rrf"] = round((time.perf_counter() - rrf_started) * 1000, 4)
            if external:
                runtime_filtered = list(rrf)
            else:
                runtime_filtered, _rejected = filter_products_for_query_plan(
                    [products_by_id[item] for item in rrf if item in products_by_id],
                    plan,
                )
                runtime_filtered = [_product_id(item) for item in runtime_filtered]
            oracle = (
                []
                if external
                else relevance_filter(
                    rrf,
                    products_by_id,
                    plan.raw_query,
                    constraints=case.get("constraints") if isinstance(case.get("constraints"), Mapping) else None,
                )
            )
            # One cold Provider call must support every offline RRF-k replay.
            # Build a deterministic union of the top candidates for all dev
            # values, then rerank the union once. WANDS has a fixed k=60 path.
            replay_rrf_ks = (60,) if external else (10, 60, 100)
            rerank_pool: list[str] = []
            for replay_rrf_k in replay_rrf_ks:
                scores: dict[str, float] = {}
                for ranking in raw_rankings:
                    for rank, product_id in enumerate(ranking, 1):
                        scores[product_id] = scores.get(product_id, 0.0) + 1.0 / (
                            replay_rrf_k + rank
                        )
                replay_rrf = [
                    product_id
                    for product_id, _score in sorted(
                        scores.items(), key=lambda item: (-item[1], item[0])
                    )[:candidate_size]
                ]
                if external:
                    replay_filtered = replay_rrf
                else:
                    replay_products, _rejected = filter_products_for_query_plan(
                        [
                            products_by_id[item]
                            for item in replay_rrf
                            if item in products_by_id
                        ],
                        plan,
                    )
                    replay_filtered = [_product_id(item) for item in replay_products]
                for product_id in replay_filtered:
                    if product_id not in rerank_pool:
                        rerank_pool.append(product_id)
                    if len(rerank_pool) >= rerank_pool_size:
                        break
                if len(rerank_pool) >= rerank_pool_size:
                    break
            reranked, rerank_ms, rerank_facts = await collector.rerank(
                plan.raw_query,
                rerank_pool,
                products_by_id,
                request_char_budget=rerank_request_char_budget,
            )
            stage_latency["rerank"] = rerank_ms
            rows.append(
                {
                    "caseId": case_id,
                    "query": query,
                    "split": case.get("split"),
                    "queryType": case.get("queryType"),
                    "labelScope": case.get("labelScope") or "full-catalog-complete-labels",
                    "expectedNoResults": bool(case.get("expectedNoResults")),
                    "relevanceGrades": dict(case.get("relevanceGrades") or {}),
                    "goldConstraints": dict(case.get("constraints") or {}),
                    "queryPlan": plan.public(),
                    "bm25ByVariant": bm25_by_variant,
                    "vectorByVariant": vector_by_variant,
                    "queryEmbeddings": embeddings,
                    "collectionRrf": rrf,
                    "runtimeFiltered": runtime_filtered,
                    "oracleGoldFiltered": oracle,
                    "rerankCandidatePool": rerank_pool,
                    "rerank": reranked,
                    "rerankFacts": rerank_facts,
                    "stageLatencyMs": stage_latency,
                }
            )
            checkpoint(_provider_facts(old_facts, audit.snapshot(), embedding_stats.snapshot()))
        facts = _provider_facts(old_facts, audit.snapshot(), embedding_stats.snapshot())

    embedding = facts["embedding"]
    if embedding["cacheHits"] or embedding["providerFailures"]:
        raise RuntimeError("Search v2 embedding evidence is incomplete")
    if len(rows) == len(cases):
        if facts["embeddingRequests"] != expected_embedding_calls:
            raise RuntimeError("Search v2 query embedding call count is incomplete")
        expected_rerank_calls = sum(
            bool(row.get("rerankCandidatePool")) for row in rows
        )
        if facts["rerankRequests"] != expected_rerank_calls:
            raise RuntimeError("Search v2 rerank call count is incomplete")
    payload = checkpoint(facts)
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".sha256.json"),
        {"path": output_path.name, "sha256": sha256_file(output_path)},
    )
    return payload


def _variant_ids(
    row: Mapping[str, Any],
    products_by_id: Mapping[str, Mapping[str, Any]],
    *,
    variant: str,
    candidate_count: int,
    rrf_k: int,
    rerank_top_n: int,
) -> list[str]:
    plan = row.get("queryPlan") if isinstance(row.get("queryPlan"), Mapping) else {}
    variants = [str(item) for item in plan.get("retrievalVariants") or []]
    raw = str(plan.get("rawQuery") or row.get("query") or "")
    bm25 = row.get("bm25ByVariant") if isinstance(row.get("bm25ByVariant"), Mapping) else {}
    vector = row.get("vectorByVariant") if isinstance(row.get("vectorByVariant"), Mapping) else {}
    raw_bm25 = list(bm25.get(raw) or [])[:candidate_count]
    normalized = next((item for item in variants if item != raw), raw)
    normalized_bm25 = list(bm25.get(normalized) or raw_bm25)[:candidate_count]
    raw_vector = list(vector.get(raw) or [])[:candidate_count]
    if variant == "raw_bm25":
        return raw_bm25
    if variant == "normalized_bm25":
        return normalized_bm25
    if variant == "vector":
        return raw_vector
    rankings = [
        list((bm25.get(item) or []))[:candidate_count] for item in variants
    ] + [list((vector.get(item) or []))[:candidate_count] for item in variants]
    scores: dict[str, float] = {}
    for ranked in rankings:
        for rank, product_id in enumerate(ranked, 1):
            scores[product_id] = scores.get(product_id, 0.0) + 1.0 / (rrf_k + rank)
    rrf = [
        product_id
        for product_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            :candidate_count
        ]
    ]
    if variant == "rrf":
        return rrf
    external = _is_external_incomplete_qrels(row.get("labelScope"))
    if external:
        filtered = rrf
    else:
        replay_plan = ProductQueryPlan(
            raw_query=raw,
            retrieval_variants=tuple(variants),
            constraints=_constraints(
                plan.get("runtimeConstraints") if isinstance(plan, Mapping) else {}
            ),
            normalization_rules=tuple(plan.get("normalizationRules") or []),
        )
        eligible, _rejected = filter_products_for_query_plan(
            [products_by_id[item] for item in rrf if item in products_by_id],
            replay_plan,
        )
        filtered = [_product_id(item) for item in eligible]
    if variant == "runtime_filter":
        return filtered
    if variant == "oracle_gold_filter":
        return relevance_filter(
            rrf,
            products_by_id,
            raw,
            constraints=row.get("goldConstraints") if isinstance(row.get("goldConstraints"), Mapping) else None,
        )
    if variant != "full_rerank":
        raise ValueError(f"unsupported Search v2 variant: {variant}")
    scores_by_id = {
        str(item.get("productId") or ""): float(item.get("score") or 0)
        for item in row.get("rerank") or []
        if isinstance(item, Mapping)
    }
    rerank_pool = set(str(item) for item in row.get("rerankCandidatePool") or [])
    rerankable = [item for item in filtered if item in rerank_pool]
    ranked = sorted(rerankable, key=lambda item: (-scores_by_id.get(item, float("-inf")), item))
    return ranked[:rerank_top_n]


def _constraint_parser_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = 0
    per_field: dict[str, dict[str, int]] = {}
    field_pairs = (
        ("category", "category"),
        ("priceMin", "budgetMin"),
        ("priceMax", "budgetMax"),
        ("requiredBrands", "requiredBrands"),
        ("excludedBrands", "excludedBrands"),
        ("scenario", "useCases"),
    )
    for row in rows:
        gold = row.get("goldConstraints") if isinstance(row.get("goldConstraints"), Mapping) else {}
        plan = row.get("queryPlan") if isinstance(row.get("queryPlan"), Mapping) else {}
        predicted = plan.get("runtimeConstraints") if isinstance(plan.get("runtimeConstraints"), Mapping) else {}
        for gold_key, predicted_key in field_pairs:
            expected = gold.get(gold_key)
            actual = predicted.get(predicted_key)
            expected_values = set(str(item).casefold() for item in expected) if isinstance(expected, list) else ({str(expected).casefold()} if expected not in (None, "") else set())
            actual_values = set(str(item).casefold() for item in actual) if isinstance(actual, list) else ({str(actual).casefold()} if actual not in (None, "") else set())
            field = per_field.setdefault(gold_key, {"tp": 0, "fp": 0, "fn": 0})
            field["tp"] += len(expected_values & actual_values)
            field["fp"] += len(actual_values - expected_values)
            field["fn"] += len(expected_values - actual_values)
            tp += len(expected_values & actual_values)
            fp += len(actual_values - expected_values)
            fn += len(expected_values - actual_values)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "truePositive": tp,
        "falsePositive": fp,
        "falseNegative": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "perField": per_field,
    }


def choose_v2_configuration(report: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze a runtime-reproducible configuration; oracle variants are excluded."""

    metrics = report.get("variantMetrics") or {}
    candidates: list[tuple[tuple[float, ...], str]] = []
    for key, value in metrics.items():
        if not str(key).startswith(("runtime_filter:", "full_rerank:")):
            continue
        curves = value.get("metricCurves") or {}
        k3 = curves.get("3") or {}
        k5 = curves.get("5") or {}
        k10 = curves.get("10") or {}
        constraint_violation = float(value.get("constraintViolationRate") or 0)
        score = (
            -constraint_violation,
            float(k5.get("ndcg") or 0),
            float(k3.get("recall") or 0),
            float(k10.get("mrr") or 0),
            1.0 if str(key).startswith("full_rerank:") else 0.0,
        )
        candidates.append((score, str(key)))
    if not candidates:
        raise ValueError("Search v2 replay has no runtime configuration candidates")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    score, selected = candidates[0]
    return {
        "selectedVariant": selected,
        "selectionOrder": [
            "constraintViolationRate",
            "ndcg@5",
            "recall@3",
            "mrr@10",
            "prefer-rerank-on-tie",
        ],
        "selectedScore": list(score),
        "diagnosticOracleExcluded": True,
    }


def replay_v2_collection(
    collection: Mapping[str, Any] | Path,
    *,
    products: Sequence[Mapping[str, Any]],
    variants: Sequence[str] = SEARCH_V2_VARIANTS,
    candidate_counts: Sequence[int] = (8, 16, 24, 50),
    rrf_k_values: Sequence[int] = (10, 60, 100),
    rerank_top_n_values: Sequence[int] = (6, 12, 24),
    k_values: Sequence[int] = SEARCH_V2_K_VALUES,
    split_filter: set[str] | None = None,
) -> dict[str, Any]:
    payload = read_gzip_json(collection) if isinstance(collection, Path) else dict(collection)
    products_by_id = {_product_id(row): row for row in products}
    source_rows = [
        row
        for row in payload.get("cases") or []
        if not split_filter or str(row.get("split")) in split_filter
    ]
    external = bool(source_rows) and all(
        _is_external_incomplete_qrels(row.get("labelScope")) for row in source_rows
    )
    variant_rows: dict[str, list[dict[str, Any]]] = {}
    with provider_audit(allow_calls=False) as audit:
        for variant in variants:
            for candidate_count in candidate_counts:
                rrf_values = rrf_k_values if variant in {"rrf", "runtime_filter", "full_rerank", "oracle_gold_filter"} else (60,)
                top_values = rerank_top_n_values if variant == "full_rerank" else (candidate_count,)
                for rrf_k in rrf_values:
                    for rerank_top_n in top_values:
                        key = f"{variant}:c{candidate_count}:rrf{rrf_k}:n{rerank_top_n}"
                        rows: list[dict[str, Any]] = []
                        for case in source_rows:
                            ranked = _variant_ids(
                                case,
                                products_by_id,
                                variant=variant,
                                candidate_count=candidate_count,
                                rrf_k=rrf_k,
                                rerank_top_n=rerank_top_n,
                            )
                            if external:
                                metrics = incomplete_judgment_case_metrics(
                                    ranked,
                                    case.get("relevanceGrades") or {},
                                    k_values=k_values,
                                )
                            else:
                                metrics = ranking_case_metrics(
                                    ranked,
                                    case.get("relevanceGrades") or {},
                                    k_values=k_values,
                                    relevant_threshold=2,
                                    expected_no_results=bool(case.get("expectedNoResults")),
                                )
                            violation_count = 0
                            if not external:
                                returned_products = [
                                    products_by_id[item]
                                    for item in ranked
                                    if item in products_by_id
                                ]
                                _eligible, rejected = filter_products_by_runtime_constraints(
                                    returned_products,
                                    _constraints(
                                        (
                                            case.get("queryPlan")
                                            if isinstance(case.get("queryPlan"), Mapping)
                                            else {}
                                        ).get("runtimeConstraints")
                                    ),
                                )
                                violation_count = len(rejected)
                            rows.append(
                                {
                                    "caseId": case["caseId"],
                                    "split": case.get("split"),
                                    "queryType": case.get("queryType"),
                                    "rankedIds": ranked,
                                    "metrics": metrics,
                                    "constraintViolationCount": violation_count,
                                    "constraintCheckedCount": len(ranked),
                                }
                            )
                        variant_rows[key] = rows
        provider_facts = audit.snapshot()

    aggregate = aggregate_incomplete_judgment_cases if external else aggregate_ranking_cases
    variant_metrics = {
        key: aggregate([row["metrics"] for row in rows])
        for key, rows in variant_rows.items()
        if rows
    }
    if not external:
        for key, rows in variant_rows.items():
            checked = sum(int(row.get("constraintCheckedCount") or 0) for row in rows)
            violations = sum(int(row.get("constraintViolationCount") or 0) for row in rows)
            variant_metrics[key]["constraintViolationCount"] = violations
            variant_metrics[key]["constraintCheckedCount"] = checked
            variant_metrics[key]["constraintViolationRate"] = round(
                violations / checked, 6
            ) if checked else 0.0
    baseline_key = next((key for key in variant_rows if key.startswith("rrf:c24:rrf60:")), next(iter(variant_rows), None))
    paired: dict[str, Any] = {}
    if baseline_key:
        available_k = {str(int(value)) for value in k_values}
        quality_k = "5" if "5" in available_k else str(max(int(value) for value in k_values))
        mrr_k = "10" if "10" in available_k else quality_k
        metric_paths = (
            (("metricsByK", quality_k, "condensedNdcg"), f"condensedNdcg@{quality_k}"),
            (("metricsByK", quality_k, "knownRelevantRecall"), f"knownRelevantRecall@{quality_k}"),
        ) if external else (
            (("metricsByK", quality_k, "ndcg"), f"ndcg@{quality_k}"),
            (("metricsByK", quality_k, "recall"), f"recall@{quality_k}"),
            (("metricsByK", mrr_k, "reciprocalRank"), f"mrr@{mrr_k}"),
        )
        baseline = [{"caseId": row["caseId"], **row["metrics"]} for row in variant_rows[baseline_key] if row["metrics"].get("applicable")]
        for key, rows in variant_rows.items():
            candidate = [{"caseId": row["caseId"], **row["metrics"]} for row in rows if row["metrics"].get("applicable")]
            if len(candidate) != len(baseline):
                continue
            for path, name in metric_paths:
                paired[f"{key}:{name}"] = paired_ranking_comparison(
                    baseline, candidate, metric_path=path
                )
    return {
        "schemaVersion": 2,
        "labelScope": "full-catalog-incomplete-qrels" if external else "full-catalog-complete-labels",
        "caseCount": len(source_rows),
        "variantMetrics": variant_metrics,
        "pairedDeltas": paired,
        "cases": variant_rows,
        "stageLatency": aggregate_stage_latency(source_rows),
        "providerFacts": provider_facts,
        "constraintParser": None if external else _constraint_parser_metrics(source_rows),
        "oracleDiagnosticOnly": "oracle_gold_filter" in variants,
    }
