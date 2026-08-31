from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from evaluation.core.io import sha256_file
from evaluation.public_transfer import (
    GOVERNANCE,
    MANIFEST_SCHEMA_VERSION,
    SCORER_VERSION,
    PublicTransferError,
    run_import,
    run_self_check,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _manifest(input_path: Path, rows: list[dict]) -> dict:
    agent_rows = [row for row in rows if row["kind"] == "agent_trial"]
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "datasetId": "public-fixture",
        "officialUrl": "https://example.org/public-fixture",
        "license": "Apache-2.0",
        "upstreamRevisionOrCommit": "v1",
        "perFileInventoryOrCanonicalInventorySha256": "1" * 64,
        "selectionPolicy": "all normalized fixture rows",
        "scorerVersion": SCORER_VERSION,
        "modelAndPromptFingerprintOrNOT_APPLICABLE": "NOT_APPLICABLE",
        "caseCountAndEligibleDenominators": {
            "caseCount": len({(row["kind"], row["caseKey"]) for row in rows}),
            "rankingCaseEligible": sum(row["kind"] == "ranking_case" for row in rows),
            "gradedRankingCaseEligible": sum(
                row["kind"] == "ranking_case" and any(grade > 0 for grade in row["qrels"].values())
                for row in rows
            ),
            "binaryRankingCaseEligible": sum(
                row["kind"] == "ranking_case"
                and any(
                    grade >= row.get("relevanceThreshold", 1) for grade in row["qrels"].values()
                )
                for row in rows
            ),
            "claimOrSpanCaseEligible": sum(row["kind"] == "claim_or_span_case" for row in rows),
            "agentTrialEligible": len(agent_rows),
            "agentCaseEligible": len({row["caseKey"] for row in agent_rows}),
        },
        "normalizedInputSha256": sha256_file(input_path),
        "exhaustiveClaimGold": True,
        "exhaustiveCitationGold": False,
        "officialAgentExecution": True,
        **GOVERNANCE,
    }


