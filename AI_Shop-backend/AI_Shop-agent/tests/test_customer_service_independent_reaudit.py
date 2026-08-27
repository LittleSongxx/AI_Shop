from __future__ import annotations

from pathlib import Path

from evaluation.core.io import atomic_write_json, atomic_write_jsonl, load_jsonl, sha256_file
from evaluation.customer_service_independent_reaudit import (
    build_independent_reaudit_expansion_handoff,
    score_independent_reaudit,
    verify_independent_reaudit_evidence,
    write_independent_reaudit_evidence,
)


def _labels(index: int, *, include_slot: bool = True) -> dict[str, object]:
    return {
        "intent": "PRODUCT_CONSULT",
        "riskLevel": "LOW",
        "shouldHandoff": False,
        "handoffSeverity": None,
        "slots": {"productName": f"商品{index}"} if include_slot else {},
    }


def _fixture(tmp_path: Path, *, identity_matches: bool = True) -> tuple[Path, ...]:
    source = tmp_path / "source.jsonl"
    template = tmp_path / "initial.open.jsonl"
    returned = tmp_path / "returned" / "initial.open.jsonl"
    attestation = tmp_path / "attestation.json"
    guideline = tmp_path / "guideline.md"
    source_rows = [
        {
            "id": f"case-{index:02d}",
            "input": {"message": f"咨询商品{index}"},
            "expected": _labels(index),
        }
        for index in range(60)
    ]
    atomic_write_jsonl(source, source_rows)
    template_rows = [
        {
            "schemaVersion": "aishop-customer-service-independent-reaudit/v1",
            "id": row["id"],
            "input": row["input"],
            "reviewerId": "UNASSIGNED_INDEPENDENT_REVIEWER",
            "labels": {
                "intent": None,
                "riskLevel": None,
                "shouldHandoff": None,
                "handoffSeverity": None,
                "slots": None,
            },
            "comment": "",
        }
        for row in source_rows[:12]
    ]
    atomic_write_jsonl(template, template_rows)
    atomic_write_json(
        template.with_suffix(template.suffix + ".manifest.json"),
        {
            "schemaVersion": "aishop-customer-service-independent-reaudit/v1",
            "artifact": "BLINDED_INDEPENDENT_REAUDIT_SHEET",
            "lifecycle": "OPEN_UNASSIGNED",
            "sourceHumanDatasetSha256": sha256_file(source),
            "sheetSha256": sha256_file(template),
            "caseCount": 12,
            "preregisteredAcceptance": {
                "criticalMismatchCount": 0,
                "intentAgreementMinimum": 0.8,
                "riskAgreementMinimum": 0.9,
                "handoffAgreementMinimum": 0.9,
                "slotExactAgreementMinimum": 0.7,
                "failureAction": "EXPAND_TO_FULL_60_CASE_INDEPENDENT_REAUDIT",
            },
        },
    )
    returned_rows = load_jsonl(template)
    for index, row in enumerate(returned_rows):
        row["reviewerId"] = "human-reviewer-c"
        row["labels"] = _labels(index, include_slot=index < 6)
    atomic_write_jsonl(returned, returned_rows)
    atomic_write_json(
        returned.with_suffix(returned.suffix + ".manifest.json"),
        {
            "schemaVersion": "aishop-customer-service-independent-reaudit/v1",
            "artifact": "BLINDED_INDEPENDENT_REAUDIT_SHEET",
            "lifecycle": "OPEN_UNASSIGNED",
            "sourceHumanDatasetSha256": sha256_file(source),
            "sheetSha256": sha256_file(template),
            "caseCount": 12,
            "preregisteredAcceptance": {
                "criticalMismatchCount": 0,
                "intentAgreementMinimum": 0.8,
                "riskAgreementMinimum": 0.9,
                "handoffAgreementMinimum": 0.9,
                "slotExactAgreementMinimum": 0.7,
                "failureAction": "EXPAND_TO_FULL_60_CASE_INDEPENDENT_REAUDIT",
            },
        },
    )
    atomic_write_json(
        attestation,
        {
            "schemaVersion": "aishop-independent-reaudit-custody-attestation/v1",
            "reviewerIdentity": (
                "human-reviewer-c" if identity_matches else "different-person"
            ),
            "custodianIdentity": "custodian-x",
            "reviewerDidNotViewDraftExpectedOrModelOutputs": True,
            "reviewerDidNotViewPriorReviewsAgreementOrAdjudication": True,
            "reviewerDidNotViewSourceHumanLabels": True,
            "reviewerIndependentOfDatasetAndModelDevelopment": True,
            "status": "COMPLETE",
        },
    )
    guideline.write_text("frozen rules\n", encoding="utf-8")
    return source, template, returned, attestation, guideline


def test_failed_initial_reaudit_is_archived_and_expanded(tmp_path: Path) -> None:
    source, template, returned, attestation, guideline = _fixture(tmp_path)
    result = score_independent_reaudit(source, template, returned, attestation)
    assert result["metrics"]["slotExactAgreement"] == 0.5
    assert result["gates"]["slotExactAgreementPassed"] is False
    assert result["status"] == "EXPANSION_REQUIRED"
    assert result["attestation"]["valid"] is True

    evidence = tmp_path / "evidence"
    written = write_independent_reaudit_evidence(
        source,
        template,
        returned,
        attestation,
        guideline,
        output_dir=evidence,
    )
    assert written["valid"] is True
    assert verify_independent_reaudit_evidence(evidence)["labelGatePassed"] is False

    expansion = tmp_path / "expansion"
    handoff = build_independent_reaudit_expansion_handoff(
        source,
        template,
        returned,
        guideline,
        output_dir=expansion,
    )
    assert handoff["remainingCaseCount"] == 48
    assert len(load_jsonl(expansion / "remaining-48.open.jsonl")) == 48
    assert len(load_jsonl(expansion / "full-60-restart.open.jsonl")) == 60


def test_v1_attestation_must_bind_the_sheet_reviewer_identity(tmp_path: Path) -> None:
    source, template, returned, attestation, _guideline = _fixture(
        tmp_path, identity_matches=False
    )
    result = score_independent_reaudit(source, template, returned, attestation)
    assert result["attestation"]["valid"] is False
    assert any(
        item["code"] == "SHEET_REVIEWER_ID_NOT_BOUND_TO_ATTESTED_IDENTITY"
        for item in result["attestation"]["findings"]
    )
