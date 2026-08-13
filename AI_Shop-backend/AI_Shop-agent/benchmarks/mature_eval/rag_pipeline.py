"""RAG retrieval cold collection and BM25/Vector/RRF/Rerank ablation replay."""

from __future__ import annotations

import asyncio
import hashlib
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
from app.harness.guardrails.channel_guard import scan_external_content
from app.harness.guardrails.query_security import separate_explicit_attack_suffix
from app.rag.canonical_facts import canonical_match, relevant_fact_ids
from app.rag.embedding import embedding_evaluation_scope
from app.rag.evaluation import _matches_expected, evaluate_results
from app.rag.query_expander import query_expansion_evaluation_scope
from app.rag.retriever import rag_retriever, rerank_evaluation_scope
from benchmarks.mature_eval.common import (
    atomic_write_json,
    read_gzip_json,
    sha256_file,
    write_gzip_json,
)

RAG_VARIANTS = ("bm25", "vector", "rrf", "rrf_rerank", "production")
RAG_K_VALUES = (1, 3, 5, 10, 20)
RAG_V3_EXPERIMENT_INSTRUCTION = (
    "Rank passages by whether they directly answer, constrain, or explicitly "
    "negate the user's e-commerce claim. Topic similarity alone must receive "
    "a low score."
)


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


