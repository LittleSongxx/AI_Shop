from evaluation.customer_service_annotation_audit import (
    _load_intent_agreement,
    build_annotation_audit,
)


def test_intent_audit_distinguishes_case_and_field_agreement():
    agreement = _load_intent_agreement()

    assert agreement["caseCount"] == 60
    assert agreement["exactAgreementCaseCount"] == 35
    assert agreement["fieldStats"]["intent"]["agreementCount"] == 57
    assert agreement["fieldStats"]["slots"]["agreementCount"] == 45
    assert agreement["fieldStats"]["intent"]["cohenKappa"] > 0.9


def test_answer_evidence_audit_keeps_unverifiable_total_separate_from_correct_subset():
    report = build_annotation_audit()
    answer = report["answerReviewAudit"]

    assert answer["labelEvidenceStatusCounts"]["UNVERIFIABLE_RUNTIME_FACT"] == 24
    assert len(answer["unverifiableRuntimeFactCaseIds"]) == 24
    assert len(answer["unverifiableDespiteCorrectLabel"]) == 19


def test_slot_provenance_does_not_call_explicit_quantity_a_hallucination():
    report = build_annotation_audit()
    provenance = report["intentSlotAudit"]["slotProvenanceAudit"]

    assert provenance["unsupportedCaseIds"] == []
    assert provenance["provenanceCounts"]["DERIVED_ALLOWED_RULE"] == 1
    assert provenance["derivedAllowedCases"][0]["caseId"] == "cs-gold-v1-055"


def test_taxonomy_review_exposes_payment_policy_and_risk_policy_boundaries():
    report = build_annotation_audit()
    cases = {
        item["caseId"]: item
        for item in report["intentSlotAudit"]["potentialTaxonomyDisputes"]
    }

    assert cases["cs-gold-v1-049"]["status"] == "TAXONOMY_GAP"
    assert cases["cs-gold-v1-014"]["status"] == "POLICY_CHOICE"
    assert report["annotationQualityAssessment"]["confirmedMislabelCaseIds"] == []
