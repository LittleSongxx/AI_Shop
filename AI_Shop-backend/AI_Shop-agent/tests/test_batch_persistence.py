from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.final_offer_snapshot_service import FinalOfferSnapshotService
from app.services.product_decision_feature_service import ProductDecisionFeatureService


def _acquire_for(cursor):
    @asynccontextmanager
    async def context():
        yield cursor

    return context


class _BatchCursor:
    def __init__(self, rows=None, *, with_executemany: bool = True):
        self.rows = rows or []
        self.execute_calls: list[tuple[str, object]] = []
        self.executemany_calls: list[tuple[str, object]] = []
        if with_executemany:
            self.executemany = self._executemany

    async def _executemany(self, sql, params):
        self.executemany_calls.append((sql, params))

    async def execute(self, sql, params=None):
        self.execute_calls.append((sql, params))

    async def fetchall(self):
        return self.rows


@pytest.mark.asyncio
async def test_decision_features_use_one_batch_write_and_one_batch_read():
    write_cursor = _BatchCursor()
    read_cursor = _BatchCursor(
        rows=[
            {
                "product_id": "p1",
                "feature_key": "brand",
                "feature_value": "Sony",
                "source_type": "STRUCTURED_ATTRIBUTE",
                "evidence_json": json.dumps({"productId": "p1"}),
                "confidence": 1,
            }
        ]
    )
    cursors = iter((write_cursor, read_cursor))
    service = ProductDecisionFeatureService()
    products = [
        {
            "product_id": "p1",
            "brand": "Sony",
            "property_values": [{"property_name": "续航", "property_value": "20h"}],
        },
        {
            "product_id": "p2",
            "brand": "Acme",
            "property_values": [{"property_name": "内存", "property_value": "16GB"}],
        },
    ]

    def acquire():
        return _acquire_for(next(cursors))()

    with patch("app.services.product_decision_feature_service.acquire", side_effect=acquire):
        annotated = await service.annotate_candidates(products)

    assert len(write_cursor.executemany_calls) == 1
    assert len(write_cursor.executemany_calls[0][1]) == 4
    assert len(write_cursor.execute_calls) == 0
    assert len(read_cursor.execute_calls) == 1
    assert annotated[0]["decisionFeatures"][0]["value"] == "Sony"
    assert annotated[1]["decisionFeatures"]


@pytest.mark.asyncio
async def test_decision_feature_write_falls_back_when_cursor_has_no_executemany():
    cursor = _BatchCursor(with_executemany=False)
    service = ProductDecisionFeatureService()
    product = {"product_id": "p1", "brand": "Sony"}

    with patch(
        "app.services.product_decision_feature_service.acquire",
        side_effect=lambda: _acquire_for(cursor)(),
    ):
        await service.ensure_structured_features(product)

    assert len(cursor.execute_calls) == 1
    assert cursor.executemany_calls == []


@pytest.mark.asyncio
async def test_offer_snapshot_persistence_prefers_executemany():
    cursor = _BatchCursor()
    service = FinalOfferSnapshotService()
    expires = datetime.now(timezone.utc)
    rows = [
        ("s1", "u1", "p1", "sku1", {"productId": "p1"}, expires),
        ("s2", "u1", "p2", "sku2", {"productId": "p2"}, expires),
    ]

    with patch(
        "app.services.final_offer_snapshot_service.acquire",
        side_effect=lambda: _acquire_for(cursor)(),
    ):
        await service._persist_many(rows)

    assert len(cursor.executemany_calls) == 1
    assert len(cursor.executemany_calls[0][1]) == 2
    assert len(cursor.execute_calls) == 0
    assert json.loads(cursor.executemany_calls[0][1][0][4])["productId"] == "p1"


@pytest.mark.asyncio
async def test_offer_snapshot_persistence_keeps_legacy_cursor_fallback():
    cursor = _BatchCursor(with_executemany=False)
    service = FinalOfferSnapshotService()
    expires = datetime.now(timezone.utc)
    rows = [
        ("s1", "u1", "p1", "sku1", {"productId": "p1"}, expires),
        ("s2", "u1", "p2", "sku2", {"productId": "p2"}, expires),
    ]

    with patch(
        "app.services.final_offer_snapshot_service.acquire",
        side_effect=lambda: _acquire_for(cursor)(),
    ):
        await service._persist_many(rows)

    assert len(cursor.execute_calls) == 2
    assert cursor.executemany_calls == []


@pytest.mark.asyncio
async def test_offer_snapshot_propagates_sku_allowlist_and_rejects_server_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FinalOfferSnapshotService()
    offer_snapshot_batch = AsyncMock(
        return_value={
            "products": [
                {
                    "product_id": "p1",
                    "product_name": "mixed title",
                    "status": 1,
                    "in_stock": True,
                    "selected_sku": {
                        "property_value_id_hash": "sku-disallowed",
                        "property_value_ids": "electric",
                        "price": 100,
                        "stock": 1,
                    },
                }
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.final_offer_snapshot_service.java_internal_client.offer_snapshot_batch",
        offer_snapshot_batch,
    )
    monkeypatch.setattr(
        "app.services.final_offer_snapshot_service.java_internal_client.estimate_single_sku_offers",
        AsyncMock(return_value=[]),
    )
    persist = AsyncMock()
    monkeypatch.setattr(service, "_persist_many", persist)

    result = await service.build(
        "u1",
        [
            {
                "product_id": "p1",
                "constraint_allowed_sku_keys": ["sku-acoustic"],
            }
        ],
    )

    assert result == []
    offer_snapshot_batch.assert_awaited_once_with(
        "u1",
        ["p1"],
        allowed_sku_keys_by_product={"p1": ["sku-acoustic"]},
    )
    persist.assert_awaited_once_with([])
