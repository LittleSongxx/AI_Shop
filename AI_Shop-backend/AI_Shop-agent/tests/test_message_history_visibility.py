from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services.message_service import AgentMessageService


class _Cursor:
    def __init__(self, fetchone_values=None, rows=None):
        self.fetchone_values = list(fetchone_values or [])
        self.rows = list(rows or [])
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))

    async def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    async def fetchall(self):
        return self.rows


def _acquire_for(cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


@pytest.mark.asyncio
async def test_clear_visible_history_moves_cursor_but_preserves_memory():
    cursor = _Cursor(
        fetchone_values=[
            {"max_message_id": 42, "active_count": 0},
            {"cleared_through": 42},
        ]
    )
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        result = await AgentMessageService().clear_visible_history("u1")

    assert result == {"clearedThroughMessageId": 42, "memoryPreserved": True}
    writes = [sql for sql, _params in cursor.calls if "INSERT INTO" in sql]
    assert len(writes) == 1
    assert "history_cleared_through_message_id" in writes[0]
    assert "agent_session_memory.history_cleared_through_message_id" in writes[0]
    assert "summary_json" not in writes[0]
    assert "state_json" not in writes[0]


@pytest.mark.asyncio
async def test_history_loader_applies_visibility_cursor_only_to_user_view():
    cursor = _Cursor(
        fetchone_values=[{"cleared_through": 42}, {"cnt": 0}], rows=[]
    )
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        result = await AgentMessageService().load_history("u1")

    assert result["list"] == []
    history_calls = [
        (sql, params)
        for sql, params in cursor.calls
        if "FROM agent_message" in sql
    ]
    assert all("message_id>%s" in sql for sql, _params in history_calls)
    assert all(params[:2] == ["u1", 42] for _sql, params in history_calls)


@pytest.mark.asyncio
async def test_clear_visible_history_refuses_while_answer_is_running():
    cursor = _Cursor(fetchone_values=[{"max_message_id": 42, "active_count": 1}])
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        with pytest.raises(ValueError, match="回复尚未结束"):
            await AgentMessageService().clear_visible_history("u1")
