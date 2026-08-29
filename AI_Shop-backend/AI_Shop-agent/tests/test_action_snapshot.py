import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.pending_action_service import PendingActionService
from app.services.pending_action_store import pending_action_store


def _capability(etag: str = "sha256:" + "a" * 64) -> dict:
    return {
        "decision": "ALLOWED",
        "snapshotVersion": "order-action-snapshot/v1",
        "snapshotEtag": etag,
        "snapshotHash": etag.removeprefix("sha256:"),
        "evaluatedAt": "2026-08-29T00:00:00Z",
        "snapshot": {"action": "CANCEL_ORDER", "orderId": "o1", "orderStatus": 0},
    }


def test_capability_snapshot_is_bounded_and_persisted_in_params():
    params = PendingActionService._with_action_snapshot(
        {"orderId": "o1", "capabilityDecision": _capability()}
    )

    snapshot = params["actionSnapshot"]
    assert snapshot["authority"] == "JAVA_ORDER_SERVICE"
    assert snapshot["version"] == "order-action-snapshot/v1"
    assert snapshot["etag"] == "sha256:" + "a" * 64
    assert snapshot["snapshot"]["orderStatus"] == 0


def test_invalid_model_supplied_etag_is_not_promoted_to_authority():
    params = PendingActionService._with_action_snapshot(
        {"orderId": "o1", "capabilityDecision": _capability("not-an-etag")}
    )
    assert "actionSnapshot" not in params


@pytest.mark.asyncio
async def test_stale_snapshot_fails_before_remote_write():
    service = PendingActionService()
    expected = _capability()["snapshotEtag"]
    pending = {
        "token": "act_stale",
        "userId": "u1",
        "actionType": "CANCEL_ORDER",
        "status": 3,
        "paramsJson": json.dumps(
            {
                "orderId": "o1",
                "actionSnapshot": {
                    "authority": "JAVA_ORDER_SERVICE",
                    "version": "order-action-snapshot/v1",
                    "etag": expected,
                },
            }
        ),
    }
    final = {
        **pending,
        "status": 4,
        "statusName": "FAILED",
        "errorMessage": "订单状态或操作资格已变化，请重新核验后发起操作",
    }
    executor = AsyncMock(return_value="should not run")
    with (
        patch.object(pending_action_store, "claim", AsyncMock(return_value=(True, pending))),
        patch.object(pending_action_store, "complete", AsyncMock(return_value=final)) as complete,
        patch(
            "app.services.pending_action_service.java_internal_client.get_order_action_capability",
            AsyncMock(
                return_value={
                    "decision": "DENIED",
                    "snapshot_version": "order-action-snapshot/v1",
                    "snapshot_etag": "sha256:" + "b" * 64,
                }
            ),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.try_lock_pending_action = AsyncMock(return_value=True)
        redis.unlock_pending_action = AsyncMock()
        redis.save_pending_action = AsyncMock()
        _action_type, ok, message = await service.confirm("u1", "act_stale", executor)

    assert ok is False
    assert "状态或操作资格已变化" in message
    executor.assert_not_awaited()
    complete.assert_awaited_once()
    assert complete.await_args.args[1] == pending_action_store.FAILED
