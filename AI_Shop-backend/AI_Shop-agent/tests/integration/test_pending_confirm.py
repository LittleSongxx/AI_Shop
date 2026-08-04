"""待确认操作 confirm 行为对齐 Java。"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.exceptions import RemoteActionRejected
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
    assert redis.save_pending_action.await_count == 2
    redis.save_pending_action.assert_any_await("act_test", pending)
    redis.save_pending_action.assert_any_await("act_test", final)

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
    assert redis.save_pending_action.await_count == 2

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
    assert redis.save_pending_action.await_count == 2
    redis.save_pending_action.assert_any_await("act_race", saved)


@pytest.mark.asyncio
async def test_uncertain_transport_failure_stays_executing_for_reconciliation(service):
    pending = {
        "token": "act_uncertain",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": json.dumps({"orderItemId": "item-1"}),
        "status": 3,
        "statusName": "EXECUTING",
    }

    async def executor(_):
        raise ConnectionError("response connection closed")

    with patch.object(
        pending_action_store,
        "claim",
        AsyncMock(return_value=(True, pending)),
    ):
        with patch.object(
            pending_action_store,
            "complete",
            AsyncMock(),
        ) as complete:
            with patch("app.services.pending_action_service.redis_service") as redis:
                redis.ensure_connected = AsyncMock()
                redis.try_lock_pending_action = AsyncMock(return_value=True)
                redis.unlock_pending_action = AsyncMock()
                redis.save_pending_action = AsyncMock()
                action_type, ok, msg = await service.confirm(
                    "u1", "act_uncertain", executor
                )

    assert action_type == "REFUND"
    assert ok is False
    assert "正在核对" in msg
    complete.assert_not_awaited()
    redis.save_pending_action.assert_awaited_once_with("act_uncertain", pending)


@pytest.mark.asyncio
async def test_reconciler_confirms_only_when_java_reports_success(service):
    pending = {
        "token": "act_reconcile",
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "paramsJson": json.dumps({"orderId": "o1"}),
        "status": 3,
        "statusName": "EXECUTING",
    }
    final = {
        **pending,
        "status": 1,
        "statusName": "CONFIRMED",
        "resultMessage": "订单已确认收货",
    }
    with (
        patch.object(
            pending_action_store,
            "list_stale_executing",
            AsyncMock(return_value=[pending]),
        ),
        patch.object(
            pending_action_store,
            "complete",
            AsyncMock(return_value=final),
        ) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_agent_action_status",
            AsyncMock(
                return_value={
                    "status": "SUCCESS",
                    "result_message": "订单已确认收货",
                }
            ),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        reconciled = await service.reconcile_stale_executing(600)

    assert reconciled == 1
    complete.assert_awaited_once_with(
        "act_reconcile", "CONFIRMED", result_message="订单已确认收货"
    )
    redis.save_pending_action.assert_awaited_once_with("act_reconcile", final)


@pytest.mark.asyncio
async def test_reconciler_keeps_inconclusive_action_executing(service):
    pending = {
        "token": "act_processing",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": json.dumps({"orderItemId": "item-1"}),
        "status": 3,
        "statusName": "EXECUTING",
    }
    with (
        patch.object(
            pending_action_store,
            "list_stale_executing",
            AsyncMock(return_value=[pending]),
        ),
        patch.object(pending_action_store, "complete", AsyncMock()) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_agent_action_status",
            AsyncMock(return_value={"status": "PROCESSING"}),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        reconciled = await service.reconcile_stale_executing(600)

    assert reconciled == 0
    complete.assert_not_awaited()
    redis.save_pending_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_remote_rejection_reconciles_domain_success(service):
    pending = {
        "token": "act_remote_success",
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "paramsJson": json.dumps({"orderId": "o1"}),
        "status": 3,
    }
    final = {
        **pending,
        "status": 1,
        "statusName": "CONFIRMED",
        "resultMessage": "订单已确认收货",
    }

    async def executor(_):
        raise RemoteActionRejected("成长值服务暂时不可用")

    with (
        patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))),
        patch.object(pending_action_store, "complete", AsyncMock(return_value=final)) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_agent_action_status",
            AsyncMock(return_value={"status": "SUCCESS", "result_message": "订单已确认收货"}),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        action_type, ok, msg = await service.confirm("u1", pending["token"], executor)

    assert (action_type, ok, msg) == ("CONFIRM_RECEIPT", True, "订单已确认收货")
    complete.assert_awaited_once_with(
        pending["token"],
        "CONFIRMED",
        result_message="订单已确认收货",
        error_message=None,
    )


@pytest.mark.asyncio
async def test_structured_remote_rejection_reconciles_definitive_failure(service):
    pending = {
        "token": "act_remote_failed",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": json.dumps({"orderItemId": "item-1"}),
        "status": 3,
    }
    final = {
        **pending,
        "status": 4,
        "statusName": "FAILED",
        "errorMessage": "当前订单状态不能申请退款",
    }

    async def executor(_):
        raise RemoteActionRejected("当前订单状态不能申请退款")

    with (
        patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))),
        patch.object(pending_action_store, "complete", AsyncMock(return_value=final)) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_agent_action_status",
            AsyncMock(return_value={"status": "FAILED", "result_message": "当前订单状态不能申请退款"}),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        action_type, ok, msg = await service.confirm("u1", pending["token"], executor)

    assert action_type == "REFUND"
    assert ok is False
    assert msg == "当前订单状态不能申请退款"
    complete.assert_awaited_once_with(
        pending["token"],
        "FAILED",
        result_message=None,
        error_message="当前订单状态不能申请退款",
    )


@pytest.mark.asyncio
async def test_structured_remote_rejection_with_processing_status_stays_executing(service):
    pending = {
        "token": "act_remote_processing",
        "userId": "u1",
        "actionType": "REFUND",
        "paramsJson": json.dumps({"orderItemId": "item-1"}),
        "status": 3,
    }

    async def executor(_):
        raise RemoteActionRejected("请求正在处理中")

    with (
        patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))),
        patch.object(pending_action_store, "complete", AsyncMock()) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_agent_action_status",
            AsyncMock(return_value={"status": "PROCESSING"}),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        action_type, ok, msg = await service.confirm("u1", pending["token"], executor)

    assert action_type == "REFUND"
    assert ok is False
    assert "核对" in msg
    complete.assert_not_awaited()
