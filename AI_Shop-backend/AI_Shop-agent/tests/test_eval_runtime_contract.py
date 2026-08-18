from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.eval_runtime.contracts import (
    FailureClass,
    RunPhase,
    aggregate_layers,
    classify_exception,
)
from benchmarks.eval_runtime.evidence import EvidenceError, EvidenceStore
from benchmarks.eval_runtime.lifecycle import LifecycleError, RunLifecycle


def test_failure_classification_separates_dependency_and_rate_limit() -> None:
    assert classify_exception(ConnectionRefusedError("connection refused")) == FailureClass.SERVICE_UNAVAILABLE
    assert classify_exception(RuntimeError("HTTP 429 rate limit")) == FailureClass.RATE_LIMITED
    assert classify_exception(RuntimeError("captcha provider missing")) == FailureClass.DEPENDENCY_ERROR
    assert classify_exception(TimeoutError("probe timed out")) == FailureClass.TIMEOUT


def test_aggregate_layers_does_not_turn_blocked_execution_into_quality_zero() -> None:
    from benchmarks.eval_runtime.contracts import CaseOutcome

    envelope = aggregate_layers(
        outcomes=[
            CaseOutcome(
                case_id="runtime-1",
                status="BLOCKED",
                executed=False,
                stage="runtime",
                failure_class=FailureClass.SERVICE_UNAVAILABLE,
            )
        ],
        quality_passed=None,
        provider_complete=False,
    )
    assert envelope["execution"]["status"] == "BLOCKED"
    assert envelope["quality"]["status"] == "NOT_EVALUABLE"
    assert envelope["provider"]["status"] == "INCOMPLETE"


def test_lifecycle_rejects_invalid_transition(tmp_path: Path) -> None:
    lifecycle = RunLifecycle(tmp_path / "lifecycle.json", suite="search-v3", run_id="search-v3-abcdef0-20260818")
    lifecycle.transition(RunPhase.PREFLIGHTED)
    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        lifecycle.transition(RunPhase.PACKAGED)


def test_evidence_store_refuses_overwrite_and_claims_once(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path, suite="search-v3", run_id="search-v3-abcdef0-20260818")
    store.write_json("validation.json", {"ok": True})
    with pytest.raises(EvidenceError, match="overwrite"):
        store.write_json("validation.json", {"ok": False})

    lock = tmp_path / "_fresh-lock.json"
    first = store.claim_fresh(lock, dataset_sha256="a" * 64)
    assert first["runId"] == "search-v3-abcdef0-20260818"
    with pytest.raises(EvidenceError, match="one-shot"):
        store.claim_fresh(lock, dataset_sha256="a" * 64)
    other = EvidenceStore(tmp_path, suite="search-v3", run_id="search-v3-abcdef1-20260818")
    with pytest.raises(EvidenceError, match="already claimed"):
        other.claim_fresh(lock, dataset_sha256="a" * 64)


def test_events_are_append_only_and_manifest_binds_hashes(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path, suite="search-v3", run_id="search-v3-abcdef0-20260818")
    artifact = store.write_json("stage-known.json", {"status": "COMPLETE"})
    store.append_event(stage="known", status="COMPLETE")
    manifest = store.manifest(required=[artifact], status="COMPLETE")
    assert "events.jsonl" in manifest["artifacts"]
    assert json.loads((store.root / "run-manifest.json").read_text())["status"] == "COMPLETE"
