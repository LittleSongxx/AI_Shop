import asyncio
import json
import shutil

import pytest

from evaluation import runner
from evaluation.core import evidence, lifecycle
from evaluation.core.contracts import (
    CASE_SCHEMA_VERSION,
    CaseResult,
    CaseStatus,
    Domain,
    EvaluationCase,
    LifecycleError,
    PreflightError,
    RunRecord,
    Split,
)
from evaluation.core.io import atomic_write_jsonl, load_jsonl


def _fingerprint():
    return {
        "capturedAt": "2026-08-20T00:00:00.000Z",
        "git": {"commit": "a" * 40, "worktreeDirty": True},
        "source": {"sha256": "b" * 64, "fileCount": 1},
        "knowledge": {"sha256": "c" * 64, "fileCount": 1},
        "providerConfiguration": {},
        "providerConfigurationSha256": "d" * 64,
    }


def _final_case():
    return {
        "schemaVersion": CASE_SCHEMA_VERSION,
        "id": "agent-fin-lifecycle-contract",
        "split": "final",
        "domain": "agent",
        "input": {"turns": [{"message": "final lifecycle contract"}]},
        "expected": {
            "terminalStatuses": ["SUCCEEDED"],
            "requiredTools": [],
            "requiredEvents": [],
        },
        "requiredProviders": ["agent-runtime"],
        "tags": ["contract"],
    }


def test_final_hash_is_claimed_once_and_execution_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(lifecycle, "CONSUMED_FINAL_PATH", tmp_path / "consumed.json")
    monkeypatch.setattr(lifecycle, "source_fingerprint", _fingerprint)
    monkeypatch.setattr(lifecycle, "validate_repository_datasets", lambda: {})
    monkeypatch.setattr(lifecycle, "validate_final_against_known", lambda cases: None)
    monkeypatch.setattr(lifecycle, "audit_final_input_exposure", lambda *args, **kwargs: [])
    dataset = tmp_path / "final.jsonl"
    atomic_write_jsonl(dataset, [_final_case()])

    frozen = lifecycle.freeze_final("release-contract-001")
    assert frozen["status"] == "FROZEN"
    claimed = lifecycle.claim_final("release-contract-001", dataset)
    assert claimed["status"] == "CLAIMED"
    with pytest.raises(LifecycleError):
        lifecycle.claim_final("release-contract-001", dataset)

    executing = lifecycle.begin_final_execution("release-contract-001", "final-run-contract-001")
    assert executing["status"] == "EXECUTING"
    with pytest.raises(LifecycleError):
        lifecycle.begin_final_execution("release-contract-001", "final-run-contract-002")
    completed = lifecycle.complete_final_execution(
        "release-contract-001",
        outcome="FAILED",
        evidence_sha256=None,
    )
    assert completed["status"] == "EXECUTED"
    attached = lifecycle.attach_final_evidence("release-contract-001", "e" * 64)
    assert attached["run"]["evidenceSha256"] == "e" * 64


def test_final_publication_error_is_terminal_and_not_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(lifecycle, "CONSUMED_FINAL_PATH", tmp_path / "consumed.json")
    monkeypatch.setattr(lifecycle, "source_fingerprint", _fingerprint)
    monkeypatch.setattr(lifecycle, "validate_repository_datasets", lambda: {})
    monkeypatch.setattr(lifecycle, "validate_final_against_known", lambda cases: None)
    monkeypatch.setattr(lifecycle, "audit_final_input_exposure", lambda *args, **kwargs: [])
    dataset = tmp_path / "final.jsonl"
    atomic_write_jsonl(dataset, [_final_case()])

    lifecycle.freeze_final("release-contract-error-001")
    lifecycle.claim_final("release-contract-error-001", dataset)
    lifecycle.begin_final_execution("release-contract-error-001", "final-run-error-001")
    errored = lifecycle.mark_final_error("release-contract-error-001")

    assert errored["status"] == "EXECUTED"
    assert errored["run"]["outcome"] == "ERROR"
    with pytest.raises(LifecycleError):
        lifecycle.begin_final_execution("release-contract-error-001", "retry-error-001")


def test_evidence_is_redacted_hashed_and_verifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "RUNS_ROOT", tmp_path / "runs")
    result = CaseResult(
        case_id="agent-dev-redaction-contract",
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={"taskSuccess": 1},
        latency_ms=12.5,
        output={
            "userId": "evaluation-user-42",
            "authorization": "Bearer super-secret-token",
            "answer": "contact 13800138000 or person@example.com",
        },
        providers={},
        assertions=[],
    )
    run = RunRecord(
        run_id="evidence-contract-001",
        split=Split.DEVELOPMENT,
        dataset_sha256="a" * 64,
        source_fingerprint=_fingerprint(),
        environment={"executionMode": "LOCAL_FULL_STACK"},
        cases=[result],
        summary={
            "runId": "evidence-contract-001",
            "split": "development",
            "datasetSha256": "a" * 64,
            "domains": {"agent": {"caseCount": 1}},
        },
        gates={"passed": True, "domainOutcomes": {}},
    )

    root, digest = evidence.write_run_evidence(run)
    verified = evidence.verify_evidence(root)
    row = load_jsonl(root / "cases.jsonl")[0]

    assert verified["verified"] is True
    assert verified["sha256SumsSha256"] == digest
    assert row["output"]["userId"].startswith("[REDACTED_ID:")
    assert row["output"]["authorization"] == "[REDACTED_SECRET]"
    assert "[REDACTED_PHONE]" in row["output"]["answer"]
    assert "[REDACTED_EMAIL]" in row["output"]["answer"]
    manifest = json.loads((root / "evidence-manifest.json").read_text("utf-8"))
    assert manifest["schemaVersion"] == "aishop-evaluation-evidence/v2"


