from pathlib import Path

from evaluation.core.io import load_json
from evaluation.customer_service_gold import apply_label_evidence_validity
from evaluation.customer_service_label_audit import (
    audit_label_consistency,
    build_label_audit_package,
    verify_label_audit_package,
)

ROOT = Path(__file__).resolve().parents[1]
V2_PACKAGE = (
    ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-human-v2-provenance-pending-20260826"
)
DATASET = V2_PACKAGE / "labels" / "customer-service-human-v2.jsonl"
PROVENANCE = V2_PACKAGE / "provenance-audit.json"
TAXONOMY = (
    ROOT
    / "evaluation"
    / "datasets"
    / "customer_service"
    / "customer-service-taxonomy-contract-v2.1.json"
)


def test_v2_label_audit_fails_closed_on_taxonomy_and_slot_policy_collisions():
    audit = audit_label_consistency(
        DATASET,
        taxonomy_contract_path=TAXONOMY,
        provenance_audit_path=PROVENANCE,
    )

    assert audit["status"] == "BLOCKED_HUMAN_READJUDICATION"
    assert audit["gates"]["releaseGateEligible"] is False
    findings = {item["code"]: item for item in audit["findings"]}
    assert set(findings) == {
        "TAXONOMY_RECOMMENT_ACTION_COLLISION",
        "SLOT_AMOUNT_SPAN_POLICY_SPLIT",
        "SLOT_BUDGET_COMPLETENESS_SPLIT",
        "SLOT_QUANTITY_SCOPE_SPLIT",
        "SLOT_PRODUCT_FEATURE_COMPOSITION_SPLIT",
    }
    assert findings["TAXONOMY_RECOMMENT_ACTION_COLLISION"]["caseIds"] == [
        "cs-candidate-v2-112",
        "cs-candidate-v2-113",
        "cs-candidate-v2-114",
    ]
    assert audit["metricValidity"]["answerCorrectness"] == "NOT_MEASURED_BY_INPUT_GOLD"


def test_label_audit_package_is_checksum_bound_and_never_release_eligible(tmp_path):
    output = tmp_path / "label-audit"
    result = build_label_audit_package(
        DATASET,
        taxonomy_contract_path=TAXONOMY,
        provenance_audit_path=PROVENANCE,
        output_dir=output,
    )

    assert result["releaseGateEligible"] is False
    verified = verify_label_audit_package(output)
    assert verified == {
        "valid": True,
        "status": "VERIFIED",
        "errors": [],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
    }
    assert "expected" not in (output / "reaudit" / "label-policy-reaudit.open.jsonl").read_text()


def test_gold_report_inherits_fail_closed_label_and_provenance_gates():
    report = load_json(
        ROOT
        / "run"
        / "evaluation-observations"
        / "customer-service-human-v2-post-routing-fix.report.json"
    )
    audit_path = (
        ROOT
        / "evaluation-evidence"
        / "benchmarks"
        / "customer-service"
        / "customer-service-human-v2-label-consistency-audit-20260826"
        / "label-consistency-audit.json"
    )

    guarded = apply_label_evidence_validity(report, audit_path)

    assert guarded["qualityClaimStatus"] == "DEVELOPMENT_DIAGNOSTIC_LABEL_AND_PROVENANCE_BLOCKED"
    assert guarded["evidenceValidity"]["blocking"] is True
    assert guarded["humanReviewPlan"]["adjudicationComplete"] is False
    assert guarded["metrics"]["intentMacroF1"]["validityStatus"] == "CONFOUNDED_BY_TAXONOMY_COLLISION"
    assert guarded["releaseGateEligible"] is False
