import json
from unittest.mock import AsyncMock

import pytest

from app.services.java_internal_client import java_internal_client
from app.services.product_search_pipeline import (
    ProductSearchResult,
    ProductSearchTrace,
    product_search_pipeline,
)
from app.services.product_service import (
    ProductService,
    filter_known_available_products,
    format_search_tool_message,
)
from app.services.search_recommend_service import search_recommend_service
from app.services.shopping_profile_service import shopping_profile_service
from app.utils.biz_payload import build_order_payload, build_product_payload


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


def test_order_payload_preserves_zero_valued_wait_payment_status():
    assistant, biz_data = build_order_payload(
        [
            {
                "order_id": "SM1",
                "order_status": 0,
                "amount": 24,
                "pay_scene": 0,
            }
        ],
        {"SM1": []},
    )

    card = json.loads(assistant or "[]")[0]
    assert json.loads(biz_data or "[]") == ["SM1"]
    assert card["orderStatus"] == 0
    assert card["orderStatusName"] == "待付款"
    assert card["payScene"] == 0


def test_out_of_stock_message_does_not_fall_back_to_hot_sale_claim():
    message = format_search_tool_message("测试手机", None, [], "out_of_stock")

    assert "均已售罄" in message
    assert "热销" not in message


@pytest.mark.parametrize("source", ["constraint_miss", "no_match", "none"])
def test_non_authoritative_empty_search_discloses_retrieval_limit(source: str):
    message = format_search_tool_message("索尼耳机", None, [], source)

    assert "本次检索" in message
    assert "不能据此断言平台无货" in message


def test_plain_empty_search_discloses_retrieval_limit():
    message = format_search_tool_message("索尼耳机", None, [], "unknown")

    assert "本次检索" in message
    assert "不能据此断言平台无货" in message


def test_comparison_incomplete_message_never_presents_a_one_sided_result():
    message = format_search_tool_message(
        "WH-1000XM6和十周年版降噪耳机如何比较",
        None,
        [],
        "comparison_incomplete",
    )

    assert "对比不完整" in message
    assert "单边对比" in message
    assert "找到" not in message


@pytest.mark.asyncio
async def test_comparison_incomplete_blocks_browse_and_hot_sale_fallback(monkeypatch):
    service = ProductService()
    monkeypatch.setattr(
        shopping_profile_service,
        "get_effective_profile",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        shopping_profile_service,
        "should_clarify",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(service, "_mission_for_request", AsyncMock(return_value={}))
    trace = ProductSearchTrace(
        query_plan={},
        result_source="comparison_incomplete",
        comparison_coverage={"wh1000xm6": 1, "十周年版": 0},
        comparison_complete=False,
        incomplete_reason="MISSING_COMPARISON_TARGETS",
    )
    monkeypatch.setattr(
        product_search_pipeline,
        "search",
        AsyncMock(return_value=ProductSearchResult([], ["xm6"], trace)),
    )
    browse = AsyncMock(return_value=[{"product_id": "unrelated"}])
    hot_sale = AsyncMock(return_value=[{"product_id": "unrelated"}])
    monkeypatch.setattr(search_recommend_service, "load_recommend_products", browse)
    monkeypatch.setattr(search_recommend_service, "load_hot_sale", hot_sale)

    _assistant, _biz, _biz_type, products, source = await service.search_products(
        "u1",
        "WH-1000XM6和十周年版降噪耳机如何比较",
        user_text="给我推荐并比较 WH-1000XM6 和十周年版降噪耳机",
    )

    assert products == []
    assert source == "comparison_incomplete"
    browse.assert_not_awaited()
    hot_sale.assert_not_awaited()