def _final_evidence_run(run_id: str) -> RunRecord:
    result = CaseResult(
        case_id=f"agent-{run_id}",
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={"taskSuccess": 1},
        latency_ms=1,
        output={},
        providers={},
        assertions=[],
    )
    return RunRecord(
        run_id=run_id,
        split=Split.FINAL,
        dataset_sha256="a" * 64,
        source_fingerprint=_fingerprint(),
        environment={"executionMode": "LOCAL_FULL_STACK"},
        cases=[result],
        summary={
            "runId": run_id,
            "split": "final",
            "datasetSha256": "a" * 64,
            "domains": {"agent": {"caseCount": 1}},
        },
        gates={"passed": True, "domainOutcomes": {"agent": True}},
    )


def test_publish_archives_previous_current_by_run_id(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    current_root = tmp_path / "evidence/current"
    monkeypatch.setattr(evidence, "RUNS_ROOT", runs_root)
    monkeypatch.setattr(evidence, "EVIDENCE_ROOT", current_root)
    old_root, old_digest = evidence.write_run_evidence(
        _final_evidence_run("final-old"),
        lifecycle={"releaseId": "release-old", "status": "EXECUTED"},
    )
    shutil.copytree(old_root, current_root)
    new_root, new_digest = evidence.write_run_evidence(
        _final_evidence_run("final-new"),
        lifecycle={"releaseId": "release-new", "status": "EXECUTED"},
    )

    published = evidence.publish_current(new_root)

    archive = tmp_path / "evidence/archive/final-old"
    assert published == new_digest
    assert evidence.verify_evidence(archive)["sha256SumsSha256"] == old_digest
    assert evidence.verify_evidence(current_root)["runId"] == "final-new"
    assert not (tmp_path / "evidence/archive/release-old").exists()
    assert all(
        not path.stat().st_mode & 0o222 for path in archive.rglob("*") if path.is_file()
    )


def _parsed_final_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="agent-fin-lifecycle-contract",
        split=Split.FINAL,
        domain=Domain.AGENT,
        input={"turns": [{"message": "contract"}]},
        expected={"terminalStatuses": ["SUCCEEDED"], "requiredTools": []},
        required_providers=("agent-runtime",),
    )


def test_evaluation_user_id_is_unique_and_fits_the_narrowest_schema() -> None:
    first = runner.evaluation_user_id("nonce-001", "agent-dev-a-very-long-case-identifier")
    second = runner.evaluation_user_id("nonce-001", "agent-dev-another-case")

    assert len(first) == 15
    assert first.startswith("ev")
    assert first != second


def _patch_runner_prerequisites(monkeypatch) -> None:
    async def no_op() -> None:
        return None

    monkeypatch.setattr(runner, "validate_repository_datasets", lambda: {})
    monkeypatch.setattr(runner, "_load_cases", lambda split, release_id: [_parsed_final_case()])
    monkeypatch.setattr(runner, "source_fingerprint", _fingerprint)
    monkeypatch.setattr(
        runner,
        "environment_facts",
        lambda: {"executionMode": "LOCAL_FULL_STACK"},
    )
    monkeypatch.setattr(runner, "init_pool", no_op)
    monkeypatch.setattr(runner, "close_pool", no_op)


def test_final_preflight_failure_does_not_consume_claim(monkeypatch):
    _patch_runner_prerequisites(monkeypatch)
    begin_calls: list[tuple[str, str]] = []

    async def fail_preflight(cases):
        raise PreflightError("provider unavailable")

    monkeypatch.setattr(runner, "run_preflight", fail_preflight)
    monkeypatch.setattr(
        runner,
        "begin_final_execution",
        lambda release_id, run_id: begin_calls.append((release_id, run_id)),
    )

    with pytest.raises(PreflightError):
        asyncio.run(
            runner.run_evaluation(
                split=Split.FINAL,
                run_id="final-contract-preflight-001",
                release_id="release-contract-preflight-001",
                publish=True,
                confirm_final=True,
            )
        )

    assert begin_calls == []


def test_final_framework_error_is_recorded_as_consumed_error(monkeypatch):
    _patch_runner_prerequisites(monkeypatch)
    completions: list[tuple[str, str, str | None]] = []

    async def pass_preflight(cases):
        return {"passed": True}

    async def crash(case, run_nonce):
        raise RuntimeError("framework crash")

    monkeypatch.setattr(runner, "run_preflight", pass_preflight)
    monkeypatch.setattr(runner, "begin_final_execution", lambda release_id, run_id: {})
    monkeypatch.setattr(runner, "_execute_case", crash)
    monkeypatch.setattr(runner, "lifecycle_status", lambda release_id: {"status": "EXECUTING"})
    monkeypatch.setattr(
        runner,
        "mark_final_error",
        lambda release_id: completions.append((release_id, "ERROR", None)),
    )

    with pytest.raises(RuntimeError, match="framework crash"):
        asyncio.run(
            runner.run_evaluation(
                split=Split.FINAL,
                run_id="final-contract-error-001",
                release_id="release-contract-error-001",
                publish=True,
                confirm_final=True,
            )
        )

    assert completions == [("release-contract-error-001", "ERROR", None)]
