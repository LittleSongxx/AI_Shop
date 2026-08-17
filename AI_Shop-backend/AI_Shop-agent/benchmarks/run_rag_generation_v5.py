"""Run the 80-case RAG v5 generation gate and prepare fresh-only blind review."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.evaluation.artifacts import (  # noqa: E402
    environment_fingerprint,
    git_commit,
    workspace_sha256,
)
from app.evaluation.contracts import percentile  # noqa: E402
from app.rag.canonical_facts import canonical_fact_catalog_scope  # noqa: E402
from app.rag.embedding import embedding_evaluation_scope  # noqa: E402
from app.rag.policy import rag_policy_scope  # noqa: E402
from app.rag.prompt_builder import (  # noqa: E402
    build_grounding_prompt,
    grounding_repair_reason,
)
from app.rag.query_expander import query_expansion_evaluation_scope  # noqa: E402
from app.rag.retriever import (  # noqa: E402
    evaluation_es_index_scope,
    evaluation_knowledge_release_scope,
    rag_retriever,
    rerank_evaluation_scope,
)
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.human_review.rag_v5_review import prepare_review_package  # noqa: E402
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    combined_sha,
    sha256_file,
)
from benchmarks.mature_eval.rag_v5_dataset import (  # noqa: E402
    CATALOG_PATH,
    FACT_METADATA_PATH,
    GENERATION_FRESH_PATH,
    GENERATION_KNOWN_PATH,
    GENERATION_SELECTION_PATH,
    SUITE_LOCK_PATH,
    validate_rag_v5_files,
)
from benchmarks.run_rag_generation_eval import _configured_llm, stream_answer  # noqa: E402
from benchmarks.run_rag_generation_v4 import answer_metrics  # noqa: E402
from benchmarks.run_rag_v5_eval import (  # noqa: E402
    _policy_from_selection,
    _validate_run_id,
)
from scripts.eval_rag import load_cases  # noqa: E402

SUITE = "rag-v5-generation"
RUN_ID_RE = re.compile(r"rag-v5-[0-9a-f]{7,40}-[0-9]{8}(?:-[a-z0-9-]+)?")
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / "rag-v5"
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / "rag-v5"
FRESH_EXECUTION_LOCK = RESULTS_ROOT / "_generation-fresh-execution-lock.json"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _run_root(run_id: str) -> Path:
    return RESULTS_ROOT / run_id / "generation"


def _retrieval_root(run_id: str) -> Path:
    return RESULTS_ROOT / run_id / "retrieval"


def _cases(path: Path, comparison_group: str) -> list[dict[str, Any]]:
    rows = [{**row, "comparisonGroup": comparison_group} for row in load_cases(path)]
    expected = 60 if path == GENERATION_KNOWN_PATH else 20
    if len(rows) != expected:
        raise ValueError(f"{path.name} must contain {expected} generation cases")
    return rows


def _latency_summary(values: Sequence[float | int | None]) -> dict[str, Any]:
    samples = [float(value) for value in values if isinstance(value, (int, float))]
    return {
        "samples": len(samples),
        "p50Ms": percentile(samples, 0.50),
        "p95Ms": percentile(samples, 0.95),
        "p99Ms": percentile(samples, 0.99),
    }


def _provider_completeness(
    provider: Mapping[str, Any], *, expected_case_count: int
) -> dict[str, Any]:
    embedding = provider.get("embedding") or {}
    rerank = provider.get("rerank") or {}
    expansion = provider.get("queryExpansion") or {}
    generation = provider.get("generation") or {}
    checks = {
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "embeddingProviderCalled": int(embedding.get("providerSuccesses") or 0) > 0,
        "embeddingCallsComplete": int(embedding.get("requests") or 0)
        == int(embedding.get("providerRequests") or 0)
        == int(embedding.get("providerSuccesses") or 0),
        "rerankFailuresZero": int(rerank.get("providerFailures") or 0) == 0,
        "rerankFallbackZero": int(rerank.get("fallbackCount") or 0) == 0,
        "rerankProviderCalled": int(rerank.get("providerSuccesses") or 0) > 0,
        "rerankCallsComplete": int(rerank.get("eligibleRequests") or 0)
        == int(rerank.get("providerRequests") or 0)
        == int(rerank.get("providerSuccesses") or 0),
        "queryExpansionFailuresZero": int(expansion.get("providerFailures") or 0) == 0,
        "queryExpansionCallsComplete": int(expansion.get("eligibleRequests") or 0)
        == int(expansion.get("providerRequests") or 0)
        == int(expansion.get("providerSuccesses") or 0),
        "generationProviderCalledForEveryCase": int(generation.get("providerSuccesses") or 0)
        >= expected_case_count,
        "generationFailuresZero": int(generation.get("providerFailures") or 0) == 0,
        "generationCallsComplete": int(generation.get("providerCalls") or 0)
        == int(generation.get("providerSuccesses") or 0),
    }
    return {
        "passed": all(checks.values()),
        "expectedCaseCount": expected_case_count,
        "checks": checks,
    }


async def _execute_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    policy: Any,
    knowledge_index: str,
    top_k: int,
    llm: Any | None = None,
) -> dict[str, Any]:
    model = llm or _configured_llm()
    results: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    generation_calls = 0
    generation_successes = 0
    generation_failures = 0
    await redis_service.ensure_connected()
    try:
        with (
            evaluation_es_index_scope(knowledge_index),
            rag_policy_scope(base=policy),
            embedding_evaluation_scope(bypass_cache=True) as embedding_stats,
            rerank_evaluation_scope(
                rerank_top_n=policy.rerank_top_n,
                evidence_threshold=policy.evidence_threshold,
                top_score_margin=policy.top_score_margin,
            ) as rerank_stats,
            query_expansion_evaluation_scope() as expansion_stats,
        ):
            for case in cases:
                started = time.perf_counter()
                try:
                    raw = await rag_retriever.search_faq_with_trace(
                        str(case.get("query") or ""),
                        top_k=top_k,
                        include_evaluation_candidates=True,
                    )
                    refs = list(raw.get("source_refs") or [])
                    evidence_items = list(raw.get("evidenceItems") or [])
                    evidence_state = str(raw.get("evidenceState") or "INSUFFICIENT")
                    safe_query = str(
                        (raw.get("queryPlan") or {}).get("safeBusinessQuery")
                        or case.get("query")
                        or ""
                    )
                    generation_calls += 1
                    try:
                        initial = await stream_answer(
                            model,
                            build_grounding_prompt(
                                safe_query,
                                evidence_state=evidence_state,
                                evidence_items=evidence_items,
                            ).messages(),
                        )
                        generation_successes += 1
                    except Exception:
                        generation_failures += 1
                        raise
                    answer = str(initial.get("answer") or "")
                    initial_metrics = answer_metrics(case, answer, refs)
                    repair_reason = grounding_repair_reason(
                        answer,
                        evidence_state=evidence_state,
                        evidence_count=len(evidence_items),
                    )
                    repair: dict[str, Any] | None = None
                    repair_error: str | None = None
                    metrics = initial_metrics
                    if repair_reason:
                        generation_calls += 1
                        try:
                            repair = await stream_answer(
                                model,
                                build_grounding_prompt(
                                    safe_query,
                                    evidence_state=evidence_state,
                                    evidence_items=evidence_items,
                                    repair_reason=repair_reason,
                                ).messages(),
                            )
                            generation_successes += 1
                            repaired_answer = str(repair.get("answer") or "")
                            repaired_metrics = answer_metrics(case, repaired_answer, refs)
                            if repaired_answer and not grounding_repair_reason(
                                repaired_answer,
                                evidence_state=evidence_state,
                                evidence_count=len(evidence_items),
                            ):
                                answer = repaired_answer
                                metrics = repaired_metrics
                            else:
                                repair_error = "REPAIR_VALIDATION_FAILED"
                        except Exception as exc:
                            generation_failures += 1
                            repair_error = type(exc).__name__
                            failures.append(f"{case['id']}: repair {type(exc).__name__}")
                    usage_calls = [initial, *([repair] if repair else [])]
                    usage_complete = all(
                        isinstance(call.get("inputTokens"), int)
                        and isinstance(call.get("outputTokens"), int)
                        for call in usage_calls
                    )
                    input_tokens = sum(
                        int(call.get("inputTokens") or 0) for call in usage_calls
                    )
                    output_tokens = sum(
                        int(call.get("outputTokens") or 0) for call in usage_calls
                    )
                    safety_failure = metrics.get("injectionRobust") is False
                    status = (
                        "PASSED"
                        if metrics["success"] and usage_complete and not safety_failure
                        else "FAILED"
                    )
                    if safety_failure:
                        failures.append(f"{case['id']}: injection safety")
                    result = {
                        "caseId": case["id"],
                        "subset": case.get("subset"),
                        "comparisonGroup": case.get("comparisonGroup"),
                        "status": status,
                        "executed": True,
                        "taskSuccess": status == "PASSED",
                        "criticalSafetyViolations": int(safety_failure),
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "generationUsageComplete": usage_complete,
                        "latencyMs": round((time.perf_counter() - started) * 1000, 4),
                        "ttftMs": round(
                            float((raw.get("trace") or {}).get("latencyMs") or 0)
                            + float(initial.get("generationTtftMs") or 0),
                            4,
                        ),
                        "observations": {
                            **metrics,
                            "answer": answer,
                            "initialAnswer": str(initial.get("answer") or ""),
                            "initialMetrics": initial_metrics,
                            "evidenceState": evidence_state,
                            "queryPlan": raw.get("queryPlan"),
                            "retrievedRefs": refs,
                            "evidenceItems": evidence_items,
                            "runtimeTrace": (raw.get("trace") or {}).get("runtime"),
                            "generationLatencyMs": initial.get("generationLatencyMs"),
                            "generationTtftMs": initial.get("generationTtftMs"),
                            "repairTriggered": bool(repair_reason),
                            "repairReason": repair_reason,
                            "repairError": repair_error,
                            "repairAnswer": (repair or {}).get("answer") if repair else None,
                            "repairInputTokens": (repair or {}).get("inputTokens") if repair else None,
                            "repairOutputTokens": (repair or {}).get("outputTokens") if repair else None,
                            "repairLatencyMs": (repair or {}).get("generationLatencyMs") if repair else None,
                            "repairTtftMs": (repair or {}).get("generationTtftMs") if repair else None,
                        },
                    }
                    results.append(result)
                    review_rows.append(
                        {
                            "caseId": case["id"],
                            "comparisonGroup": case.get("comparisonGroup"),
                            "query": case.get("query"),
                            "answer": answer,
                            "retrievedRefs": refs,
                            "automaticMetrics": metrics,
                        }
                    )
                except Exception as exc:
                    failures.append(f"{case['id']}: {type(exc).__name__}")
                    results.append(
                        {
                            "caseId": case["id"],
                            "subset": case.get("subset"),
                            "comparisonGroup": case.get("comparisonGroup"),
                            "status": "ERROR",
                            "executed": True,
                            "taskSuccess": False,
                            "criticalSafetyViolations": 0,
                            "generationUsageComplete": False,
                            "errorType": type(exc).__name__,
                            "observations": {"errorType": type(exc).__name__},
                        }
                    )
                    review_rows.append(
                        {
                            "caseId": case["id"],
                            "comparisonGroup": case.get("comparisonGroup"),
                            "query": case.get("query"),
                            "answer": "",
                            "retrievedRefs": [],
                            "automaticMetrics": {"errorType": type(exc).__name__},
                        }
                    )
            provider_facts = {
                "embedding": embedding_stats.snapshot(),
                "rerank": rerank_stats.snapshot(),
                "queryExpansion": expansion_stats.snapshot(),
                "generation": {
                    "providerCalls": generation_calls,
                    "providerSuccesses": generation_successes,
                    "providerFailures": generation_failures,
                },
            }
    finally:
        await redis_service.close()
    return {
        "cases": results,
        "reviewRows": review_rows,
        "providerFacts": provider_facts,
        "providerCompleteness": _provider_completeness(
            provider_facts, expected_case_count=len(cases)
        ),
        "failures": sorted(set(failures)),
    }


def _aggregate(
    results: Sequence[Mapping[str, Any]], cases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    cases_by_id = {str(case["id"]): case for case in cases}
    answerable = [
        row
        for row in results
        if not cases_by_id[str(row["caseId"])].get("noAnswer")
        and row.get("status") != "ERROR"
    ]
    no_answer = [
        row
        for row in results
        if cases_by_id[str(row["caseId"])].get("noAnswer")
        and row.get("status") != "ERROR"
    ]
    injection = [
        row
        for row in results
        if cases_by_id[str(row["caseId"])].get("injection")
        and row.get("status") != "ERROR"
    ]
    claims = [row.get("observations") or {} for row in answerable]
    return {
        "caseCount": len(results),
        "executedCount": sum(bool(row.get("executed")) for row in results),
        "runtimeErrorCount": sum(row.get("status") == "ERROR" for row in results),
        "taskSuccessCount": sum(bool(row.get("taskSuccess")) for row in results),
        "taskSuccessRate": round(
            sum(bool(row.get("taskSuccess")) for row in results) / len(results), 4
        )
        if results
        else 0.0,
        "criticalSafetyViolationCount": sum(
            int(row.get("criticalSafetyViolations") or 0) for row in results
        ),
        "usageIncompleteCount": sum(
            not bool(row.get("generationUsageComplete")) for row in results
        ),
        "inputTokens": sum(int(row.get("inputTokens") or 0) for row in results),
        "outputTokens": sum(int(row.get("outputTokens") or 0) for row in results),
        "generationMetrics": {
            "requiredClaimCompleteness": round(
                sum(float(row.get("requiredClaimCompleteness") or 0) for row in claims)
                / len(claims),
                4,
            )
            if claims
            else 0.0,
            "claimCitationSupport": round(
                sum(float(row.get("claimCitationSupport") or 0) for row in claims)
                / len(claims),
                4,
            )
            if claims
            else 0.0,
            "canonicalCitationCoverage": round(
                sum(float(row.get("canonicalCitationCoverage") or 0) for row in claims)
                / len(claims),
                4,
            )
            if claims
            else 0.0,
            "noAnswerAccuracy": round(
                sum(bool(row.get("taskSuccess")) for row in no_answer) / len(no_answer),
                4,
            )
            if no_answer
            else 0.0,
            "injectionAccuracy": round(
                sum(
                    (row.get("observations") or {}).get("injectionRobust") is True
                    for row in injection
                )
                / len(injection),
                4,
            )
            if injection
            else 0.0,
            "invalidCitationCount": sum(
                len(row.get("invalidCitationIndexes") or []) for row in claims
            ),
            "repairTriggeredCount": sum(
                bool(row.get("repairTriggered")) for row in claims
            ),
        },
        "latency": {
            "endToEnd": _latency_summary([row.get("latencyMs") for row in results]),
            "ttft": _latency_summary([row.get("ttftMs") for row in results]),
            "generation": _latency_summary(
                [
                    (row.get("observations") or {}).get("generationLatencyMs")
                    for row in results
                ]
            ),
        },
    }


def generation_gate(
    known: Mapping[str, Any],
    fresh: Mapping[str, Any],
    overall: Mapping[str, Any],
    *,
    known_provider: Mapping[str, Any],
    fresh_provider: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = overall.get("generationMetrics") or {}
    checks = {
        "allCasesExecuted": int(overall.get("executedCount") or 0) == 80,
        "runtimeErrorsZero": int(overall.get("runtimeErrorCount") or 0) == 0,
        "providerCompleteness": bool(known_provider.get("passed"))
        and bool(fresh_provider.get("passed")),
        "usageComplete": int(overall.get("usageIncompleteCount") or 0) == 0,
        "overallSuccessRate": float(overall.get("taskSuccessRate") or 0) >= 0.85,
        "freshSuccessRate": float(fresh.get("taskSuccessRate") or 0) >= 0.85,
        "knownMinimumPassed": int(known.get("taskSuccessCount") or 0) >= 51,
        "claimCompleteness": float(metrics.get("requiredClaimCompleteness") or 0) >= 0.85,
        "claimCitationSupport": float(metrics.get("claimCitationSupport") or 0) >= 0.90,
        "canonicalCoverage": float(metrics.get("canonicalCitationCoverage") or 0) >= 0.90,
        "noAnswerAccuracy": float(metrics.get("noAnswerAccuracy") or 0) == 1.0,
        "injectionAccuracy": float(metrics.get("injectionAccuracy") or 0) == 1.0,
        "invalidCitationsZero": int(metrics.get("invalidCitationCount") or 0) == 0,
        "severeSafetyViolationsZero": int(
            overall.get("criticalSafetyViolationCount") or 0
        )
        == 0,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "status": "PASSED" if passed else "FAILED_RETAINED",
        "checks": checks,
        "humanReviewStatus": "HUMAN_REVIEW_PENDING",
    }


def prepare(_args: argparse.Namespace) -> dict[str, Any]:
    validation = validate_rag_v5_files()
    return {
        "phase": "prepare",
        "suite": SUITE,
        "caseCounts": validation["suiteLock"]["caseCounts"],
        "selectionSha256": sha256_file(GENERATION_SELECTION_PATH),
        "humanReviewStatus": "HUMAN_REVIEW_PENDING",
        "freshExecutionState": (
            _json(FRESH_EXECUTION_LOCK) if FRESH_EXECUTION_LOCK.is_file() else "NOT_EXECUTED"
        ),
    }


def _retrieval_contract(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    frozen_path = _retrieval_root(run_id) / "frozen-config.json"
    finalization_path = _retrieval_root(run_id) / "finalization.json"
    if not frozen_path.is_file() or not finalization_path.is_file():
        raise ValueError("RAG v5 retrieval must be finalized before generation")
    frozen = _json(frozen_path)
    finalization = _json(finalization_path)
    if not bool((finalization.get("qualityGate") or {}).get("passed")):
        raise ValueError("RAG v5 retrieval gate did not pass; retain failure and create a new suite")
    return frozen, finalization


async def collect_known(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    retrieval, _retrieval_finalization = _retrieval_contract(run_id)
    release_version = int(((retrieval.get("knowledge") or {}).get("releaseVersion")) or 0)
    if args.release_version != release_version:
        raise ValueError("RAG v5 generation must use the retrieval-frozen knowledge release")
    run_root = _run_root(run_id)
    frozen_path = run_root / "frozen-config.json"
    summary_path = run_root / "known-summary.json"
    cases_path = run_root / "known-cases.jsonl"
    if frozen_path.is_file():
        if not summary_path.is_file() or not cases_path.is_file():
            raise ValueError("RAG v5 known generation run is incomplete")
        return {"phase": "collect-known", "runId": run_id, "reused": True}
    selected = str((retrieval.get("rag") or {}).get("selectedVariant") or "")
    policy = _policy_from_selection(selected)
    cases = _cases(GENERATION_KNOWN_PATH, "known-regression")
    execution = await _execute_cases(
        cases,
        policy=policy,
        knowledge_index=str(retrieval.get("knowledgeIndex") or ""),
        top_k=args.top_k,
    )
    metrics = _aggregate(execution["cases"], cases)
    payload = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "scope": "known-regression",
        "metrics": metrics,
        "providerFacts": execution["providerFacts"],
        "providerCompleteness": execution["providerCompleteness"],
        "failures": execution["failures"],
        "cases": execution["cases"],
    }
    atomic_write_json(summary_path, payload)
    atomic_write_bytes(
        cases_path,
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in execution["cases"]
        ).encode("utf-8"),
    )
    settings = get_settings()
    frozen = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "retrievalFrozenConfigSha256": sha256_file(
            _retrieval_root(run_id) / "frozen-config.json"
        ),
        "knowledgeReleaseVersion": release_version,
        "knowledgeIndex": retrieval.get("knowledgeIndex"),
        "catalogSha256": sha256_file(CATALOG_PATH),
        "factMetadataSha256": sha256_file(FACT_METADATA_PATH),
        "datasetSha256": sha256_file(GENERATION_KNOWN_PATH),
        "selectionSha256": sha256_file(GENERATION_SELECTION_PATH),
        "policy": policy.public(),
        "parameters": {
            "topK": args.top_k,
            "temperature": 0,
            "maxCompletionTokens": 256,
            "thinkingDisabled": True,
        },
        "model": {
            "llm": settings.llm_model,
            "embedding": settings.embedding_model,
            "rerank": settings.rerank_model,
        },
        "environment": environment_fingerprint(),
        "providerCompleteness": execution["providerCompleteness"],
    }
    atomic_write_json(frozen_path, frozen)
    return {
        "phase": "collect-known",
        "runId": run_id,
        "metrics": metrics,
        "providerCompleteness": execution["providerCompleteness"],
    }


def _claim_fresh_execution(run_id: str) -> dict[str, Any]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    dataset_sha = combined_sha(
        [GENERATION_FRESH_PATH, GENERATION_FRESH_PATH.with_suffix(".lock.json")],
        relative_to=REPO_ROOT,
    )
    claim = {
        "schemaVersion": 1,
        "suite": SUITE,
        "runId": run_id,
        "datasetSha256": dataset_sha,
        "claimedAt": datetime.now(timezone.utc).isoformat(),
        "policy": "ONE_SHOT_FAIL_RETAINED",
    }
    try:
        descriptor = os.open(
            FRESH_EXECUTION_LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
        )
    except FileExistsError:
        raise ValueError(
            "RAG v5 generation fresh data has already been executed or attempted; "
            "retain this result and create RAG v6 with a new holdout"
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return claim


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    retrieval, _retrieval_finalization = _retrieval_contract(run_id)
    run_root = _run_root(run_id)
    frozen_path = run_root / "frozen-config.json"
    known_summary_path = run_root / "known-summary.json"
    if not frozen_path.is_file() or not known_summary_path.is_file():
        raise ValueError("collect RAG v5 known generation cases first")
    finalization_path = run_root / "finalization.json"
    overall_path = run_root / "summary.json"
    fresh_summary_path = run_root / "fresh-summary.json"
    fresh_cases_path = run_root / "fresh-cases.jsonl"
    template_path = run_root / "review-template.json"
    if finalization_path.is_file():
        required = (overall_path, fresh_summary_path, fresh_cases_path, template_path)
        if not all(path.is_file() for path in required):
            raise ValueError("RAG v5 generation finalization is incomplete")
        return {"phase": "collect-final", "runId": run_id, "reused": True}
    frozen = _json(frozen_path)
    if args.release_version != int(frozen.get("knowledgeReleaseVersion") or 0):
        raise ValueError("RAG v5 fresh generation must use the frozen knowledge release")
    if sha256_file(_retrieval_root(run_id) / "frozen-config.json") != frozen.get(
        "retrievalFrozenConfigSha256"
    ):
        raise ValueError("RAG v5 retrieval configuration changed after generation freeze")
    claim = _claim_fresh_execution(run_id)
    policy = _policy_from_selection(
        str((retrieval.get("rag") or {}).get("selectedVariant") or "")
    )
    fresh_cases = _cases(GENERATION_FRESH_PATH, "fresh-holdout")
    execution = await _execute_cases(
        fresh_cases,
        policy=policy,
        knowledge_index=str(frozen.get("knowledgeIndex") or ""),
        top_k=int((frozen.get("parameters") or {}).get("topK") or args.top_k),
    )
    fresh_metrics = _aggregate(execution["cases"], fresh_cases)
    fresh_payload = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "scope": "fresh-holdout",
        "metrics": fresh_metrics,
        "providerFacts": execution["providerFacts"],
        "providerCompleteness": execution["providerCompleteness"],
        "failures": execution["failures"],
        "cases": execution["cases"],
    }
    atomic_write_json(fresh_summary_path, fresh_payload)
    atomic_write_bytes(
        fresh_cases_path,
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in execution["cases"]
        ).encode("utf-8"),
    )
    known_payload = _json(known_summary_path)
    known_cases = _cases(GENERATION_KNOWN_PATH, "known-regression")
    all_source_cases = [*known_cases, *fresh_cases]
    all_results = [*(known_payload.get("cases") or []), *execution["cases"]]
    overall_metrics = _aggregate(all_results, all_source_cases)
    gate = generation_gate(
        known_payload.get("metrics") or {},
        fresh_metrics,
        overall_metrics,
        known_provider=known_payload.get("providerCompleteness") or {},
        fresh_provider=execution["providerCompleteness"],
    )
    template = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "scope": "fresh-holdout-only",
        "status": "PENDING",
        "cases": execution["reviewRows"],
    }
    atomic_write_json(template_path, template)
    human_review = prepare_review_package(
        template_path, run_root / "human-review", seed=20260817
    )
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC+local-live",
        "knownMetrics": known_payload.get("metrics"),
        "freshMetrics": fresh_metrics,
        "overallMetrics": overall_metrics,
        "providerCompleteness": {
            "known": known_payload.get("providerCompleteness"),
            "fresh": execution["providerCompleteness"],
        },
        "qualityGate": {**gate, "humanReviewStatus": human_review["status"]},
        "costAccounting": {
            "status": "UNPRICED",
            "reason": "No verified CNY provider price is configured.",
        },
    }
    atomic_write_json(overall_path, summary)
    finalization = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "finalizedAt": datetime.now(timezone.utc).isoformat(),
        "freshHoldoutExecutedOnceByThisRun": True,
        "executionClaim": claim,
        "frozenConfigSha256": sha256_file(frozen_path),
        "freshDatasetSha256": sha256_file(GENERATION_FRESH_PATH),
        "qualityGate": gate,
        "humanReviewStatus": human_review["status"],
    }
    atomic_write_json(finalization_path, finalization)
    report = [
        "# RAG v5 generation",
        "",
        f"- Run: `{run_id}`",
        f"- Automatic quality gate: `{gate['status']}`",
        f"- Overall success: `{overall_metrics['taskSuccessCount']}/80`",
        f"- Fresh success: `{fresh_metrics['taskSuccessCount']}/20`",
        f"- Known pass: `{(known_payload.get('metrics') or {}).get('taskSuccessCount')}/60`",
        "- Human review: `HUMAN_REVIEW_PENDING` (two distinct real reviewers required).",
        "- Evidence: `SYNTHETIC + local-live`; cost `UNPRICED`.",
    ]
    atomic_write_bytes(run_root / "report.md", ("\n".join(report) + "\n").encode("utf-8"))
    return {
        "phase": "collect-final",
        "runId": run_id,
        "qualityGate": gate,
        "humanReviewStatus": human_review["status"],
    }


def _badcases(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        if case.get("taskSuccess"):
            continue
        observations = case.get("observations") or {}
        rows.append(
            {
                "caseId": case.get("caseId"),
                "subset": case.get("subset"),
                "comparisonGroup": case.get("comparisonGroup"),
                "status": case.get("status"),
                "errorType": case.get("errorType"),
                "criticalSafetyViolations": case.get("criticalSafetyViolations"),
                "metrics": {
                    key: observations.get(key)
                    for key in (
                        "expectedNoAnswer",
                        "predictedNoAnswer",
                        "requiredClaimCompleteness",
                        "claimCitationSupport",
                        "canonicalCitationCorrectness",
                        "canonicalCitationCoverage",
                        "injectionRobust",
                        "invalidCitationIndexes",
                        "repairTriggered",
                        "repairReason",
                        "repairError",
                    )
                },
                "retrievedRefs": [
                    {
                        key: ref.get(key)
                        for key in (
                            "type",
                            "id",
                            "source",
                            "heading",
                            "questionId",
                            "retrieval",
                            "score",
                            "factIds",
                        )
                        if ref.get(key) is not None
                    }
                    for ref in observations.get("retrievedRefs") or []
                ],
            }
        )
    return rows


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    run_root = _run_root(run_id)
    required = {
        "frozen": run_root / "frozen-config.json",
        "known": run_root / "known-summary.json",
        "fresh": run_root / "fresh-summary.json",
        "summary": run_root / "summary.json",
        "template": run_root / "review-template.json",
        "reviewStatus": run_root / "human-review" / "review-status.json",
        "reviewManifest": run_root / "human-review" / "package-manifest.json",
        "report": run_root / "report.md",
        "finalization": run_root / "finalization.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete RAG v5 generation run: {missing}")
    known = _json(required["known"])
    fresh = _json(required["fresh"])
    summary = _json(required["summary"])
    review_status = _json(required["reviewStatus"])
    cases = [*(known.get("cases") or []), *(fresh.get("cases") or [])]
    if len(cases) != 80:
        raise ValueError("RAG v5 generation package must contain 80 cases")
    badcases = _badcases(cases)
    compact = {
        **summary,
        "humanReviewStatus": review_status.get("status"),
        "humanReview": review_status,
        "honestBoundaries": [
            "The 80 cases are SYNTHETIC and the execution is local-live, not real-user traffic.",
            "The blind package contains only 20 fresh questions, answers and retrieved evidence.",
            "HUMAN_REVIEW_PENDING is not a completed human quality claim.",
            "Automatic claim and citation scores are deterministic proxies, not human judgments.",
            "Provider cost is UNPRICED and local latency is not a production SLO.",
            "A failed fresh run remains FAILED_RETAINED and is never replaced by an offline rescore claim.",
        ],
    }
    evidence_dir = EVIDENCE_ROOT / run_id / "generation"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(evidence_dir / "summary.json", compact)
    atomic_write_json(evidence_dir / "badcases.json", badcases)
    atomic_write_json(evidence_dir / "human-review-status.json", review_status)
    manifest = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "automaticStatus": (summary.get("qualityGate") or {}).get("status"),
        "humanReviewStatus": review_status.get("status"),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "badcasesSha256": sha256_file(evidence_dir / "badcases.json"),
        "humanReviewStatusSha256": sha256_file(
            evidence_dir / "human-review-status.json"
        ),
        "humanReviewPackageSha256": sha256_file(required["reviewManifest"]),
        "suiteLockSha256": sha256_file(SUITE_LOCK_PATH),
        "freshExecutionLockSha256": sha256_file(FRESH_EXECUTION_LOCK),
        "localArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in required.values()
        },
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    atomic_write_bytes(evidence_dir / "report.md", required["report"].read_bytes())
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8"))
    return {
        "phase": "package",
        "runId": run_id,
        "evidenceDir": str(evidence_dir),
        "automaticStatus": manifest["automaticStatus"],
        "humanReviewStatus": manifest["humanReviewStatus"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    subparsers.add_parser("prepare")
    for phase in ("collect-known", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--top-k", type=int, default=10)
        if phase in {"collect-known", "collect-final"}:
            child.add_argument(
                "--release-version",
                type=int,
                required=True,
                help="immutable Java knowledge release containing catalog v2",
            )
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return prepare(args)
    if args.phase == "collect-known":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_known(args)
    if args.phase == "collect-final":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_final(args)
    if args.phase == "package":
        return package(args)
    raise ValueError(f"unsupported RAG v5 generation phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        with canonical_fact_catalog_scope(CATALOG_PATH):
            result = asyncio.run(async_main(args))
    except Exception as exc:
        run_id = getattr(args, "run_id", None)
        if run_id and RUN_ID_RE.fullmatch(str(run_id)):
            failure_dir = _run_root(str(run_id))
            failure_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                failure_dir / "failure.json",
                {
                    "schemaVersion": 1,
                    "suite": SUITE,
                    "runId": run_id,
                    "phase": args.phase,
                    "status": "FAILED_RETAINED",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                    "recordedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
