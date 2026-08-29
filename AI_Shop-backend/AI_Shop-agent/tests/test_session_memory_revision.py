from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory.models import SessionMemory
from app.memory.session_memory_service import SessionMemoryService


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands: list[tuple] = []

    async def watch(self, _key):
        return None

    async def get(self, key):
        return self.redis.values.get(key)

    def multi(self):
        return None

    def setex(self, key, ttl, value):
        self.commands.append(("setex", key, ttl, value))
        return self

    def delete(self, key):
        self.commands.append(("delete", key))
        return self

    async def execute(self):
        for command in self.commands:
            if command[0] == "setex":
                _, key, _ttl, value = command
                self.redis.values[key] = value
            else:
                self.redis.values.pop(command[1], None)
        return [True for _ in self.commands]

    async def reset(self):
        self.commands.clear()


class _Redis:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)

    def pipeline(self, transaction=True):
        assert transaction is True
        return _Pipeline(self)


class _MemoryDb:
    def __init__(self):
        self.row: dict | None = None
        self.query = ""
        self.args: tuple | None = None
        self.rowcount = 0

    async def execute(self, query, args=()):
        self.query = query
        self.args = args
        if query.lstrip().upper().startswith("INSERT"):
            incoming_state = json.loads(args[2])
            expected = int(args[-1])
            current_state = (self.row or {}).get("state_json") or {}
            current_revision = int(current_state.get("memoryRevision") or 0)
            if self.row is None or current_revision == expected:
                self.row = {
                    "summary_json": json.loads(args[1]),
                    "state_json": incoming_state,
                }
                self.rowcount = 1
            else:
                self.rowcount = 0


class _Acquire:
    def __init__(self, cursor):
        self.cursor = cursor

    async def __aenter__(self):
        return self.cursor

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_memory_save_advances_revision_and_rejects_stale_writer(monkeypatch):
    service = SessionMemoryService()
    service.ensure_table = AsyncMock()
    db = _MemoryDb()
    monkeypatch.setattr(
        "app.memory.session_memory_service.acquire", lambda: _Acquire(db)
    )
    monkeypatch.setattr(
        "app.memory.session_memory_service.get_settings",
        lambda: SimpleNamespace(session_redis_ttl=3600),
    )
    redis = _Redis()

    first = SessionMemory(user_id="u1")
    first.state["pendingAction"] = {"summary": "first"}
    assert await service.save(first, redis) is True
    assert first.revision == 1
    assert json.loads(redis.values["mall:agent:session:u1"])["revision"] == 1
    # The guarded state assignment is last so MySQL evaluates every other
    # field against the previous revision (assignments are left-to-right).
    assert db.query.index("state_json=IF") > db.query.index("updated_at=IF")

    stale = SessionMemory(user_id="u1")
    stale.state["pendingAction"] = {"summary": "stale"}
    assert await service.save(stale, redis) is False
    assert stale.revision == 0
    assert db.row["state_json"]["pendingAction"]["summary"] == "first"


@pytest.mark.asyncio
async def test_redis_cas_does_not_clobber_newer_snapshot(monkeypatch):
    service = SessionMemoryService()
    monkeypatch.setattr(
        "app.memory.session_memory_service.get_settings",
        lambda: SimpleNamespace(session_redis_ttl=3600),
    )
    redis = _Redis()
    redis.values["mall:agent:session:u1"] = json.dumps(
        {
            "revision": 2,
            "summary": {},
            "state": {"memoryRevision": 2},
        }
    )
    stale = SessionMemory(user_id="u1", revision=1)
    stale.state["pendingAction"] = {"summary": "stale"}

    assert (
        await service._save_redis(
            "u1",
            stale,
            redis,
            expected_revision=1,
            next_revision=2,
        )
        is False
    )
    assert json.loads(redis.values["mall:agent:session:u1"])["revision"] == 2
