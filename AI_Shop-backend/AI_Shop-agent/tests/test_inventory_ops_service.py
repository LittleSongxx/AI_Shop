from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.inventory_ops_service import InventoryOpsService


def _settings(enabled: bool = True):
    return SimpleNamespace(
        data_analyst_enabled=enabled,
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


@pytest.mark.asyncio
async def test_inventory_ops_builds_manual_priorities_from_two_governed_views(monkeypatch):
    inventory = [
        {
            "product_id": "p1",
            "product_name": "热销缺货商品",
            "property_value_id_hash": "sku-1",
            "stock": 0,
            "risk_level": "OUT_OF_STOCK",
        },
        {
            "product_id": "p2",
            "product_name": "低库存商品",
            "property_value_id_hash": "sku-2",
            "stock": 5,
            "risk_level": "LOW_STOCK",
        },
        {
            "product_id": "p3",
            "product_name": "无销量低库存商品",
            "property_value_id_hash": "sku-3",
            "stock": 8,
            "risk_level": "LOW_STOCK",
        },
    ]
    sales = [
        {"product_id": "p1", "paid_units": 24, "refunded_units": 4},
        {"product_id": "p2", "paid_units": 6, "refunded_units": 1},
    ]
    execute = AsyncMock(side_effect=[inventory, sales])
    monkeypatch.setattr("app.services.inventory_ops_service.get_settings", _settings)
    monkeypatch.setattr("app.services.inventory_ops_service._execute_sql", execute)
    events = _stub_episode(monkeypatch)

    result = await InventoryOpsService().suggestions(
        admin_id="admin",
        lookback_days=30,
        limit=10,
    )

    assert result["status"] == "SUCCEEDED"
    assert result["manualOnly"] is True
    assert result["lineage"] == [
        "analytics_inventory_risk",
        "analytics_product_sales_daily",
    ]
    assert [row["priority"] for row in result["suggestions"]] == [
        "CRITICAL",
        "MEDIUM",
        "LOW",
    ]
    assert result["suggestions"][0]["productNetUnits"] == 20
    assert all(row["manualOnly"] for row in result["suggestions"])
    assert all("purchaseQuantity" not in row for row in result["suggestions"])
    assert execute.await_count == 2
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
