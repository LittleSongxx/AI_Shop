"""Independent repeated Agent evaluation and pass^k evidence."""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evaluation.core.contracts import CaseResult, Domain, EvaluationCase
from evaluation.core.quality_metrics import pass_power_k
from evaluation.core.usage import merge_usage


@dataclass(frozen=True)
class TrialContext:
    trial_id: str
    evaluation_user_id: str
    request_id: str
    idempotency_key: str
    trace_id: str
    isolation_nonce: str
    # Opaque local-evaluation credential. It is deliberately excluded from
    # public evidence and is consumed once by the Agent API.
    fault_capability: str | None = None
    fault_evidence_id: str | None = None

    def public(self) -> dict[str, Any]:
        # The bearer token is intentionally generated inside the adapter and is
        # never returned here.  These identifiers are safe correlation IDs.
        return {
            "trialId": self.trial_id,
            "evaluationUserId": self.evaluation_user_id,
            "requestId": self.request_id,
            "idempotencyKey": self.idempotency_key,
            "traceId": self.trace_id,
            "isolationNonce": self.isolation_nonce,
        }


def trial_context(run_id: str, case_id: str, trial_number: int) -> TrialContext:
    if trial_number < 1:
        raise ValueError("trial_number must be positive")
    nonce = secrets.token_hex(8)
    material = f"{run_id}\0{case_id}\0{trial_number}\0{nonce}".encode()
    digest = hashlib.sha256(material).hexdigest()
    trial_id = f"{case_id}-t{trial_number:02d}-{digest[:8]}"
    return TrialContext(
        trial_id=trial_id,
        evaluation_user_id="ev" + digest[:13],
        request_id="eval-req-" + digest[:20],
        idempotency_key="eval-idem-" + digest[20:44],
        trace_id="eval-trace-" + digest[44:64],
        isolation_nonce=nonce,
    )


def _critical(case: EvaluationCase) -> bool:
    policy = case.repeat_policy or {}
    if "critical" in policy:
        return bool(policy["critical"])
    tags = set(case.tags).union(case.slice_tags)
    return bool(tags.intersection({"critical", "write", "confirmation", "idempotency"}))


def _terminal_state_correct(result: CaseResult) -> bool:
    return int(result.metrics.get("terminalStateCorrectness") or 0) == 1


def _state_diff_matches(result: CaseResult) -> bool:
    if result.state_diff is None:
        return False
    return bool(result.state_diff.get("matched"))


def _duplicate_effects(result: CaseResult) -> int:
    if result.state_diff is None:
        return 0
    return int(result.state_diff.get("duplicateSideEffectCount") or 0)


