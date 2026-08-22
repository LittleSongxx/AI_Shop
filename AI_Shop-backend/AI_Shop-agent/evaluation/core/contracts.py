from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from evaluation import SCHEMA_VERSION

# v2 remains a first-class read-only compatibility format.  New artifacts use
# v3, but parsers and evidence verification intentionally accept both versions
# so an archived final can always be replayed and audited.
CASE_SCHEMA_VERSION_V2 = "aishop-evaluation-case/v2"
CASE_SCHEMA_VERSION_V3 = "aishop-evaluation-case/v3"
CASE_SCHEMA_VERSION = CASE_SCHEMA_VERSION_V2
DATASET_LOCK_SCHEMA_VERSION_V2 = "aishop-evaluation-dataset-lock/v2"
DATASET_LOCK_SCHEMA_VERSION_V3 = "aishop-evaluation-dataset-lock/v3"
DATASET_LOCK_SCHEMA_VERSION = DATASET_LOCK_SCHEMA_VERSION_V2
RUN_SCHEMA_VERSION_V2 = "aishop-evaluation-run/v2"
RUN_SCHEMA_VERSION_V3 = "aishop-evaluation-run/v3"
RUN_SCHEMA_VERSION = RUN_SCHEMA_VERSION_V2
EVIDENCE_SCHEMA_VERSION_V2 = "aishop-evaluation-evidence/v2"
EVIDENCE_SCHEMA_VERSION_V3 = "aishop-evaluation-evidence/v3"
EVIDENCE_SCHEMA_VERSION = EVIDENCE_SCHEMA_VERSION_V2

SUPPORTED_CASE_SCHEMA_VERSIONS = frozenset({CASE_SCHEMA_VERSION_V2, CASE_SCHEMA_VERSION_V3})
SUPPORTED_DATASET_LOCK_SCHEMA_VERSIONS = frozenset(
    {DATASET_LOCK_SCHEMA_VERSION_V2, DATASET_LOCK_SCHEMA_VERSION_V3}
)
SUPPORTED_RUN_SCHEMA_VERSIONS = frozenset({RUN_SCHEMA_VERSION_V2, RUN_SCHEMA_VERSION_V3})
SUPPORTED_EVIDENCE_SCHEMA_VERSIONS = frozenset(
    {EVIDENCE_SCHEMA_VERSION_V2, EVIDENCE_SCHEMA_VERSION_V3}
)


class EvaluationError(RuntimeError):
    """Base class for a fail-closed evaluation failure."""


class ValidationError(EvaluationError):
    """A contract or dataset is invalid."""


class PreflightError(EvaluationError):
    """A required provider or dependency is unavailable."""


class LifecycleError(EvaluationError):
    """A final-set lifecycle transition is invalid."""


class ExecutionError(EvaluationError):
    """A case could not be executed against the real application."""


class Domain(StrEnum):
    SEARCH = "search"
    RAG = "rag"
    AGENT = "agent"


class Split(StrEnum):
    DEVELOPMENT = "development"
    REGRESSION = "regression"
    FINAL = "final"


class CaseStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class CostStatus(StrEnum):
    PRICED = "PRICED"
    UNPRICED = "UNPRICED"
    MISSING_USAGE = "MISSING_USAGE"


class SemanticLabel(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTORY = "CONTRADICTORY"
    UNDECIDABLE = "UNDECIDABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    split: Split
    domain: Domain
    input: dict[str, Any]
    expected: dict[str, Any]
    required_providers: tuple[str, ...]
    tags: tuple[str, ...] = ()
    slice_tags: tuple[str, ...] = ()
    state_fixture: dict[str, Any] | None = None
    state_assertions: tuple[dict[str, Any], ...] = ()
    repeat_policy: dict[str, Any] | None = None
    fault_recovery_contract: dict[str, Any] | None = None
    schema_version: str = CASE_SCHEMA_VERSION

    def public(self) -> dict[str, Any]:
        value = {
            "schemaVersion": self.schema_version,
            "id": self.case_id,
            "split": self.split.value,
            "domain": self.domain.value,
            "input": self.input,
            "expected": self.expected,
            "requiredProviders": list(self.required_providers),
            "tags": list(self.tags),
        }
        if self.schema_version == CASE_SCHEMA_VERSION_V3 or any(
            (
                self.slice_tags,
                self.state_fixture is not None,
                self.state_assertions,
                self.repeat_policy is not None,
                self.fault_recovery_contract is not None,
            )
        ):
            value.update(
                {
                    "sliceTags": list(self.slice_tags),
                    "stateFixture": self.state_fixture,
                    "stateAssertions": list(self.state_assertions),
                    "repeatPolicy": self.repeat_policy,
                    "faultRecoveryContract": self.fault_recovery_contract,
                }
            )
        return value


@dataclass
class CaseResult:
    case_id: str
    domain: Domain
    status: CaseStatus
    metrics: dict[str, float | int]
    latency_ms: float
    output: dict[str, Any]
    providers: dict[str, Any]
    assertions: list[dict[str, Any]]
    error: dict[str, str] | None = None
    started_at: str | None = None
    completed_at: str | None = None
    # v3 evidence fields.  Defaults keep old v2 adapters and archived records
    # loadable without fabricating measurements.
    usage: dict[str, Any] = field(default_factory=dict)
    slice: str | None = None
    trial_id: str | None = None
    state_diff: dict[str, Any] | None = None
    fault_scenario: dict[str, Any] | None = None
    semantic_judgment: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["domain"] = self.domain.value
        value["status"] = self.status.value
        # v3 consumers use the JSON contract's camelCase names. Keep the
        # original snake_case keys as a compatibility projection for v2
        # readers and existing evidence tooling.
        for snake, camel in (
            ("trial_id", "trialId"),
            ("state_diff", "stateDiff"),
            ("fault_scenario", "faultScenario"),
            ("semantic_judgment", "semanticJudgment"),
        ):
            if value.get(snake) is not None:
                value[camel] = value[snake]
        return value


@dataclass(frozen=True)
class MetricEstimate:
    name: str
    value: float
    sample_count: int
    kind: str
    interval_method: str | None = None
    confidence_level: float | None = None
    lower: float | None = None
    upper: float | None = None
    notes: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "sampleCount": self.sample_count,
            "kind": self.kind,
            "interval": (
                {
                    "method": self.interval_method,
                    "confidenceLevel": self.confidence_level,
                    "lower": self.lower,
                    "upper": self.upper,
                }
                if self.interval_method
                else None
            ),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class GateDecision:
    domain: str
    metric: str
    passed: bool
    operator: str
    threshold: float
    observed: float | None
    evaluated_field: str
    reason: str

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunRecord:
    run_id: str
    split: Split
    dataset_sha256: str
    source_fingerprint: dict[str, Any]
    environment: dict[str, Any]
    cases: list[CaseResult] = field(default_factory=list)
    trials: list[CaseResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RUN_SCHEMA_VERSION
    framework_schema_version: str = SCHEMA_VERSION

    def public(self, *, include_cases: bool = True) -> dict[str, Any]:
        value = {
            "schemaVersion": self.schema_version,
            "frameworkSchemaVersion": self.framework_schema_version,
            "runId": self.run_id,
            "split": self.split.value,
            "datasetSha256": self.dataset_sha256,
            "sourceFingerprint": self.source_fingerprint,
            "environment": self.environment,
            "summary": self.summary,
            "gates": self.gates,
        }
        if include_cases:
            value["cases"] = [case.public() for case in self.cases]
            if self.trials:
                value["trials"] = [trial.public() for trial in self.trials]
        return value
