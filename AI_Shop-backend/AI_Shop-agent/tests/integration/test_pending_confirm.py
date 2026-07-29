"""待确认操作 confirm 行为对齐 Java。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.pending_action_service import PendingActionService
from app.services.pending_action_store import pending_action_store


@pytest.fixture
def service():
    return PendingActionService()

@pytest.mark.asyncio
async def test_confirm_success_persists_result(service):
    # claim() 在 DB 侧已将 status 写为 EXECUTING(3)，返回 post-UPDATE 行
    pending = {
        "token": "act_test",
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "paramsJson": json.dumps({"orderId": "o1"}),
        "status": 3,
    }

    async def executor(p):
        assert p["token"] == "act_test"
        return "订单 o1 已确认收货"

    final = {**pending, "status": 1, "resultMessage": "订单 o1 已确认收货"}
    with patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))):
        with patch.object(pending_action_store, "complete", AsyncMock(return_value=final)):
            with patch("app.services.pending_action_service.redis_service") as redis:
                redis.ensure_connected = AsyncMock()
                redis.try_lock_pending_action = AsyncMock(return_value=True)
                redis.unlock_pending_action = AsyncMock()
                redis.save_pending_action = AsyncMock()
                action_type, ok, msg = await service.confirm("u1", "act_test", executor)

    assert action_type == "CONFIRM_RECEIPT"
    assert ok is True
    assert "确认收货" in msg
    redis.save_pending_action.assert_awaited_once_with("act_test", final)

@pytest.mark.asyncio
async def test_confirm_failure_persists_error(service):
    # claim() 在 DB 侧已将 status 写为 EXECUTING(3)，返回 post-UPDATE 行
    pending = {
        "token": "act_fail",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": "{}",
        "status": 3,
    }

    async def executor(_):
        raise ValueError("退款失败：订单状态不允许")

    with patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))):
        with patch.object(
            pending_action_store,
            "complete",
            AsyncMock(return_value={**pending, "status": 4, "errorMessage": "退款失败：订单状态不允许"}),
        ):
            with patch("app.services.pending_action_service.redis_service") as redis:
                redis.ensure_connected = AsyncMock()
                redis.try_lock_pending_action = AsyncMock(return_value=True)
                redis.unlock_pending_action = AsyncMock()
                redis.save_pending_action = AsyncMock()
                action_type, ok, msg = await service.confirm("u1", "act_fail", executor)

    assert action_type == "REFUND"
    assert ok is False
    assert "退款失败" in msg
    redis.save_pending_action.assert_awaited_once()

@pytest.mark.asyncio
async def test_confirm_idempotent_on_processed(service):
    pending = {
        "token": "act_done",
        "userId": "u1",
        "actionType": "REFUND",
        "status": 1,
        "resultMessage": "退款已处理",
    }

    with patch.object(
        pending_action_store,
        "claim",
        AsyncMock(return_value=(False, pending)),
    ):
        with patch("app.services.pending_action_service.redis_service") as redis:
            redis.ensure_connected = AsyncMock()
            redis.try_lock_pending_action = AsyncMock(return_value=True)
            redis.unlock_pending_action = AsyncMock()
            action_type, ok, msg = await service.confirm("u1", "act_done", AsyncMock())

    assert action_type == "REFUND"
    assert ok is True
    assert msg == "退款已处理"


@pytest.mark.asyncio
async def test_confirm_returns_saved_result_when_completion_lookup_is_needed(service):
    # claim() 在 DB 侧已将 status 写为 EXECUTING(3)，返回 post-UPDATE 行
    pending = {
        "token": "act_race",
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "status": 3,
    }
    saved = {
        **pending,
        "status": 1,
        "resultMessage": "已保存的确认结果",
    }

    with patch.object(
        pending_action_store,
        "claim",
        AsyncMock(return_value=(True, pending)),
    ):
        with patch.object(
            pending_action_store,
            "complete",
            AsyncMock(return_value=None),
        ):
            with patch.object(
                pending_action_store,
                "get",
                AsyncMock(return_value=saved),
            ):
                with patch("app.services.pending_action_service.redis_service") as redis:
                    redis.ensure_connected = AsyncMock()
                    redis.try_lock_pending_action = AsyncMock(return_value=True)
                    redis.unlock_pending_action = AsyncMock()
                    redis.save_pending_action = AsyncMock()
                    action_type, ok, msg = await service.confirm(
                        "u1",
                        "act_race",
                        AsyncMock(return_value="本地执行结果"),
                    )

    assert action_type == "CONFIRM_RECEIPT"
    assert ok is True
    assert msg == "已保存的确认结果"
    redis.save_pending_action.assert_awaited_once_with("act_race", saved)
