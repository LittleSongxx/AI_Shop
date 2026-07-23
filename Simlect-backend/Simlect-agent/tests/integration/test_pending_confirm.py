"""待确认操作 confirm 行为对齐 Java。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.pending_action_service import PendingActionService


@pytest.fixture
def service():
    return PendingActionService()

@pytest.mark.asyncio
async def test_confirm_success_deletes_pending(service):
    pending = {
        "token": "act_test",
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "paramsJson": json.dumps({"orderId": "o1"}),
        "status": 0,
    }

    async def executor(p):
        assert p["token"] == "act_test"
        return "订单 o1 已确认收货"

    with patch.object(service, "load_owned", AsyncMock(return_value=pending)):
        with patch("app.services.pending_action_service.redis_service") as redis:
            redis.ensure_connected = AsyncMock()
            redis.try_lock_pending_action = AsyncMock(return_value=True)
            redis.unlock_pending_action = AsyncMock()
            redis.delete_pending_action = AsyncMock()
            action_type, ok, msg = await service.confirm("u1", "act_test", executor)

    assert action_type == "CONFIRM_RECEIPT"
    assert ok is True
    assert "确认收货" in msg
    redis.delete_pending_action.assert_awaited_once_with("act_test")

@pytest.mark.asyncio
async def test_confirm_failure_keeps_pending(service):
    pending = {
        "token": "act_fail",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": "{}",
        "status": 0,
    }

    async def executor(_):
        raise ValueError("退款失败：订单状态不允许")

    with patch.object(service, "load_owned", AsyncMock(return_value=pending)):
        with patch("app.services.pending_action_service.redis_service") as redis:
            redis.ensure_connected = AsyncMock()
            redis.try_lock_pending_action = AsyncMock(return_value=True)
            redis.unlock_pending_action = AsyncMock()
            redis.delete_pending_action = AsyncMock()
            action_type, ok, msg = await service.confirm("u1", "act_fail", executor)

    assert action_type == "REFUND"
    assert ok is False
    assert "退款失败" in msg
    redis.delete_pending_action.assert_not_awaited()

@pytest.mark.asyncio
async def test_confirm_idempotent_on_processed(service):
    pending = {"token": "act_done", "userId": "u1", "status": 1}

    with patch.object(service, "load_owned", AsyncMock(return_value=pending)):
        with patch("app.services.pending_action_service.redis_service") as redis:
            redis.ensure_connected = AsyncMock()
            redis.try_lock_pending_action = AsyncMock(return_value=True)
            redis.unlock_pending_action = AsyncMock()
            with pytest.raises(ValueError, match="已处理"):
                await service.confirm("u1", "act_done", AsyncMock())