def _safe_success(**changes: bool) -> dict[str, bool]:
    values = {
        "state_oracle_eligible": True,
        "goal_state_match": True,
        "terminal_state_correct": True,
        "policy_and_tool_trace_pass": True,
        "authoritative_object_field_value_match": True,
        "confirmation_timeline_pass": True,
        "no_forbidden_or_duplicate_side_effect": True,
    }
    values.update(changes)
    return values


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return bool(
            {"query", "answer", "snippet", "comment", "reason", "caseId"} & value.keys()
        ) or any(_contains_forbidden_key(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def test_self_check_is_deterministic_and_keeps_external_agent_not_run(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    run_self_check(output=first)
    run_self_check(output=second)

    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()
    report = json.loads((first / "report.json").read_text(encoding="utf-8"))
    assert {field: report[field] for field in GOVERNANCE} == GOVERNANCE
    assert report["domains"]["agent"]["status"] == "NOT_RUN"
    assert report["domains"]["claimOrSpan"]["metricAvailability"] == {
        "claimPrecision": "UNAVAILABLE",
        "claimRecall": "UNAVAILABLE",
        "claimF1": "UNAVAILABLE",
        "citationPrecision": "UNAVAILABLE",
        "citationRecall": "UNAVAILABLE",
        "citationF1": "UNAVAILABLE",
    }
    assert report["aggregationPolicy"] == "NO_WEIGHTED_TOTAL"
    assert "overallScore" not in report
    assert not _contains_forbidden_key(report)
    expected_sums = "".join(
        f"{sha256_file(first / name)}  {name}\n" for name in ("manifest.json", "report.json")
    )
    assert (first / "SHA256SUMS").read_text(encoding="utf-8") == expected_sums


def test_import_reports_per_slice_denominators_and_case_intervals(tmp_path: Path) -> None:
    rows = [
        {
            "kind": "ranking_case",
            "caseKey": "rank-a",
            "slice": "rgb-zh",
            "ranking": ["partly-relevant", "relevant"],
            "qrels": {"partly-relevant": 2, "relevant": 3},
            "relevanceThreshold": 3,
        },
        {
            "kind": "ranking_case",
            "caseKey": "rank-b",
            "slice": "zz-other",
            "ranking": ["unjudged", "approximately-relevant"],
            "qrels": {"approximately-relevant": 2},
            "relevanceThreshold": 3,
        },
        {
            "kind": "ranking_case",
            "caseKey": "rank-c",
            "slice": "zz-zero",
            "ranking": ["unjudged", "judged-zero"],
            "qrels": {"judged-zero": 0},
            "relevanceThreshold": 3,
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "claim-a",
            "slice": "ragtruth",
            "task": "binary_classification",
            "goldPositive": True,
            "predictedPositive": True,
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "claim-b",
            "slice": "ragtruth",
            "task": "binary_classification",
            "goldPositive": False,
            "predictedPositive": True,
        },
        {
            "kind": "claim_or_span_case",
            "caseKey": "rgb-a",
            "slice": "rgb-zh",
            "task": "answer_groups",
            "prediction": "答案是北京，时间为 2024 年。",
            "goldAnswerGroups": [["北京"], ["2024", "二〇二四"]],
        },
        {
            "kind": "agent_trial",
            "caseKey": "agent-a",
            "slice": "official-agent",
            "trialNumber": 1,
            "safe_success": _safe_success(),
        },
        {
            "kind": "agent_trial",
            "caseKey": "agent-a",
            "slice": "official-agent",
            "trialNumber": 2,
            "safe_success": _safe_success(goal_state_match=False),
        },
    ]
    input_path = tmp_path / "normalized.jsonl"
    manifest_path = tmp_path / "source-manifest.json"
    _write_jsonl(input_path, rows)
    manifest_path.write_text(
        json.dumps(_manifest(input_path, rows), ensure_ascii=False), encoding="utf-8"
    )

    report_path = run_import(
        manifest_path=manifest_path,
        input_path=input_path,
        output=tmp_path / "output",
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    ranking_domain = report["domains"]["ranking"]
    ranking = ranking_domain["sliceResults"][0]
    claim = report["domains"]["claimOrSpan"]["sliceResults"][0]
    answer_groups = next(
        row
        for row in report["domains"]["claimOrSpan"]["sliceResults"]
        if row["task"] == "answer_groups"
    )
    agent = report["domains"]["agent"]["sliceResults"][0]
    assert ranking_domain["caseDenominator"] == 3
    assert ranking_domain["gradedCaseDenominator"] == 2
    assert ranking_domain["binaryCaseDenominator"] == 1
    assert ranking_domain["metrics"]["precisionAt1"]["value"] == 0.0
    assert ranking_domain["metrics"]["precisionAt1"]["denominator"] == 1
    assert ranking_domain["metrics"]["mrrAt10"]["value"] == 0.5
    assert ranking_domain["metrics"]["ndcgAt10"]["denominator"] == 2
    assert ranking_domain["metrics"]["hitAt10"]["value"] == 1.0
    assert ranking_domain["metrics"]["unjudgedAt5"]["value"] == 0.333333
    assert ranking["caseDenominator"] == 1
    assert ranking["binaryCaseDenominator"] == 1
    assert ranking["metrics"]["precisionAt1"]["value"] == 0.0
    assert ranking["metrics"]["mrrAt10"]["value"] == 0.5
    assert 0 < ranking["metrics"]["ndcgAt5"]["value"] < 1
    assert ranking["metrics"]["recallAt5"]["interval"]["method"] == "case-bootstrap"
    no_binary = ranking_domain["sliceResults"][1]
    assert no_binary["binaryCaseDenominator"] == 0
    assert no_binary["metrics"]["precisionAt1"]["status"] == "UNAVAILABLE"
    assert no_binary["metrics"]["ndcgAt10"]["status"] == "AVAILABLE"
    no_graded = ranking_domain["sliceResults"][2]
    assert no_graded["gradedCaseDenominator"] == 0
    assert no_graded["metrics"]["ndcgAt10"]["status"] == "UNAVAILABLE"
    assert no_graded["metrics"]["unjudgedAt10"]["status"] == "AVAILABLE"
    assert claim["caseDenominator"] == 2
    assert claim["metrics"]["precision"]["value"] == 0.5
    assert claim["metrics"]["f1"]["interval"]["method"] == "case-bootstrap"
    assert answer_groups["metrics"]["answerGroupCoverage"]["value"] == 1.0
    assert answer_groups["metrics"]["allGroupsMatchedRate"]["value"] == 1.0
    assert answer_groups["metrics"]["precision"]["status"] == "UNAVAILABLE"
    assert agent["trialsPerCase"] == 2
    assert agent["trialDenominator"] == 2
    assert agent["caseDenominator"] == 1
    assert agent["metrics"]["safeTrialRate"]["value"] == 0.5
    assert agent["metrics"]["passPower"]["value"] == 0.0
    assert not _contains_forbidden_key(report)


def test_import_accepts_v1_scorer_manifest_and_records_successor_version(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "kind": "ranking_case",
            "caseKey": "legacy-rank",
            "slice": "legacy",
            "ranking": ["relevant"],
            "qrels": {"relevant": 1},
        }
    ]
    input_path = tmp_path / "normalized.jsonl"
    manifest_path = tmp_path / "source-manifest.json"
    _write_jsonl(input_path, rows)
    manifest = _manifest(input_path, rows)
    manifest["scorerVersion"] = "aishop-public-transfer-scorer/v1"
    manifest["officialAgentExecution"] = False
    manifest["caseCountAndEligibleDenominators"]["agentTrialEligible"] = 0
    manifest["caseCountAndEligibleDenominators"]["agentCaseEligible"] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report_path = run_import(
        manifest_path=manifest_path,
        input_path=input_path,
        output=tmp_path / "output",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["scorerVersion"] == SCORER_VERSION
    assert report["sourceManifestScorerVersion"] == "aishop-public-transfer-scorer/v1"


@pytest.mark.parametrize("invalid", ["schema", "hash", "unknown"])
def test_import_rejects_invalid_schema_hash_and_unknown_fields(
    tmp_path: Path, invalid: str
) -> None:
    rows = [
        {
            "kind": "ranking_case",
            "caseKey": "rank-a",
            "slice": "rgb-zh",
            "ranking": ["relevant"],
            "qrels": {"relevant": 1},
        }
    ]
    input_path = tmp_path / "normalized.jsonl"
    manifest_path = tmp_path / "source-manifest.json"
    _write_jsonl(input_path, rows)
    manifest = _manifest(input_path, rows)
    manifest["officialAgentExecution"] = False
    manifest["caseCountAndEligibleDenominators"]["agentTrialEligible"] = 0
    manifest["caseCountAndEligibleDenominators"]["agentCaseEligible"] = 0
    if invalid == "schema":
        manifest["schemaVersion"] = "unknown/v1"
    elif invalid == "hash":
        manifest["normalizedInputSha256"] = "0" * 64
    else:
        rows[0]["query"] = "must never be accepted"
        _write_jsonl(input_path, rows)
        manifest["normalizedInputSha256"] = sha256_file(input_path)
    manifest_path.write_text(json.dumps(deepcopy(manifest)), encoding="utf-8")

    with pytest.raises(PublicTransferError):
        run_import(
            manifest_path=manifest_path,
            input_path=input_path,
            output=tmp_path / "output",
        )
