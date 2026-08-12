from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiomysql import IntegrityError

from app.api.routes import agent as agent_routes
from app.exceptions import PendingActionConflict
from app.services.pending_action_service import PendingActionService
from app.services.pending_action_store import PendingActionStore, pending_action_store


def _stored(*, fingerprint: str, token: str = "act_existing") -> dict:
    return {
        "token": token,
        "userId": "u1",
        "actionType": "CONFIRM_RECEIPT",
        "paramsJson": '{"orderId":"o1"}',
        "businessKey": "u1:CONFIRM_RECEIPT:o1",
        "argsFingerprint": fingerprint,
        "status": 0,
        "statusName": "PENDING",
    }


@pytest.mark.asyncio
async def test_same_business_key_and_canonical_args_reuse_original_token():
    service = PendingActionService()
    captured: dict = {}

    async def reuse(pending):
        captured.update(pending)
        return _stored(fingerprint=pending["argsFingerprint"]), False

    with (
        patch.object(pending_action_store, "create", side_effect=reuse),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.get_bound_message_id = AsyncMock(return_value=9)
        redis.save_pending_action = AsyncMock()
        result = await service.create_pending(
            "CONFIRM_RECEIPT",
            "u1",
            {"orderItems": [{"productId": "p1"}], "orderId": "o1"},
            "确认订单",
        )

    assert result["token"] == "act_existing"
    assert captured["businessKey"] == "u1:CONFIRM_RECEIPT:o1"
    assert captured["paramsJson"] == '{"orderId":"o1","orderItems":[{"productId":"p1"}]}'
    redis.save_pending_action.assert_awaited_once_with("act_existing", result)


@pytest.mark.asyncio
async def test_same_business_key_with_different_args_is_a_conflict():
    service = PendingActionService()
    existing = _stored(fingerprint="0" * 64)

    with (
        patch.object(
            pending_action_store,
            "create",
            AsyncMock(return_value=(existing, False)),
        ),
        patch("app.services.pending_action_service.redis_service") as redis,
    ):
        redis.ensure_connected = AsyncMock()
        redis.get_bound_message_id = AsyncMock(return_value=None)
        redis.save_pending_action = AsyncMock()
        with pytest.raises(PendingActionConflict, match="参数不同"):
            await service.create_pending(
                "CONFIRM_RECEIPT",
                "u1",
                {"orderId": "o1", "note": "different"},
                "修改后的提案",
            )

    redis.save_pending_action.assert_not_awaited()


class _DuplicateCursor:
    def __init__(self, row: dict):
        self.row = row
        self.statements: list[str] = []

    async def execute(self, sql, _params=None):
        self.statements.append(" ".join(sql.split()))
        if "INSERT INTO agent_pending_action" in sql:
            raise IntegrityError(1062, "Duplicate entry for uk_agent_pending_active_business")

    async def fetchone(self):
        return self.row


def _acquire_for(cursor):
    @asynccontextmanager
    async def _acquire():
        yield cursor

    return _acquire


@pytest.mark.asyncio
async def test_database_unique_key_race_reads_the_active_owner():
    now = datetime.now()
    row = {
        "action_token": "act_winner",
        "user_id": "u1",
        "action_type": "REFUND",
        "message_id": 1,
        "params_json": '{"orderItemId":"item-1"}',
        "business_key": "u1:REFUND:item-1",
        "args_fingerprint": "a" * 64,
        "active_business_key": "u1:REFUND:item-1",
        "status": "PENDING",
        "reconcile_attempts": 0,
        "expires_at": now + timedelta(minutes=10),
        "created_at": now,
    }
    cursor = _DuplicateCursor(row)
    pending = {
        "token": "act_loser",
        "userId": "u1",
        "actionType": "REFUND",
        "messageId": 1,
        "paramsJson": '{"orderItemId":"item-1"}',
        "businessKey": "u1:REFUND:item-1",
        "argsFingerprint": "a" * 64,
    }

    with patch("app.services.pending_action_store.acquire", _acquire_for(cursor)):
        stored, created = await PendingActionStore().create(pending)

    assert created is False
    assert stored["token"] == "act_winner"
    assert any("expires_at <= NOW()" in sql for sql in cursor.statements)
    assert any("WHERE active_business_key=%s" in sql for sql in cursor.statements)


@pytest.mark.asyncio
async def test_non_duplicate_insert_error_is_not_hidden():
    now = datetime.now()
    cursor = _DuplicateCursor({"expires_at": now, "created_at": now})

    async def invalid_execute(sql, _params=None):
        if "INSERT INTO agent_pending_action" in sql:
            raise IntegrityError(1048, "business_key cannot be null")

    cursor.execute = invalid_execute
    with patch("app.services.pending_action_store.acquire", _acquire_for(cursor)):
        with pytest.raises(IntegrityError) as raised:
            await PendingActionStore().create(
                {
                    "token": "act_invalid",
                    "userId": "u1",
                    "actionType": "REFUND",
                    "paramsJson": "{}",
                    "businessKey": "u1:REFUND:item-1",
                    "argsFingerprint": "a" * 64,
                }
            )

    assert raised.value.args[0] == 1048


@pytest.mark.asyncio
async def test_review_lookup_only_allows_uncertain_states():
    service = PendingActionService()
    with pytest.raises(ValueError, match="status 仅支持"):
        await service.list_for_review(status="CONFIRMED")

    with patch.object(
        pending_action_store,
        "list_for_review",
        AsyncMock(return_value=[{"token": "act_manual"}]),
    ) as lookup:
        rows = await service.list_for_review(
            status="manual_review",
            token=" act_manual ",
            user_id=" u1 ",
            business_key=" u1:REFUND:item-1 ",
            limit=500,
        )

    assert rows == [{"token": "act_manual"}]
    lookup.assert_awaited_once_with(
        status="MANUAL_REVIEW",
        token="act_manual",
        user_id="u1",
        business_key="u1:REFUND:item-1",
        limit=500,
    )


def test_review_lookup_route_requires_admin_assertion_dependency():
    # 治理层改造：管理端只读接口从内部 token 迁移到管理员断言签名
    # （管理员身份经 HMAC 断言，路由内再做细粒度权限），内部 token 不再准入。
    route = next(
        item
        for item in agent_routes.router.routes
        if getattr(item, "path", None) == "/agent/admin/loadPendingActions"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}

    assert agent_routes._require_admin in dependencies
    assert agent_routes._require_internal_token not in dependencies
