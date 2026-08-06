import json
from unittest.mock import AsyncMock

import pytest

from app.api.routes.agent import report_click
from app.auth.token_service import TokenUserInfo
from app.constants import (
    IMPRESSION_ATTRIBUTION_TTL,
    REDIS_AGENT_IMPRESSION_REQUEST,
)
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)
from app.services.redis_service import RedisService


class FakePipeline:
    def __init__(self, redis, transaction: bool):
        self.redis = redis
        self.ops: list[tuple[str, tuple]] = []
        redis.transactions.append(transaction)

    def setex(self, *args):
        self.ops.append(("setex", args))

    def lpush(self, *args):
        self.ops.append(("lpush", args))

    def ltrim(self, *args):
        self.ops.append(("ltrim", args))

    def expire(self, *args):
        self.ops.append(("expire", args))

    async def execute(self):
        for name, args in self.ops:
            await getattr(self.redis, name)(*args)


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.transactions: list[bool] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.expirations[key] = ttl

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self, transaction)

    async def eval(self, _script: str, numkeys: int, *args):
        assert numkeys == 3
        snapshot_key, click_key, dedup_key = args[:3]
        user_id, product_id, request_id = args[3:6]
        position, ts, max_entries, log_ttl, dedup_ttl = map(int, args[6:])
        raw = self.values.get(snapshot_key)
        if not raw:
            return []
        snapshot = json.loads(raw)
        product_ids = snapshot.get("productIds") or []
        if (
            snapshot.get("userId") != user_id
            or position < 1
            or position > len(product_ids)
            or str(product_ids[position - 1]) != product_id
        ):
            return []
        source = str(snapshot.get("source") or "")[:40]
        if dedup_key in self.values:
            return ["DUPLICATE", source, str(position)]
        event = {
            "ts": ts,
            "productId": product_id,
            "source": source,
            "position": position,
            "requestId": request_id,
        }
        await self.lpush(click_key, json.dumps(event, ensure_ascii=False))
        await self.ltrim(click_key, 0, max_entries - 1)
        await self.expire(click_key, log_ttl)
        await self.setex(dedup_key, dedup_ttl, "1")
        return ["RECORDED", source, str(position)]


@pytest.mark.asyncio
async def test_impression_snapshot_validates_user_product_and_one_based_position(monkeypatch):
    service = RedisService()
    fake_redis = FakeRedis()
    service._client = fake_redis
    monkeypatch.setattr(service, "_push_capped", AsyncMock())
    request_id = "0123456789abcdef0123456789abcdef"

    await service.log_impression(
        "u1",
        ["p1", "p2", "p3"],
        query="手机",
        source="hybrid",
        request_id=request_id,
    )

    assert len(fake_redis.values) == 1
    snapshot_key = next(iter(fake_redis.values))
    assert snapshot_key.startswith(REDIS_AGENT_IMPRESSION_REQUEST)
    assert request_id not in snapshot_key
    assert fake_redis.expirations[snapshot_key] == IMPRESSION_ATTRIBUTION_TTL
    assert json.loads(fake_redis.values[snapshot_key])["productIds"] == ["p1", "p2", "p3"]
    assert fake_redis.transactions == [True]
    assert len(next(iter(fake_redis.lists.values()))) == 1
    assert await service.validate_click_attribution("u1", request_id, "p2", 2) == {
        "source": "hybrid",
        "position": 2,
        "requestId": request_id,
        "productId": "p2",
    }
    assert await service.validate_click_attribution("u2", request_id, "p2", 2) is None
    assert await service.validate_click_attribution("u1", request_id, "p3", 2) is None
    assert await service.validate_click_attribution("u1", request_id, "p2", 1) is None
    assert await service.validate_click_attribution("u1", request_id, "p2", 0) is None


@pytest.mark.asyncio
async def test_report_click_rejects_invalid_attribution_without_logging(monkeypatch):
    record_click = AsyncMock(return_value=None)
    monkeypatch.setattr(
        recommendation_attribution_service, "record_click", record_click
    )

    response = await report_click(
        productId="p2",
        requestId="0123456789abcdef0123456789abcdef",
        position=2,
        user=TokenUserInfo(user_id="u1"),
    )

    assert response.status == "error"
    assert response.code == 600
    record_click.assert_awaited_once_with(
        "u1", "0123456789abcdef0123456789abcdef", "p2", 2
    )


@pytest.mark.asyncio
async def test_report_click_logs_only_canonical_snapshot_values(monkeypatch):
    request_id = "0123456789abcdef0123456789abcdef"
    record_click = AsyncMock(
        return_value={
            "source": "hot_sale",
            "position": 2,
            "requestId": request_id,
            "productId": "p2",
            "occurredAt": "2026-08-06T09:00:00.000",
        }
    )
    monkeypatch.setattr(
        recommendation_attribution_service,
        "record_click",
        record_click,
    )

    response = await report_click(
        productId="p2",
        requestId=request_id,
        position=2,
        user=TokenUserInfo(user_id="u1"),
    )

    assert response.status == "success"
    assert response.data == {
        "source": "hot_sale",
        "position": 2,
        "requestId": request_id,
        "productId": "p2",
        "occurredAt": "2026-08-06T09:00:00.000",
    }
    record_click.assert_awaited_once_with(
        "u1",
        request_id,
        "p2",
        2,
    )


@pytest.mark.asyncio
async def test_attributed_click_is_atomically_deduplicated_per_impression(monkeypatch):
    service = RedisService()
    fake_redis = FakeRedis()
    service._client = fake_redis
    monkeypatch.setattr(service, "_push_capped", AsyncMock())
    request_id = "0123456789abcdef0123456789abcdef"
    await service.log_impression(
        "u1", ["p1", "p2"], source="hybrid", request_id=request_id
    )

    first = await service.log_attributed_click("u1", request_id, "p2", 2)
    duplicate = await service.log_attributed_click("u1", request_id, "p2", 2)

    assert first and first["duplicate"] is False
    assert duplicate and duplicate["duplicate"] is True
    click_lists = [
        values
        for key, values in fake_redis.lists.items()
        if ":click:userId:" in key
    ]
    assert len(click_lists) == 1
    assert len(click_lists[0]) == 1
