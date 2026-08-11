from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.inventory_ops_service import InventoryOpsService


def _settings(enabled: bool = True):
    return SimpleNamespace(
        data_analyst_enabled=enabled,
        inventory_ops_enabled=True,
        analytics_max_days=90,
        analytics_max_rows=200,
        analytics_query_timeout_ms=3000,
    )


def _stub_episode(monkeypatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(
        "app.services.inventory_ops_service.episode_service.start_run",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.services.inventory_ops_service.episode_service.record_step",
        lambda event, **_kwargs: events.append(event),
    )
    monkeypatch.setattr(
        "app.services.inventory_ops_service.episode_service.finish_run",
        lambda *_args, **_kwargs: None,
    )
    return events


def test_inventory_forecast_id_is_stable_and_fits_schema():
    row = {"product_id": "product-1", "sku_key": "sku-1"}
    first = InventoryOpsService._forecast_id(row, "2026-08-11")
    second = InventoryOpsService._forecast_id(row, "2026-08-11")

    assert first == second
    assert len(first) == 64


@pytest.mark.asyncio
async def test_inventory_ops_builds_sku_forecasts_from_governed_view(monkeypatch):
    forecasts = [
        {
            "snapshot_date": "2026-08-11",
            "product_id": "p1",
            "product_name": "热销缺货商品",
            "sku_key": "sku-1",
            "current_stock": 0,
            "risk_level": "OUT_OF_STOCK",
            "inbound_quantity": 0,
            "ewma_daily_demand": 2,
            "lead_time_days": 7,
            "safety_stock": 2,
            "review_period_days": 14,
            "min_order_quantity": 5,
            "reorder_point": 16,
            "suggested_replenish_quantity": 45,
            "coverage_days": 0,
            "confidence": 0.8,
        },
        {
            "snapshot_date": "2026-08-11",
            "product_id": "p2",
            "product_name": "低库存商品",
            "sku_key": "sku-2",
            "current_stock": 5,
            "risk_level": "LOW_STOCK",
            "inbound_quantity": 0,
            "ewma_daily_demand": 0.4,
            "lead_time_days": 7,
            "safety_stock": 3,
            "review_period_days": 14,
            "min_order_quantity": 10,
            "reorder_point": 5.8,
            "suggested_replenish_quantity": 10,
            "coverage_days": 12.5,
            "confidence": 0.5,
        },
        {
            "snapshot_date": "2026-08-11",
            "product_id": "p3",
            "product_name": "无销量低库存商品",
            "sku_key": "sku-3",
            "current_stock": 8,
            "risk_level": "LOW_STOCK",
            "inbound_quantity": 0,
            "ewma_daily_demand": 0,
            "lead_time_days": 7,
            "safety_stock": 0,
            "review_period_days": 14,
            "min_order_quantity": 1,
            "reorder_point": 0,
            "suggested_replenish_quantity": 0,
            "coverage_days": None,
            "confidence": 0,
        },
    ]
    execute = AsyncMock(return_value=forecasts)
    persist = AsyncMock(return_value=(3, None))
    monkeypatch.setattr("app.services.inventory_ops_service.get_settings", _settings)
    monkeypatch.setattr("app.services.inventory_ops_service._execute_sql", execute)
    monkeypatch.setattr(InventoryOpsService, "_persist_forecasts", persist)
    events = _stub_episode(monkeypatch)

    result = await InventoryOpsService().suggestions(
        admin_id="admin",
        lookback_days=30,
        limit=10,
    )

    assert result["status"] == "SUCCEEDED"
    assert result["manualOnly"] is True
    assert result["lineage"] == [
        "analytics_inventory_forecast",
        "analytics_inventory_risk",
    ]
    assert [row["priority"] for row in result["suggestions"]] == [
        "CRITICAL",
        "HIGH",
        "LOW",
    ]
    assert result["lookbackDays"] == 28
    assert result["suggestions"][0]["suggestedReplenishQuantity"] == 45
    assert result["suggestions"][1]["suggestedReplenishQuantity"] % 10 == 0
    assert all(row["manualOnly"] for row in result["suggestions"])
    assert all("purchaseOrder" not in row for row in result["suggestions"])
    assert execute.await_count == 1
    persist.assert_awaited_once()
    assert events == ["INVENTORY_OPS_PLAN", "INVENTORY_OPS_RESULT"]


@pytest.mark.asyncio
async def test_inventory_ops_is_closed_when_data_analyst_is_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.services.inventory_ops_service.get_settings",
        lambda: _settings(enabled=False),
    )
    execute = AsyncMock()
    monkeypatch.setattr("app.services.inventory_ops_service._execute_sql", execute)

    result = await InventoryOpsService().suggestions(admin_id="admin")

    assert result == {
        "status": "DISABLED",
        "warnings": ["DATA_ANALYST_ENABLED=false"],
    }
    execute.assert_not_awaited()
