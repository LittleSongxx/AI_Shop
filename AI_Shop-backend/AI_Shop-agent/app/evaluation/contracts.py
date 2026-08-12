"""Versioned, provider-neutral evaluation result contract.

The contract intentionally keeps raw prompts and conversations out of the
summary. Suites may attach sanitized observations to individual case results,
while evidence source and execution mode remain explicit at both levels.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EVAL_SCHEMA_VERSION = "aishop-eval/v1"

EvidenceSource = Literal["SYNTHETIC", "LOCAL_PILOT", "REAL_USER"]
ExecutionMode = Literal["deterministic", "local-live", "online-live"]
CaseStatus = Literal["PASSED", "FAILED", "ERROR", "SKIPPED"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvaluationAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    passed: bool
    expected: Any = None
    actual: Any = None
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"] = "ERROR"
    message: str | None = Field(default=None, max_length=1000)


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(default=EVAL_SCHEMA_VERSION, alias="schemaVersion")
    suite: str = Field(min_length=1, max_length=120)
    run_id: str = Field(alias="runId", min_length=1, max_length=160)
    case_id: str = Field(alias="caseId", min_length=1, max_length=160)
    subset: str = Field(min_length=1, max_length=120)
    split: str = Field(min_length=1, max_length=32)
    priority: Literal["P0", "P1", "P2"]
    status: CaseStatus
    executed: bool
    task_success: bool = Field(alias="taskSuccess")
    tool_correct: bool | None = Field(default=None, alias="toolCorrect")
    parameter_correct: bool | None = Field(default=None, alias="parameterCorrect")
    safety_violations: list[str] = Field(default_factory=list, alias="safetyViolations")
    critical_safety_violations: int = Field(
        default=0, alias="criticalSafetyViolations", ge=0
    )
    assertions: list[EvaluationAssertion] = Field(default_factory=list)
    error_type: str | None = Field(default=None, alias="errorType", max_length=160)
    error_message: str | None = Field(default=None, alias="errorMessage", max_length=1000)
    latency_ms: float | None = Field(default=None, alias="latencyMs", ge=0)
    ttft_ms: float | None = Field(default=None, alias="ttftMs", ge=0)
    step_count: int = Field(default=0, alias="stepCount", ge=0)
    model_call_count: int = Field(default=0, alias="modelCallCount", ge=0)
    tool_call_count: int = Field(default=0, alias="toolCallCount", ge=0)
    input_tokens: int = Field(default=0, alias="inputTokens", ge=0)
    output_tokens: int = Field(default=0, alias="outputTokens", ge=0)
    cost_cny: float = Field(default=0.0, alias="costCny", ge=0)
    evidence_source: EvidenceSource = Field(alias="evidenceSource")
    execution_mode: ExecutionMode = Field(alias="executionMode")
    observations: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != EVAL_SCHEMA_VERSION:
            raise ValueError(f"schemaVersion must be {EVAL_SCHEMA_VERSION}")
        return value

    @model_validator(mode="after")
    def validate_execution_state(self) -> "EvaluationCaseResult":
        if not self.executed and self.status != "SKIPPED":
            raise ValueError("an unexecuted case must be SKIPPED")
        if self.status == "PASSED" and not self.task_success:
            raise ValueError("a passed case must have taskSuccess=true")
        if self.status in {"FAILED", "ERROR"} and self.task_success:
            raise ValueError("a failed/error case cannot have taskSuccess=true")
        if self.status == "ERROR" and not self.error_type:
            raise ValueError("an error case must include errorType")
        if self.executed and not self.assertions:
            raise ValueError("an executed case must include assertions")
        assertions_passed = all(assertion.passed for assertion in self.assertions)
        if self.status == "PASSED" and not assertions_passed:
            raise ValueError("a passed case cannot contain failed assertions")
        if self.status == "FAILED" and assertions_passed:
            raise ValueError("a failed case must contain a failed assertion")
        if self.critical_safety_violations > len(self.safety_violations):
            raise ValueError("criticalSafetyViolations exceeds safetyViolations")
        return self


class EvaluationRunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(default=EVAL_SCHEMA_VERSION, alias="schemaVersion")
    suite: str = Field(min_length=1, max_length=120)
    run_id: str = Field(alias="runId", min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=_utc_now, alias="createdAt")
    git_commit: str = Field(alias="gitCommit", min_length=1, max_length=64)
    workspace_sha256: str = Field(alias="workspaceSha256", min_length=64, max_length=64)
    dataset_sha256: str = Field(alias="datasetSha256", min_length=64, max_length=64)
    evidence_source: EvidenceSource = Field(alias="evidenceSource")
    execution_mode: ExecutionMode = Field(alias="executionMode")
    environment: dict[str, Any]
    model: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metadata: EvaluationRunMetadata
    cases: list[EvaluationCaseResult]
    summary: dict[str, Any]

    @model_validator(mode="after")
    def validate_run(self) -> "EvaluationRun":
        if not self.cases:
            raise ValueError("evaluation run contains no cases")
        if any(case.run_id != self.metadata.run_id for case in self.cases):
            raise ValueError("case runId differs from metadata runId")
        if any(case.suite != self.metadata.suite for case in self.cases):
            raise ValueError("case suite differs from metadata suite")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation run contains duplicate caseId values")
        return self


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def _distribution(values: list[float], *, suffix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"samples": len(values)}
    for name, quantile in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        value = percentile(values, quantile)
        result[f"{name}{suffix}"] = round(value, 4) if value is not None else None
    result[f"max{suffix}"] = round(max(values), 4) if values else None
    return result


def aggregate_case_results(cases: list[EvaluationCaseResult]) -> dict[str, Any]:
    if not cases:
        raise ValueError("cannot aggregate an empty case list")
    executed = [case for case in cases if case.executed]
    successes = [case for case in executed if case.task_success]
    latency = [case.latency_ms for case in executed if case.latency_ms is not None]
    ttft = [case.ttft_ms for case in executed if case.ttft_ms is not None]
    total_cost = round(sum(case.cost_cny for case in executed), 8)
    total_input_tokens = sum(case.input_tokens for case in executed)
    total_output_tokens = sum(case.output_tokens for case in executed)
    safety = sum(len(case.safety_violations) for case in executed)
    critical_safety = sum(case.critical_safety_violations for case in executed)
    status_counts = Counter(case.status for case in cases)
    subset_counts = Counter(case.subset for case in cases)
    return {
        "schemaVersion": EVAL_SCHEMA_VERSION,
        "caseCount": len(cases),
        "executedCount": len(executed),
        "unexecutedCount": len(cases) - len(executed),
        "taskSuccesses": len(successes),
        "taskSuccessRate": round(len(successes) / len(executed), 6) if executed else 0.0,
        "toolCorrectRate": _optional_rate(executed, "tool_correct"),
        "parameterCorrectRate": _optional_rate(executed, "parameter_correct"),
        "statusCounts": dict(sorted(status_counts.items())),
        "subsetCounts": dict(sorted(subset_counts.items())),
        "safetyViolationCount": safety,
        "criticalSafetyViolationCount": critical_safety,
        "latency": _distribution([float(value) for value in latency], suffix="Ms"),
        "ttft": _distribution([float(value) for value in ttft], suffix="Ms"),
        "stepCount": _distribution([float(case.step_count) for case in executed]),
        "modelCallCount": _distribution(
            [float(case.model_call_count) for case in executed]
        ),
        "toolCallCount": _distribution([float(case.tool_call_count) for case in executed]),
        "inputTokens": total_input_tokens,
        "outputTokens": total_output_tokens,
        "totalTokens": total_input_tokens + total_output_tokens,
        "costCny": total_cost,
        "costPerSuccessfulTaskCny": (
            round(total_cost / len(successes), 8) if successes else None
        ),
        "sampleDisclosure": {
            "p99Reliable": len(latency) >= 100,
            "message": (
                "未采集延迟样本，P99 不可用。"
                if not latency
                else (
                    None
                    if len(latency) >= 100
                    else "样本少于 100，P99 仅作样本描述，不作为总体尾延迟结论。"
                )
            ),
        },
    }


def _optional_rate(cases: list[EvaluationCaseResult], field: str) -> float | None:
    values = [getattr(case, field) for case in cases if getattr(case, field) is not None]
    if not values:
        return None
    return round(sum(value is True for value in values) / len(values), 6)
