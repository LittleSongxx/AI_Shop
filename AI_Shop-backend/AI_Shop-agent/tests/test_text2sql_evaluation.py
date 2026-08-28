from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from evaluation.text2sql.answer_review import (
    ANSWER_REVIEW_FILE,
    ANSWER_REVIEW_SCHEMA,
    _candidate_projection,
    _write_open_package,
    adjudicate_answer_reviews,
    compare_answer_reviews,
    seal_answer_review,
    validate_answer_review,
)
from evaluation.text2sql.cases_v0 import build_candidates
from evaluation.text2sql.catalog import build_catalog, verify_catalog
from evaluation.text2sql.contracts import Annotation, ResultOracle
from evaluation.text2sql.dataset import (
    DEFAULT_DATASET,
    load_cases,
    validate_v0,
    verify_human_gold,
    verify_lock,
)
from evaluation.text2sql.final_report import build_final_report
from evaluation.text2sql.fixture import fingerprint, source_data_fingerprint, verify
from evaluation.text2sql.freeze import SOURCE_PATHS
from evaluation.text2sql.io import (
    canonical_json_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
    verify_sha256s,
    write_json,
    write_jsonl,
    write_sha256s,
)
from evaluation.text2sql.mutations import generate_mutations
from evaluation.text2sql.review import (
    adjudicate_gold,
    compare_reviews,
    create_open_packages,
    seal_review,
    validate_review,
)
from evaluation.text2sql.runner import RunConfig, run
from evaluation.text2sql.scoring import (
    normalize_legacy_outcome,
    score_case,
    score_denotation,
    summarize,
)
from evaluation.text2sql.trace import _scan_estimate


def test_input_freeze_binds_supply_compiler_and_policy_sources():
    assert {
        "AI_Shop-backend/AI_Shop-agent/app/services/analytics_policy.py",
        "AI_Shop-backend/AI_Shop-agent/app/services/analytics_semantic_compiler.py",
    } <= set(SOURCE_PATHS)


def test_provisional_catalog_is_hashed_and_covers_exactly_ten_views():
    catalog = build_catalog()
    result = verify_catalog(catalog)

    assert result["verified"] is True
    assert len(catalog["views"]) == 10
    assert catalog["currency"] == "CNY"
    assert catalog["timezone"] == "Asia/Shanghai"
    assert catalog["releaseGateEligible"] is False
    assert (
        catalog["views"]["analytics_inventory_forecast"]["columns"]["confidence"]["unit"] == "RATIO"
    )
    assert "统计置信概率" in catalog["views"]["analytics_inventory_forecast"]["forbiddenClaims"]


def test_v0_candidate_distribution_and_flow_contract_are_exact():
    summary = validate_v0(build_candidates())

    assert summary["caseCount"] == 80
    assert summary["outcomes"] == {
        "ABSTAIN": 10,
        "ANSWER": 48,
        "CLARIFY": 10,
        "DENY": 12,
    }
    assert summary["flows"]["multiBranch"] == 4
    assert summary["flows"]["pagination"] >= 8
    assert summary["flows"]["export"] >= 6
    assert summary["flows"]["clarification"] >= 8


def test_tracked_candidate_oracles_are_fully_materialized_and_typed():
    cases = load_cases()

    for case in cases:
        if not case.expected.reference_sql:
            assert case.expected.result_oracle.mode == "NO_QUERY"
            continue
        assert len(case.expected.branch_result_oracles) == len(case.expected.branches)
        assert all(oracle.materialized for oracle in case.expected.branch_result_oracles)
        for oracle in case.expected.branch_result_oracles:
            for row in oracle.rows:
                for column, column_type in oracle.column_types.items():
                    value = row[column]
                    if value is not None and column_type.get("type") == "DECIMAL":
                        assert isinstance(value, str)
                    if value is not None and column_type.get("unit") == "CNY":
                        assert len(value.rsplit(".", 1)[-1]) == 2


