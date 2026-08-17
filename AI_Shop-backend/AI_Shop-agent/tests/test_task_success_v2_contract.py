from __future__ import annotations

import json

import pytest

from benchmarks import run_task_success_eval as v1
from benchmarks import run_task_success_v2_eval as v2


def test_frozen_v2_contract_keeps_v1_and_all_sequences() -> None:
    cases = v2.load_cases(v2.DEFAULT_DATASET)
    lock = v2.validate_contract(cases, v2.DEFAULT_DATASET, v2.DEFAULT_LOCK)

    assert len(cases) == 44
    assert cases[:37] == v1.load_cases(v1.DEFAULT_DATASET)
    assert [case["id"] for case in cases[37:]] == lock["requiredSequenceIds"]
    assert lock["caseCounts"] == {
        "knownSingleTurn": 37,
        "sequence": 7,
        "total": 44,
    }
    assert lock["datasetSha256"] == v2.dataset_sha256(v2.DEFAULT_DATASET)


def test_v2_sequences_cover_the_complete_action_surface() -> None:
    cases = v2.load_cases(v2.DEFAULT_DATASET)
    actions = {
        step["action"]
        for case in cases
        if case.get("kind") == "sequence"
        for step in case["steps"]
    }

    assert actions == set(v2.ALLOWED_ACTIONS)
    assert all(
        case["fixtureIsolation"] == "ISOLATED_EVALUATION_ONLY"
        for case in cases[37:]
    )


def test_agent_v2_suite_descriptor_matches_frozen_lock() -> None:
    suite = json.loads(
        (v2.PROJECT_ROOT / "benchmarks" / "suites" / "agent-v2.json").read_text(
            encoding="utf-8"
        )
    )
    lock = json.loads(v2.DEFAULT_LOCK.read_text(encoding="utf-8"))

    assert suite["datasetSha256"] == lock["datasetSha256"]
    assert suite["knownDatasetSha256"] == lock["knownDatasetSha256"]
    assert suite["sequenceActions"] == lock["requiredActions"]
    assert suite["resultRoot"] == lock["resultRoot"]


def test_v2_contract_rejects_dataset_drift(tmp_path) -> None:
    dataset = tmp_path / "task_success_v2.jsonl"
    dataset.write_text(v2.DEFAULT_DATASET.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(v2.EvaluationContractError, match="SHA-256"):
        v2.validate_contract(v2.load_cases(dataset), dataset, v2.DEFAULT_LOCK)


def test_v2_contract_rejects_sequence_action_drift(tmp_path) -> None:
    cases = v2.load_cases(v2.DEFAULT_DATASET)
    cases[-1]["steps"][0]["action"] = "unknownAction"
    dataset = tmp_path / "task_success_v2.jsonl"
    dataset.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    lock = json.loads(v2.DEFAULT_LOCK.read_text(encoding="utf-8"))
    lock["datasetSha256"] = v2.dataset_sha256(dataset)
    lock_path = tmp_path / "task_success_v2.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    with pytest.raises(v2.EvaluationContractError, match="unsupported action"):
        v2.validate_contract(cases, dataset, lock_path)


def test_v2_gate_includes_tool_arguments_and_terminal_state() -> None:
    summary = {
        "taskSuccessRate": 1.0,
        "executionCompletenessRate": 1.0,
        "providerCompletenessRate": 1.0,
        "toolSelectionAccuracy": 1.0,
        "toolArgumentAccuracy": 0.94,
        "terminalStateAccuracy": 0.94,
        "severeSafetyViolationCount": 0,
    }
    thresholds = json.loads(v2.DEFAULT_LOCK.read_text(encoding="utf-8"))["thresholds"]

    assert v2.threshold_failures(summary, thresholds) == [
        "toolArgumentAccuracy=0.9400 < 0.9500",
        "terminalStateAccuracy=0.9400 < 0.9500",
    ]
