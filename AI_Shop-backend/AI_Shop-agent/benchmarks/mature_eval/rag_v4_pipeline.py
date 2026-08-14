"""RAG v4 production-aligned collection and offline replay."""

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
from app.rag.canonical_facts import canonical_match, relevant_fact_ids
from app.rag.embedding import embedding_evaluation_scope
from app.rag.evaluation import evaluate_results
from app.rag.evidence_selector import evidence_item_limit
from app.rag.policy import RagRetrievalPolicy, rag_policy_scope, runtime_rag_policy
from app.rag.query_expander import query_expansion_evaluation_scope
from app.rag.query_planner import plan_rag_query
from app.rag.retriever import (
    evaluation_es_index_scope,
    rag_retriever,
    rerank_evaluation_scope,
)
from benchmarks.mature_eval.common import read_gzip_json, sha256_file, write_gzip_json

RAG_V4_VARIANTS = ("bm25", "vector", "rrf", "rrf_rerank", "production")
RAG_V4_K_VALUES = (1, 2, 3, 5, 10, 20)


def load_rag_v4_sets(
    public_path: Path,
    known_path: Path,
    fresh_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    from scripts.eval_rag import load_cases

    public = load_cases(public_path)
    known = load_cases(known_path)
    fresh = load_cases(fresh_path)
    expected = (72, 144, 48)
    actual = (len(public), len(known), len(fresh))
    if actual != expected:
        raise ValueError(f"RAG v4 requires {expected}, got {actual}")
    rows = [*public, *known, *fresh]
    # Public/dev intentionally reuses a subset of the 144 known-regression
    # surfaces.  They are separate evaluation observations, not separate
    # claims.  IDs must therefore be unique inside a split; the runtime key is
    # namespaced with the split below so checkpoints cannot collapse them.
    for name, values in (
        ("public", public),
        ("known_regression", known),
        ("fresh_holdout", fresh),
    ):
        ids = [str(row.get("id") or "") for row in values]
        if "" in ids or len(ids) != len(set(ids)):
            raise ValueError(f"RAG v4 case IDs must be unique within {name}")
    from app.rag.canonical_facts import get_canonical_fact_catalog

    errors = [
        error for row in rows for error in get_canonical_fact_catalog().validate_case(row)
    ]
    if errors:
        raise ValueError("RAG v4 contract invalid:\n- " + "\n- ".join(errors))
    for row in public:
        row["split"] = "public"
    for row in known:
        row["split"] = "known_regression"
    for row in fresh:
        if row.get("split") != "fresh_holdout":
            raise ValueError("RAG v4 fresh cases must use split=fresh_holdout")
    historical_ids = {str(row.get("id") or "") for row in [*public, *known]}
    historical_queries = {
        " ".join(str(row.get("query") or "").split()).casefold()
        for row in [*public, *known]
    }
    fresh_ids = {str(row.get("id") or "") for row in fresh}
    fresh_queries = {
        " ".join(str(row.get("query") or "").split()).casefold() for row in fresh
    }
    if historical_ids.intersection(fresh_ids):
        raise ValueError("RAG v4 fresh IDs overlap public or known regression")
    if historical_queries.intersection(fresh_queries):
        raise ValueError("RAG v4 fresh queries overlap public or known regression")
    return {"public": public, "known_regression": known, "fresh_holdout": fresh}


def scoped_case_id(case: Mapping[str, Any]) -> str:
    split = str(case.get("split") or "unspecified")
    case_id = str(case.get("id") or "")
    if not case_id:
        raise ValueError("RAG v4 case ID must be non-empty")
    return f"{split}:{case_id}"


def _provider_snapshot(embedding: Any, rerank: Any, expansion: Any) -> dict[str, Any]:
    return {
        "embedding": embedding.snapshot(),
        "rerank": rerank.snapshot(),
        "queryExpansion": expansion.snapshot(),
    }


def _merge_provider_facts(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge a resumed cold collection without losing earlier Provider facts."""

    old_embedding = previous.get("embedding") or {}
    new_embedding = current.get("embedding") or {}
    embedding_numeric = (
        "requests",
        "cacheHits",
        "providerRequests",
        "providerSuccesses",
        "providerFailures",
        "breakerRejections",
    )
    old_rerank = previous.get("rerank") or {}
    new_rerank = current.get("rerank") or {}
    rerank_numeric = (
        "eligibleRequests",
        "providerRequests",
        "providerSuccesses",
        "providerFailures",
        "fallbackCount",
    )
    old_expansion = previous.get("queryExpansion") or {}
    new_expansion = current.get("queryExpansion") or {}
    expansion_numeric = (
        "eligibleRequests",
        "providerRequests",
        "providerSuccesses",
        "providerFailures",
    )
    reasons: dict[str, int] = {}
    for source in (
        old_rerank.get("fallbackReasons") or {},
        new_rerank.get("fallbackReasons") or {},
    ):
        for key, value in source.items():
            reasons[str(key)] = reasons.get(str(key), 0) + int(value or 0)
    return {
        "embedding": {
            **{
                key: int(old_embedding.get(key) or 0)
                + int(new_embedding.get(key) or 0)
                for key in embedding_numeric
            },
            "bypassCache": True,
            "responseRecords": [
                *(old_embedding.get("responseRecords") or []),
                *(new_embedding.get("responseRecords") or []),
            ],
        },
        "rerank": {
            **{
                key: int(old_rerank.get(key) or 0)
                + int(new_rerank.get(key) or 0)
                for key in rerank_numeric
            },
            "fallbackReasons": dict(sorted(reasons.items())),
            "responseRecords": [
                *(old_rerank.get("responseRecords") or []),
                *(new_rerank.get("responseRecords") or []),
            ],
        },
        "queryExpansion": {
            key: int(old_expansion.get(key) or 0)
            + int(new_expansion.get(key) or 0)
            for key in expansion_numeric
        },
    }


async def collect_rag_v4_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    output_path: Path,
    candidate_size: int = 20,
    policy: RagRetrievalPolicy | None = None,
    contextual_mode: str = "context_prefix",
    knowledge_index: str | None = None,
) -> dict[str, Any]:
    """Run the real retriever once and retain channels for zero-call replay."""

    if contextual_mode not in {"original", "context_prefix"}:
        raise ValueError("contextual_mode must be original or context_prefix")
    previous = read_gzip_json(output_path) if output_path.is_file() else {}
    if previous and (
        previous.get("kind") != "rag-v4-cold-collection"
        or int(previous.get("candidateSize") or 0) != candidate_size
        or previous.get("contextualMode") != contextual_mode
        or previous.get("knowledgeIndex") != knowledge_index
    ):
        raise ValueError("existing RAG v4 checkpoint settings do not match")
    rows = [dict(row) for row in previous.get("cases") or []]
    existing = {str(row.get("evaluationCaseId") or ""): row for row in rows}
    requested = [scoped_case_id(case) for case in cases]
    if "" in requested or len(requested) != len(set(requested)):
        raise ValueError("RAG v4 case IDs must be unique and non-empty")
    if not set(existing).issubset(requested):
        raise ValueError("RAG v4 checkpoint contains an out-of-scope case")
    requested_by_id = {scoped_case_id(case): case for case in cases}
    for evaluation_case_id, row in existing.items():
        expected = requested_by_id[evaluation_case_id]
        if row.get("query") != expected.get("query"):
            raise ValueError(
                f"RAG v4 checkpoint query changed for {evaluation_case_id}"
            )
    selected_policy = policy or runtime_rag_policy()
    previous_provider = previous.get("providerFacts") or {}

    def checkpoint(provider: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schemaVersion": 4,
            "kind": "rag-v4-cold-collection",
            "candidateSize": candidate_size,
            "contextualMode": contextual_mode,
            "knowledgeIndex": knowledge_index,
            "policy": selected_policy.public(),
            "providerFacts": dict(provider),
            "cases": rows,
        }
        write_gzip_json(output_path, payload)
        return payload

    with (
        rag_policy_scope(base=selected_policy),
        embedding_evaluation_scope(bypass_cache=True) as embedding_stats,
        rerank_evaluation_scope(
            rerank_top_n=selected_policy.rerank_top_n,
            evidence_threshold=selected_policy.evidence_threshold,
            top_score_margin=selected_policy.top_score_margin,
        ) as rerank_stats,
        query_expansion_evaluation_scope() as expansion_stats,
        evaluation_es_index_scope(knowledge_index),
    ):
        for case in cases:
            case_id = str(case["id"])
            evaluation_case_id = scoped_case_id(case)
            if evaluation_case_id in existing:
                continue
            started = time.perf_counter()
            raw = await rag_retriever.search_faq_with_trace(
                str(case.get("query") or ""),
                top_k=candidate_size,
                include_evaluation_candidates=True,
            )
            runtime = (raw.get("trace") or {}).get("runtime") or {}
            channels = raw.get("_evaluationChannels") or {}
            refs = raw.get("source_refs") or []
            mode = str((raw.get("trace") or {}).get("mode") or "")
            exact = refs if mode == "exact" else []
            rows.append(
                {
                    "caseId": case_id,
                    "evaluationCaseId": evaluation_case_id,
                    "split": case.get("split"),
                    "query": case.get("query"),
                    "case": dict(case),
                    "safeQuery": (raw.get("queryPlan") or {}).get("safeBusinessQuery"),
                    "queryPlan": raw.get("queryPlan"),
                    "evidenceState": raw.get("evidenceState"),
                    "securityFlags": raw.get("securityFlags") or [],
                    "quarantinedCandidateIds": [
                        str(item.get("id") or "")
                        for item in (raw.get("trace") or {}).get("contamination") or []
                        if str(item.get("id") or "")
                    ],
                    "exactFaq": exact,
                    "bm25": channels.get("bm25") or [],
                    "vector": channels.get("vector") or [],
                    "rrf": channels.get("rrf") or [],
                    "rerank": channels.get("rerank") or [],
                    "production": refs,
                    "candidateRefs": raw.get("_evaluationCandidateRefs") or [],
                    "runtimeTrace": runtime,
                    "stageLatencyMs": {
                        **(runtime.get("stageLatencyMs") or {}),
                        "total": round((time.perf_counter() - started) * 1000, 4),
                    },
                }
            )
            checkpoint(
                _merge_provider_facts(
                    previous_provider,
                    _provider_snapshot(embedding_stats, rerank_stats, expansion_stats),
                )
            )
        provider_facts = _merge_provider_facts(
            previous_provider,
            _provider_snapshot(embedding_stats, rerank_stats, expansion_stats),
        )
    if provider_facts["embedding"].get("cacheHits") or provider_facts["embedding"].get("providerFailures"):
        raise RuntimeError("RAG v4 embedding evidence is incomplete")
    if provider_facts["rerank"].get("providerFailures") or provider_facts["rerank"].get("fallbackCount"):
        raise RuntimeError("RAG v4 rerank fallback detected")
    payload = checkpoint(provider_facts)
    payload["rawSha256"] = sha256_file(output_path)
    return payload


def _refs_for_variant(
    row: Mapping[str, Any],
    *,
    variant: str,
    top_n: int,
    threshold: float,
    margin: float | None,
) -> list[dict[str, Any]]:
    exact = [dict(ref) for ref in row.get("exactFaq") or []]
    if variant == "production" and exact:
        return exact[:1]
    source_key = "rerank" if variant == "production" else variant
    source = list(row.get(source_key) or [])
    if variant in {"rrf_rerank", "production"}:
        source = source[:top_n]
    if variant == "production":
        policy = runtime_rag_policy()
        query_plan = row.get("queryPlan") or {}
        fact_hints = set(query_plan.get("factHints") or ())
        if not fact_hints:
            fact_hints.update(plan_rag_query(str(row.get("query") or "")).fact_hints)
        quarantined = set(row.get("quarantinedCandidateIds") or ())
        source = [ref for ref in source if str(ref.get("id") or "") not in quarantined]
        top = max((float(ref.get("score") or 0) for ref in source), default=0.0)
        hinted: list[dict[str, Any]] = []
        ordinary: list[dict[str, Any]] = []
        for ref in source:
            score = float(ref.get("score") or 0)
            is_hinted = bool(fact_hints.intersection(ref.get("factIds") or ()))
            floor = min(threshold, policy.canonical_hint_floor) if is_hinted else threshold
            if score < floor:
                continue
            if not is_hinted and margin is not None and score < top - margin:
                continue
            (hinted if is_hinted else ordinary).append(dict(ref))
        source = [*hinted, *ordinary]
        evidence_limit = evidence_item_limit(
            (query_plan.get("subquestions") or ()),
            configured_max=policy.max_evidence_items,
            preferred_fact_ids=tuple(fact_hints),
            candidates=source,
        )
        selected: list[dict[str, Any]] = []
        covered_facts: set[str] = set()
        for ref in source:
            facts = {str(value) for value in ref.get("factIds") or () if str(value)}
            if facts and facts.issubset(covered_facts):
                continue
            selected.append(ref)
            covered_facts.update(facts)
            if len(selected) == evidence_limit:
                break
        source = selected
    return source


def _rank_ids(case: Mapping[str, Any], refs: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, float]]:
    facts = relevant_fact_ids(case)
    if facts:
        labels = {fact: 1.0 for fact in facts}
        ranked = [
            sorted(matches)[0]
            if (matches := canonical_match(case, ref))
            else f"unjudged:{index}:{ref.get('id')}"
            for index, ref in enumerate(refs)
        ]
        return ranked, labels
    refs_expected = case.get("relevantRefs") or []
    labels = {str(index): 1.0 for index, _ in enumerate(refs_expected)}
    return [str(ref.get("id") or f"unjudged:{index}") for index, ref in enumerate(refs)], labels


def replay_rag_v4_collection(
    collection: Mapping[str, Any] | Path,
    *,
    variants: Sequence[str] = RAG_V4_VARIANTS,
    top_n_values: Sequence[int] = (3, 6, 10),
    thresholds: Sequence[float] = (0.50, 0.55, 0.60, 0.65, 0.70),
    margins: Sequence[float | None] = (0.0, 0.05, 0.10),
    k_values: Sequence[int] = RAG_V4_K_VALUES,
    split_filter: set[str] | None = None,
) -> dict[str, Any]:
    payload = read_gzip_json(collection) if isinstance(collection, Path) else dict(collection)
    variant_metrics: dict[str, Any] = {}
    variant_cases: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        top_values = top_n_values if variant in {"rrf_rerank", "production"} else (20,)
        threshold_values = thresholds if variant == "production" else (0.0,)
        margin_values = margins if variant == "production" else (None,)
        for top_n in top_values:
            for threshold in threshold_values:
                for margin in margin_values:
                    key = f"{variant}:n{top_n}:t{threshold:.2f}:m{'off' if margin is None else f'{margin:.2f}'}"
                    cases: list[dict[str, Any]] = []
                    results: list[dict[str, Any]] = []
                    ranking_rows: list[dict[str, Any]] = []
                    for row in payload.get("cases") or []:
                        if split_filter and str(row.get("split")) not in split_filter:
                            continue
                        case = dict(row["case"])
                        refs = _refs_for_variant(
                            row,
                            variant=variant,
                            top_n=top_n,
                            threshold=threshold,
                            margin=margin,
                        )
                        cases.append(case)
                        results.append(
                            {
                                "source_refs": refs,
                                "trace": {
                                    "hit": bool(refs),
                                    "latencyMs": (row.get("stageLatencyMs") or {}).get("total"),
                                },
                            }
                        )
                        ranked, labels = _rank_ids(case, refs)
                        ranking_rows.append(
                            ranking_case_metrics(
                                ranked,
                                labels,
                                k_values=k_values,
                                expected_no_results=bool(case.get("noAnswer", not labels)),
                            )
                        )
                    if not cases:
                        continue
                    retrieval = evaluate_results(cases, results, top_k=max(k_values))
                    ranking = aggregate_ranking_cases(ranking_rows)
                    variant_metrics[key] = {
                        "metricCurves": ranking["metricCurves"],
                        "noResultAccuracy": ranking["noResultAccuracy"],
                        "citationCorrectness": retrieval["citationCorrectness"],
                        "canonicalCitationCorrectness": retrieval["canonicalCitationCorrectness"],
                        "canonicalCitationCoverage": retrieval["canonicalCitationCoverage"],
                        "noAnswerAccuracy": retrieval["noAnswerAccuracy"],
                        "injectionRobustness": retrieval["injectionRobustness"],
                        "strictExactRefPrecision": retrieval["strictExactRefPrecision"],
                        "rerankTopN": top_n,
                        "evidenceThreshold": threshold,
                        "topScoreMargin": margin,
                        "perCase": retrieval["perCase"],
                    }
                    variant_cases[key] = [
                        {
                            "caseId": str(case["id"]),
                            "evaluationCaseId": scoped_case_id(case),
                            "split": case.get("split"),
                            "ranking": ranking,
                        }
                        for case, ranking in zip(cases, ranking_rows)
                    ]
    baseline_key = next(
        (key for key in variant_cases if key.startswith("rrf:")),
        next(iter(variant_cases), None),
    )
    paired: dict[str, Any] = {}
    if baseline_key:
        baseline = [
            {"caseId": row["evaluationCaseId"], **row["ranking"]}
            for row in variant_cases[baseline_key]
            if row["ranking"].get("applicable")
        ]
        for key, rows in variant_cases.items():
            candidate = [
                {"caseId": row["evaluationCaseId"], **row["ranking"]}
                for row in rows
                if row["ranking"].get("applicable")
            ]
            if len(candidate) != len(baseline) or not baseline:
                continue
            for metric, name, k in (("ndcg", "ndcg", "5"), ("recall", "recall", "5"), ("reciprocalRank", "mrr", "10")):
                paired[f"{key}:{name}@{k}"] = paired_ranking_comparison(
                    baseline,
                    candidate,
                    metric_path=["metricsByK", k, metric],
                )
    return {
        "schemaVersion": 4,
        "kind": "rag-v4-offline-replay",
        "providerFacts": {"embeddingRequests": 0, "rerankRequests": 0, "queryExpansionRequests": 0},
        "metricCurves": {key: value["metricCurves"] for key, value in variant_metrics.items()},
        "variantMetrics": variant_metrics,
        "pairedDeltas": paired,
        "confidenceIntervals": {key: value["confidenceInterval"] for key, value in paired.items()},
        "stageLatency": aggregate_stage_latency(payload.get("cases") or []),
        "cases": variant_cases,
        "policy": payload.get("policy"),
        "contextualMode": payload.get("contextualMode"),
    }


def choose_rag_v4_configuration(report: Mapping[str, Any]) -> dict[str, Any]:
    candidates = {
        key: value
        for key, value in (report.get("variantMetrics") or {}).items()
        if key.startswith("production:")
    }
    if not candidates:
        raise ValueError("RAG v4 replay has no production candidates")

    def score(item: tuple[str, Mapping[str, Any]]) -> tuple[float, ...]:
        key, value = item
        curve3 = (value.get("metricCurves") or {}).get("3") or {}
        curve5 = (value.get("metricCurves") or {}).get("5") or {}
        curve10 = (value.get("metricCurves") or {}).get("10") or {}
        return (
            float(value.get("injectionRobustness") or 0) == 1.0,
            float(value.get("noAnswerAccuracy") or 0),
            float(curve3.get("recall") or 0),
            float(value.get("canonicalCitationCoverage") or 0),
            float(curve5.get("ndcg") or 0),
            float(curve10.get("mrr") or 0),
            key,
        )

    selected, metrics = max(candidates.items(), key=score)
    return {
        "selectedVariant": selected,
        "selectionData": "public + known regression only",
        "selectedMetrics": metrics,
        "policy": report.get("policy"),
    }
