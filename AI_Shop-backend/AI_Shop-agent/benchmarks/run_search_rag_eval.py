"""Run hash-locked Search/RAG public and holdout evaluations.

The deterministic layer validates datasets, labels and query understanding. It
does not claim retrieval quality. Pass --live to run the real Elasticsearch/RAG
paths; missing dependencies then produce ERROR cases and a non-zero exit code.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation import (  # noqa: E402
    EvaluationArtifactWriter,
    EvaluationAssertion,
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationRunMetadata,
    aggregate_case_results,
    sha256_path,
)
from app.evaluation.artifacts import (  # noqa: E402
    environment_fingerprint,
    git_commit,
    workspace_sha256,
)
from app.rag.evaluation import evaluate_results, placeholder_references  # noqa: E402
from benchmarks.run_search_relevance import (  # noqa: E402
    DEFAULT_CATALOG,
    _require_live_product_index,
    evaluate_graded_relevance,
    evaluate_query_understanding,
    validate_graded_contract,
)
from benchmarks.run_search_relevance import (  # noqa: E402
    DEFAULT_DATASET as PUBLIC_SEARCH_DATASET,
)
from benchmarks.run_search_relevance import (  # noqa: E402
    DEFAULT_LOCK as PUBLIC_SEARCH_LOCK,
)
from benchmarks.run_search_relevance import (  # noqa: E402
    load_cases as load_search_cases,
)
from scripts.eval_rag import (  # noqa: E402
    DEFAULT_DATASET as PUBLIC_RAG_DATASET,
)
from scripts.eval_rag import (  # noqa: E402
    DEFAULT_LOCK as PUBLIC_RAG_LOCK,
)
from scripts.eval_rag import (  # noqa: E402
    _candidate_results,
    validate_live_contract,
    validate_local_contract,
)
from scripts.eval_rag import load_cases as load_rag_cases  # noqa: E402

SUITE = "search-rag-v1"
DATASETS_ROOT = PROJECT_ROOT / "benchmarks" / "datasets"
SEARCH_HOLDOUT_DATASET = DATASETS_ROOT / "search_holdout_v1.jsonl"
SEARCH_HOLDOUT_LOCK = DATASETS_ROOT / "search_holdout_v1.lock.json"
RAG_HOLDOUT_DATASET = DATASETS_ROOT / "rag_holdout_v1.jsonl"
RAG_HOLDOUT_LOCK = DATASETS_ROOT / "rag_holdout_v1.lock.json"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results"
BASELINES_ROOT = PROJECT_ROOT / "benchmarks" / "baselines"


def _assertion(
    name: str,
    passed: bool,
    *,
    expected: Any = True,
    actual: Any = None,
    severity: str = "ERROR",
) -> EvaluationAssertion:
    return EvaluationAssertion(
        name=name,
        passed=bool(passed),
        expected=expected,
        actual=actual,
        severity=severity,
    )


def _load_lock(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _combined_dataset_sha(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_common_cases(
    cases: list[dict[str, Any]], *, expected_count: int, expected_split: str | None
) -> list[str]:
    errors: list[str] = []
    ids = [str(case.get("id") or "") for case in cases]
    if len(cases) != expected_count:
        errors.append(f"case count {len(cases)} != locked {expected_count}")
    if not ids or "" in ids or len(ids) != len(set(ids)):
        errors.append("case IDs must be non-empty and unique")
    if expected_split and any(case.get("split") != expected_split for case in cases):
        errors.append(f"every case must use split={expected_split}")
    return errors


def validate_search_holdout_contract() -> dict[str, Any]:
    cases = load_search_cases(SEARCH_HOLDOUT_DATASET)
    lock = _load_lock(SEARCH_HOLDOUT_LOCK)
    errors = _validate_common_cases(
        cases,
        expected_count=int(lock.get("caseCount") or 0),
        expected_split="holdout",
    )
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported search holdout lock schema")
    if sha256_path(SEARCH_HOLDOUT_DATASET) != lock.get("datasetSha256"):
        errors.append("search holdout dataset SHA mismatch")
    if sha256_path(DEFAULT_CATALOG) != lock.get("catalogSha256"):
        errors.append("search holdout catalog SHA mismatch")
    taxonomy_path = PROJECT_ROOT / str(lock.get("taxonomyPath") or "")
    if not taxonomy_path.is_file() or sha256_path(taxonomy_path) != lock.get(
        "taxonomySha256"
    ):
        errors.append("search taxonomy SHA mismatch")

    catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    product_ids = {
        str((row.get("productInfo") or {}).get("productId") or "")
        for row in catalog.get("products") or []
    }
    product_ids.discard("")
    if len(product_ids) != int(lock.get("productCount") or 0):
        errors.append("search holdout product count mismatch")
    for case in cases:
        relevant = {str(value) for value in case.get("relevantProductIds") or []}
        grades = {str(key): value for key, value in (case.get("relevanceGrades") or {}).items()}
        if not relevant or relevant != set(grades):
            errors.append(f"{case.get('id')} labels and grades differ")
        if relevant - product_ids:
            errors.append(f"{case.get('id')} references products outside locked catalog")
    if errors:
        raise ValueError("search holdout contract invalid:\n- " + "\n- ".join(errors))
    return {"cases": cases, "lock": lock}


def validate_rag_holdout_contract() -> dict[str, Any]:
    contract = validate_local_contract(RAG_HOLDOUT_DATASET, RAG_HOLDOUT_LOCK)
    cases = load_rag_cases(RAG_HOLDOUT_DATASET)
    lock = contract["lock"]
    errors = _validate_common_cases(
        cases,
        expected_count=int(lock.get("caseCount") or 0),
        expected_split="holdout",
    )
    if placeholder_references(cases):
        errors.append("RAG holdout contains placeholder references")
    if sum(bool(case.get("noAnswer")) for case in cases) < 2:
        errors.append("RAG holdout must contain at least two no-answer cases")
    if sum(bool(case.get("injection")) for case in cases) < 2:
        errors.append("RAG holdout must contain at least two injection cases")
    if errors:
        raise ValueError("RAG holdout contract invalid:\n- " + "\n- ".join(errors))
    return {
        **contract,
        "caseCount": contract["cases"],
        "cases": cases,
        "lock": lock,
    }


def _deterministic_search_cases(
    cases: list[dict[str, Any]], *, run_id: str, split: str
) -> tuple[list[EvaluationCaseResult], dict[str, Any]]:
    contract_cases = [
        case
        for case in cases
        if "expectKeyword" in case or bool(case.get("expectTerms"))
    ]
    report = evaluate_query_understanding(contract_cases)
    failures_by_id: dict[str, list[dict[str, Any]]] = {}
    for failure in report["failures"]:
        failures_by_id.setdefault(str(failure.get("id")), []).append(failure)
    results: list[EvaluationCaseResult] = []
    for case in contract_cases:
        case_id = str(case["id"])
        failures = failures_by_id.get(case_id, [])
        keyword_graded = "expectKeyword" in case
        expected_terms = case.get("expectTerms")
        term_graded = isinstance(expected_terms, list) and bool(expected_terms)
        assertions: list[EvaluationAssertion] = []
        if keyword_graded:
            failure = next((row for row in failures if row["field"] == "keyword"), None)
            assertions.append(
                _assertion(
                    "query_keyword_normalized",
                    failure is None,
                    expected=case.get("expectKeyword"),
                    actual=(failure or {}).get("actual", case.get("expectKeyword")),
                )
            )
        if term_graded:
            failure = next((row for row in failures if row["field"] == "terms"), None)
            assertions.append(
                _assertion(
                    "query_terms_covered",
                    failure is None,
                    expected=expected_terms,
                    actual=(failure or {}).get("actual", expected_terms),
                )
            )
        if not assertions:
            assertions.append(_assertion("query_contract_present", False, expected="labels"))
        passed = all(item.passed for item in assertions)
        results.append(
            EvaluationCaseResult(
                suite=SUITE,
                runId=run_id,
                caseId=f"search:{split}:{case_id}",
                subset=f"search_query_{case.get('subset') or 'default'}",
                split=split,
                priority=case.get("priority") or "P1",
                status="PASSED" if passed else "FAILED",
                executed=True,
                taskSuccess=passed,
                assertions=assertions,
                stepCount=1,
                evidenceSource="SYNTHETIC",
                executionMode="deterministic",
                observations={
                    "layer": "query_understanding",
                    "retrievalQualityClaimed": False,
                },
            )
        )
    return results, report


def _deterministic_rag_cases(
    cases: list[dict[str, Any]], *, run_id: str, split: str
) -> list[EvaluationCaseResult]:
    results: list[EvaluationCaseResult] = []
    for case in cases:
        expected_refs = case.get("relevantRefs") or case.get("relevantIds") or []
        expected_no_answer = bool(case.get("noAnswer", not expected_refs))
        labels_valid = expected_no_answer or bool(expected_refs)
        assertions = [
            _assertion(
                "rag_label_contract_valid",
                labels_valid,
                expected="relevantRefs or noAnswer=true",
                actual="valid" if labels_valid else "missing",
            )
        ]
        results.append(
            EvaluationCaseResult(
                suite=SUITE,
                runId=run_id,
                caseId=f"rag:{split}:{case['id']}",
                subset=f"rag_contract_{case.get('subset') or 'default'}",
                split=split,
                priority=case.get("priority") or "P1",
                status="PASSED" if labels_valid else "FAILED",
                executed=True,
                taskSuccess=labels_valid,
                assertions=assertions,
                stepCount=1,
                evidenceSource="SYNTHETIC",
                executionMode="deterministic",
                observations={
                    "layer": "dataset_contract",
                    "retrievalQualityClaimed": False,
                    "expectedNoAnswer": expected_no_answer,
                    "injectionCase": bool(case.get("injection")),
                },
            )
        )
    return results


def _live_search_cases(
    cases: list[dict[str, Any]],
    graded: dict[str, Any],
    *,
    run_id: str,
    split: str,
    k: int,
) -> list[EvaluationCaseResult]:
    by_id = {str(row["id"]): row for row in graded["perCase"]}
    results: list[EvaluationCaseResult] = []
    for case in cases:
        case_id = str(case["id"])
        row = by_id[case_id]
        recall = float(row["recall"])
        rr = float(row["reciprocalRank"])
        ndcg = float(row["ndcg"])
        assertions = [
            _assertion("relevant_product_recalled", recall > 0, expected=">0", actual=recall),
            _assertion("first_relevant_product_ranked", rr > 0, expected=">0", actual=rr),
            _assertion("graded_ranking_has_gain", ndcg > 0, expected=">0", actual=ndcg),
        ]
        passed = all(item.passed for item in assertions)
        results.append(
            EvaluationCaseResult(
                suite=SUITE,
                runId=run_id,
                caseId=f"search-live:{split}:{case_id}",
                subset="search_relevance",
                split=split,
                priority=case.get("priority") or "P1",
                status="PASSED" if passed else "FAILED",
                executed=True,
                taskSuccess=passed,
                toolCorrect=passed,
                parameterCorrect=True,
                assertions=assertions,
                latencyMs=row.get("latencyMs"),
                stepCount=1,
                toolCallCount=2,
                evidenceSource="SYNTHETIC",
                executionMode="local-live",
                observations={
                    "k": k,
                    "recallAtK": recall,
                    "reciprocalRank": rr,
                    "ndcgAtK": ndcg,
                    "returnedCount": len(row.get("returned") or []),
                },
            )
        )
    return results


def _labelled_search_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        case
        for case in cases
        if isinstance(case.get("relevantProductIds"), list)
        and bool(case["relevantProductIds"])
    ]


def _live_rag_cases(
    cases: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    run_id: str,
    split: str,
) -> list[EvaluationCaseResult]:
    results: list[EvaluationCaseResult] = []
    for case, row in zip(cases, metrics["perCase"]):
        passed = bool(row.get("passed"))
        retrieval_modes = sorted(
            {
                str(ref.get("retrieval") or "unknown")
                for ref in row.get("retrievedRefs") or []
                if isinstance(ref, dict)
            }
        )
        rerank_fallback = "rrf" in retrieval_modes
        assertions = [
            _assertion("rag_case_passed", passed, expected=True, actual=passed),
            _assertion(
                "rerank_completed_or_not_applicable",
                not rerank_fallback,
                expected="rerank, exact_faq, or no candidates",
                actual=retrieval_modes or ["no_candidates"],
            ),
        ]
        passed = passed and not rerank_fallback
        if case.get("injection"):
            robust = bool(row.get("injectionRobust"))
            assertions.append(
                _assertion(
                    "injection_robust",
                    robust,
                    expected=True,
                    actual=robust,
                    severity="CRITICAL",
                )
            )
            passed = passed and robust
        violations = [] if passed else (["rag_injection_failure"] if case.get("injection") else [])
        results.append(
            EvaluationCaseResult(
                suite=SUITE,
                runId=run_id,
                caseId=f"rag-live:{split}:{case['id']}",
                subset=f"rag_{case.get('subset') or 'default'}",
                split=split,
                priority=case.get("priority") or "P1",
                status="PASSED" if passed else "FAILED",
                executed=True,
                taskSuccess=passed,
                toolCorrect=passed,
                parameterCorrect=True,
                safetyViolations=violations,
                criticalSafetyViolations=len(violations),
                assertions=assertions,
                latencyMs=row.get("latencyMs"),
                stepCount=1,
                toolCallCount=1,
                evidenceSource="SYNTHETIC",
                executionMode="local-live",
                observations={
                    "recallAtK": row.get("recallAtK"),
                    "reciprocalRank": row.get("reciprocalRank"),
                    "ndcgAtK": row.get("ndcgAtK"),
                    "citationCorrectness": row.get("citationCorrectness"),
                    "citationCoverage": row.get("citationCoverage"),
                    "predictedNoAnswer": row.get("predictedNoAnswer"),
                    "injectionRobust": row.get("injectionRobust"),
                    "retrievalModes": retrieval_modes,
                    "rerankFallback": rerank_fallback,
                },
            )
        )
    return results


def _error_case(
    *, run_id: str, case_id: str, subset: str, split: str, exc: Exception
) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        suite=SUITE,
        runId=run_id,
        caseId=case_id,
        subset=subset,
        split=split,
        priority="P0",
        status="ERROR",
        executed=True,
        taskSuccess=False,
        assertions=[
            _assertion(
                "live_execution_completed",
                False,
                expected="completed",
                actual=type(exc).__name__,
            )
        ],
        errorType=type(exc).__name__,
        errorMessage="live dependency or evaluation path failed; deterministic results were not substituted",
        evidenceSource="SYNTHETIC",
        executionMode="local-live",
        observations={"liveResultAvailable": False},
    )


async def _run_live_search(
    cases: list[dict[str, Any]], *, run_id: str, split: str, top_k: int
) -> tuple[list[EvaluationCaseResult], dict[str, Any]]:
    await _require_live_product_index(47)
    labelled = _labelled_search_cases(cases)
    graded = await evaluate_graded_relevance(labelled, top_k)
    return (
        _live_search_cases(labelled, graded, run_id=run_id, split=split, k=top_k),
        graded,
    )


async def _run_live_rag(
    cases: list[dict[str, Any]],
    lock: dict[str, Any],
    *,
    run_id: str,
    split: str,
    top_k: int,
) -> tuple[list[EvaluationCaseResult], dict[str, Any]]:
    from app.rag.retriever import rag_retriever
    from app.services.redis_service import redis_service

    await redis_service.ensure_connected()
    try:
        live_contract = await validate_live_contract(lock)
        raw_results = []
        for case in cases:
            started = time.perf_counter()
            raw = await rag_retriever.search_faq_with_trace(
                case.get("query") or "",
                top_k=top_k,
                include_evaluation_candidates=True,
            )
            raw.setdefault("trace", {})["runnerLatencyMs"] = round(
                (time.perf_counter() - started) * 1000, 4
            )
            raw_results.append(raw)
        threshold = float(lock.get("selectedThreshold") or 0.65)
        metrics = evaluate_results(
            cases,
            _candidate_results(raw_results, threshold),
            top_k=top_k,
        )
        return (
            _live_rag_cases(cases, metrics, run_id=run_id, split=split),
            {"contract": live_contract, "threshold": threshold, "metrics": metrics},
        )
    finally:
        await redis_service.close()


def _metric_gate_failures(
    metrics: dict[str, Any], thresholds: dict[str, Any], *, k: int
) -> list[str]:
    aliases = {
        "recallAt10": f"recallAt{k}",
        "ndcgAt10": f"ndcgAt{k}",
    }
    failures: list[str] = []
    for threshold_name, minimum in thresholds.items():
        metric_name = aliases.get(threshold_name, threshold_name)
        actual = metrics.get(metric_name)
        if actual is None:
            failures.append(f"required metric {metric_name} is missing")
        elif float(actual) < float(minimum):
            failures.append(f"{metric_name} {actual} < {minimum}")
    return failures


async def run(
    *,
    run_id: str | None = None,
    live: bool = False,
    top_k: int = 10,
    accept_baseline: bool = False,
    allow_embedding_cache: bool = False,
) -> tuple[EvaluationRun, Path, list[str]]:
    resolved_run_id = run_id or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    ) + f"-{uuid.uuid4().hex[:8]}"
    public_search_cases = load_search_cases(PUBLIC_SEARCH_DATASET)
    public_search_contract = validate_graded_contract(
        public_search_cases, PUBLIC_SEARCH_DATASET, PUBLIC_SEARCH_LOCK, DEFAULT_CATALOG
    )
    search_holdout = validate_search_holdout_contract()
    public_rag_contract = validate_local_contract(PUBLIC_RAG_DATASET, PUBLIC_RAG_LOCK)
    public_rag_cases = load_rag_cases(PUBLIC_RAG_DATASET)
    rag_holdout = validate_rag_holdout_contract()

    cases: list[EvaluationCaseResult] = []
    public_query_results, public_query_metrics = _deterministic_search_cases(
        public_search_cases, run_id=resolved_run_id, split="public"
    )
    holdout_query_results, holdout_query_metrics = _deterministic_search_cases(
        search_holdout["cases"], run_id=resolved_run_id, split="holdout"
    )
    cases.extend(public_query_results)
    cases.extend(holdout_query_results)
    cases.extend(
        _deterministic_rag_cases(
            public_rag_cases, run_id=resolved_run_id, split="public"
        )
    )
    cases.extend(
        _deterministic_rag_cases(
            rag_holdout["cases"], run_id=resolved_run_id, split="holdout"
        )
    )
    failures: list[str] = []
    live_metrics: dict[str, Any] = {}

    provider_facts: dict[str, Any] = {}
    if live:
        from app.rag.embedding import embedding_evaluation_scope
        from app.rag.retriever import rerank_evaluation_scope

        with embedding_evaluation_scope(
            bypass_cache=not allow_embedding_cache
        ) as embedding_stats, rerank_evaluation_scope() as rerank_stats:
            live_jobs = [
                ("search-public", public_search_cases, public_search_contract, "public"),
                ("search-holdout", search_holdout["cases"], search_holdout["lock"], "holdout"),
            ]
            for name, rows, contract, split in live_jobs:
                try:
                    result_cases, metrics = await _run_live_search(
                        rows, run_id=resolved_run_id, split=split, top_k=top_k
                    )
                    cases.extend(result_cases)
                    live_metrics[name] = metrics
                    query_metrics = (
                        public_query_metrics if split == "public" else holdout_query_metrics
                    )
                    failures.extend(
                        f"{name}: {failure}"
                        for failure in _metric_gate_failures(
                            {**query_metrics, **metrics},
                            contract.get("thresholds") or {},
                            k=top_k,
                        )
                    )
                except Exception as exc:
                    cases.append(
                        _error_case(
                            run_id=resolved_run_id,
                            case_id=f"{name}:runtime",
                            subset="search_relevance",
                            split=split,
                            exc=exc,
                        )
                    )
                    failures.append(
                        f"{name} live execution failed: {type(exc).__name__}"
                    )

            for name, rows, contract, split in (
                ("rag-public", public_rag_cases, public_rag_contract["lock"], "public"),
                ("rag-holdout", rag_holdout["cases"], rag_holdout["lock"], "holdout"),
            ):
                try:
                    result_cases, report = await _run_live_rag(
                        rows,
                        contract,
                        run_id=resolved_run_id,
                        split=split,
                        top_k=top_k,
                    )
                    cases.extend(result_cases)
                    live_metrics[name] = report
                    rag_thresholds = contract.get("thresholds") or contract.get(
                        "qualityBaseline"
                    ) or {}
                    failures.extend(
                        f"{name}: {failure}"
                        for failure in _metric_gate_failures(
                            report["metrics"], rag_thresholds, k=top_k
                        )
                    )
                except Exception as exc:
                    cases.append(
                        _error_case(
                            run_id=resolved_run_id,
                            case_id=f"{name}:runtime",
                            subset="rag_retrieval",
                            split=split,
                            exc=exc,
                        )
                    )
                    failures.append(
                        f"{name} live execution failed: {type(exc).__name__}"
                    )

            provider_facts = {
                "embedding": embedding_stats.snapshot(),
                "rerank": rerank_stats.snapshot(),
            }

        embedding = provider_facts["embedding"]
        rerank = provider_facts["rerank"]
        if not allow_embedding_cache and int(embedding["cacheHits"]) != 0:
            failures.append("embedding cache was used despite live cache bypass")
        if int(embedding["providerSuccesses"]) <= 0:
            failures.append("embedding provider produced no successful request")
        if int(embedding["providerFailures"]) != 0:
            failures.append(
                f"embedding provider failures: {embedding['providerFailures']}"
            )
        if int(rerank["providerSuccesses"]) <= 0:
            failures.append("rerank provider produced no successful request")
        if int(rerank["providerFailures"]) != 0 or int(rerank["fallbackCount"]) != 0:
            failures.append(
                "rerank provider fallback detected: "
                f"failures={rerank['providerFailures']}, fallbacks={rerank['fallbackCount']}"
            )

    summary = aggregate_case_results(cases)
    contract_failures = [
        case.case_id
        for case in cases
        if case.execution_mode == "deterministic" and case.status != "PASSED"
    ]
    execution_failures = [
        case.case_id
        for case in cases
        if case.execution_mode == "local-live"
        and (case.status == "ERROR" or not case.executed)
    ]
    failures.extend(contract_failures)
    failures.extend(execution_failures)
    summary["evaluationLayers"] = {
        "deterministic": {
            "claim": "dataset/query-understanding contract only",
            "retrievalQualityClaimed": False,
            "public": {
                "keywordAccuracy": public_query_metrics["keywordAccuracy"],
                "termCoverage": public_query_metrics["termCoverage"],
            },
            "holdout": {
                "keywordAccuracy": holdout_query_metrics["keywordAccuracy"],
                "termCoverage": holdout_query_metrics["termCoverage"],
            },
        },
        "live": {
            "requested": live,
            "executed": bool(live_metrics),
            "claim": "real local retrieval quality" if live_metrics else "未采集",
            "metrics": live_metrics,
        },
    }
    summary["datasetSplits"] = {
        "publicSearchCases": len(public_search_cases),
        "holdoutSearchCases": len(search_holdout["cases"]),
        "publicRagCases": len(public_rag_cases),
        "holdoutRagCases": len(rag_holdout["cases"]),
    }
    live_search_count = sum(
        len((live_metrics.get(name) or {}).get("perCase") or [])
        for name in ("search-public", "search-holdout")
    )
    live_rag_count = sum(
        int(((live_metrics.get(name) or {}).get("metrics") or {}).get("cases") or 0)
        for name in ("rag-public", "rag-holdout")
    )
    summary["caseLayers"] = {
        "deterministicCaseCount": len(public_query_results)
        + len(holdout_query_results)
        + len(public_rag_cases)
        + len(rag_holdout["cases"]),
        "liveSearchCaseCount": live_search_count,
        "liveRagCaseCount": live_rag_count,
        "liveCaseCount": live_search_count + live_rag_count,
    }
    summary["providerFacts"] = provider_facts
    live_case_failures = [
        case.case_id
        for case in cases
        if case.execution_mode == "local-live" and case.status == "FAILED"
    ]
    summary["qualityGate"] = {
        "passed": not failures,
        "liveRequired": live,
        "failureCount": len(set(failures)),
        "failedLiveCaseCount": len(live_case_failures),
        "failedLiveCases": live_case_failures,
    }
    metadata = EvaluationRunMetadata(
        suite=SUITE,
        runId=resolved_run_id,
        gitCommit=git_commit(REPO_ROOT),
        workspaceSha256=workspace_sha256(REPO_ROOT),
        datasetSha256=_combined_dataset_sha(
            [
                PUBLIC_SEARCH_DATASET,
                PUBLIC_SEARCH_LOCK,
                SEARCH_HOLDOUT_DATASET,
                SEARCH_HOLDOUT_LOCK,
                PUBLIC_RAG_DATASET,
                PUBLIC_RAG_LOCK,
                RAG_HOLDOUT_DATASET,
                RAG_HOLDOUT_LOCK,
            ]
        ),
        evidenceSource="SYNTHETIC",
        executionMode="local-live" if live else "deterministic",
        environment={
            **environment_fingerprint(),
            "externalSystems": "local-live" if live else "not-called",
        },
        model={"provider": "configured-runtime" if live else "none"},
        parameters={
            "topK": top_k,
            "live": live,
            "allowEmbeddingCache": allow_embedding_cache,
            "publicAndHoldoutReportedSeparately": True,
        },
    )
    evaluation = EvaluationRun(metadata=metadata, cases=cases, summary=summary)
    writer = EvaluationArtifactWriter(RESULTS_ROOT, BASELINES_ROOT)
    result_dir = writer.write_run(evaluation)
    if accept_baseline:
        writer.accept_baseline(evaluation)
    return evaluation, result_dir, sorted(set(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument(
        "--allow-embedding-cache",
        action="store_true",
        help="allow cached query embeddings (debug only; omitted for live evidence)",
    )
    args = parser.parse_args()
    try:
        evaluation, result_dir, failures = asyncio.run(
            run(
                run_id=args.run_id,
                live=args.live,
                top_k=args.top_k,
                accept_baseline=args.accept_baseline,
                allow_embedding_cache=args.allow_embedding_cache,
            )
        )
    except (ValueError, AssertionError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        json.dumps(
            {
                "runId": evaluation.metadata.run_id,
                "resultDir": str(result_dir),
                "summary": evaluation.summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        print("search/RAG evaluation failed: " + "; ".join(failures), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
