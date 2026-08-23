from collections import Counter
from pathlib import Path

import pytest

from evaluation.core.io import atomic_write_jsonl
from evaluation.customer_service_gold import (
    HUMAN_STATUS,
    CustomerServiceGoldError,
    evaluate_predictions,
    load_gold_dataset,
)

DATASET = Path(__file__).parents[1] / "evaluation" / "datasets" / "customer_service" / "gold-v1.jsonl"
CANDIDATE_V2_ADDITIONS = (
    Path(__file__).parents[1]
    / "evaluation"
    / "datasets"
    / "customer_service"
    / "candidate-v2-additions.jsonl"
)


def _perfect_predictions(rows):
    return {
        row["id"]: {
            "intent": row["expected"]["intent"],
            "riskLevel": row["expected"]["riskLevel"],
            "shouldHandoff": row["expected"]["shouldHandoff"],
            "nextAction": "HANDOFF" if row["expected"]["shouldHandoff"] else "ANSWER",
            "entities": row["expected"]["slots"],
        }
        for row in rows
    }


def test_gold_dataset_is_independent_and_valid():
    rows = load_gold_dataset(DATASET)
    assert len(rows) == 60
    assert all(row["annotation"]["status"] == "DRAFT_NEEDS_HUMAN_REVIEW" for row in rows)
    assert rows[-1]["sliceTags"] == ["chat", "acknowledgement", "low-risk-chat"]


def test_report_exposes_pending_blinded_human_review_plan():
    rows = load_gold_dataset(DATASET)[:1]
    report = evaluate_predictions(rows, _perfect_predictions(rows), provenance={"mode": "fixture"})
    plan = report["humanReviewPlan"]
    assert plan["status"] == "PENDING_INDEPENDENT_REVIEW"
    assert plan["requiredAnnotators"] == 2
    assert plan["blindedFirstPass"] is True
    assert plan["adjudicationRequired"] is True
    assert report["releaseGateEligible"] is False


def test_human_verified_status_requires_and_exposes_review_evidence():
    row = load_gold_dataset(DATASET)[0]
    row["annotation"] = {
        "status": HUMAN_STATUS,
        "reviewers": ["reviewer-a", "reviewer-b"],
        "adjudicator": "lead-reviewer",
        "reviewEvidence": {"sourceDatasetSha256": "a" * 64},
    }
    report = evaluate_predictions(
        [row],
        _perfect_predictions([row]),
        provenance={"mode": "fixture", "datasetSha256": "b" * 64},
    )
    assert report["status"] == HUMAN_STATUS
    assert report["humanReviewPlan"]["status"] == "COMPLETE"
    assert report["humanReviewPlan"]["adjudicationComplete"] is True
    assert HUMAN_STATUS in report["metrics"]["intentMacroF1"]["notes"]
    assert "not customer satisfaction" in report["limitations"][0]


def test_human_verified_dataset_rejects_missing_reviewer_hashes(tmp_path):
    row = load_gold_dataset(DATASET)[0]
    row["annotation"] = {
        "status": HUMAN_STATUS,
        "reviewers": ["reviewer-a", "reviewer-b"],
        "adjudicator": "lead-reviewer",
        "reviewEvidence": {"sourceDatasetSha256": "a" * 64},
    }
    target = tmp_path / "human.jsonl"
    atomic_write_jsonl(target, [row])
    with pytest.raises(CustomerServiceGoldError, match="reviewASha256"):
        load_gold_dataset(target)


def test_metrics_keep_handoff_suggested_out_of_handoff_recall():
    rows = load_gold_dataset(DATASET)[:1]
    row = rows[0]
    row["expected"] = {
        "intent": "CHAT",
        "riskLevel": "HIGH",
        "shouldHandoff": True,
        "handoffSeverity": "CRITICAL",
        "slots": {},
    }
    report = evaluate_predictions(
        rows,
        {
            row["id"]: {
                "intent": "CHAT",
                "riskLevel": "HIGH",
                "nextAction": "HANDOFF_SUGGESTED",
                "shouldHandoff": False,
                "entities": {},
            }
        },
        provenance={"mode": "fixture"},
    )
    assert report["metrics"]["handoffRecall"]["value"] == 0.0
    assert report["metrics"]["criticalHandoffMissRate"]["value"] == 1.0
    assert report["status"] == "PROVISIONAL_NOT_HUMAN_GOLD"
    assert report["releaseGateEligible"] is False


def test_slot_metrics_use_non_empty_slot_denominator_only():
    rows = load_gold_dataset(DATASET)[:2]
    predictions = _perfect_predictions(rows)
    report = evaluate_predictions(rows, predictions, provenance={"mode": "fixture"})
    metric = report["metrics"]["slotExactMatch"]
    assert metric["numerator"] == metric["denominator"] == 2
    assert metric["value"] == 1.0