def test_candidate_lock_verifies_release_boundary_and_hashes():
    result = verify_lock()

    assert result["verified"] is True
    assert result["checks"]["releaseBoundary"] is True


def test_open_review_packages_strip_candidate_labels_and_shuffle_independently(tmp_path):
    result = create_open_packages(tmp_path / "reviews")
    directory_a = Path(result["packages"]["A"])
    directory_b = Path(result["packages"]["B"])

    assert validate_review(directory_a, require_complete=False)["valid"] is True
    assert validate_review(directory_b, require_complete=False)["valid"] is True
    inputs_a = read_jsonl(directory_a / "review-inputs.jsonl")
    inputs_b = read_jsonl(directory_b / "review-inputs.jsonl")
    assert [row["id"] for row in inputs_a] != [row["id"] for row in inputs_b]
    encoded = json.dumps(inputs_a, ensure_ascii=False)
    for forbidden in (
        '"expected"',
        '"outcome"',
        '"referenceSql"',
        '"resultOracle"',
        '"sliceTags"',
        '"risk"',
    ):
        assert forbidden not in encoded


def _complete_synthetic_review(directory: Path, reviewer_id: str) -> None:
    """Exercise the review machinery without creating human-review evidence."""
    candidates = {case.case_id: case for case in load_cases()}
    rows = read_jsonl(directory / "gold-review.open.jsonl")
    label_fields = {
        "outcome",
        "completion",
        "reasonCode",
        "branches",
        "referenceSql",
        "resultOracle",
        "branchResultOracles",
        "expectedFailedBranchIds",
        "clarificationQuestion",
        "clarificationOptions",
        "requiredFacts",
        "forbiddenClaims",
    }
    for row in rows:
        case_id = row["input"]["id"]
        expected = candidates[case_id].expected.model_dump(by_alias=True, mode="json")
        row["label"].update({key: expected[key] for key in label_fields})
        row["label"].update(
            {
                "reviewerId": reviewer_id,
                "reviewedAt": "2026-08-27T12:00:00+08:00",
                "notes": "synthetic test fixture; not human evidence",
            }
        )
    write_jsonl(directory / "gold-review.open.jsonl", rows, overwrite=True)


def test_review_seal_compare_and_gold_adjudication_round_trip(tmp_path):
    packages = create_open_packages(tmp_path / "open")
    open_a = Path(packages["packages"]["A"])
    open_b = Path(packages["packages"]["B"])
    _complete_synthetic_review(open_a, "synthetic-reviewer-a")
    _complete_synthetic_review(open_b, "synthetic-reviewer-b")

    sealed_a = tmp_path / "sealed-a"
    sealed_b = tmp_path / "sealed-b"
    seal_review(open_a, sealed_a)
    seal_review(open_b, sealed_b)
    comparison = tmp_path / "comparison"
    compared = compare_reviews(sealed_a, sealed_b, comparison)
    assert compared["agreementCount"] == 80
    assert compared["disagreementCount"] == 0

    gold = tmp_path / "gold"
    result = adjudicate_gold(sealed_a, sealed_b, comparison, gold)
    assert result["status"] == "HUMAN_VERIFIED"
    assert len(load_cases(gold / "gold-v0.jsonl")) == 80
    assert read_json(gold / "evidence.json")["pureHumanUnaided"] is False
    assert verify_sha256s(gold)
    assert verify_human_gold(gold / "gold-v0.jsonl")["verified"] is True


def test_review_package_rejects_modified_input_projection(tmp_path):
    packages = create_open_packages(tmp_path / "open")
    directory = Path(packages["packages"]["A"])
    inputs = read_jsonl(directory / "review-inputs.jsonl")
    rows = read_jsonl(directory / "gold-review.open.jsonl")
    inputs[0]["question"] = "被误改的问题"
    rows[0]["input"]["question"] = "被误改的问题"
    write_jsonl(directory / "review-inputs.jsonl", inputs, overwrite=True)
    write_jsonl(directory / "gold-review.open.jsonl", rows, overwrite=True)

    with pytest.raises(ValueError, match="source binding failed"):
        validate_review(directory, require_complete=False)


