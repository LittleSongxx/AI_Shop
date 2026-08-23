import json
from pathlib import Path

import pytest

from evaluation.core.io import atomic_write_jsonl, load_jsonl
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset
from evaluation.customer_service_review import (
    ADJUDICATION_SCHEMA,
    CustomerServiceReviewError,
    compare_human_reviews,
    export_review_sheet,
    merge_human_reviews,
    render_agreement_markdown,
    seal_review_sheet,
    validate_review_sheet,
)

DATASET = (
    Path(__file__).parents[1]
    / "evaluation"
    / "datasets"
    / "customer_service"
    / "gold-v1.jsonl"
)


def _labels(row):
    expected = row["expected"]
    return {
        "intent": expected["intent"],
        "riskLevel": expected["riskLevel"],
        "shouldHandoff": expected["shouldHandoff"],
        "handoffSeverity": expected.get("handoffSeverity"),
        "slots": expected["slots"],
    }


def _fill_open_sheet(path: Path, reviewer: str, *, mutate=None) -> None:
    rows = load_jsonl(path)
    source = {row["id"]: row for row in load_gold_dataset(DATASET)}
    for row in rows:
        row["reviewerId"] = reviewer
        row["labels"] = _labels(source[row["id"]])
        if mutate:
            mutate(row)
    atomic_write_jsonl(path, rows)


def _export_pair(tmp_path: Path):
    open_a = tmp_path / "review-a.open.jsonl"
    open_b = tmp_path / "review-b.open.jsonl"
    export_review_sheet(DATASET, open_a, reviewer_id="annotator-a", seed=11)
    export_review_sheet(DATASET, open_b, reviewer_id="annotator-b", seed=29)
    _fill_open_sheet(open_a, "annotator-a")
    _fill_open_sheet(open_b, "annotator-b")
    sealed_a = tmp_path / "review-a.sealed.jsonl"
    sealed_b = tmp_path / "review-b.sealed.jsonl"
    seal_review_sheet(DATASET, open_a, sealed_a)
    seal_review_sheet(DATASET, open_b, sealed_b)
    return sealed_a, sealed_b


def test_export_is_blinded_and_validate_does_not_leak_expected(tmp_path: Path):
    sheet = tmp_path / "review.open.jsonl"
    manifest = export_review_sheet(DATASET, sheet, reviewer_id="annotator-a", seed=7)

    rows = load_jsonl(sheet)
    assert len(rows) == 60
    assert manifest["lifecycle"] == "OPEN"
    assert manifest["containsExpectedOrPredicted"] is False
    assert "PRODUCT_SEARCH" in manifest["labelSchema"]["intentValues"]
    assert manifest["labelSchema"]["requiredFields"] == [
        "intent",
        "riskLevel",
        "shouldHandoff",
        "slots",
    ]
    assert all("expected" not in row and "predicted" not in row for row in rows)
    # Derived metadata such as slice tags can reveal the draft label and must
    # not be shown to either independent reviewer.
    assert all("sliceTags" not in row and "difficulty" not in row for row in rows)
    assert all(all(value is None for value in row["labels"].values()) for row in rows)
    assert validate_review_sheet(DATASET, sheet)["reviewerId"] == "annotator-a"
    with pytest.raises(CustomerServiceReviewError, match="labels are incomplete"):
        validate_review_sheet(DATASET, sheet, require_complete=True)


def test_seal_refreshes_hash_and_merge_produces_human_verified_dataset(tmp_path: Path):
    sealed_a, sealed_b = _export_pair(tmp_path)
    assert validate_review_sheet(DATASET, sealed_a, require_complete=True)["lifecycle"] == "SEALED"

    output = tmp_path / "customer-service-human-v1.jsonl"
    evidence = tmp_path / "customer-service-human-v1.evidence.json"
    report = merge_human_reviews(
        DATASET,
        sealed_a,
        sealed_b,
        output_dataset_path=output,
        evidence_path=evidence,
    )

    rows = load_gold_dataset(output)
    assert len(rows) == 60
    assert all(row["annotation"]["status"] == HUMAN_STATUS for row in rows)
    assert report["exactAgreementCaseCount"] == 60
    assert report["disagreementCaseCount"] == 0
    assert report["releaseGateEligible"] is False


