from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.models.commerce_outcome import CommerceOutcomeEvent
from app.services.commerce_outcome_ledger_service import (
    CommerceOutcomeLedgerService,
)


def _event(**overrides) -> CommerceOutcomeEvent:
    values = {
        "eventId": "evt-order-1",
        "source": "PAYMENT",
        "idempotencyKey": "order:payment:1",
        "eventType": "PAYMENT",
        "userId": "u1",
        "requestId": "request-1",
        "productId": "p1",
        "skuKey": "sku-1",
        "orderId": "order-1",
        "position": 2,
        "payload": {
            "quantity": 1,
            "paidAmount": 399.0,
            "currency": "CNY",
            "address": "must-not-persist",
            "rawMessage": "must-not-persist",
        },
        "occurredAt": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return CommerceOutcomeEvent.model_validate(values)


def test_attributed_event_requires_product_and_position():
    with pytest.raises(ValidationError, match="productId and position"):
        _event(productId=None, position=None)


def test_required_identifiers_reject_whitespace():
    with pytest.raises(ValidationError, match="identifier must not be blank"):
        _event(eventId="   ")


def test_source_cannot_claim_another_domains_event_type():
    with pytest.raises(ValidationError, match="ORDER cannot emit ADD_TO_CART"):
        _event(source="ORDER", eventType="ADD_TO_CART")


@pytest.mark.asyncio
async def test_forged_attribution_is_rejected_before_insert(monkeypatch):
    service = CommerceOutcomeLedgerService()
    monkeypatch.setattr(service, "_verified_impression", AsyncMock(return_value=None))
    insert = AsyncMock()
    monkeypatch.setattr(service, "_insert", insert)

    result = await service.record(_event())

    assert result == {
        "eventId": "evt-order-1",
        "accepted": False,
        "status": "INVALID_ATTRIBUTION",
    }
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_event_is_sanitized_and_inserted_immutably(monkeypatch):
    service = CommerceOutcomeLedgerService()
    attribution = {
        "position": 2,
        "source": "hybrid",
        "retrievalMode": "text",
        "matchType": None,
        "recallSource": None,
        "modelVersion": None,
        "runId": "verified-run-1",
    }
    monkeypatch.setattr(
        service, "_verified_impression", AsyncMock(return_value=attribution)
    )
    insert = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_insert", insert)
    monkeypatch.setattr(
        "app.services.commerce_outcome_ledger_service.episode_service.record_step",
        lambda *_args, **_kwargs: None,
    )

    result = await service.record(_event())

    assert result["status"] == "RECORDED"
    payload = insert.await_args.args[1]
    persisted_event = insert.await_args.args[0]
    assert persisted_event.runId == "verified-run-1"
    assert payload["paidAmount"] == 399.0
    assert payload["currency"] == "CNY"
    assert payload["attributionStatus"] == "VERIFIED"
    assert payload["attribution"] == attribution
    assert "address" not in payload
    assert "rawMessage" not in payload


@pytest.mark.asyncio
async def test_duplicate_domain_event_is_accepted_without_mutation(monkeypatch):
    service = CommerceOutcomeLedgerService()
    monkeypatch.setattr(
        service,
        "_verified_impression",
        AsyncMock(return_value={"position": 2, "source": "hybrid"}),
    )
    monkeypatch.setattr(service, "_insert", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.services.commerce_outcome_ledger_service.episode_service.record_step",
        lambda *_args, **_kwargs: None,
    )

    result = await service.record(_event())

    assert result["accepted"] is True
    assert result["status"] == "DUPLICATE"


def test_non_finite_payload_numbers_are_removed():
    payload = CommerceOutcomeLedgerService._sanitize_payload(
        "PAYMENT", {"paidAmount": float("nan"), "quantity": 1}
    )

    assert payload == {"quantity": 1}


@pytest.mark.asyncio
async def test_event_time_outside_retention_is_rejected(monkeypatch):
    service = CommerceOutcomeLedgerService()
    verify = AsyncMock()
    monkeypatch.setattr(service, "_verified_impression", verify)

    result = await service.record(
        _event(occurredAt=datetime.now(timezone.utc) - timedelta(days=181))
    )

    assert result["status"] == "EVENT_TIME_OUT_OF_RANGE"
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_impression_events_are_deterministic_and_privacy_bounded(monkeypatch):
    service = CommerceOutcomeLedgerService()
    record_batch = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "record_batch", record_batch)

    await service.record_impressions(
        user_id="u1",
        request_id="request-1",
        product_ids=["p1", "p2"],
        recommendation_source="hybrid",
        retrieval_mode="text",
        match_type=None,
        subject_label=None,
        recall_source="rrf",
        model_version="embedding-v1",
    )

    events = record_batch.await_args.args[0]
    assert [event.position for event in events] == [1, 2]
    assert events[0].idempotencyKey == "impression:request-1:p1:1"
    assert events[0].payload == {
        "position": 1,
        "recommendationSource": "hybrid",
        "retrievalMode": "text",
        "matchType": None,
        "subjectLabel": None,
        "recallSource": "rrf",
        "modelVersion": "embedding-v1",
    }
