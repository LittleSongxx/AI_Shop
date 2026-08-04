from __future__ import annotations

import pytest

from app.graph.checkpoint.redis_saver import (
    CheckpointPersistenceError,
    RedisCheckpointSaver,
)


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str):
        self.data[key] = value

    async def delete(self, key: str):
        self.data.pop(key, None)


class FailingRedis(FakeRedis):
    async def setex(self, key: str, _ttl: int, value: str):
        raise ConnectionError("redis unavailable")


@pytest.mark.asyncio
async def test_checkpoint_round_trip_restores_typed_values_without_pickle():
    redis = FakeRedis()
    first = RedisCheckpointSaver(redis, key_prefix="test:checkpoint")
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "ts": "2026-07-24T00:00:00+00:00",
        "id": "0001",
        "channel_values": {"messages": [{"role": "user", "content": "hello"}]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
        "updated_channels": ["messages"],
    }

    saved_config = await first.aput(
        config,
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {"messages": "1"},
    )
    await first.aput_writes(
        saved_config,
        [("messages", {"role": "assistant", "content": "hi"})],
        "task-1",
    )

    restored = RedisCheckpointSaver(redis, key_prefix="test:checkpoint")
    assert await restored.hydrate_thread("thread-1") is True

    item = await restored.aget_tuple(saved_config)
    assert item is not None
    assert item.checkpoint["channel_values"]["messages"][0]["content"] == "hello"
    assert item.pending_writes[0][2]["content"] == "hi"


@pytest.mark.asyncio
async def test_checkpoint_write_failure_is_not_silently_downgraded_to_memory():
    saver = RedisCheckpointSaver(FailingRedis(), key_prefix="test:checkpoint")
    config = {"configurable": {"thread_id": "thread-fail", "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "ts": "2026-07-24T00:00:00+00:00",
        "id": "0001",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": [],
    }

    with pytest.raises(CheckpointPersistenceError):
        await saver.aput(
            config,
            checkpoint,
            {"source": "input", "step": 0, "parents": {}},
            {},
        )
    assert saver.persist_failures == 1