def test_open_sheet_can_be_validated_after_label_edits(tmp_path: Path):
    sheet = tmp_path / "review-edited.open.jsonl"
    export_review_sheet(DATASET, sheet, reviewer_id="annotator-a")
    _fill_open_sheet(sheet, "annotator-a")
    manifest = validate_review_sheet(DATASET, sheet, require_complete=True)
    assert manifest["lifecycle"] == "OPEN"


def test_compare_reports_pre_adjudication_agreement_and_conflict_badcases(tmp_path: Path):
    sealed_a, _ = _export_pair(tmp_path)

    def mutate(row):
        if row["id"] == "cs-gold-v1-041":
            row["labels"]["intent"] = "PRODUCT_SEARCH"

    open_b = tmp_path / "review-b-conflict.open.jsonl"
    export_review_sheet(DATASET, open_b, reviewer_id="annotator-c", seed=31)
    _fill_open_sheet(open_b, "annotator-c", mutate=mutate)
    sealed_b = tmp_path / "review-b-conflict.sealed.jsonl"
    seal_review_sheet(DATASET, open_b, sealed_b)

    report = compare_human_reviews(DATASET, sealed_a, sealed_b)

    assert report["status"] == "PENDING_ADJUDICATION"
    assert report["releaseGateEligible"] is False
    assert report["caseCount"] == 60
    assert report["exactAgreementCaseCount"] == 59
    assert report["disagreementCaseCount"] == 1
    assert report["fieldStats"]["intent"]["agreementCount"] == 59
    assert report["fieldStats"]["intent"]["cohenKappa"] < 1
    conflict = report["disagreements"][0]
    assert conflict["caseId"] == "cs-gold-v1-041"
    rendered = json.dumps(report, ensure_ascii=False)
    assert '"expected":' not in rendered
    assert '"predicted":' not in rendered
    markdown = render_agreement_markdown(report)
    assert "cs-gold-v1-041" in markdown
    assert "模型准确率" in markdown


