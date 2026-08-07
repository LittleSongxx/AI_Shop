from app.services.episode_evaluator import (
    evaluate_order_aftersales_episode,
    recursive_subset_match,
)


def _episode(**signals):
    return {
        "status": "SUCCEEDED",
        "scenario": "ORDER_AFTERSALES",
        "datasetEligible": "UNREVIEWED",
        "quality": {"verifierPassed": True},
        "rewardSignals": signals,
    }


def test_cancel_episode_requires_confirmation_and_known_terminal_outcome():
    proposed = evaluate_order_aftersales_episode(
        _episode(
            actionType="CANCEL_ORDER",
            actionProposed=True,
            userConfirmed=False,
        )
    )
    assert proposed["verdict"] == "AWAITING_CONFIRMATION"
    assert proposed["reviewEligible"] is False

    unknown = evaluate_order_aftersales_episode(
        _episode(
            actionType="CANCEL_ORDER",
            actionProposed=True,
            userConfirmed=True,
            remoteOutcomeKnown=False,
            outcome="MANUAL_REVIEW",
        )
    )
    assert unknown["verdict"] == "OUTCOME_UNKNOWN"
    assert unknown["reviewEligible"] is False

    complete = evaluate_order_aftersales_episode(
        _episode(
            actionType="CANCEL_ORDER",
            actionProposed=True,
            userConfirmed=True,
            remoteOutcomeKnown=True,
            outcome="CONFIRMED",
            orderStatusAfter=4,
        )
    )
    assert complete["verdict"] == "CANCEL_CONFIRMED"
    assert complete["reviewEligible"] is True
    assert complete["trainingEligible"] is False


def test_resolved_support_case_requires_structured_human_result():
    open_case = evaluate_order_aftersales_episode(
        _episode(
            actionType="CREATE_SUPPORT_CASE",
            caseCreated=True,
            caseStatus="OPEN",
        )
    )
    assert open_case["verdict"] == "SUPPORT_CASE_OPEN"
    assert open_case["reviewEligible"] is False

    resolved = _episode(
        actionType="CREATE_SUPPORT_CASE",
        caseCreated=True,
        caseStatus="RESOLVED",
        humanResolved=True,
        humanResolutionCode="REFUND_COMPLETED",
        humanRootCause="DAMAGED_IN_TRANSIT",
        humanResolutionSummaryFingerprint={"sha256": "a" * 64, "chars": 18},
    )
    resolved["datasetEligible"] = "APPROVED"
    evaluation = evaluate_order_aftersales_episode(resolved)
    assert evaluation["verdict"] == "SUPPORT_CASE_RESOLVED"
    assert evaluation["reviewEligible"] is True
    assert evaluation["trainingEligible"] is True


def test_verifier_failure_blocks_review_even_with_known_domain_outcome():
    episode = _episode(
        actionType="CANCEL_ORDER",
        actionProposed=True,
        userConfirmed=True,
        remoteOutcomeKnown=True,
        outcome="CONFIRMED",
    )
    episode["rewardSignals"]["verifier"] = {
        "passed": False,
        "action": "DEGRADE",
        "issueCodes": ["DYNAMIC_FACT_WITHOUT_TOOL"],
    }
    evaluation = evaluate_order_aftersales_episode(episode)
    assert evaluation["verdict"] == "VERIFIER_FAILED"
    assert evaluation["reviewEligible"] is False


def test_recursive_subset_match_reports_nested_paths():
    actual = {"intent": "CANCEL_ORDER", "entities": {"orderId": "123"}}
    assert recursive_subset_match({"intent": "CANCEL_ORDER"}, actual) == []
    assert recursive_subset_match({"entities": {"status": "PAID"}}, actual) == [
        "$.entities.status: missing"
    ]