def test_answer_review_projection_hides_version_scores_sql_and_opaque_ids():
    projected = _candidate_projection(
        {
            "initial": {"httpStatus": 200},
            "normalized": {
                "outcome": "ANSWER",
                "completion": "COMPLETE",
                "status": "SUCCEEDED",
                "answer": "完成",
                "rows": [{"amount": "1.00"}],
                "sql": "SELECT amount FROM analytics_sales_daily",
                "queries": [{"sql": "SELECT 1"}],
                "runId": "run-secret",
                "resultSetId": "result-secret",
                "catalogVersion": "analytics-provisional-v0.20260827",
            },
            "score": {"trustedRequestPassed": True},
            "phase": "post-foundation",
        }
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert "post-foundation" not in encoded
    assert "trustedRequestPassed" not in encoded
    assert "SELECT amount" not in encoded
    assert "run-secret" not in encoded
    assert "result-secret" not in encoded
    assert projected["answer"] == "完成"


def _complete_answer_review(
    directory: Path,
    *,
    reviewer: str,
    decisions: dict[str, tuple[str, list[str], str]],
) -> None:
    rows = read_jsonl(directory / ANSWER_REVIEW_FILE)
    for row in rows:
        item_id = row["input"]["reviewItemId"]
        decision, issues, notes = decisions.get(item_id, ("ACCEPT", [], ""))
        row["label"] = {
            "decision": decision,
            "issueCodes": issues,
            "notes": notes,
            "reviewerId": reviewer,
            "reviewedAt": "2026-08-28T16:00:00+08:00",
        }
    write_jsonl(directory / ANSWER_REVIEW_FILE, rows, overwrite=True)


def test_answer_review_seal_compare_and_decision_only_adjudication(tmp_path):
    inputs = []
    bindings = []
    for case_number in range(80):
        case_id = f"t2s-v0-{case_number + 1:03d}"
        for source in ("pre-foundation", "post-foundation"):
            item_id = f"item-{case_number:03d}-{source}"
            item = {
                "schemaVersion": ANSWER_REVIEW_SCHEMA,
                "reviewItemId": item_id,
                "question": f"问题 {case_number}",
                "gold": {"expectedOutcome": "ANSWER"},
                "candidate": {"outcome": "ANSWER", "answer": "结果"},
            }
            inputs.append(item)
            bindings.append(
                {
                    "reviewItemId": item_id,
                    "caseId": case_id,
                    "source": source,
                    "trial": 1,
                    "inputSha256": sha256_bytes(canonical_json_bytes(item)),
                    "rawRecordSha256": "0" * 64,
                }
            )
    catalog = Path(__file__).parents[1] / "evaluation/datasets/text2sql/catalog-v0.provisional.json"
    open_a = tmp_path / "open-a"
    open_b = tmp_path / "open-b"
    _write_open_package(open_a, package_id="A", inputs=inputs, catalog=catalog)
    _write_open_package(open_b, package_id="B", inputs=list(reversed(inputs)), catalog=catalog)
    disagreement_id = inputs[0]["reviewItemId"]
    consensus_reject_id = inputs[1]["reviewItemId"]
    _complete_answer_review(
        open_a,
        reviewer="human-a",
        decisions={consensus_reject_id: ("REJECT", ["WRONG_RESULT"], "结果错误")},
    )
    _complete_answer_review(
        open_b,
        reviewer="human-b",
        decisions={
            disagreement_id: ("REJECT", ["WRONG_OUTCOME"], "结论不同"),
            consensus_reject_id: ("REJECT", ["OTHER"], "另一种失败原因"),
        },
    )
    assert validate_answer_review(open_a, require_complete=True)["complete"] is True
    sealed_a = tmp_path / "sealed-a"
    sealed_b = tmp_path / "sealed-b"
    seal_answer_review(open_a, sealed_a)
    seal_answer_review(open_b, sealed_b)

    control = tmp_path / "control"
    control.mkdir()
    write_jsonl(control / "source-bindings.jsonl", bindings)
    write_json(
        control / "manifest.json",
        {
            "schemaVersion": ANSWER_REVIEW_SCHEMA,
            "disagreementPolicy": "decision-only",
        },
    )
    write_sha256s(control)
    comparison = tmp_path / "comparison"
    compared = compare_answer_reviews(sealed_a, sealed_b, control, comparison)
    assert compared["agreementCount"] == 159
    assert compared["disagreementCount"] == 1
    instructions = (comparison / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "只能填 `ACCEPT` 或 `REJECT`" in instructions
    assert "不得与 A/B 相同" in instructions
    assert "WRONG_RESULT" in instructions

    adjudication = read_jsonl(comparison / "adjudication.open.jsonl")
    adjudication[0]["label"] = {
        "decision": "ACCEPT",
        "issueCodes": [],
        "notes": "第三人确认可接受",
        "reviewerId": "human-c",
        "reviewedAt": "2026-08-28T17:00:00+08:00",
    }
    write_jsonl(comparison / "adjudication.open.jsonl", adjudication, overwrite=True)
    final = tmp_path / "final"
    result = adjudicate_answer_reviews(sealed_a, sealed_b, comparison, control, final)
    assert result["status"] == "HUMAN_REVIEWED_ADJUDICATED"
    assert result["reviewers"] == ["human-a", "human-b"]
    assert result["adjudicator"] == "human-c"
    assert sum(result["pairedHumanTransitions"].values()) == 80
    assert verify_sha256s(final)


def test_final_report_binds_all_evidence_and_keeps_release_boundary(tmp_path):
    boundary = {
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    metric = {"eligible": 80, "passed": 40, "rate": 0.5}
    summary = {
        "caseCount": 80,
        "outcome": metric,
        "completion": metric,
        "plan": metric,
        "execution": metric,
        "denotation": metric,
        "narrative": metric,
        "flow": metric,
        "trustedRequest": metric,
        "ordinaryTrustedAnswer": metric,
        "infrastructureFailures": 2,
        "severeSecurityFailures": 0,
        "threeRunStability": {
            "outcome": {"eligible": 80, "passed": 75, "rate": 0.9375},
            "fullDecision": {"eligible": 80, "passed": 47, "rate": 0.5875},
        },
        "efficiency": {},
    }

    def seal(directory: Path) -> None:
        write_sha256s(directory)

    pre = tmp_path / "pre"
    pre.mkdir()
    write_json(
        pre / "manifest.json",
        {
            "phase": "pre-foundation",
            "caseCount": 80,
            "trialCount": 3,
            "executionCount": 240,
            "canonicalTrial": 1,
            **boundary,
        },
    )
    write_jsonl(pre / "raw-responses.jsonl", [{"fixture": True}])
    seal(pre)

    post = tmp_path / "post"
    post.mkdir()
    write_json(
        post / "manifest.json",
        {
            "phase": "post-foundation",
            "caseCount": 80,
            "trialCount": 3,
            "executionCount": 240,
            "canonicalTrial": 1,
            "summaryAllTrials": {**summary, "caseCount": 240},
            **boundary,
        },
    )
    write_jsonl(post / "raw-responses.jsonl", [{"fixture": True}])
    seal(post)

    paired = tmp_path / "paired"
    paired.mkdir()
    write_json(
        paired / "manifest.json",
        {
            "hardConditionChecks": {
                "completeExecutions": True,
                "zeroPostSevereSecurityFailures": True,
                "denyFixturesUnchanged": True,
                "goldHumanVerified": True,
                "sha256Verified": True,
            },
            "standardizedPreCanonical": summary,
            "standardizedPostCanonical": summary,
            "pairedTransitions": {},
            **boundary,
        },
    )
    write_jsonl(paired / "paired-canonical.jsonl", [{"fixture": True}])
    seal(paired)

    gold = tmp_path / "gold"
    gold.mkdir()
    write_json(
        gold / "evidence.json",
        {
            "status": "HUMAN_REVIEWED_ADJUDICATED",
            "summary": {"caseCount": 80},
            **boundary,
        },
    )
    write_jsonl(gold / "gold-v0.jsonl", [{"fixture": True}])
    seal(gold)

    answer = tmp_path / "answer"
    answer.mkdir()
    write_json(
        answer / "evidence.json",
        {
            "status": "HUMAN_REVIEWED_ADJUDICATED",
            "reviewers": ["human-a", "human-b"],
            "adjudicator": "human-c",
            "agreementCount": 155,
            "disagreementCount": 5,
            "finalDecisionCountsByBlindedSource": {
                "pre-foundation": {"ACCEPT": 0, "REJECT": 80},
                "post-foundation": {"ACCEPT": 29, "REJECT": 51},
            },
            "pairedHumanTransitions": {"IMPROVED": 29, "UNCHANGED_REJECT": 51},
            "humanDecisionAuthority": True,
            "aiAssistanceUsed": True,
            "pureHumanUnaided": False,
            **boundary,
        },
    )
    write_jsonl(answer / "answer-review.adjudicated.jsonl", [{"fixture": True}])
    seal(answer)

    verification = tmp_path / "verification"
    verification.mkdir()
    write_json(verification / "verification.json", {"relevantChecksPassed": True})
    seal(verification)

    output = tmp_path / "final"
    result = build_final_report(
        pre=pre,
        post=post,
        paired=paired,
        gold=gold,
        answer_review=answer,
        verification=verification,
        output=output,
    )

    assert result["status"] == "DEVELOPMENT_PROVISIONAL_EVIDENCE_COMPLETE"
    assert result["releaseGateEligible"] is False
    assert result["humanCanonicalReview"]["decisionCountsBySource"]["post-foundation"] == {
        "ACCEPT": 29,
        "REJECT": 51,
    }
    assert "不得进入质量发布门禁" in (output / "REPORT.md").read_text(encoding="utf-8")
    assert verify_sha256s(output)


def test_human_annotation_requires_two_distinct_reviewers_and_hashes():
    digest = "a" * 64
    annotation = Annotation(
        status="HUMAN_VERIFIED",
        humanDecisionAuthority=True,
        reviewers=["reviewer-a", "reviewer-b"],
        reviewEvidence={
            "sourceDatasetSha256": digest,
            "reviewASha256": digest,
            "reviewBSha256": digest,
        },
    )

    assert annotation.human_decision_authority is True
    with pytest.raises(ValueError, match="two distinct"):
        Annotation(
            status="HUMAN_VERIFIED",
            humanDecisionAuthority=True,
            reviewers=["same", "same"],
            reviewEvidence={
                "sourceDatasetSha256": digest,
                "reviewASha256": digest,
                "reviewBSha256": digest,
            },
        )


def test_decimal_denotation_uses_gold_type_and_preserves_exact_string():
    oracle = ResultOracle(
        mode="EXACT_ROWS",
        columns=["amount"],
        columnTypes={"amount": {"type": "DECIMAL", "scale": 2, "unit": "CNY"}},
        rows=[{"amount": "0.30"}],
        materialized=True,
    )

    assert score_denotation(oracle, {"rows": [{"amount": "0.30"}]})["passed"] is True
    assert score_denotation(oracle, {"rows": [{"amount": "0.31"}]})["passed"] is False


def test_layered_scorer_accepts_a_fully_grounded_answer_contract():
    case = load_cases()[0]
    branch = case.expected.branches[0]
    oracle = case.expected.branch_result_oracles[0]
    response = {
        "outcome": "ANSWER",
        "completion": "COMPLETE",
        "status": "SUCCEEDED",
        "catalogVersion": "analytics-provisional-v0.20260827",
        "dataAsOf": "2026-08-27T12:00:00+08:00",
        "period": {"startDate": branch.start_date, "endDate": branch.end_date},
        "sql": case.expected.reference_sql[0],
        "lineage": [branch.semantic_view],
        "columns": oracle.columns,
        "columnTypes": oracle.column_types,
        "rows": oracle.rows,
        "metricDefinitions": [{"name": metric} for metric in branch.metrics],
        "answer": "暂定口径，仅供运营核对。",
        "highlights": [],
    }
    trace = {
        "plan": {
            "branches": [
                {
                    "branch_id": branch.branch_id,
                    "semantic_view": branch.semantic_view,
                    "metrics": branch.metrics,
                    "dimensions": branch.dimensions,
                    "start_date": branch.start_date,
                    "end_date": branch.end_date,
                }
            ]
        },
        "dbTimeMs": 4,
        "scanEstimate": {"estimatedRows": 7, "branches": []},
        "run": {"inputTokens": 10, "outputTokens": 5, "costCny": "0.0001"},
        "steps": [
            {"eventType": "LLM_CALL"},
            {"eventType": "LLM_CALL"},
            {"eventType": "DATA_ANALYST_QUERY", "latencyMs": 4},
        ],
    }

    score = score_case(
        case,
        response,
        trace=trace,
        flow={"pagination": {"completed": True, "snapshotBound": True}},
        latency_ms=20.0,
    )

    assert score["trustedRequestPassed"] is True
    assert score["plan"]["passed"] is True
    assert score["execution"]["denotationPassed"] is True
    assert summarize([score])["ordinaryTrustedAnswer"]["rate"] == 1.0


def test_security_scorer_ignores_phone_like_digits_in_opaque_result_identifiers():
    case = load_cases()[0]
    response = {
        "outcome": "ANSWER",
        "completion": "COMPLETE",
        "status": "SUCCEEDED",
        "answer": "查询完成。",
        "rows": [],
        "resultHash": "ebc327313d71a988399f8f12ae194c3bbc91288306d0f4e14132466596a58dc5",
        "resultSetId": "ars_116fa5a0d14647379481a9e7a3a2900f",
    }

    score = score_case(case, response)

    assert score["security"]["piiPatternExposed"] is False
    assert score["security"]["severeFailure"] is False


def test_security_scorer_still_flags_phone_in_user_visible_content():
    case = load_cases()[0]
    response = {
        "outcome": "ANSWER",
        "completion": "COMPLETE",
        "status": "SUCCEEDED",
        "answer": "客户联系电话是 13800138000。",
        "rows": [],
        "resultHash": "safe-opaque-value",
    }

    score = score_case(case, response)

    assert score["security"]["piiPatternExposed"] is True
    assert score["security"]["severeFailure"] is True


def test_explain_scan_estimate_is_structured_and_additive():
    result = _scan_estimate(
        [
            {
                "eventType": "DATA_ANALYST_EXPLAIN",
                "status": "OK",
                "output": {
                    "branchId": "a",
                    "sqlHash": "x",
                    "rows": [{"rows": 12}, {"rows": "8"}],
                },
            },
            {"eventType": "DATA_ANALYST_QUERY", "output": {"rows": 99}},
        ]
    )

    assert result == {
        "estimatedRows": 20,
        "branches": [
            {
                "branchId": "a",
                "estimatedRows": 20,
                "sqlHash": "x",
                "status": "OK",
            }
        ],
    }


def test_explain_scan_estimate_does_not_report_zero_when_view_privilege_blocks_it():
    result = _scan_estimate(
        [
            {
                "eventType": "DATA_ANALYST_EXPLAIN",
                "status": "DEGRADED",
                "output": {
                    "branchId": "inventory",
                    "sqlHash": "x",
                    "rows": [],
                    "scanEstimate": None,
                    "explainDiagnostic": {
                        "status": "UNAVAILABLE",
                        "reasonCode": "EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE",
                    },
                },
            }
        ]
    )

    assert result == {
        "estimatedRows": None,
        "branches": [
            {
                "branchId": "inventory",
                "estimatedRows": None,
                "sqlHash": "x",
                "status": "DEGRADED",
                "reasonCode": "EXPLAIN_UNAVAILABLE_VIEW_PRIVILEGE",
            }
        ],
    }


@pytest.mark.parametrize(
    ("payload", "status", "expected"),
    [
        ({"status": "SUCCEEDED"}, 200, "ANSWER"),
        ({"status": "NEEDS_CLARIFICATION"}, 200, "CLARIFY"),
        ({}, 403, "DENY"),
        ({"status": "FAILED"}, 500, None),
    ],
)
def test_legacy_outcome_adapter(payload, status, expected):
    assert normalize_legacy_outcome(payload, http_status=status) == expected


def test_official_runner_refuses_ai_draft_before_creating_output(tmp_path):
    output = tmp_path / "forbidden-baseline"
    with pytest.raises(ValueError, match="HUMAN_VERIFIED"):
        run(
            RunConfig(
                phase="pre-foundation",
                output=output,
                dataset=DEFAULT_DATASET,
            )
        )
    assert not output.exists()


def test_evaluation_clock_is_validated_and_forbidden_in_production(monkeypatch):
    local = Settings(
        _env_file=None,
        data_analyst_enabled=False,
        analytics_eval_fixed_now="2026-08-27 12:00:00",
    )
    local.validate_runtime()

    production = Settings(
        _env_file=None,
        app_env="production",
        data_analyst_enabled=False,
        analytics_eval_fixed_now="2026-08-27 12:00:00",
    )
    with pytest.raises(ValueError, match="ANALYTICS_EVAL_FIXED_NOW"):
        production.validate_runtime()

    from app.services import data_analyst_service

    monkeypatch.setattr(
        data_analyst_service,
        "get_settings",
        lambda: SimpleNamespace(analytics_eval_fixed_now="2026-08-27 12:00:00"),
    )
    assert data_analyst_service._question_dates("最近7天") == (
        data_analyst_service.date(2026, 8, 21),
        data_analyst_service.date(2026, 8, 27),
    )


def test_fixture_fingerprint_pins_images_clock_and_real_migrations():
    value = fingerprint()

    assert value["mysqlImage"] == "mysql:8.4.11"
    assert value["fixedTimestamp"] == "2026-08-27 12:00:00 Asia/Shanghai"
    assert any(
        path.endswith("AI_Shop-admin/src/main/resources/db/migration/R__current_schema.sql")
        for path in value["files"]
    )


def test_mutation_generator_covers_aggregation_sort_date_and_threshold():
    sql = "SELECT SUM(stock) AS stock FROM analytics_inventory_risk WHERE stock <= 0 AND date BETWEEN '2026-08-21' AND '2026-08-27' ORDER BY stock DESC LIMIT 3"
    names = {name for name, _ in generate_mutations(sql)}

    assert names == {
        "aggregation_sum_to_count",
        "sort_desc_to_asc",
        "threshold_lte_to_lt",
        "date_start_plus_one_day",
    }


@pytest.mark.mysql
@pytest.mark.skipif(
    os.getenv("TEXT2SQL_EVAL_MYSQL_TESTS") != "1",
    reason="set TEXT2SQL_EVAL_MYSQL_TESTS=1 after fixture-bootstrap",
)
def test_real_mysql_fixture_enforces_ten_view_reader_boundary():
    result = verify()
    before = source_data_fingerprint()
    after = source_data_fingerprint()

    assert result["checks"] == {
        "allTenViewsReadable": True,
        "allForbiddenOperationsDenied": True,
        "grantsAreViewOnly": True,
        "fixedCurrentDate": True,
        "mysqlVersion": True,
    }
    assert before["tableCount"] > 0
    assert before["sha256"] == after["sha256"]
