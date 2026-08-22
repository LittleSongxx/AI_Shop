from __future__ import annotations

import hashlib
import inspect
import os
import re
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.db.pool import close_pool, init_pool
from evaluation.adapters.agent import run_agent_case
from evaluation.adapters.rag import run_rag_case
from evaluation.adapters.search import run_search_case
from evaluation.core.config import load_suite
from evaluation.core.contracts import (
    RUN_SCHEMA_VERSION_V3,
    CaseResult,
    CaseStatus,
    Domain,
    EvaluationCase,
    RunRecord,
    Split,
)
from evaluation.core.datasets import (
    canonical_dataset_sha256,
    load_split,
    parse_case,
    validate_repository_datasets,
)
from evaluation.core.evidence import publish_current, write_run_evidence
from evaluation.core.fingerprints import environment_facts, source_fingerprint
from evaluation.core.gates import evaluate_gates
from evaluation.core.io import load_jsonl, utc_now
from evaluation.core.lifecycle import (
    attach_final_evidence,
    begin_final_execution,
    complete_final_execution,
    final_dataset_path,
    lifecycle_status,
    mark_final_error,
)
from evaluation.core.metrics import aggregate_domain
from evaluation.core.preflight import run_preflight
from evaluation.core.slices import DEFAULT_SEARCH_SLICES, aggregate_slice_metrics
from evaluation.core.usage import summarize_usage
from evaluation.repeat_runner import TrialContext, run_repeated_agent_cases

_RUN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{5,95}$")


def validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(f"invalid run id: {value!r}")
    return value


def evaluation_user_id(run_nonce: str, case_id: str) -> str:
    """Return an isolated ID that fits the production agent_message varchar(15)."""

    material = f"{run_nonce}\0{case_id}".encode("utf-8")
    return "ev" + hashlib.sha256(material).hexdigest()[:13]


def _load_cases(split: Split, release_id: str | None) -> list[EvaluationCase]:
    if split is not Split.FINAL:
        return load_split(split)
    if not release_id:
        raise ValueError("--release-id is required for final")
    path = final_dataset_path(release_id)
    return [parse_case(row, expected_split=Split.FINAL) for row in load_jsonl(path)]


def _error_result(case: EvaluationCase, exc: Exception) -> CaseResult:
    now = utc_now()
    return CaseResult(
        case_id=case.case_id,
        domain=case.domain,
        status=CaseStatus.ERROR,
        metrics={},
        latency_ms=0,
        output={},
        providers={},
        assertions=[],
        error={
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        },
        started_at=now,
        completed_at=now,
        slice=case.slice_tags[0] if case.slice_tags else None,
        usage={
            "inputTokens": 0,
            "outputTokens": 0,
            "providerCalls": 0,
            "pricedCalls": 0,
            "unpricedCalls": 0,
            "costCny": None,
            "costStatus": "MISSING_USAGE",
            "usageReported": False,
        },
    )


async def _execute_case(
    case: EvaluationCase,
    run_nonce: str,
    trial_context: TrialContext | None = None,
) -> CaseResult:
    try:
        user_id = (
            trial_context.evaluation_user_id
            if trial_context is not None
            else evaluation_user_id(run_nonce, case.case_id)
        )
        if case.domain is Domain.SEARCH:
            return await run_search_case(
                case,
                user_id=user_id,
            )
        if case.domain is Domain.RAG:
            return await run_rag_case(case)
        return await run_agent_case(
            case,
            user_id=user_id,
            timeout_seconds=float(os.getenv("AI_EVAL_AGENT_TIMEOUT_SECONDS", "240")),
            trial_context=trial_context,
        )
    except Exception as exc:
        return _error_result(case, exc)


