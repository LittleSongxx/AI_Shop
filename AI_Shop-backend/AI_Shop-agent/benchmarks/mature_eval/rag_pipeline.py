"""RAG retrieval cold collection and BM25/Vector/RRF/Rerank ablation replay."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.evaluation.ranking import (
    aggregate_ranking_cases,
    aggregate_stage_latency,
    paired_ranking_comparison,
    ranking_case_metrics,
)
from app.rag.embedding import embedding_evaluation_scope
from app.rag.evaluation import _matches_expected, evaluate_results
from app.rag.retriever import rag_retriever, rerank_evaluation_scope
from benchmarks.mature_eval.common import (
    atomic_write_json,
    read_gzip_json,
    sha256_file,
    write_gzip_json,
)

RAG_VARIANTS = ("bm25", "vector", "rrf", "rrf_rerank", "production")
RAG_K_VALUES = (1, 3, 5, 10, 20)


def load_rag_sets(
    public_path: Path,
    regression_path: Path,
    fresh_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    from scripts.eval_rag import load_cases

    public = load_cases(public_path)
    regression = load_cases(regression_path)
    fresh = load_cases(fresh_path)
    if len(public) != 34 or len(regression) != 16 or len(fresh) != 14:
        raise ValueError(
            f"RAG v2 requires 34 public + 16 regression + 14 fresh cases, got "
            f"{len(public)} + {len(regression)} + {len(fresh)}"
        )
    ids = [str(case.get("id") or "") for case in [*public, *regression, *fresh]]
    if "" in ids or len(ids) != len(set(ids)):
        raise ValueError("RAG v2 case IDs must be non-empty and unique across splits")
    for case in public:
        case.setdefault("split", "public")
    for case in regression:
        case["split"] = "regression"
    for case in fresh:
        if case.get("split") != "fresh_holdout":
            raise ValueError("RAG fresh cases must use split=fresh_holdout")
    return {"public": public, "regression": regression, "fresh_holdout": fresh}


def _doc_to_ref(doc: Mapping[str, Any], version: int) -> dict[str, Any]:
    return rag_retriever._source_refs([dict(doc)], version)[0]


def _expected_grades(case: Mapping[str, Any]) -> dict[str, float]:
    refs = case.get("relevantRefs") or []
    return {str(index): float(ref.get("grade", ref.get("relevance", 1))) for index, ref in enumerate(refs)}


def _ref_ranking_ids(case: Mapping[str, Any], refs: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, float]]:
    expected = [ref for ref in case.get("relevantRefs") or [] if isinstance(ref, Mapping)]
    grades = _expected_grades(case)
    ranked: list[str] = []
    for ref_index, ref in enumerate(refs):
        matched = next(
            (index for index, expected_ref in enumerate(expected) if _matches_expected(dict(expected_ref), dict(ref))),
            None,
        )
        ranked.append(str(matched) if matched is not None else f"unjudged:{ref_index}:{ref.get('id')}")
    return ranked, grades


async def collect_rag_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    output_path: Path,
    candidate_size: int = 20,
) -> dict[str, Any]:
    """Collect raw channels and full rerank order once for offline replay."""

    previous: dict[str, Any] = {}
    if output_path.is_file():
        previous = read_gzip_json(output_path)
        if (
            previous.get("kind") != "rag-cold-collection"
            or int(previous.get("candidateSize") or 0) != candidate_size
        ):
            raise ValueError("existing RAG checkpoint does not match collection settings")
    rows = [dict(row) for row in previous.get("cases") or []]
    existing_by_id = {str(row.get("caseId") or ""): row for row in rows}
    requested_ids = [str(case.get("id") or "") for case in cases]
    if "" in requested_ids or len(requested_ids) != len(set(requested_ids)):
        raise ValueError("RAG collection case IDs must be non-empty and unique")
    if not set(existing_by_id).issubset(requested_ids):
        raise ValueError("RAG checkpoint contains cases outside this collection")
    old_facts = previous.get("providerFacts") or {}

    def merge_stats(old: Mapping[str, Any], current: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
        result = {
            key: int(old.get(key) or 0) + int(current.get(key) or 0)
            for key in keys
        }
        result["responseRecords"] = [
            *(old.get("responseRecords") or []),
            *(current.get("responseRecords") or []),
        ]
        return result

    def provider_snapshot(embedding_stats: Any, rerank_stats: Any) -> dict[str, Any]:
        embedding_current = embedding_stats.snapshot()
        embedding = merge_stats(
            old_facts.get("embedding") or {},
            embedding_current,
            (
                "requests",
                "cacheHits",
                "providerRequests",
                "providerSuccesses",
                "providerFailures",
                "breakerRejections",
            ),
        )
        embedding["bypassCache"] = True
        rerank_current = rerank_stats.snapshot()
        rerank = merge_stats(
            old_facts.get("rerank") or {},
            rerank_current,
            (
                "eligibleRequests",
                "providerRequests",
                "providerSuccesses",
                "providerFailures",
                "fallbackCount",
            ),
        )
        reasons: dict[str, int] = {}
        for source in (
            (old_facts.get("rerank") or {}).get("fallbackReasons") or {},
            rerank_current.get("fallbackReasons") or {},
        ):
            for key, value in source.items():
                reasons[str(key)] = reasons.get(str(key), 0) + int(value)
        rerank["fallbackReasons"] = dict(sorted(reasons.items()))
        return {"embedding": embedding, "rerank": rerank}

    def checkpoint(provider_facts: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schemaVersion": 1,
            "kind": "rag-cold-collection",
            "candidateSize": candidate_size,
            "providerFacts": dict(provider_facts),
            "cases": rows,
        }
        write_gzip_json(output_path, payload)
        return payload

    with embedding_evaluation_scope(bypass_cache=True) as embedding_stats, rerank_evaluation_scope() as rerank_stats:
        for case in cases:
            case_id = str(case["id"])
            if case_id in existing_by_id:
                if existing_by_id[case_id].get("query") != case.get("query"):
                    raise ValueError(f"RAG checkpoint query changed for {case_id}")
                continue
            query = rag_retriever.normalize_query(str(case.get("query") or ""))
            catalog = await rag_retriever._knowledge_catalog()
            version = int((catalog or {}).get("version") or 0)
            exact_started = time.perf_counter()
            exact = await rag_retriever._exact_faq(query, version)
            exact_ms = round((time.perf_counter() - exact_started) * 1000, 4)
            filters: list[dict[str, Any]] = []
            knowledge_enabled = bool(catalog)
            if knowledge_enabled:
                filters.append(
                    {
                        "bool": {
                            "should": [
                                {"term": {"metadata.dataType": "faq"}},
                                {
                                    "bool": {
                                        "must": [
                                            {"term": {"metadata.dataType": "knowledge"}},
                                            {"range": {"metadata.version": {"lte": version}}},
                                            {"terms": {"metadata.documentId": catalog.get("active_document_ids") or ["__none__"]}},
                                        ]
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            data_types = ("faq", "knowledge") if knowledge_enabled else ("faq",)
            bm25_started = time.perf_counter()
            bm25 = await rag_retriever._keyword_search_docs(query, data_types, candidate_size, filters)
            bm25_ms = round((time.perf_counter() - bm25_started) * 1000, 4)
            vector_started = time.perf_counter()
            vector = await rag_retriever._vector_search(
                query,
                data_types,
                candidate_size,
                extra_filters=filters,
            )
            vector_ms = round((time.perf_counter() - vector_started) * 1000, 4)
            rrf_started = time.perf_counter()
            rrf = rag_retriever._rrf_docs([bm25, vector], candidate_size)
            rrf_ms = round((time.perf_counter() - rrf_started) * 1000, 4)
            rerank_started = time.perf_counter()
            reranked = await rag_retriever._rerank(query, rrf, len(rrf)) if rrf else []
            rerank_ms = round((time.perf_counter() - rerank_started) * 1000, 4)
            rows.append(
                {
                    "caseId": case_id,
                    "split": case.get("split"),
                    "query": case.get("query"),
                    "noAnswer": bool(case.get("noAnswer")),
                    "injection": bool(case.get("injection")),
                    "case": dict(case),
                    "knowledgeVersion": version,
                    "exactFaq": rag_retriever._source_refs(
                        [rag_retriever._faq_row_to_doc(exact, 1.0)], version
                    ) if exact else [],
                    "bm25": [rag_retriever._source_refs([doc], version)[0] for doc in bm25],
                    "vector": [rag_retriever._source_refs([doc], version)[0] for doc in vector],
                    "rrf": [rag_retriever._source_refs([doc], version)[0] for doc in rrf],
                    "rerank": [rag_retriever._source_refs([doc], version)[0] for doc in reranked],
                    "stageLatencyMs": {
                        "exactFaq": exact_ms,
                        "bm25": bm25_ms,
                        "vector": vector_ms,
                        "rrf": rrf_ms,
                        "rerank": rerank_ms,
                    },
                }
            )
            checkpoint(provider_snapshot(embedding_stats, rerank_stats))
        provider_facts = provider_snapshot(embedding_stats, rerank_stats)
    if provider_facts["embedding"]["cacheHits"] or provider_facts["embedding"]["providerFailures"]:
        raise RuntimeError("RAG cold collection embedding evidence is incomplete")
    if provider_facts["rerank"]["providerFailures"] or provider_facts["rerank"]["fallbackCount"]:
        raise RuntimeError("RAG cold collection detected rerank fallback")
    payload = checkpoint(provider_facts)
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".sha256.json"),
        {"path": output_path.name, "sha256": sha256_file(output_path)},
    )
    return payload


def _candidate_refs(
    row: Mapping[str, Any],
    *,
    variant: str,
    rerank_top_n: int,
    evidence_threshold: float,
) -> list[dict[str, Any]]:
    exact = [dict(ref) for ref in row.get("exactFaq") or []]
    if variant == "production" and exact:
        return exact[:1]
    source = {
        "bm25": row.get("bm25") or [],
        "vector": row.get("vector") or [],
        "rrf": row.get("rrf") or [],
        "rrf_rerank": row.get("rerank") or [],
        "production": row.get("rerank") or [],
    }[variant]
    refs = [dict(ref) for ref in source]
    if variant in {"rrf_rerank", "production"}:
        refs = refs[:rerank_top_n]
    if variant == "production":
        refs = [ref for ref in refs if float(ref.get("score") or 0) >= evidence_threshold]
    return refs


def replay_rag_collection(
    collection: Mapping[str, Any] | Path,
    *,
    variants: Sequence[str] = RAG_VARIANTS,
    rerank_top_n_values: Sequence[int] = (3, 6, 10),
    evidence_thresholds: Sequence[float] = (0.55, 0.65, 0.75),
    k_values: Sequence[int] = RAG_K_VALUES,
    split_filter: set[str] | None = None,
) -> dict[str, Any]:
    payload = read_gzip_json(collection) if isinstance(collection, Path) else dict(collection)
    variant_metrics: dict[str, Any] = {}
    variant_cases: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        for top_n in (rerank_top_n_values if variant in {"rrf_rerank", "production"} else [20]):
            for threshold in (evidence_thresholds if variant == "production" else [0.0]):
                key = f"{variant}:n{top_n}:t{threshold:.2f}"
                cases: list[dict[str, Any]] = []
                results: list[dict[str, Any]] = []
                ranking_rows: list[dict[str, Any]] = []
                for row in payload.get("cases") or []:
                    if split_filter and str(row.get("split")) not in split_filter:
                        continue
                    case = dict(row["case"])
                    refs = _candidate_refs(
                        row,
                        variant=variant,
                        rerank_top_n=top_n,
                        evidence_threshold=threshold,
                    )
                    cases.append(case)
                    results.append(
                        {
                            "source_refs": refs,
                            "trace": {"hit": bool(refs), "latencyMs": None},
                        }
                    )
                    ranked_ids, labels = _ref_ranking_ids(case, refs)
                    ranking_rows.append(
                        ranking_case_metrics(
                            ranked_ids,
                            labels,
                            k_values=k_values,
                            expected_no_results=bool(case.get("noAnswer", not labels)),
                        )
                    )
                retrieval = evaluate_results(cases, results, top_k=max(k_values))
                curves = aggregate_ranking_cases(ranking_rows)
                variant_metrics[key] = {
                    "metricCurves": curves["metricCurves"],
                    "noResultAccuracy": curves["noResultAccuracy"],
                    "citationCorrectness": retrieval["citationCorrectness"],
                    "labelCitationPrecision": retrieval["labelCitationPrecision"],
                    "citationCoverage": retrieval["citationCoverage"],
                    "noAnswerAccuracy": retrieval["noAnswerAccuracy"],
                    "injectionRobustness": retrieval["injectionRobustness"],
                    "perCase": retrieval["perCase"],
                }
                variant_cases[key] = [
                    {"caseId": str(case["id"]), "ranking": ranking}
                    for case, ranking in zip(cases, ranking_rows)
                ]
    baseline_key = next(
        (key for key in variant_cases if key.startswith("rrf:")),
        next(iter(variant_cases), None),
    )
    paired_deltas: dict[str, Any] = {}
    if baseline_key:
        baseline = [
            {"caseId": row["caseId"], **row["ranking"]}
            for row in variant_cases[baseline_key]
            if row["ranking"].get("applicable")
        ]
        for key, rows in variant_cases.items():
            candidate = [
                {"caseId": row["caseId"], **row["ranking"]}
                for row in rows
                if row["ranking"].get("applicable")
            ]
            if len(candidate) != len(baseline):
                continue
            for metric_name, metric_key, metric_k in (
                ("ndcg", "ndcg", "5"),
                ("recall", "recall", "5"),
                ("reciprocalRank", "mrr", "10"),
            ):
                if not baseline or metric_k not in (baseline[0].get("metricsByK") or {}):
                    continue
                paired_deltas[f"{key}:{metric_key}@{metric_k}"] = paired_ranking_comparison(
                    baseline,
                    candidate,
                    metric_path=["metricsByK", metric_k, metric_name],
                )
    return {
        "schemaVersion": 1,
        "kind": "rag-offline-replay",
        "providerFacts": {
            "embeddingRequests": 0,
            "rerankRequests": 0,
            "sourceCollectionProviderFacts": payload.get("providerFacts") or {},
        },
        "metricCurves": {
            key: value["metricCurves"] for key, value in variant_metrics.items()
        },
        "variantMetrics": variant_metrics,
        "pairedDeltas": paired_deltas,
        "confidenceIntervals": {
            key: value["confidenceInterval"] for key, value in paired_deltas.items()
        },
        "stageLatency": aggregate_stage_latency(payload.get("cases") or []),
        "cases": variant_cases,
    }


def choose_rag_configuration(replay: Mapping[str, Any]) -> dict[str, Any]:
    candidates = {
        key: value
        for key, value in (replay.get("variantMetrics") or {}).items()
        if key.startswith("production:")
    }
    if not candidates:
        raise ValueError("RAG replay contains no production configuration candidates")

    def score(item: tuple[str, Mapping[str, Any]]) -> tuple[float, float, float, str]:
        key, report = item
        curve = (report.get("metricCurves") or {}).get("5") or {}
        return (
            float(curve.get("ndcg") or 0),
            float(curve.get("recall") or 0),
            float(report.get("noAnswerAccuracy") or 0),
            key,
        )

    key, metrics = max(candidates.items(), key=score)
    return {
        "selectedVariant": key,
        "selectionData": "public + known regression only",
        "selectedMetrics": metrics,
    }
