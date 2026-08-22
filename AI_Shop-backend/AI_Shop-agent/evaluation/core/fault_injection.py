"""Request-scoped failure injection and recovery-contract evaluation.

This module uses ``ContextVar`` rather than process environment variables, so a
fault can only affect the evaluation request that explicitly entered the scope.
Production code can call ``fault_point`` at provider boundaries without any
behavior change outside that scope.
"""

from __future__ import annotations

import contextvars
import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.core.contracts import ValidationError

SUPPORTED_TARGETS = frozenset(
    {
        "bm25",
        "vector",
        "embedding",
        "rerank",
        "java-product",
        "java-inventory",
        "java-offer-snapshot",
        "llm",
        "redis-checkpoint",
        "worker-deadline",
        "mcp-tool",
        "confirmation",
        "request",
    }
)
SUPPORTED_MODES = frozenset(
    {
        "timeout",
        "5xx",
        "empty",
        "exception",
        "breaker-rejected",
        "partial",
        "malformed",
        "duplicate",
    }
)

FAULT_EVIDENCE_PRODUCTION_BOUNDARY = "PRODUCTION_BOUNDARY"
FAULT_EVIDENCE_HARNESS_BOUNDARY = "HARNESS_BOUNDARY"
FAULT_EVIDENCE_UNSUPPORTED = "UNSUPPORTED"

# These targets are injected inside the same process at the actual provider or
# authoritative-service call boundary. Agent API/Worker/MCP run in separate
# processes, so a ContextVar in the evaluator cannot prove those boundaries.
_PRODUCTION_BOUNDARY_TARGETS = frozenset(
    {
        "bm25",
        "vector",
        "embedding",
        "rerank",
        "java-product",
        "java-inventory",
        "java-offer-snapshot",
        "llm",
        "redis-checkpoint",
        "worker-deadline",
        "mcp-tool",
    }
)
_HARNESS_BOUNDARY_TARGETS = frozenset({"request", "confirmation"})
SUPPORTED_GATE_MODES = frozenset({"HARD", "SHADOW"})


def fault_evidence_level(target: str) -> str:
    if target in _PRODUCTION_BOUNDARY_TARGETS:
        return FAULT_EVIDENCE_PRODUCTION_BOUNDARY
    if target in _HARNESS_BOUNDARY_TARGETS:
        return FAULT_EVIDENCE_HARNESS_BOUNDARY
    return FAULT_EVIDENCE_UNSUPPORTED


class InjectedFailure(RuntimeError):
    def __init__(self, *, target: str, mode: str, scenario_id: str):
        self.target = target
        self.mode = mode
        self.scenario_id = scenario_id
        super().__init__(f"evaluation fault injected: {target}/{mode} ({scenario_id})")


@dataclass(frozen=True)
class FaultScenario:
    scenario_id: str
    target: str
    mode: str
    expected: dict[str, Any]
    case_id: str | None = None
    once: bool = True
    gate_mode: str = "HARD"
    evidence_note: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.scenario_id,
            "target": self.target,
            "mode": self.mode,
            "expected": self.expected,
            "caseId": self.case_id,
            "once": self.once,
            "gateMode": self.gate_mode,
            "declaredEvidenceLevel": fault_evidence_level(self.target),
            "evidenceNote": self.evidence_note,
        }


@dataclass
class _ScopeState:
    scenarios: tuple[FaultScenario, ...]
    consumed: set[str]
    events: list[dict[str, Any]]


_ACTIVE: contextvars.ContextVar[_ScopeState | None] = contextvars.ContextVar(
    "evaluation_failure_injection_scope", default=None
)


def parse_fault_scenario(value: Any, *, index: int = 1) -> FaultScenario:
    if not isinstance(value, dict):
        raise ValidationError(f"fault scenario {index} must be an object")
    target = str(value.get("target") or "")
    mode = str(value.get("mode") or "")
    if target not in SUPPORTED_TARGETS:
        raise ValidationError(f"fault scenario {index} has unsupported target {target!r}")
    if mode not in SUPPORTED_MODES:
        raise ValidationError(f"fault scenario {index} has unsupported mode {mode!r}")
    expected = value.get("expected")
    if not isinstance(expected, dict):
        raise ValidationError(f"fault scenario {index}.expected must be an object")
    required = {"unsafeAnswer", "hardConstraintBypass"}
    if not required.issubset(expected):
        raise ValidationError(
            f"fault scenario {index}.expected is missing {sorted(required - set(expected))}"
        )
    scenario_id = str(value.get("id") or f"{target}-{mode}-{index}")
    evidence_level = fault_evidence_level(target)
    default_gate_mode = (
        "HARD"
        if evidence_level == FAULT_EVIDENCE_PRODUCTION_BOUNDARY
        else "SHADOW"
    )
    gate_mode = str(value.get("gateMode") or default_gate_mode).upper()
    if gate_mode not in SUPPORTED_GATE_MODES:
        raise ValidationError(
            f"fault scenario {index}.gateMode must be one of {sorted(SUPPORTED_GATE_MODES)}"
        )
    if gate_mode == "HARD" and evidence_level != FAULT_EVIDENCE_PRODUCTION_BOUNDARY:
        raise ValidationError(
            f"fault scenario {index} cannot use HARD gate at {evidence_level}"
        )
    return FaultScenario(
        scenario_id=scenario_id,
        target=target,
        mode=mode,
        expected=dict(expected),
        case_id=str(value["caseId"]) if value.get("caseId") else None,
        once=bool(value.get("once", True)),
        gate_mode=gate_mode,
        evidence_note=(
            str(value.get("evidenceNote") or "").strip() or None
        ),
    )


