"""Provider-neutral contracts for the single evaluation lifecycle."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RunPhase(StrEnum):
    VALIDATED = "VALIDATED"
    PREFLIGHTED = "PREFLIGHTED"
    KNOWN_COLLECTED = "KNOWN_COLLECTED"
    FROZEN = "FROZEN"
    FINAL_COLLECTED = "FINAL_COLLECTED"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"
    PACKAGED = "PACKAGED"
    BLOCKED = "BLOCKED"
    FAILED_RETAINED = "FAILED_RETAINED"


class RunState(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED_RETAINED = "FAILED_RETAINED"


class FailureClass(StrEnum):
    NONE = "NONE"
    QUALITY_FAIL = "QUALITY_FAIL"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELLED = "CANCELLED"
    CONTRACT_ERROR = "CONTRACT_ERROR"


@dataclass(frozen=True)
class CaseOutcome:
    """Sanitized, layer-neutral outcome for one evaluation case."""

    case_id: str
    status: str
    executed: bool
    stage: str
    failure_class: FailureClass = FailureClass.NONE
    error_type: str | None = None
    error_message: str | None = None
    attempts: int = 1
    latency_ms: float | None = None
    provider_facts: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    replay_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["failureClass"] = value.pop("failure_class").value
        value["caseId"] = value.pop("case_id")
        value["errorType"] = value.pop("error_type")
        value["errorMessage"] = value.pop("error_message")
        value["latencyMs"] = value.pop("latency_ms")
        value["providerFacts"] = value.pop("provider_facts")
        value["traceId"] = value.pop("trace_id")
        value["replayFingerprint"] = value.pop("replay_fingerprint")
        return value


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    scope: str = "quality"
    failure_class: FailureClass = FailureClass.NONE
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["failureClass"] = value.pop("failure_class").value
        return value


@dataclass
class StageResult:
    stage: str
    status: str = "COMPLETE"
    result: dict[str, Any] = field(default_factory=dict)
    outcomes: list[CaseOutcome] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)
    provider_facts: dict[str, Any] = field(default_factory=dict)
    failure_class: FailureClass = FailureClass.NONE
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "result": self.result,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "gates": [item.to_dict() for item in self.gates],
            "providerFacts": self.provider_facts,
            "failureClass": self.failure_class.value,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
        }


def classify_exception(exc: BaseException) -> FailureClass:
    """Map common runtime failures without exposing credentials or payloads."""

    if isinstance(exc, asyncio.CancelledError):
        return FailureClass.CANCELLED
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return FailureClass.TIMEOUT
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "circuit" in name or "breaker" in message or "circuit_open" in message:
        return FailureClass.CIRCUIT_OPEN
    if any(token in name or token in message for token in ("ratelimit", "rate_limit", "429", "quota")):
        return FailureClass.RATE_LIMITED
    if any(token in name for token in ("connection", "connect", "http", "network")):
        return FailureClass.SERVICE_UNAVAILABLE
    if any(
        token in message
        for token in (
            "connection refused",
            "all connection attempts failed",
            "captcha",
            "noauth",
            "redis",
            "mysql",
            "rabbitmq",
            "elasticsearch",
            "service unavailable",
        )
    ):
        return FailureClass.DEPENDENCY_ERROR
    if any(token in name or token in message for token in ("provider", "embedding", "rerank", "llm")):
        return FailureClass.PROVIDER_ERROR
    if any(token in name or token in message for token in ("budget", "quotaexceeded")):
        return FailureClass.BUDGET_EXCEEDED
    if any(token in name or token in message for token in ("contract", "validation", "valueerror")):
        return FailureClass.CONTRACT_ERROR
    return FailureClass.DEPENDENCY_ERROR


def aggregate_layers(
    *,
    outcomes: list[CaseOutcome],
    quality_passed: bool | None = None,
    provider_complete: bool | None = None,
    human_review_status: str = "NOT_REQUIRED",
    evidence_status: str = "ACCEPTED",
) -> dict[str, Any]:
    """Build the common execution/quality/provider/evidence envelope."""

    failures = Counter(item.failure_class.value for item in outcomes if item.executed)
    blocked = [
        item
        for item in outcomes
        if item.failure_class
        in {
            FailureClass.SERVICE_UNAVAILABLE,
            FailureClass.DEPENDENCY_ERROR,
            FailureClass.TIMEOUT,
            FailureClass.CANCELLED,
        }
    ]
    execution_status = (
        "COMPLETE"
        if outcomes and all(item.executed for item in outcomes)
        else "BLOCKED"
        if blocked or not outcomes
        else "PARTIAL"
    )
    if quality_passed is None:
        quality_status = "NOT_EVALUABLE" if execution_status != "COMPLETE" else "UNKNOWN"
    else:
        quality_status = "PASSED" if quality_passed else "FAILED"
    provider_status = (
        "NOT_APPLICABLE"
        if provider_complete is None
        else "COMPLETE"
        if provider_complete
        else "INCOMPLETE"
    )
    return {
        "execution": {
            "status": execution_status,
            "caseCount": len(outcomes),
            "executedCount": sum(item.executed for item in outcomes),
            "failureClasses": dict(sorted(failures.items())),
        },
        "quality": {"status": quality_status},
        "provider": {"status": provider_status},
        "humanReview": {"status": human_review_status},
        "evidence": {
            "status": evidence_status,
            "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
        },
    }
