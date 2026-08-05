from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services.message_service import AgentMessageService


class _Cursor:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []

    async def execute(self, sql: str, params: tuple | None = None):
        self.calls.append((sql, params))

    async def fetchall(self):
        return [{"intent": "SEARCH_PRODUCT"}, {"intent": "CHAT"}]


def _acquire_for(cursor: _Cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


@pytest.mark.asyncio
async def test_recent_intents_uses_agent_message_send_time_column():
    cursor = _Cursor()
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        result = await AgentMessageService().get_recent_intents("user-1", limit=4)

    assert result == ["SEARCH_PRODUCT", "CHAT"]
    sql, params = cursor.calls[0]
    assert "send_time > DATE_SUB" in sql
    assert "created_at" not in sql
    assert params[-1] == 4


@pytest.mark.asyncio
async def test_reset_unresolved_count_targets_only_current_message():
    cursor = _Cursor()
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        await AgentMessageService().reset_unresolved_count(42)

    sql, params = cursor.calls[0]
    assert "SET unresolved_count=0" in sql
    assert "WHERE message_id=%s" in sql
    assert params == (42,)