async def run_evaluation(
    *,
    split: Split,
    run_id: str,
    release_id: str | None = None,
    publish: bool = False,
    confirm_final: bool = False,
) -> tuple[RunRecord, Path]:
    run_id = validate_run_id(run_id)
    if split is Split.FINAL:
        if not confirm_final or not publish:
            raise ValueError("final requires both --confirm-final and --publish")
    elif publish:
        raise ValueError("only a final run can be published as current evidence")

    # Static validation and dependency probes are deliberately completed before
    # consuming the one-shot final claim. Provider probes never receive final
    # case content, so a configuration error cannot strand a release in
    # EXECUTING or create an opportunity to inspect final outputs selectively.
    validate_repository_datasets()
    cases = _load_cases(split, release_id)
    dataset_sha256 = canonical_dataset_sha256(cases)
    fingerprint = source_fingerprint()
    environment = environment_facts()
    results: list[CaseResult] = []
    final_started = False
    await init_pool()
    try:
        environment["preflight"] = await run_preflight(cases)
        if split is Split.FINAL:
            begin_final_execution(str(release_id or ""), run_id)
            final_started = True
        run_nonce = secrets.token_hex(4)
        repeated_trials: list[CaseResult] = []
        repeated_metrics: dict[str, Any] = {
            "status": "NOT_RUN",
            "reason": "use the repeat command for visible development/regression evidence",
        }
        for case in cases:
            if split is Split.FINAL and case.domain is Domain.AGENT:
                continue
            results.append(await _execute_case(case, run_nonce))
        if split is Split.FINAL:
            agent_cases = [case for case in cases if case.domain is Domain.AGENT]

            async def execute_trial(
                case: EvaluationCase, context: TrialContext
            ) -> CaseResult:
                # Keep the v2 test seam (and downstream custom adapters) that
                # still expose the two-argument executor callable.
                if len(inspect.signature(_execute_case).parameters) < 3:
                    return await _execute_case(case, run_nonce)  # type: ignore[call-arg]
                return await _execute_case(case, run_nonce, context)

            repeated_trials, repeated_metrics = await run_repeated_agent_cases(
                agent_cases,
                run_id=run_id,
                k=8,
                execute=execute_trial,
            )
            first_trials: dict[str, CaseResult] = {}
            for trial in repeated_trials:
                first_trials.setdefault(trial.case_id, trial)
            results.extend(first_trials[case.case_id] for case in agent_cases)
    except BaseException:
        if final_started:
            state = lifecycle_status(str(release_id))
            if state.get("status") == "EXECUTING":
                mark_final_error(str(release_id))
        raise
    finally:
        await close_pool()

    suite = load_suite()
    by_domain: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_domain[result.domain.value].append(result)
    semantic_rows = [
        result.semantic_judgment
        for result in results
        if result.domain is Domain.RAG and result.semantic_judgment is not None
    ]
    usage_results = [
        *[result for result in results if result.domain is not Domain.AGENT],
        *(repeated_trials if repeated_trials else [result for result in results if result.domain is Domain.AGENT]),
    ]
    usage_metrics = summarize_usage(
        [
            {
                "domain": result.domain.value,
                "slice": result.slice or "unsliced",
                "provider": result.usage.get("provider") or "aggregate",
                "model": result.usage.get("model") or "aggregate",
                "usage": result.usage,
            }
            for result in usage_results
        ]
    )
    search_slice_metrics = aggregate_slice_metrics(
        by_domain.get(Domain.SEARCH.value, []),
        expected_slices=(
            DEFAULT_SEARCH_SLICES if any(case.slice_tags for case in cases) else ()
        ),
    )
    resilience_rows = [result for result in [*results, *repeated_trials] if result.fault_scenario]
    summary = {
        "schemaVersion": "aishop-evaluation-summary/v3",
        "runId": run_id,
        "split": split.value,
        "datasetSha256": dataset_sha256,
        "executionMode": "LOCAL_FULL_STACK",
        "domains": {
            domain: aggregate_domain(
                by_domain.get(domain, []),
                config,
                suite["statisticalPolicy"],
            )
            for domain, config in suite["domains"].items()
        },
        "sliceMetrics": {"search": search_slice_metrics},
        "repeatedAgentMetrics": repeated_metrics,
        "resilienceMetrics": {
            "scenarioCount": len(resilience_rows),
            "passedCount": sum(
                bool((result.fault_scenario or {}).get("recoveryPassed"))
                for result in resilience_rows
            ),
            "allContractsPassed": bool(resilience_rows)
            and all(
                bool((result.fault_scenario or {}).get("recoveryPassed"))
                for result in resilience_rows
            ),
            "status": "MEASURED" if resilience_rows else "NOT_RUN",
        },
        "usageMetrics": usage_metrics,
        "semanticShadowMetrics": {
            "caseCount": len(semantic_rows),
            "availableCount": sum(bool(row.get("available")) for row in semantic_rows),
            "unavailableCount": sum(not bool(row.get("available")) for row in semantic_rows),
            "disagreementCount": sum(int(row.get("disagreementCount") or 0) for row in semantic_rows),
            "shadowOnly": True,
            "hardGate": False,
            "humanGroundTruth": False,
        },
        "completedAt": utc_now(),
    }
    gates = evaluate_gates(summary, suite, split=split.value)
    slice_gate_rows = {
        name: (
            bool(value["normalQualityGate"]["casePassRate"])
            and bool(value["normalQualityGate"]["constraintViolationsZero"])
            and bool(value["normalQualityGate"]["providerComplete"])
        )
        for name, value in search_slice_metrics.items()
        if int(value.get("metrics", {}).get("normalQualityCaseCount") or 0) > 0
    }
    slice_gate_passed = bool(slice_gate_rows) and all(slice_gate_rows.values())
    repeated_gate_passed = (
        bool(repeated_metrics.get("hardGate", {}).get("passed"))
        if split is Split.FINAL
        else True
    )
    gates["v3HardGates"] = {
        "searchSlices": {
            "passed": slice_gate_passed,
            "outcomes": slice_gate_rows,
            "policy": "EVERY_NORMAL_SEARCH_SLICE_MUST_PASS_WITHOUT_WEIGHTING",
        },
        "repeatedAgent": {
            "passed": repeated_gate_passed,
            "k": 8 if split is Split.FINAL else None,
        },
    }
    gates["passed"] = bool(gates.get("passed")) and slice_gate_passed and repeated_gate_passed
    run = RunRecord(
        run_id=run_id,
        split=split,
        dataset_sha256=dataset_sha256,
        source_fingerprint=fingerprint,
        environment=environment,
        cases=results,
        trials=repeated_trials,
        summary=summary,
        gates=gates,
        schema_version=RUN_SCHEMA_VERSION_V3,
        framework_schema_version="aishop-evaluation/v3",
    )
    lifecycle = None
    try:
        if split is Split.FINAL:
            outcome = "PASSED" if gates["passed"] else "FAILED"
            lifecycle = complete_final_execution(
                str(release_id),
                outcome=outcome,
                evidence_sha256=None,
            )
        evidence_root, evidence_sha256 = write_run_evidence(run, lifecycle=lifecycle)
        if split is Split.FINAL:
            if gates["passed"]:
                publish_current(evidence_root)
            attach_final_evidence(str(release_id), evidence_sha256)
    except BaseException:
        if final_started:
            state = lifecycle_status(str(release_id))
            if state.get("status") in {"EXECUTING", "EXECUTED"}:
                mark_final_error(str(release_id))
        raise
    return run, evidence_root