def summarize_repeated_agent(
    cases: Sequence[EvaluationCase],
    results: Sequence[CaseResult],
    *,
    k: int,
) -> dict[str, Any]:
    if k < 1:
        raise ValueError("k must be positive")
    expected = {case.case_id: case for case in cases if case.domain is Domain.AGENT}
    explicit_critical_ids = {
        case.case_id for case in expected.values() if _critical(case)
    }
    # An empty critical set is an invalid evaluation configuration: reporting
    # pass^k=0 would make a perfectly executed suite fail only because its
    # author omitted labels.  Use the conservative fallback of treating every
    # Agent case as critical and expose the policy in the evidence.
    critical_ids = explicit_critical_ids or set(expected)
    critical_selection_policy = (
        "EXPLICIT_CASE_POLICY"
        if explicit_critical_ids
        else "ALL_AGENT_CASES_FALLBACK_WHEN_NO_CRITICAL_LABEL"
    )
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        if result.domain is not Domain.AGENT:
            raise ValueError("repeat results must contain only Agent cases")
        if result.case_id not in expected:
            raise ValueError(f"unexpected repeated Agent case {result.case_id}")
        if not result.trial_id:
            raise ValueError(f"repeated result {result.case_id} has no trialId")
        grouped[result.case_id].append(result)
    per_case: dict[str, Any] = {}
    outcomes: list[list[bool]] = []
    critical_outcomes: list[list[bool]] = []
    for case_id, case in expected.items():
        rows = grouped.get(case_id, [])
        if len(rows) != k:
            trial_outcomes = [False] * k
            execution_rate = len(rows) / k
        else:
            trial_outcomes = [row.status.value == "PASSED" for row in rows]
            execution_rate = 1.0
        outcomes.append(trial_outcomes)
        if case_id in critical_ids:
            critical_outcomes.append(trial_outcomes)
        per_case[case_id] = {
            "critical": case_id in critical_ids,
            "expectedTrials": k,
            "executedTrials": len(rows),
            "trialExecutionRate": execution_rate,
            "trialPassRate": sum(trial_outcomes) / k,
            "passPower": float(all(trial_outcomes)),
            "trialIds": [row.trial_id for row in rows],
            "terminalStateCorrect": bool(rows) and all(_terminal_state_correct(row) for row in rows),
            "stateDiffMatched": bool(rows) and all(_state_diff_matches(row) for row in rows),
            "duplicateSideEffectCount": sum(_duplicate_effects(row) for row in rows),
        }
    tool_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        expected_tools = result.output.get("expectedTools") or []
        actual_tools = result.output.get("tools") or []
        expected_label = ",".join(sorted(map(str, expected_tools))) or "NONE"
        actual_label = ",".join(sorted(set(map(str, actual_tools)))) or "NONE"
        tool_confusion[expected_label][actual_label] += 1
    duplicate_count = sum(_duplicate_effects(result) for result in results)
    terminal_correct = sum(_terminal_state_correct(result) for result in results)
    state_matches = sum(_state_diff_matches(result) for result in results)
    runtime_errors = sum(result.status.value == "ERROR" for result in results)
    severe = sum(int(result.metrics.get("severeSafetyViolationCount") or 0) for result in results)
    expected_trials = len(expected) * k
    critical_pass = pass_power_k(critical_outcomes) if critical_outcomes else 0.0
    retry_rows = [
        int(result.metrics["retryIdempotency"])
        for result in results
        if "retryIdempotency" in result.metrics
    ]
    return {
        "k": k,
        "caseCount": len(expected),
        "criticalCaseCount": len(critical_outcomes),
        "criticalSelectionPolicy": critical_selection_policy,
        "expectedTrialCount": expected_trials,
        "executedTrialCount": len(results),
        "trialExecutionRate": len(results) / expected_trials if expected_trials else 0.0,
        f"pass^{k}": pass_power_k(outcomes) if outcomes else 0.0,
        "criticalWorkflowPassPower": critical_pass,
        "terminalStateCorrectness": terminal_correct / len(results) if results else 0.0,
        "stateDiffMatchRate": state_matches / len(results) if results else 0.0,
        "duplicateSideEffectCount": duplicate_count,
        "retryRecoveryRate": sum(retry_rows) / len(retry_rows) if retry_rows else 0.0,
        "runtimeErrorCount": runtime_errors,
        "severeSafetyViolationCount": severe,
        "toolRoutingConfusionMatrix": {
            expected_label: dict(sorted(counts.items()))
            for expected_label, counts in sorted(tool_confusion.items())
        },
        "usage": merge_usage(result.usage for result in results),
        "perCase": per_case,
        "hardGate": {
            "passed": (
                len(results) == expected_trials
                and critical_pass == 1.0
                and terminal_correct == len(results)
                and state_matches == len(results)
                and duplicate_count == 0
                and runtime_errors == 0
                and severe == 0
            ),
            "policy": (
                "ALL_CRITICAL_CASES_PASS_ALL_TRIALS_AND_ALL_TERMINAL_STATE_DIFF_"
                "IDEMPOTENCY_SAFETY_CHECKS_PASS"
            ),
        },
    }


async def run_repeated_agent_cases(
    cases: Sequence[EvaluationCase],
    *,
    run_id: str,
    k: int,
    execute: Callable[[EvaluationCase, TrialContext], Awaitable[CaseResult]],
) -> tuple[list[CaseResult], dict[str, Any]]:
    if not 1 <= k <= 32:
        raise ValueError("k must be between 1 and 32")
    agent_cases = [case for case in cases if case.domain is Domain.AGENT]
    if not agent_cases:
        raise ValueError("repeat requires at least one Agent case")
    results: list[CaseResult] = []
    seen_ids: set[str] = set()
    for case in agent_cases:
        for index in range(1, k + 1):
            context = trial_context(run_id, case.case_id, index)
            if context.trial_id in seen_ids:
                raise RuntimeError("trial ID collision")
            seen_ids.add(context.trial_id)
            result = await execute(case, context)
            result.trial_id = context.trial_id
            result.output.setdefault("trialContext", context.public())
            result.output.setdefault("expectedTools", list(case.expected.get("requiredTools") or []))
            results.append(result)
    return results, summarize_repeated_agent(agent_cases, results, k=k)
