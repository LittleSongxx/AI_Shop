from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import check_visual_index


def _settings(**overrides):
    values = {
        "visual_search_enabled": True,
        "visual_index_consumer_enabled": True,
        "visual_api_key": "visual-key",
        "rabbitmq_url": "amqp://example",
        "visual_index_queue": "visual.index.queue",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_check_skips_queue_probe_when_consumer_is_disabled(monkeypatch):
    connect = AsyncMock()
    monkeypatch.setattr(check_visual_index, "get_settings", lambda: _settings(
        visual_index_consumer_enabled=False
    ))
    monkeypatch.setattr(
        check_visual_index.visual_product_index,
        "status",
        AsyncMock(return_value={"servingCurrentModel": True}),
    )
    monkeypatch.setattr(check_visual_index.aio_pika, "connect_robust", connect)

    result = await check_visual_index.check()

    assert result == {
        "servingCurrentModel": True,
        "queueState": "DISABLED",
        "state": "DEGRADED",
        "reason": "VISUAL_INDEX_CONSUMER_DISABLED",
    }
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_reports_queue_counts_from_aio_pika_declaration(monkeypatch):
    queue = SimpleNamespace(
        declaration_result=SimpleNamespace(message_count=7, consumer_count=1)
    )
    channel = SimpleNamespace(declare_queue=AsyncMock(return_value=queue))
    connection = SimpleNamespace(
        channel=AsyncMock(return_value=channel),
        close=AsyncMock(),
    )
    monkeypatch.setattr(check_visual_index, "get_settings", _settings)
    monkeypatch.setattr(
        check_visual_index.visual_product_index,
        "status",
        AsyncMock(return_value={"servingCurrentModel": True}),
    )
    monkeypatch.setattr(
        check_visual_index.aio_pika,
        "connect_robust",
        AsyncMock(return_value=connection),
    )

    result = await check_visual_index.check()

    assert result["state"] == "READY"
    assert result["reason"] is None
    assert result["queueMessages"] == 7
    assert result["queueConsumers"] == 1
    channel.declare_queue.assert_awaited_once_with(
        "visual.index.queue",
        passive=True,
    )
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_without_cloud_key_is_degraded_but_successful(monkeypatch):
    status = AsyncMock(return_value={"servingCurrentModel": True})
    connect = AsyncMock()
    monkeypatch.setattr(
        check_visual_index,
        "get_settings",
        lambda: _settings(visual_api_key=""),
    )
    monkeypatch.setattr(
        check_visual_index.visual_product_index,
        "status",
        status,
    )
    monkeypatch.setattr(
        check_visual_index.aio_pika,
        "connect_robust",
        connect,
    )

    result = await check_visual_index.check()

    assert result == {
        "queueState": "DISABLED",
        "state": "DEGRADED",
        "reason": "VISUAL_API_KEY_NOT_CONFIGURED",
    }
    status.assert_not_awaited()
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_shot_check_closes_http_clients_in_the_same_event_loop(monkeypatch):
    check = AsyncMock(return_value={"state": "READY"})
    close = AsyncMock()
    monkeypatch.setattr(check_visual_index, "check", check)
    monkeypatch.setattr(check_visual_index, "close_clients", close)

    result = await check_visual_index.check_and_close()

    assert result == {"state": "READY"}
    check.assert_awaited_once()
    close.assert_awaited_once()
