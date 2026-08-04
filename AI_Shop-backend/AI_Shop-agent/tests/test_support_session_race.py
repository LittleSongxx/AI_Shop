from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from aiomysql import IntegrityError

from app.services.support_service import SupportService


class _FailingCursor:
    def __init__(self, error: IntegrityError):
        self.error = error

    async def execute(self, *_args, **_kwargs):
        raise self.error


def _failing_acquire(error: IntegrityError):
    @asynccontextmanager
    async def acquire():
        yield _FailingCursor(error)

    return acquire


class _Cursor:
    def __init__(self, *, rowcount: int = 1, row: dict | None = None):
        self.rowcount = rowcount
        self.row = row
        self.lastrowid = 91
        self.calls: list[tuple[str, tuple | None]] = []

    async def execute(self, sql: str, params: tuple | None = None):
        self.calls.append((sql, params))
        return self.rowcount

    async def fetchone(self):
        return self.row


def _acquire_for(cursor: _Cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


def _create(service: SupportService):
    return service.create_or_get(
        "u1",
        1,
        {"intent": "CHAT", "sentiment": "NEUTRAL"},
        "USER_REQUEST",
        "summary",
    )


@pytest.mark.asyncio
async def test_active_session_unique_race_returns_the_winner():
    service = SupportService()
    winner = {"session_id": "winner", "user_id": "u1", "status": "QUEUED"}
    duplicate = IntegrityError(
        1062,
        "Duplicate entry 'u1' for key 'support_session.uk_support_active_user'",
    )
    with (
        patch.object(service, "get_active", AsyncMock(side_effect=[None, winner])),
        patch("app.services.support_service.acquire", _failing_acquire(duplicate)),
    ):
        assert await _create(service) == winner


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_hidden_as_a_race():
    service = SupportService()
    invalid_row = IntegrityError(1048, "Column 'user_id' cannot be null")
    with (
        patch.object(service, "get_active", AsyncMock(return_value=None)),
        patch("app.services.support_service.acquire", _failing_acquire(invalid_row)),
    ):
        with pytest.raises(IntegrityError) as raised:
            await _create(service)

    assert raised.value is invalid_row


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["resolve", "return_to_ai"])
async def test_admin_terminal_transition_checks_owner_in_atomic_update(method_name):
    service = SupportService()
    cursor = _Cursor(rowcount=0)
    with (
        patch("app.services.support_service.acquire", _acquire_for(cursor)),
        patch.object(service, "get_by_id", AsyncMock()) as get_by_id,
        patch.object(service, "publish_both", AsyncMock()) as publish,
    ):
        method = getattr(service, method_name)
        with pytest.raises(ValueError, match="不属于当前客服"):
            await method("session-1", "admin-a")

    update_sql, params = cursor.calls[0]
    assert "assigned_admin IS NULL OR assigned_admin=%s" in update_sql
    assert params is not None and params[-1] == "admin-a"
    get_by_id.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_cancel_does_not_publish_when_session_transition_loses_race():
    service = SupportService()
    cursor = _Cursor(rowcount=0)
    with (
        patch("app.services.support_service.acquire", _acquire_for(cursor)),
        patch.object(service, "get_by_id", AsyncMock()) as get_by_id,
        patch.object(service, "publish_both", AsyncMock()) as publish,
    ):
        result = await service.cancel_by_user("session-1", "u1")

    assert result is None
    get_by_id.assert_not_awaited()
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_locks_session_and_commits_state_with_both_messages():
    service = SupportService()
    session = {
        "session_id": "session-1",
        "user_id": "u1",
        "status": "ASSIGNED",
        "assigned_admin": "admin-a",
    }
    cursor = _Cursor(rowcount=1, row=session)
    final = {**session, "status": "ACTIVE"}
    with (
        patch("app.services.support_service.acquire", _acquire_for(cursor)),
        patch.object(service, "get_by_id", AsyncMock(return_value=final)),
        patch.object(service, "publish_admin", AsyncMock()),
        patch("app.services.support_service.redis_service.publish_ws", AsyncMock()),
    ):
        assert await service.reply("session-1", "admin-a", "您好") == final

    statements = [sql.strip() for sql, _ in cursor.calls]
    assert statements[0] == "START TRANSACTION"
    assert "FOR UPDATE" in statements[1]
    assert any("UPDATE support_session" in sql for sql in statements)
    assert any("INSERT INTO support_message" in sql for sql in statements)
    assert any("INSERT INTO agent_message" in sql for sql in statements)
    assert statements[-1] == "COMMIT"


@pytest.mark.asyncio
async def test_reply_rolls_back_when_locked_session_belongs_to_another_admin():
    service = SupportService()
    cursor = _Cursor(
        rowcount=1,
        row={
            "session_id": "session-1",
            "user_id": "u1",
            "status": "ASSIGNED",
            "assigned_admin": "admin-b",
        },
    )
    with patch("app.services.support_service.acquire", _acquire_for(cursor)):
        with pytest.raises(ValueError, match="其他客服"):
            await service.reply("session-1", "admin-a", "越权回复")

    statements = [sql.strip() for sql, _ in cursor.calls]
    assert statements[-1] == "ROLLBACK"
    assert not any("INSERT INTO support_message" in sql for sql in statements)
