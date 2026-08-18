"""Shared lifecycle and evidence primitives for the single evaluation entrypoint."""

from .contracts import (
    CaseOutcome,
    EvalRunManifest,
    EvidenceLevel,
    FailureClass,
    GateResult,
    RunPhase,
    RunState,
    StageResult,
    aggregate_layers,
    classify_exception,
)
from .evidence import EvidenceStore
from .lifecycle import LifecycleError, RunLifecycle
from .registry import SuiteDefinition, list_suites, load_suite

__all__ = [
    "CaseOutcome",
    "EvalRunManifest",
    "EvidenceLevel",
    "EvidenceStore",
    "FailureClass",
    "GateResult",
    "LifecycleError",
    "RunLifecycle",
    "RunPhase",
    "RunState",
    "StageResult",
    "SuiteDefinition",
    "aggregate_layers",
    "classify_exception",
    "list_suites",
    "load_suite",
]
