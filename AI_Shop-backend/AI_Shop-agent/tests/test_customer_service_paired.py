from pathlib import Path

from evaluation.customer_service_paired import (
    build_paired_evidence_package,
    compare_customer_service_reports,
    verify_paired_evidence_package,
)

ROOT = Path(__file__).resolve().parents[1]
BEFORE = (
    ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-http-v32-human-v2-pre-fix-20260826"
    / "report.json"
)
AFTER = ROOT / "run" / "evaluation-observations" / "customer-service-human-v2-post-routing-fix.report.json"
LABEL_AUDIT = (
    ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-human-v2-label-consistency-audit-20260826"
    / "label-consistency-audit.json"
)


def test_paired_report_shows_material_fix_but_remains_non_release():
    report = compare_customer_service_reports(BEFORE, AFTER, label_audit_path=LABEL_AUDIT)

    assert report["comparisonDesign"]["caseCount"] == 120
    assert report["binaryPairedMetrics"]["handoffRecall"]["before"]["numerator"] == 22
    assert report["binaryPairedMetrics"]["handoffRecall"]["after"]["numerator"] == 32
    assert report["binaryPairedMetrics"]["criticalHandoffSuccess"]["regressionCount"] == 0
    assert report["aggregatePairedMetrics"]["intentMacroF1"]["absoluteDelta"] > 0.20
    assert report["aggregatePairedMetrics"]["slotEntitySpanF1"]["absoluteDelta"] > 0.20
    assert report["regressionGuard"]["passed"] is True
    assert report["gates"]["developmentFixValidated"] is True
    assert report["gates"]["labelConsistencyPassed"] is False
    assert report["gates"]["releaseGateEligible"] is False


def test_paired_package_is_reproducible_and_checksum_bound(tmp_path, monkeypatch):
    # Package mechanics do not need to repeat the full statistical workload;
    # the preceding test exercises the preregistered production sample count.
    monkeypatch.setattr("evaluation.customer_service_paired._BOOTSTRAP_SAMPLES", 100)
    output = tmp_path / "paired"
    result = build_paired_evidence_package(
        BEFORE,
        AFTER,
        label_audit_path=LABEL_AUDIT,
        output_dir=output,
    )

    assert result["developmentFixValidated"] is True
    assert verify_paired_evidence_package(output) == {
        "valid": True,
        "status": "VERIFIED",
        "errors": [],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
    }
