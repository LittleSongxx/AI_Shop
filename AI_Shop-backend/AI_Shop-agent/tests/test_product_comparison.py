import json
from unittest.mock import AsyncMock

import pytest

from app.graph.nodes import tools_node
from app.services.final_offer_snapshot_service import final_offer_snapshot_service
from app.services.mcp_tool_router import mcp_tool_router
from app.services.product_comparison_service import (
    ComparisonCandidateDenied,
    ComparisonSnapshotMissing,
    ProductComparisonError,
    normalize_comparison_ids,
    product_comparison_service,
)
from app.services.product_decision_feature_service import product_decision_feature_service
from app.services.product_service import product_service
from app.services.shopping_mission_service import shopping_mission_service


def test_comparison_requires_two_to_four_unique_products():
    assert normalize_comparison_ids(["p1", "p1", "p2"]) == ["p1", "p2"]
    with pytest.raises(ProductComparisonError, match="2 到 4"):
        normalize_comparison_ids(["p1"])
    with pytest.raises(ProductComparisonError, match="2 到 4"):
        normalize_comparison_ids(["p1", "p2", "p3", "p4", "p5"])


@pytest.mark.asyncio
async def test_comparison_rejects_product_outside_recent_candidates(monkeypatch):
    monkeypatch.setattr(shopping_mission_service, "load", AsyncMock(return_value={}))
    monkeypatch.setattr(
        shopping_mission_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    load_products = AsyncMock()
    monkeypatch.setattr(product_service, "_load_products_by_ids", load_products)

    with pytest.raises(ComparisonCandidateDenied):
        await product_comparison_service.compare("u1", ["p1", "other"])

    load_products.assert_not_awaited()


@pytest.mark.asyncio
async def test_comparison_uses_user_bound_final_offers_and_verified_features(monkeypatch):
    mission = {
        "candidateProducts": [
            {
                "productId": "p1",
                "estimatedPayable": 100,
                "recommendation": {"bestFor": "通勤降噪", "tradeoff": "不适合高强度运动"},
                "sourceMessageId": 10,
            },
            {
                "productId": "p2",
                "estimatedPayable": 200,
                "sourceMessageId": 10,
            },
        ]
    }
    monkeypatch.setattr(shopping_mission_service, "load", AsyncMock(return_value=mission))
    monkeypatch.setattr(
        shopping_mission_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    products = [
        {
            "product_id": "p1",
            "product_name": "耳机 A",
            "cover": "a.jpg",
            "properties": [{"propertyName": "降噪", "propertyValues": [{"propertyValue": "支持"}]}],
        },
        {
            "product_id": "p2",
            "product_name": "耳机 B",
            "cover": "b.jpg",
            "properties": [],
        },
    ]
    monkeypatch.setattr(
        product_service,
        "_load_products_by_ids",
        AsyncMock(return_value=products),
    )
    annotated = [
        {
            **products[0],
            "decisionFeatures": [
                {"key": "noise_cancellation", "value": "支持", "reviewStatus": "VERIFIED"}
            ],
        },
        {**products[1], "decisionFeatures": []},
    ]
    monkeypatch.setattr(
        product_decision_feature_service,
        "annotate_candidates",
        AsyncMock(return_value=annotated),
    )
    offers = [
        {
            **annotated[0],
            "status": "1",
            "in_stock": True,
            "total_stock": 8,
            "base_price": 150,
            "estimated_payable": 120,
            "offer_snapshot_id": "offer-p1",
            "sku_key": "sku-p1",
            "coupon_status": "AVAILABLE",
            "coupon": {"couponName": "满减券", "estimatedDiscount": 30},
            "quote_expires_at": "2026-08-10T12:00:00Z",
            "delivery_promise": "明日送达",
        },
        {
            **annotated[1],
            "status": "1",
            "in_stock": True,
            "total_stock": 3,
            "base_price": 260,
            "estimated_payable": 200,
            "offer_snapshot_id": "offer-p2",
            "sku_key": "sku-p2",
            "coupon_status": "UNAVAILABLE",
            "coupon": None,
            "quote_expires_at": "2026-08-10T12:00:00Z",
        },
    ]
    monkeypatch.setattr(final_offer_snapshot_service, "build", AsyncMock(return_value=offers))

    result = await product_comparison_service.compare("u1", ["p1", "p2"])
    card = json.loads(result.assistant_cards or "{}")

    assert result.biz_type == "product_comparison"
    assert card["type"] == "PRODUCT_COMPARISON"
    assert card["snapshotType"] == "REAL_TIME"
    assert card["products"][0]["minPrice"] == 120
    assert card["products"][0]["priceChanged"] is True
    assert card["products"][0]["offerSnapshotId"] == "offer-p1"
    assert card["products"][0]["coupon"]["couponName"] == "满减券"
    assert card["products"][1]["availability"] == "ON_SALE"
    assert "降噪" in card["dimensions"]


@pytest.mark.asyncio
async def test_comparison_fails_when_any_live_snapshot_is_missing(monkeypatch):
    monkeypatch.setattr(
        shopping_mission_service,
        "load",
        AsyncMock(
            return_value={
                "candidateProducts": [
                    {"productId": "p1"},
                    {"productId": "p2"},
                ]
            }
        ),
    )
    monkeypatch.setattr(
        shopping_mission_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    monkeypatch.setattr(
        product_service,
        "_load_products_by_ids",
        AsyncMock(
            return_value=[
                {"product_id": "p1", "product_name": "A"},
                {"product_id": "p2", "product_name": "B"},
            ]
        ),
    )
    monkeypatch.setattr(
        product_decision_feature_service,
        "annotate_candidates",
        AsyncMock(
            return_value=[
                {"product_id": "p1", "product_name": "A"},
                {"product_id": "p2", "product_name": "B"},
            ]
        ),
    )
    monkeypatch.setattr(
        final_offer_snapshot_service,
        "build",
        AsyncMock(
            return_value=[
                {
                    "product_id": "p1",
                    "product_name": "A",
                    "status": "1",
                    "in_stock": True,
                    "estimated_payable": 100,
                    "offer_snapshot_id": "offer-p1",
                    "sku_key": "sku-p1",
                }
            ]
        ),
    )

    with pytest.raises(ComparisonSnapshotMissing):
        await product_comparison_service.compare("u1", ["p1", "p2"])


@pytest.mark.asyncio
async def test_verified_ui_selection_overrides_model_comparison_args(monkeypatch):
    invoke = AsyncMock(
        return_value=type(
            "Result",
            (),
            {
                "content": "ok",
                "assistant_cards": None,
                "biz_type": None,
                "biz_data": None,
                "to_tool_message": lambda self: self.content,
                "to_biz_dict": lambda self: None,
            },
        )()
    )
    monkeypatch.setattr(mcp_tool_router, "invoke", invoke)
    monkeypatch.setattr(
        "app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False)
    )
    state = {
        "agent_msg": {"runId": "run-compare"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [],
        "pending_tool_calls": [
            {
                "id": "call-1",
                "name": "COMPARE_PRODUCTS",
                "args": {"productIds": ["attacker-1", "attacker-2"]},
            }
        ],
        "comparison_product_ids": ["p1", "p2"],
        "tools_called": [],
        "react_round": 1,
        "rag_mode": "conditional",
        "rag_queries": [],
        "rag_retrieval_count": 0,
        "rag_source_refs": [],
    }

    await tools_node(state)

    assert invoke.await_args.args[1]["productIds"] == ["p1", "p2"]
