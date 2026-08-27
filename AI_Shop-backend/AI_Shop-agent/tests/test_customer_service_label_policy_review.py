from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.core.io import (
    atomic_write_json,
    atomic_write_jsonl,
    load_json,
    load_jsonl,
    sha256_file,
)
from evaluation.customer_service_label_policy_review import (
    CustomerServiceLabelPolicyReviewError,
    compare_label_policy_reviews,
    seal_label_policy_review_sheet,
    validate_label_policy_review_sheet,
    verify_pending_label_policy_review_evidence,
    write_pending_label_policy_review_evidence,
)


def _labels(*, intent: str = "PRODUCT_SEARCH") -> dict[str, object]:
    return {
        "intent": intent,
        "riskLevel": "LOW",
        "shouldHandoff": False,
        "handoffSeverity": None,
        "slots": {"productName": "耳机"},
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "source.jsonl"
    template = tmp_path / "template.jsonl"
    taxonomy = tmp_path / "taxonomy.json"
    context = tmp_path / "context.jsonl"
    source_rows = [
        {"id": "case-1", "input": {"message": "推荐耳机"}, "expected": _labels()},
        {"id": "case-2", "input": {"message": "比较耳机"}, "expected": _labels()},
    ]
    atomic_write_jsonl(source, source_rows)
    template_rows = [
        {
            "schemaVersion": "aishop-customer-service-label-policy-reaudit/v1",
            "id": row["id"],
            "input": row["input"],
            "reviewerId": "UNASSIGNED",
            "guidelinesVersion": "customer-service-taxonomy-v2.1",
            "labels": {
                "intent": None,
                "riskLevel": None,
                "shouldHandoff": None,
                "handoffSeverity": None,
                "slots": None,
            },
            "comment": "",
        }
        for row in source_rows
    ]
    atomic_write_jsonl(template, template_rows)
    atomic_write_json(
        taxonomy,
        {
            "contractVersion": "customer-service-taxonomy-v2.1",
            "intents": {"PRODUCT_SEARCH": {}, "PRODUCT_CONSULT": {}},
        },
    )
    atomic_write_jsonl(
        context,
        [
            {
                "schemaVersion": "aishop-customer-service-label-policy-reaudit/v1",
                "id": row["id"],
                "input": row["input"],
                "currentImmutableExpected": row["expected"],
                "issueCodes": ["TEST_POLICY"],
                "useOnlyAfterBothBlindSheetsAreSealed": True,
            }
            for row in source_rows
        ],
    )
    return source, template, taxonomy, context


def _review(
    tmp_path: Path,
    source: Path,
    template: Path,
    taxonomy: Path,
    *,
    reviewer: str,
    second_intent: str,
) -> Path:
    path = tmp_path / "delivery" / f"{reviewer}.open.jsonl"
    rows = load_jsonl(template)
    for row in rows:
        row["reviewerId"] = reviewer
        row["labels"] = _labels(
            intent=second_intent if row["id"] == "case-2" else "PRODUCT_SEARCH"
        )
    atomic_write_jsonl(path, rows)
    atomic_write_json(
        path.with_suffix(path.suffix + ".manifest.json"),
        {
            "schemaVersion": "aishop-customer-service-label-policy-reaudit/v1",
            "artifact": "BLINDED_LABEL_POLICY_REVIEW_SHEET",
            "lifecycle": "OPEN",
            "caseCount": 2,
            "reviewerId": reviewer,
            "sheetPath": str(path.resolve()),
            "sheetSha256": "0" * 64,
            "sourceDatasetSha256": sha256_file(source),
            "sourceTemplateSha256": sha256_file(template),
            "taxonomyContractSha256": sha256_file(taxonomy),
            "labelSchema": {
                "intentValues": ["PRODUCT_SEARCH", "PRODUCT_CONSULT"],
            },
        },
    )
    return path


def test_label_policy_return_seal_compare_and_pending_package(tmp_path: Path) -> None:
    source, template, taxonomy, context = _fixture(tmp_path)
    exported_a = _review(
        tmp_path,
        source,
        template,
        taxonomy,
        reviewer="reviewer-a",
        second_intent="PRODUCT_SEARCH",
    )
    exported_b = _review(
        tmp_path,
        source,
        template,
        taxonomy,
        reviewer="reviewer-b",
        second_intent="PRODUCT_CONSULT",
    )
    returned = tmp_path / "intake" / exported_a.name
    returned.parent.mkdir()
    returned.write_bytes(exported_a.read_bytes())
    returned.with_suffix(returned.suffix + ".manifest.json").write_bytes(
        exported_a.with_suffix(exported_a.suffix + ".manifest.json").read_bytes()
    )
    assert validate_label_policy_review_sheet(
        source, template, taxonomy, returned, require_complete=True
    )["reviewerId"] == "reviewer-a"

    sealed_a = tmp_path / "sealed-a.jsonl"
    sealed_b = tmp_path / "sealed-b.jsonl"
    seal_label_policy_review_sheet(source, template, taxonomy, returned, sealed_a)
    seal_label_policy_review_sheet(source, template, taxonomy, exported_b, sealed_b)
    agreement = compare_label_policy_reviews(
        source, template, taxonomy, sealed_a, sealed_b
    )
    assert agreement["exactAgreementCaseCount"] == 1
    assert agreement["disagreementCaseCount"] == 1

    pending = tmp_path / "pending"
    editable = tmp_path / "adjudication.open.jsonl"
    result = write_pending_label_policy_review_evidence(
        source,
        template,
        taxonomy,
        context,
        sealed_a,
        sealed_b,
        output_dir=pending,
        adjudication_output=editable,
    )
    assert result["valid"] is True
    assert result["disagreementCaseCount"] == 1
    assert len(load_jsonl(editable)) == 1
    assert verify_pending_label_policy_review_evidence(pending)["valid"] is True


def test_label_policy_review_rejects_modified_source_text(tmp_path: Path) -> None:
    source, template, taxonomy, _context = _fixture(tmp_path)
    review = _review(
        tmp_path,
        source,
        template,
        taxonomy,
        reviewer="reviewer-a",
        second_intent="PRODUCT_SEARCH",
    )
    rows = load_jsonl(review)
    rows[0]["input"]["message"] = "被修改的题面"
    atomic_write_jsonl(review, rows)
    with pytest.raises(
        CustomerServiceLabelPolicyReviewError, match="immutable review fields"
    ):
        validate_label_policy_review_sheet(
            source, template, taxonomy, review, require_complete=True
        )


def test_pending_label_policy_package_rejects_checksum_tampering(
    tmp_path: Path,
) -> None:
    source, template, taxonomy, context = _fixture(tmp_path)
    review_a = _review(
        tmp_path,
        source,
        template,
        taxonomy,
        reviewer="reviewer-a",
        second_intent="PRODUCT_SEARCH",
    )
    review_b = _review(
        tmp_path,
        source,
        template,
        taxonomy,
        reviewer="reviewer-b",
        second_intent="PRODUCT_CONSULT",
    )
    sealed_a = tmp_path / "sealed-a.jsonl"
    sealed_b = tmp_path / "sealed-b.jsonl"
    seal_label_policy_review_sheet(source, template, taxonomy, review_a, sealed_a)
    seal_label_policy_review_sheet(source, template, taxonomy, review_b, sealed_b)
    pending = tmp_path / "pending"
    write_pending_label_policy_review_evidence(
        source,
        template,
        taxonomy,
        context,
        sealed_a,
        sealed_b,
        output_dir=pending,
    )
    os_manifest = pending / "lifecycle.json"
    os_manifest.chmod(0o644)
    lifecycle = load_json(os_manifest)
    lifecycle["releaseGateEligible"] = True
    atomic_write_json(os_manifest, lifecycle)
    with pytest.raises(CustomerServiceLabelPolicyReviewError, match="checksum"):
        verify_pending_label_policy_review_evidence(pending)