def load_rag_v3_sets(
    public_path: Path,
    known_path: Path,
    fresh_path: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    from app.rag.canonical_facts import get_canonical_fact_catalog
    from scripts.eval_rag import load_cases

    public = load_cases(public_path)
    known = load_cases(known_path)
    fresh = load_cases(fresh_path) if fresh_path and fresh_path.is_file() else []
    expected = (48, 64, 32 if fresh_path else 0)
    actual = (len(public), len(known), len(fresh))
    if actual != expected:
        raise ValueError(f"RAG v3 requires {expected}, got {actual}")
    rows = [*public, *known, *fresh]
    ids = [str(case.get("id") or "") for case in rows]
    if "" in ids or len(ids) != len(set(ids)):
        raise ValueError("RAG v3 case IDs must be non-empty and unique")
    errors = [
        error
        for case in rows
        for error in get_canonical_fact_catalog().validate_case(case)
    ]
    if errors:
        raise ValueError("RAG v3 contract invalid:\n- " + "\n- ".join(errors))
    return {"public": public, "known_regression": known, "fresh_holdout": fresh}


def _doc_to_ref(doc: Mapping[str, Any], version: int) -> dict[str, Any]:
    return rag_retriever._source_refs([dict(doc)], version)[0]


def _expected_grades(case: Mapping[str, Any]) -> dict[str, float]:
    refs = case.get("relevantRefs") or []
    return {str(index): float(ref.get("grade", ref.get("relevance", 1))) for index, ref in enumerate(refs)}


def _ref_ranking_ids(case: Mapping[str, Any], refs: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, float]]:
    expected_facts = relevant_fact_ids(case)
    if expected_facts:
        labels = {fact_id: 1.0 for fact_id in expected_facts}
        ranked = []
        for ref_index, ref in enumerate(refs):
            matches = canonical_match(case, ref)
            ranked.append(
                sorted(matches)[0]
                if matches
                else f"unjudged:{ref_index}:{ref.get('id')}"
            )
        return ranked, labels
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
    collect_instruction_ablation: bool = False,
    primary_rerank_instruction: str | None = None,
    primary_rerank_channel: str = "rerank",
) -> dict[str, Any]:
    """Collect raw channels and full rerank order once for offline replay."""

    if primary_rerank_channel not in {"rerank", "rerankExperimental"}:
        raise ValueError("primary rerank channel is invalid")
    if collect_instruction_ablation and primary_rerank_channel != "rerank":
        raise ValueError("instruction ablation requires the base rerank channel")
    instruction_sha = (
        hashlib.sha256(primary_rerank_instruction.encode("utf-8")).hexdigest()
        if primary_rerank_instruction is not None
        else None
    )
    previous: dict[str, Any] = {}
    if output_path.is_file():
        previous = read_gzip_json(output_path)
        if (
            previous.get("kind") != "rag-cold-collection"
            or int(previous.get("candidateSize") or 0) != candidate_size
            or bool(previous.get("instructionAblation"))
            != bool(collect_instruction_ablation)
            or str(previous.get("primaryRerankChannel") or "rerank")
            != primary_rerank_channel
            or previous.get("primaryRerankInstructionSha256") != instruction_sha
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

    def provider_snapshot(
        embedding_stats: Any, rerank_stats: Any, expansion_stats: Any
    ) -> dict[str, Any]:
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
        expansion_current = expansion_stats.snapshot()
        expansion = merge_stats(
            old_facts.get("queryExpansion") or {},
            expansion_current,
            ("eligibleRequests", "providerRequests", "providerSuccesses", "providerFailures"),
        )
        expansion.pop("responseRecords", None)
        return {
            "embedding": embedding,
            "rerank": rerank,
            "queryExpansion": expansion,
        }

    def checkpoint(provider_facts: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schemaVersion": 1,
            "kind": "rag-cold-collection",
            "candidateSize": candidate_size,
            "instructionAblation": collect_instruction_ablation,
            "primaryRerankChannel": primary_rerank_channel,
            "primaryRerankInstructionSha256": instruction_sha,
            "providerFacts": dict(provider_facts),
            "cases": rows,
        }
        write_gzip_json(output_path, payload)
        return payload

    with (
        embedding_evaluation_scope(bypass_cache=True) as embedding_stats,
        rerank_evaluation_scope() as rerank_stats,
        query_expansion_evaluation_scope() as expansion_stats,
    ):
        for case in cases:
            case_id = str(case["id"])
            if case_id in existing_by_id:
                if existing_by_id[case_id].get("query") != case.get("query"):
                    raise ValueError(f"RAG checkpoint query changed for {case_id}")
                continue
            query = rag_retriever.normalize_query(str(case.get("query") or ""))
            separation = separate_explicit_attack_suffix(query)
            safe_query = rag_retriever.normalize_query(separation.safe_query)
            direct_verdict = scan_external_content(query)
            quarantined = direct_verdict.contaminated and not separation.separated
            if not safe_query or quarantined:
                rows.append(
                    {
                        "caseId": case_id,
                        "split": case.get("split"),
                        "query": case.get("query"),
                        "safeQuery": "",
                        "queryVariants": [],
                        "securityFlags": sorted(
                            set(separation.security_flags).union(
                                direct_verdict.matched_rules if quarantined else []
                            )
                        ),
                        "noAnswer": bool(case.get("noAnswer")),
                        "injection": bool(case.get("injection")),
                        "case": dict(case),
                        "knowledgeVersion": 0,
                        "exactFaq": [],
                        "bm25": [],
                        "vector": [],
                        "rrf": [],
                        "rerank": [],
                        "rerankExperimental": [],
                        "stageLatencyMs": {"exactFaq": 0, "bm25": 0, "vector": 0, "rrf": 0, "rerank": 0, "rerankExperimental": 0},
                    }
                )
                checkpoint(
                    provider_snapshot(embedding_stats, rerank_stats, expansion_stats)
                )
                continue
            query = safe_query
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
                                {"term": {"metadata.dataType.keyword": "faq"}},
                                {
                                    "bool": {
                                        "must": [
                                            {"term": {"metadata.dataType.keyword": "knowledge"}},
                                            {"term": {"metadata.status.keyword": "PUBLISHED"}},
                                            {"range": {"metadata.version": {"lte": version}}},
                                            {"terms": {"metadata.documentId.keyword": catalog.get("active_document_ids") or ["__none__"]}},
                                        ]
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            data_types = ("faq", "knowledge") if knowledge_enabled else ("faq",)
            query_variants = await rag_retriever._query_variants(query)
            bm25_started = time.perf_counter()
            bm25_groups = await asyncio.gather(
                *[
                    rag_retriever._keyword_search_docs(
                        variant, data_types, candidate_size, filters
                    )
                    for variant in query_variants
                ]
            )
            bm25 = rag_retriever._rrf_docs(bm25_groups, candidate_size)
            bm25_ms = round((time.perf_counter() - bm25_started) * 1000, 4)
            vector_started = time.perf_counter()
            vector_groups = await asyncio.gather(
                *[
                    rag_retriever._vector_search(
                        variant,
                        data_types,
                        candidate_size,
                        extra_filters=filters,
                    )
                    for variant in query_variants
                ]
            )
            vector = rag_retriever._rrf_docs(vector_groups, candidate_size)
            vector_ms = round((time.perf_counter() - vector_started) * 1000, 4)
            rrf_started = time.perf_counter()
            rrf = rag_retriever._rrf_docs([bm25, vector], candidate_size)
            rrf_ms = round((time.perf_counter() - rrf_started) * 1000, 4)
            rerank_started = time.perf_counter()
            reranked = (
                await rag_retriever._rerank(
                    query,
                    rrf,
                    len(rrf),
                    instruction_override=primary_rerank_instruction,
                )
                if rrf
                else []
            )
            rerank_ms = round((time.perf_counter() - rerank_started) * 1000, 4)
            experiment_started = time.perf_counter()
            experimental = (
                await rag_retriever._rerank(
                    query,
                    rrf,
                    len(rrf),
                    instruction_override=RAG_V3_EXPERIMENT_INSTRUCTION,
                )
                if rrf and collect_instruction_ablation
                else []
            )
            experiment_ms = round((time.perf_counter() - experiment_started) * 1000, 4)
            rows.append(
                {
                    "caseId": case_id,
                    "split": case.get("split"),
                    "query": case.get("query"),
                    "safeQuery": query,
                    "queryVariants": query_variants,
                    "securityFlags": list(separation.security_flags),
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
                    "rerank": [
                        rag_retriever._source_refs([doc], version)[0]
                        for doc in reranked
                    ] if primary_rerank_channel == "rerank" else [],
                    "rerankExperimental": [
                        rag_retriever._source_refs([doc], version)[0]
                        for doc in (
                            reranked
                            if primary_rerank_channel == "rerankExperimental"
                            else experimental
                        )
                    ],
                    "stageLatencyMs": {
                        "exactFaq": exact_ms,
                        "bm25": bm25_ms,
                        "vector": vector_ms,
                        "rrf": rrf_ms,
                        "rerank": rerank_ms if primary_rerank_channel == "rerank" else 0,
                        "rerankExperimental": (
                            rerank_ms
                            if primary_rerank_channel == "rerankExperimental"
                            else experiment_ms
                        ),
                    },
                }
            )
            checkpoint(
                provider_snapshot(embedding_stats, rerank_stats, expansion_stats)
            )
        provider_facts = provider_snapshot(
            embedding_stats, rerank_stats, expansion_stats
        )
    if provider_facts["embedding"]["cacheHits"] or provider_facts["embedding"]["providerFailures"]:
        raise RuntimeError("RAG cold collection embedding evidence is incomplete")
    if provider_facts["rerank"]["providerFailures"] or provider_facts["rerank"]["fallbackCount"]:
        raise RuntimeError("RAG cold collection detected rerank fallback")
    if provider_facts["queryExpansion"]["providerFailures"]:
        raise RuntimeError("RAG cold collection detected query expansion failure")
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
    top_score_margin: float | None = None,
    rerank_channel: str = "rerank",
) -> list[dict[str, Any]]:
    exact = [dict(ref) for ref in row.get("exactFaq") or []]
    if variant == "production" and exact:
        return exact[:1]
    source = {
        "bm25": row.get("bm25") or [],
        "vector": row.get("vector") or [],
        "rrf": row.get("rrf") or [],
        "rrf_rerank": row.get(rerank_channel) or [],
        "production": row.get(rerank_channel) or [],
    }[variant]
    refs = [dict(ref) for ref in source]
    if variant in {"rrf_rerank", "production"}:
        refs = refs[:rerank_top_n]
    if variant == "production":
        refs = [ref for ref in refs if float(ref.get("score") or 0) >= evidence_threshold]
        if top_score_margin is not None and refs:
            top_score = max(float(ref.get("score") or 0) for ref in refs)
            refs = [
                ref
                for ref in refs
                if float(ref.get("score") or 0) >= top_score - top_score_margin
            ]
    return refs


def replay_rag_collection(
    collection: Mapping[str, Any] | Path,
    *,
    variants: Sequence[str] = RAG_VARIANTS,
    rerank_top_n_values: Sequence[int] = (3, 6, 10),
    evidence_thresholds: Sequence[float] = (0.55, 0.65, 0.75),
    top_score_margins: Sequence[float | None] = (None,),
    rerank_channels: Sequence[str] = ("rerank",),
    k_values: Sequence[int] = RAG_K_VALUES,
    split_filter: set[str] | None = None,
) -> dict[str, Any]:
    payload = read_gzip_json(collection) if isinstance(collection, Path) else dict(collection)
    variant_metrics: dict[str, Any] = {}
    variant_cases: dict[str, list[dict[str, Any]]] = {}
    expanded_keys = len(top_score_margins) > 1 or len(rerank_channels) > 1
    for variant in variants:
        for top_n in (rerank_top_n_values if variant in {"rrf_rerank", "production"} else [20]):
            thresholds = evidence_thresholds if variant == "production" else [0.0]
            margins = top_score_margins if variant == "production" else [None]
            channels = (
                rerank_channels
                if variant in {"rrf_rerank", "production"}
                else ["rerank"]
            )
            for threshold in thresholds:
                for margin in margins:
                    for channel in channels:
                        channel_key = (
                            "exp" if channel == "rerankExperimental" else "base"
                        )
                        margin_key = "off" if margin is None else f"{margin:.2f}"
                        key = f"{variant}:n{top_n}:t{threshold:.2f}"
                        if expanded_keys:
                            key += f":m{margin_key}:i{channel_key}"
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
                                top_score_margin=margin,
                                rerank_channel=channel,
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
                                    expected_no_results=bool(
                                        case.get("noAnswer", not labels)
                                    ),
                                )
                            )
                        retrieval = evaluate_results(
                            cases, results, top_k=max(k_values)
                        )
                        curves = aggregate_ranking_cases(ranking_rows)
                        variant_metrics[key] = {
                            "metricCurves": curves["metricCurves"],
                            "noResultAccuracy": curves["noResultAccuracy"],
                            "citationCorrectness": retrieval["citationCorrectness"],
                            "labelCitationPrecision": retrieval[
                                "labelCitationPrecision"
                            ],
                            "citationCoverage": retrieval["citationCoverage"],
                            "canonicalCitationCorrectness": retrieval[
                                "canonicalCitationCorrectness"
                            ],
                            "canonicalCitationCoverage": retrieval[
                                "canonicalCitationCoverage"
                            ],
                            "strictExactRefPrecision": retrieval[
                                "strictExactRefPrecision"
                            ],
                            "noAnswerAccuracy": retrieval["noAnswerAccuracy"],
                            "injectionRobustness": retrieval[
                                "injectionRobustness"
                            ],
                            "rerankChannel": channel,
                            "topScoreMargin": margin,
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
    latency = aggregate_stage_latency(payload.get("cases") or [])
    for key, metrics in variant_metrics.items():
        channel = str(metrics.get("rerankChannel") or "rerank")
        stages = ("exactFaq", "bm25", "vector", "rrf", channel)
        samples = [
            float((latency.get(stage) or {}).get("p95Ms") or 0) for stage in stages
        ]
        metrics["localStageP95SumMs"] = round(sum(samples), 6)
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
        "stageLatency": latency,
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

    def score(item: tuple[str, Mapping[str, Any]]) -> tuple[float, ...]:
        key, report = item
        curve = (report.get("metricCurves") or {}).get("5") or {}
        injection = float(report.get("injectionRobustness") or 0)
        no_answer = float(report.get("noAnswerAccuracy") or 0)
        return (
            float(injection >= 1.0),
            injection,
            no_answer,
            float(curve.get("recall") or 0),
            float(report.get("canonicalCitationCoverage") or 0),
            float(curve.get("ndcg") or 0),
            float(curve.get("mrr") or 0),
            -float(report.get("localStageP95SumMs") or 0),
            key,
        )

    key, metrics = max(candidates.items(), key=score)
    return {
        "selectedVariant": key,
        "selectionData": "public + known regression only",
        "selectedMetrics": metrics,
    }
