import json
from unittest.mock import AsyncMock

import pytest

from app.services.java_internal_client import java_internal_client
from app.services.product_service import (
    ProductService,
    filter_known_available_products,
    format_search_tool_message,
)
from app.utils.biz_payload import build_product_payload


def test_filter_known_available_products_keeps_unknown_stock():
    products = [
        {"product_id": "1", "total_stock": 0},
        {"product_id": "2", "in_stock": False},
        {"product_id": "3"},
        {"product_id": "4", "total_stock": 6},
    ]

    assert [p["product_id"] for p in filter_known_available_products(products)] == ["3", "4"]


@pytest.mark.asyncio
async def test_load_products_by_ids_attaches_batch_stock_brand_and_skus(monkeypatch):
    snapshot = {
        "products": [
            {
                "product_id": "p1",
                "product_name": "测试手机",
                "status": 1,
                "min_price": 1999,
                "max_price": 2499,
            }
        ],
        "property_values": [
            {
                "product_id": "p1",
                "property_name": "品牌",
                "property_value": "测试品牌",
            }
        ],
        "skus": [{"product_id": "p1", "price": 1999}],
        "total_stocks": {"p1": 8},
    }
    monkeypatch.setattr(java_internal_client, "snapshot_batch", AsyncMock(return_value=snapshot))

    products = await ProductService()._load_products_by_ids(["p1"])

    assert products[0]["brand"] == "测试品牌"
    assert products[0]["total_stock"] == 8
    assert products[0]["in_stock"] is True
    assert products[0]["skus"][0]["price"] == 1999


@pytest.mark.asyncio
async def test_load_products_by_ids_does_not_fan_out_when_batch_is_unavailable(monkeypatch):
    monkeypatch.setattr(java_internal_client, "snapshot_batch", AsyncMock(return_value=None))
    detail = AsyncMock()
    monkeypatch.setattr(java_internal_client, "get_product_detail", detail)

    products = await ProductService()._load_products_by_ids(["p1", "p2"])

    assert products == []
    detail.assert_not_awaited()


def test_product_payload_contains_guidance_and_stock_fields():
    assistant, biz_data = build_product_payload(
        [
            {
                "product_id": "p1",
                "product_name": "测试手机",
                "cover": "a.jpg,b.jpg",
                "min_price": 1999,
                "max_price": 2499,
                "brand": "测试品牌",
                "status": 1,
                "total_stock": 8,
                "_recommend_reason": "符合预算、适合办公",
            }
        ]
    )

    card = json.loads(assistant)[0]
    assert json.loads(biz_data or "[]") == ["p1"]
    assert card == {
        "productId": "p1",
        "productName": "测试手机",
        "cover": "a.jpg",
        "minPrice": 1999,
        "maxPrice": 2499,
        "brand": "测试品牌",
        "totalStock": 8,
        "inStock": True,
        "availability": "ON_SALE",
        "reason": "符合预算、适合办公",
    }


def test_out_of_stock_message_does_not_fall_back_to_hot_sale_claim():
    message = format_search_tool_message("测试手机", None, [], "out_of_stock")

    assert "均已售罄" in message
    assert "热销" not in message
