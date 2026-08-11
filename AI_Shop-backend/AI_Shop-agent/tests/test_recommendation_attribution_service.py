from unittest.mock import AsyncMock

import pytest

from app.services.recommendation_attribution_service import (
    RecommendationAttributionService,
)


@pytest.mark.asyncio
async def test_impression_persists_before_cache(monkeypatch):
    service = RecommendationAttributionService()
    calls: list[str] = []

    async def persist(*_args, **_kwargs):
        calls.append("database")

    async def cache(*_args, **_kwargs):
        calls.append("redis")

    async def ledger(*_args, **_kwargs):
        calls.append("ledger")

    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "recommendation_event_store.record_impressions",
        persist,
    )
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service.redis_service.log_impression",
        cache,
    )
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_impressions",
        ledger,
    )

    await service.record_impression(
        "u1", ["p1", "p2"], request_id="request-1", source="hybrid"
    )

    assert calls == ["database", "ledger", "redis"]


@pytest.mark.asyncio
async def test_missing_durable_impression_never_becomes_click(monkeypatch):
    service = RecommendationAttributionService()
    persistent_click = AsyncMock(return_value=None)
    redis_click = AsyncMock()
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "recommendation_event_store.record_click",
        persistent_click,
    )
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "redis_service.log_attributed_click",
        redis_click,
    )
    ledger_click = AsyncMock()
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_click",
        ledger_click,
    )

    result = await service.record_click("u1", "request-1", "p1", 1)

    assert result is None
    ledger_click.assert_not_awaited()
    redis_click.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_failure_cannot_erase_persistent_click(monkeypatch):
    service = RecommendationAttributionService()
    expected = {
        "requestId": "request-1",
        "productId": "p1",
        "position": 1,
        "source": "hybrid",
        "occurredAt": "2026-08-06T09:00:00.000",
    }
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "recommendation_event_store.record_click",
        AsyncMock(return_value=expected),
    )
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "redis_service.log_attributed_click",
        AsyncMock(return_value=None),
    )
    ledger_click = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.recommendation_attribution_service."
        "commerce_outcome_ledger_service.record_click",
        ledger_click,
    )

    assert await service.record_click("u1", "request-1", "p1", 1) == expected
    ledger_click.assert_awaited_once_with(expected, "u1")