def test_merge_requires_adjudication_for_disagreement(tmp_path: Path):
    sealed_a, sealed_b = _export_pair(tmp_path)

    def mutate(row):
        if row["id"] == "cs-gold-v1-041":
            row["labels"]["intent"] = "PRODUCT_SEARCH"

    open_b = tmp_path / "review-b-disagree.open.jsonl"
    export_review_sheet(DATASET, open_b, reviewer_id="annotator-c", seed=31)
    _fill_open_sheet(open_b, "annotator-c", mutate=mutate)
    sealed_b_disagree = tmp_path / "review-b-disagree.sealed.jsonl"
    seal_review_sheet(DATASET, open_b, sealed_b_disagree)

    with pytest.raises(CustomerServiceReviewError, match="unresolved reviewer disagreements"):
        merge_human_reviews(
            DATASET,
            sealed_a,
            sealed_b_disagree,
            output_dataset_path=tmp_path / "should-not-exist.jsonl",
            evidence_path=tmp_path / "should-not-exist.json",
        )

    adjudication = tmp_path / "adjudication.jsonl"
    adjudication.write_text(
        json.dumps(
            {
                "schemaVersion": ADJUDICATION_SCHEMA,
                "id": "cs-gold-v1-041",
                "adjudicator": "lead-reviewer",
                "reason": "按问题句式和商品属性问法归类为商品咨询",
                "finalLabels": _labels(load_gold_dataset(DATASET)[40]),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "human-with-adjudication.jsonl"
    evidence = tmp_path / "human-with-adjudication.json"
    report = merge_human_reviews(
        DATASET,
        sealed_a,
        sealed_b_disagree,
        adjudication_path=adjudication,
        output_dataset_path=output,
        evidence_path=evidence,
    )
    assert report["disagreementCaseCount"] == 1
    assert report["disagreements"][0]["caseId"] == "cs-gold-v1-041"
    assert load_gold_dataset(output)[40]["annotation"]["adjudicator"] == "lead-reviewer"


def test_seal_rejects_model_or_gold_leak(tmp_path: Path):
    sheet = tmp_path / "leaky.open.jsonl"
    export_review_sheet(DATASET, sheet, reviewer_id="annotator-a")
    rows = load_jsonl(sheet)
    rows[0]["expected"] = {"intent": "CHAT"}
    atomic_write_jsonl(sheet, rows)
    with pytest.raises(CustomerServiceReviewError, match="leaks model/gold fields"):
        seal_review_sheet(DATASET, sheet, tmp_path / "leaky.sealed.jsonl")


def test_default_export_order_is_stable_but_reviewer_specific(tmp_path: Path):
    sheet_a = tmp_path / "a.open.jsonl"
    sheet_b = tmp_path / "b.open.jsonl"
    manifest_a = export_review_sheet(DATASET, sheet_a, reviewer_id="annotator-a")
    manifest_b = export_review_sheet(DATASET, sheet_b, reviewer_id="annotator-b")
    assert manifest_a["orderSeed"] != manifest_b["orderSeed"]


def test_seal_accepts_only_open_artifact(tmp_path: Path):
    open_sheet = tmp_path / "review.open.jsonl"
    export_review_sheet(DATASET, open_sheet, reviewer_id="annotator-a")
    _fill_open_sheet(open_sheet, "annotator-a")
    sealed = tmp_path / "review.sealed.jsonl"
    seal_review_sheet(DATASET, open_sheet, sealed)
    with pytest.raises(CustomerServiceReviewError, match="only OPEN"):
        seal_review_sheet(DATASET, sealed, tmp_path / "second.sealed.jsonl")


def test_nested_model_field_leak_is_rejected(tmp_path: Path):
    sheet = tmp_path / "leaky-nested.open.jsonl"
    export_review_sheet(DATASET, sheet, reviewer_id="annotator-a")
    rows = load_jsonl(sheet)
    rows[0]["comment"] = {"notes": {"expected": {"intent": "CHAT"}}}
    atomic_write_jsonl(sheet, rows)
    with pytest.raises(CustomerServiceReviewError, match="leaks model/gold fields"):
        seal_review_sheet(DATASET, sheet, tmp_path / "leaky-nested.sealed.jsonl")


def test_blinded_sheet_rejects_derived_context_leak(tmp_path: Path):
    sheet = tmp_path / "derived-context.open.jsonl"
    export_review_sheet(DATASET, sheet, reviewer_id="annotator-a")
    rows = load_jsonl(sheet)
    rows[0]["sliceTags"] = ["critical-handoff"]
    atomic_write_jsonl(sheet, rows)
    with pytest.raises(CustomerServiceReviewError, match="unknown review fields"):
        seal_review_sheet(DATASET, sheet, tmp_path / "derived-context.sealed.jsonl")


def test_adjudication_cannot_override_agreement(tmp_path: Path):
    sealed_a, sealed_b = _export_pair(tmp_path)
    adjudication = tmp_path / "unneeded-adjudication.jsonl"
    adjudication.write_text(
        json.dumps(
            {
                "schemaVersion": ADJUDICATION_SCHEMA,
                "id": "cs-gold-v1-001",
                "adjudicator": "lead-reviewer",
                "reason": "无冲突时不应允许覆盖",
                "finalLabels": _labels(load_gold_dataset(DATASET)[0]),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CustomerServiceReviewError, match="only allowed for reviewer disagreements"):
        merge_human_reviews(
            DATASET,
            sealed_a,
            sealed_b,
            adjudication_path=adjudication,
            output_dataset_path=tmp_path / "should-not-exist.jsonl",
            evidence_path=tmp_path / "should-not-exist.json",
        )
