from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.core.contracts import Split
from evaluation.core.datasets import parse_case
from evaluation.core.io import EVIDENCE_ROOT, load_jsonl
from evaluation.search_paired_replay import (
    DEFAULT_V9_HOLDOUT,
    SearchPairedReplayError,
    build_paired_replay_report,
    load_replay_cases,
    verify_paired_replay_evidence,
    write_paired_replay_evidence,
)


def _case():
    row = next(
        row
        for row in load_jsonl(DEFAULT_V9_HOLDOUT)
        if row["id"] == "search-fin-v9-23-snack-100"
    )
    return parse_case(row, expected_split=Split.FINAL)


def _row(case, ranking, *, status="PASSED", violations=0):
    return {
        "case_id": case.case_id,
        "domain": "search",
        "status": status,
        "metrics": {"constraintViolationCount": violations},
        "latency_ms": 123.0,
        "output": {
            "query": case.input["query"],
            "constraints": case.input.get("constraints") or {},
            "ranking": list(ranking),
            "trace": [],
        },
        "providers": {"embedding": {"complete": True}},
        "error": None,
    }


def _report(candidate_ranking):
    case = _case()
    baseline = _row(case, ["065293686460191"])
    candidate = _row(case, candidate_ranking)
    return build_paired_replay_report(
        baseline_rows={case.case_id: baseline},
        candidate_rows={case.case_id: candidate},
        cases=[case],
        provenance={
            "runId": "search-paired-test",
            "baselineRunId": "final-20260822-ai-quality-v9",
            "baselineEvidenceSha256SumsSha256": "a" * 64,
            "holdoutFileSha256": "b" * 64,
            "selectedQrelsSha256": "c" * 64,
        },
    )


def test_paired_replay_reports_recovered_relevant_ids_and_metric_delta():
    report = _report(
        ["065293686460191", "303019597302892", "438316828084252"]
    )
    comparison = report["comparisons"][0]
    assert comparison["delta"]["recallAt10"] > 0
    assert comparison["recoveredRelevantIds"] == [
        "303019597302892",
        "438316828084252",
    ]
    assert comparison["remainingMissedRelevantIds"] == []
    assert report["metrics"]["delta"]["recallAt10Micro"] > 0
    assert report["baselineFinalModified"] is False
    assert report["qrelsModified"] is False


def test_paired_replay_keeps_new_badcases_and_constraints_visible():
    case = _case()
    baseline = _row(case, ["065293686460191"])
    candidate = _row(case, ["not-relevant"], violations=1)
    report = build_paired_replay_report(
        baseline_rows={case.case_id: baseline},
        candidate_rows={case.case_id: candidate},
        cases=[case],
        provenance={"runId": "search-paired-test"},
    )
    badcase = report["badcases"][0]
    assert "PAIRED_RANKING_REGRESSION" in badcase["reasons"]
    assert "DROPPED_RELEVANT_RESULT" in badcase["reasons"]
    assert "NEW_IRRELEVANT_RESULT" in badcase["reasons"]
    assert "HARD_CONSTRAINT_VIOLATION" in badcase["reasons"]


def test_paired_replay_fails_closed_when_query_or_case_set_differs():
    case = _case()
    baseline = _row(case, ["065293686460191"])
    baseline["output"]["query"] = "changed"
    with pytest.raises(SearchPairedReplayError, match="baseline query"):
        build_paired_replay_report(
            baseline_rows={case.case_id: baseline},
            candidate_rows={case.case_id: _row(case, ["065293686460191"])},
            cases=[case],
            provenance={"runId": "search-paired-test"},
        )


def test_v9_replay_loader_selects_only_declared_search_cases():
    all_cases, selected = load_replay_cases(
        DEFAULT_V9_HOLDOUT,
        case_ids=["search-fin-v9-23-snack-100", "search-fin-v9-47-compare-xm"],
    )
    assert len(all_cases) == 125
    assert [case.case_id for case in selected] == [
        "search-fin-v9-23-snack-100",
        "search-fin-v9-47-compare-xm",
    ]


def test_paired_replay_evidence_is_write_once_hashed_and_read_only(tmp_path: Path):
    target = tmp_path / "paired"
    verification = write_paired_replay_evidence(
        _report(["065293686460191"]), target
    )
    assert verification["verified"] is True
    assert verify_paired_replay_evidence(target)["runId"] == "search-paired-test"
    assert all(not path.stat().st_mode & 0o222 for path in target.iterdir() if path.is_file())
    with pytest.raises(FileExistsError):
        write_paired_replay_evidence(_report(["065293686460191"]), target)


def test_paired_replay_refuses_current_or_archive_write_boundary():
    with pytest.raises(SearchPairedReplayError, match="cannot write"):
        write_paired_replay_evidence(
            _report(["065293686460191"]), EVIDENCE_ROOT / "forbidden"
        )