def load_fault_scenarios(path: Path) -> list[FaultScenario]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid fault scenario file {path}: {exc}") from exc
    rows = value.get("scenarios") if isinstance(value, dict) else value
    if not isinstance(rows, list) or not rows:
        raise ValidationError("fault scenario file must contain a non-empty scenarios array")
    scenarios = [parse_fault_scenario(item, index=index) for index, item in enumerate(rows, 1)]
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValidationError("fault scenario IDs must be unique")
    return scenarios


class FailureInjectionScope(AbstractContextManager["FailureInjectionScope"]):
    def __init__(self, *scenarios: FaultScenario):
        if not scenarios:
            raise ValueError("FailureInjectionScope requires at least one scenario")
        self._state = _ScopeState(tuple(scenarios), set(), [])
        self._token: contextvars.Token[_ScopeState | None] | None = None

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._state.events)

    def __enter__(self) -> "FailureInjectionScope":
        if _ACTIVE.get() is not None:
            raise RuntimeError("nested FailureInjectionScope is not allowed")
        self._token = _ACTIVE.set(self._state)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._token is not None:
            _ACTIVE.reset(self._token)
            self._token = None


def active_fault(target: str) -> FaultScenario | None:
    state = _ACTIVE.get()
    if state is None:
        return None
    for scenario in state.scenarios:
        if scenario.target != target:
            continue
        if scenario.once and scenario.scenario_id in state.consumed:
            continue
        return scenario
    return None


def fault_point(target: str) -> str | None:
    """Apply the active fault and return non-exceptional modes to the caller."""

    scenario = active_fault(target)
    if scenario is None:
        return None
    state = _ACTIVE.get()
    assert state is not None
    state.consumed.add(scenario.scenario_id)
    state.events.append(
        {
            "eventType": "FAULT_INJECTED",
            "scenarioId": scenario.scenario_id,
            "target": target,
            "mode": scenario.mode,
            "faultEvidenceLevel": fault_evidence_level(target),
        }
    )
    if scenario.mode in {"timeout", "5xx", "exception", "breaker-rejected", "malformed"}:
        raise InjectedFailure(
            target=scenario.target,
            mode=scenario.mode,
            scenario_id=scenario.scenario_id,
        )
    return scenario.mode


def assess_recovery(scenario: FaultScenario, observed: dict[str, Any]) -> dict[str, Any]:
    """Compare observed recovery evidence against the predeclared contract."""

    expected = scenario.expected
    checks: list[dict[str, Any]] = []
    mappings = {
        "fallbackAllowed": "fallbackUsed",
        "unsafeAnswer": "unsafeAnswer",
        "hardConstraintBypass": "hardConstraintBypass",
        "terminalState": "terminalState",
    }
    for expected_key, observed_key in mappings.items():
        if expected_key not in expected:
            continue
        expected_value = expected[expected_key]
        actual = observed.get(observed_key)
        if expected_key == "fallbackAllowed" and expected_value is True:
            # Allowed does not mean required, but the evaluator must still
            # have observed a response and classified whether fallback ran.
            passed = isinstance(actual, bool)
        else:
            passed = actual is not None and actual == expected_value
        checks.append(
            {
                "name": expected_key,
                "expected": expected_value,
                "observed": actual,
                "passed": passed,
            }
        )
    trace = observed.get("failureTrace")
    trace_rows = trace if isinstance(trace, list) else []
    matching_trace = [
        row
        for row in trace_rows
        if isinstance(row, dict)
        and row.get("scenarioId") == scenario.scenario_id
        and row.get("target") == scenario.target
        and row.get("mode") == scenario.mode
        and row.get("eventType") == "FAULT_INJECTED"
    ]
    trace_complete = bool(matching_trace)
    evidence_level = observed.get("faultEvidenceLevel")
    expected_evidence_level = fault_evidence_level(scenario.target)
    evidence_level_matches = evidence_level == expected_evidence_level
    request_outcome_observed = observed.get("requestOutcomeObserved") is True
    case_not_error = observed.get("caseStatus") in {"PASSED", "FAILED"}
    next_request_recovered = observed.get("nextRequestRecovered") is True
    checks.extend(
        [
            {
                "name": "failureTrace",
                "expected": "NON_EMPTY",
                "observed": matching_trace,
                "passed": trace_complete,
            },
            {
                "name": "faultEvidenceLevel",
                "expected": expected_evidence_level,
                "observed": evidence_level,
                "passed": evidence_level_matches,
            },
            {
                "name": "requestOutcomeObserved",
                "expected": True,
                "observed": observed.get("requestOutcomeObserved"),
                "passed": request_outcome_observed,
            },
            {
                "name": "caseStatusNotError",
                "expected": ["PASSED", "FAILED"],
                "observed": observed.get("caseStatus"),
                "passed": case_not_error,
            },
            {
                "name": "nextRequestRecovered",
                "expected": True,
                "observed": observed.get("nextRequestRecovered"),
                "passed": next_request_recovered,
            },
        ]
    )
    return {
        "scenario": scenario.public(),
        "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "hardGateEligible": (
            scenario.gate_mode == "HARD"
            and evidence_level == FAULT_EVIDENCE_PRODUCTION_BOUNDARY
        ),
        "faultEvidenceLevel": evidence_level,
    }
