import asyncio
import time
from types import SimpleNamespace

import pytest

from app.api.websocket import (
    ConnectionManager,
    start_ws_listener,
    stop_ws_listener,
    ws_listener_status,
)


class FakeWebSocket:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.frames: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.frames.append(data)


@pytest.mark.asyncio
async def test_slow_socket_is_bounded_and_removed(monkeypatch):
    monkeypatch.setattr(
        "app.api.websocket.get_settings",
        lambda: SimpleNamespace(ws_send_timeout_seconds=0.1),
    )
    manager = ConnectionManager()
    fast = FakeWebSocket()
    slow = FakeWebSocket(delay=0.5)
    manager._connections["u1"] = {fast, slow}

    started = time.perf_counter()
    await manager.send_json(
        "u1",
        {
            "messageType": "agent",
            "runId": "run-1",
            "requestId": "req-1",
            "episodeId": "ep-1",
        },
    )

    assert time.perf_counter() - started < 0.3
    assert fast.frames
    assert slow not in manager._connections["u1"]


@pytest.mark.asyncio
async def test_empty_fanout_is_non_blocking():
    manager = ConnectionManager()
    await manager.send_json("missing", {"messageType": "agent"})


class FailingPubSub:
    def __init__(self):
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, *_topics):
        return None

    def __aiter__(self):
        return self

    def listen(self):
        return self

    async def __anext__(self):
        raise RuntimeError("redis listener test failure")

    async def unsubscribe(self, *_topics):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


class FakeRedis:
    def __init__(self):
        self.pubsub_instance = FailingPubSub()

    def pubsub(self):
        return self.pubsub_instance


@pytest.mark.asyncio
async def test_listener_failure_is_visible_and_cleaned_up():
    redis = FakeRedis()
    await start_ws_listener(redis)
    for _ in range(10):
        if ws_listener_status()["taskState"] == "done":
            break
        await asyncio.sleep(0)

    status = ws_listener_status()
    assert status["up"] is False
    assert status["taskState"] == "done"
    assert status["lastError"] == "RuntimeError"
    assert redis.pubsub_instance.unsubscribed is True
    assert redis.pubsub_instance.closed is True
    await stop_ws_listener()
