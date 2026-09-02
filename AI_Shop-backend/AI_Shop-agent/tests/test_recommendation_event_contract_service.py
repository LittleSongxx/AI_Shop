from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.services.recommendation_event_store as recommendation_event_store_module
from app.api.routes.v1 import recommendation_event
from app.auth.token_service import TokenUserInfo
from app.domain.recommendation.contracts import RecommendationEvent
from app.services.recommendation_attribution_service import (
    RecommendationAttributionService,
)
from app.services.recommendation_event_store import (
    RecommendationEventConflict,
    recommendation_event_store,
)


def _event(event_type: str) -> RecommendationEvent:
    return RecommendationEvent(
        eventId=f"event-{event_type.lower()}",
        idempotencyKey=f"idem-{event_type.lower()}",
        eventType=event_type,
        requestId="req-1",
        runId="run-1",
        productId="p1",
        position=1,
        modelVersion="recommendation-v1",
    )


@pytest.mark.asyncio
async def test_click_event_projects_only_verified_canonical_touchpoint(monkeypatch):
    service = RecommendationAttributionService()
    canonical = {
        "eventId": "event-click",
        "idempotencyKey": "idem-click",
        "eventType": "CLICK",
        "requestId": "req-1",
        "runId": "run-1",
        "productId": "p1",
        "position": 1,
        "source": "hybrid",
        "retrievalMode": "text",
        "modelVersion": "recommendation-v1",
    }
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "recommendation_event_store.record_event",
        AsyncMock(return_value=canonical),
    )
    ledger = AsyncMock()
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_click",
        ledger,
    )

    assert await service.record_event("u1", _event("CLICK")) == canonical
    ledger.assert_awaited_once_with(canonical, "u1")


@pytest.mark.asyncio
async def test_idempotent_click_retry_accepts_canonical_server_projection(monkeypatch):
    row = {
        "client_event_id": "event-click",
        "idempotency_key": "idem-click",
        "request_id": "req-1",
        "run_id": "canonical-run",
        "product_id": "p1",
        "position": 1,
        "source": "hybrid",
        "retrieval_mode": "text",
        "match_type": None,
        "subject_label": None,
        "recall_source": None,
        "model_version": "canonical-model",
        "event_type": "CLICK",
        "occurred_at": datetime.now(timezone.utc),
    }

    class Cursor:
        async def execute(self, sql, _params=()):
            assert "idempotency_key" in sql

        async def fetchone(self):
            return row

    @asynccontextmanager
    async def acquire():
        yield Cursor()

    monkeypatch.setattr(recommendation_event_store_module, "acquire", acquire)

    retry = _event("CLICK")
    assert (await recommendation_event_store.record_event("u1", retry))["runId"] == (
        "canonical-run"
    )

    conflicting = retry.model_copy(update={"product_id": "p2"})
    with pytest.raises(RecommendationEventConflict):
        await recommendation_event_store.record_event("u1", conflicting)


@pytest.mark.asyncio
async def test_add_to_cart_touchpoint_does_not_spoof_java_commerce_fact(monkeypatch):
    service = RecommendationAttributionService()
    canonical = {
        "eventId": "event-add_to_cart",
        "eventType": "ADD_TO_CART",
        "requestId": "req-1",
        "productId": "p1",
        "position": 1,
    }
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "recommendation_event_store.record_event",
        AsyncMock(return_value=canonical),
    )
    click_ledger = AsyncMock()
    impression_ledger = AsyncMock()
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_click",
        click_ledger,
    )
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_impressions",
        impression_ledger,
    )

    assert await service.record_event("u1", _event("ADD_TO_CART")) == canonical
    click_ledger.assert_not_awaited()
    impression_ledger.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_cannot_report_payment_or_repeat_purchase():
    with pytest.raises(HTTPException) as error:
        await recommendation_event(
            _event("PAYMENT"),
            user=TokenUserInfo(user_id="u1"),
        )

    assert error.value.status_code == 403
