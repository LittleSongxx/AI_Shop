from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import commerce_outcome_queue_consumer as consumer_module


def _message(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        body=json.dumps(payload).encode("utf-8"),
        headers={},
        ack=AsyncMock(),
        nack=AsyncMock(),
        reject=AsyncMock(),
    )


def _batch() -> dict:
    return {
        "events": [
            {
                "eventId": "event-1",
                "source": "ORDER",
                "idempotencyKey": "business-1",
                "eventType": "CANCEL",
                "userId": "user-1",
                "productId": "product-1",
                "skuKey": "sku-1",
                "orderId": "order-1",
                "payload": {"reasonCode": "USER_CANCEL"},
                "occurredAt": "2026-08-20T00:00:00Z",
            }
        ]
    }


@pytest.mark.asyncio
async def test_consumer_acks_only_after_ledger_persists(monkeypatch):
    record_batch = AsyncMock(
        return_value=[{"eventId": "event-1", "status": "RECORDED"}]
    )
    monkeypatch.setattr(
        consumer_module.commerce_outcome_ledger_service,
        "record_batch",
        record_batch,
    )
    message = _message(_batch())

    await consumer_module.CommerceOutcomeQueueConsumer()._handle(message)

    message.ack.assert_awaited_once_with()
    message.nack.assert_not_awaited()
    assert record_batch.await_args.args[0][0].eventId == "event-1"


@pytest.mark.asyncio
async def test_consumer_requeues_transient_persistence_failure(monkeypatch):
    monkeypatch.setattr(
        consumer_module.commerce_outcome_ledger_service,
        "record_batch",
        AsyncMock(
            return_value=[
                {"eventId": "event-1", "status": "PERSISTENCE_FAILED"}
            ]
        ),
    )
    message = _message(_batch())

    await consumer_module.CommerceOutcomeQueueConsumer()._handle(message)

    message.nack.assert_awaited_once_with(requeue=True)
    message.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_dead_letters_malformed_contract():
    message = _message({"events": [{"eventId": "missing-required-fields"}]})

    await consumer_module.CommerceOutcomeQueueConsumer()._handle(message)

    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()