def test_missing_prediction_is_a_badcase_not_a_silent_pass():
    rows = load_gold_dataset(DATASET)[:1]
    report = evaluate_predictions(rows, {}, provenance={"mode": "fixture"})
    assert report["cases"][0]["predicted"]["intent"] == "__MISSING__"
    assert report["metrics"]["intentMacroF1"]["badcaseCount"] == 1


def test_customer_service_intervals_use_predeclared_stratified_bootstrap():
    rows = load_gold_dataset(DATASET)
    report = evaluate_predictions(
        rows, _perfect_predictions(rows), provenance={"mode": "fixture"}
    )
    assert report["bootstrapPolicy"]["primaryStrata"] == [
        "intent",
        "riskLevel",
        "shouldHandoff",
    ]
    assert (
        report["metrics"]["intentMacroF1"]["confidenceInterval95"]["method"]
        == "stratified-case-bootstrap-macro-F1"
    )
    assert (
        report["metrics"]["slotEntitySpanF1"]["confidenceInterval95"]["method"]
        == "stratified-case-bootstrap-micro-F1"
    )


def test_slot_micro_f1_interval_and_fraction_use_the_same_statistic():
    rows = load_gold_dataset(DATASET)[:2]
    predictions = _perfect_predictions(rows)
    predictions[rows[0]["id"]]["entities"] = {"productName": "索尼"}
    report = evaluate_predictions(rows, predictions, provenance={"mode": "fixture"})
    metric = report["metrics"]["slotEntitySpanF1"]
    assert metric["value"] == round(metric["numerator"] / metric["denominator"], 6)
    interval = metric["confidenceInterval95"]
    assert interval["lower"] <= metric["value"] <= interval["upper"]
    counts = metric["componentCounts"]
    assert metric["numerator"] == 2 * counts["truePositive"]
    assert metric["denominator"] == (
        2 * counts["truePositive"]
        + counts["falsePositive"]
        + counts["falseNegative"]
    )


def test_candidate_v2_additions_are_balanced_draft_not_fake_gold():
    rows = load_gold_dataset(CANDIDATE_V2_ADDITIONS)
    intent_counts = Counter(row["expected"]["intent"] for row in rows)
    assert len(rows) == 60
    assert len(intent_counts) == 20
    assert set(intent_counts.values()) == {3}
    assert all(
        row["annotation"]["status"] == "DRAFT_NEEDS_HUMAN_REVIEW" for row in rows
    )
    assert sum(row["expected"]["riskLevel"] == "HIGH" for row in rows) >= 9
    assert sum(row["expected"]["shouldHandoff"] for row in rows) >= 14
    assert any("full-width-currency" in row.get("sliceTags", []) for row in rows)
    assert any("consult-search-boundary" in row.get("sliceTags", []) for row in rows)


def test_critical_handoff_miss_is_present_in_badcase_rows():
    rows = load_gold_dataset(DATASET)[:1]
    row = rows[0]
    row["expected"] = {
        "intent": "CHAT",
        "riskLevel": "HIGH",
        "shouldHandoff": True,
        "handoffSeverity": "CRITICAL",
        "slots": {},
    }
    report = evaluate_predictions(
        rows,
        {
            row["id"]: {
                "intent": "CHAT",
                "riskLevel": "HIGH",
                "shouldHandoff": False,
                "nextAction": "ANSWER",
                "entities": {},
            }
        },
        provenance={"mode": "fixture"},
    )
    assert report["metrics"]["criticalHandoffMissRate"]["badcaseIds"] == [row["id"]]
    assert report["badcases"][0]["caseId"] == row["id"]


def test_invalid_annotation_status_fails_closed(tmp_path):
    target = tmp_path / "bad.jsonl"
    target.write_text(
        '{"schemaVersion":"aishop-customer-service-gold/v1","id":"x",'
        '"input":{"message":"hi"},"expected":{"intent":"CHAT",'
        '"riskLevel":"LOW","shouldHandoff":false,"slots":{}},'
        '"annotation":{"status":"MODEL_VERIFIED"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(CustomerServiceGoldError):
        load_gold_dataset(target)


def test_canonical_slot_diagnostic_separates_unmapped_human_extensions():
    row = load_gold_dataset(DATASET)[0]
    row["expected"] = {
        **row["expected"],
        "slots": {
            "productName": "索尼 WH-1000XM6",
            "amount": "2000",
            "brand": "索尼",
        },
    }
    report = evaluate_predictions(
        [row],
        {
            row["id"]: {
                "intent": "PRODUCT_SEARCH",
                "riskLevel": "LOW",
                "shouldHandoff": False,
                "entities": {
                    "productName": "索尼 WH-1000XM6",
                    "amount": "2000",
                },
            }
        },
        provenance={"mode": "fixture"},
    )
    diagnostics = report["canonicalSlotDiagnostics"]
    assert diagnostics["metrics"]["canonicalSlotExactMatch"]["value"] == 1.0
    assert report["badcases"][0]["rootCause"] == "GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED"
