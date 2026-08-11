from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.visual.consumer import (
    VisualIndexConsumer,
    VisualProductIndexEvent,
    parse_visual_product_index_event,
)


def test_visual_index_event_accepts_only_versioned_product_events():
    assert parse_visual_product_index_event(
        {"dataId": "P_100", "type": "product", "version": 1723000000000}
    ) == VisualProductIndexEvent("P_100", 1723000000000)
    assert parse_visual_product_index_event(
        {"dataId": "faq-1", "type": "faq", "version": 1}
    ) is None

    with pytest.raises(ValueError, match="PRODUCT_ID"):
        parse_visual_product_index_event(
            {"dataId": "../../bad", "type": "product", "version": 1}
        )
    with pytest.raises(ValueError, match="PRODUCT_VERSION"):
        parse_visual_product_index_event(
            {"dataId": "P_100", "type": "product", "version": 0}
        )


def _message(payload: dict, *, headers: dict | None = None):
    return SimpleNamespace(
        body=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
        message_id="message-1",
        ack=AsyncMock(),
        reject=AsyncMock(),
        nack=AsyncMock(),
        channel=SimpleNamespace(get_exchange=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_visual_consumer_indexes_product_and_acks(monkeypatch):
    consumer = VisualIndexConsumer()
    message = _message({"dataId": "P_100", "type": "product", "version": 7})
    index_product = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "app.visual.consumer.visual_catalog_indexer.index_product", index_product
    )

    await consumer._handle(message)

    index_product.assert_awaited_once_with("P_100", product_version=7)
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_visual_consumer_ignores_non_product_without_indexing(monkeypatch):
    consumer = VisualIndexConsumer()
    message = _message({"dataId": "faq-1", "type": "faq", "version": 7})
    index_product = AsyncMock()
    monkeypatch.setattr(
        "app.visual.consumer.visual_catalog_indexer.index_product", index_product
    )

    await consumer._handle(message)

    index_product.assert_not_awaited()
    message.ack.assert_awaited_once()
    message.reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_visual_consumer_dead_letters_after_bounded_retries(monkeypatch):
    consumer = VisualIndexConsumer()
    message = _message(
        {"dataId": "P_100", "type": "product", "version": 7},
        headers={"x-visual-index-retry": 3},
    )
    monkeypatch.setattr(
        "app.visual.consumer.visual_catalog_indexer.index_product",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )
    settings = SimpleNamespace(visual_index_max_retries=3)
    monkeypatch.setattr("app.visual.consumer.get_settings", lambda: settings)

    await consumer._handle(message)

    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()
