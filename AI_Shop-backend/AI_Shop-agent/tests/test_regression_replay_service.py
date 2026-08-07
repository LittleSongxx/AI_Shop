from unittest.mock import AsyncMock

import pytest

from app.services.badcase_service import badcase_service
from app.services.regression_replay_service import RegressionReplayService


@pytest.mark.asyncio
async def test_text_regression_replay_uses_deterministic_intent_rules():
    service = RegressionReplayService()
    result = await service.run_case(
        {
            "case_id": 1,
            "name": "cancel intent",
            "input": {"userMessage": "我想取消这个待付款订单"},
            "expected": {"intent": "CANCEL_ORDER", "intentType": "CANCEL_ORDER"},
        }
    )

    assert result["replayType"] == "TEXT_INTENT"
    assert result["result"] == "PASS"
    assert result["actual"]["source"] in {
        "structural",
        "rule",
        "rule_priority",
        "rule_fallback",
    }


@pytest.mark.asyncio
async def test_episode_regression_replay_uses_fact_evaluator():
    service = RegressionReplayService()
    result = await service.run_case(
        {
            "case_id": 2,
            "name": "known cancel outcome",
            "input": {
                "episode": {
                    "status": "SUCCEEDED",
                    "scenario": "ORDER_AFTERSALES",
                    "datasetEligible": "UNREVIEWED",
                    "quality": {"verifierPassed": True},
                    "rewardSignals": {
                        "actionType": "CANCEL_ORDER",
                        "actionProposed": True,
                        "userConfirmed": True,
                        "remoteOutcomeKnown": True,
                        "outcome": "CONFIRMED",
                    },
                }
            },
            "expected": {"verdict": "CANCEL_CONFIRMED", "reviewEligible": True},
        }
    )

    assert result["replayType"] == "EPISODE"
    assert result["result"] == "PASS"


@pytest.mark.asyncio
async def test_run_active_records_each_result(monkeypatch):
    service = RegressionReplayService()
    monkeypatch.setattr(
        service,
        "_load_active",
        AsyncMock(
            return_value=[
                {
                    "case_id": 3,
                    "name": "mismatch",
                    "input": {"userMessage": "取消订单"},
                    "expected": {"intent": "QUERY_LOGISTICS"},
                }
            ]
        ),
    )
    record = AsyncMock()
    monkeypatch.setattr(badcase_service, "record_regression_result", record)

    summary = await service.run_active()

    assert summary["total"] == 1
    assert summary["failed"] == 1
    record.assert_awaited_once_with(3, "FAIL")
