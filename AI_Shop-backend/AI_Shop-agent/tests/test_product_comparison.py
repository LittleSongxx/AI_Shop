import json
from unittest.mock import AsyncMock

import pytest

from app.graph.nodes import tools_node
from app.services.mcp_tool_router import mcp_tool_router
from app.services.product_comparison_service import (
    ComparisonCandidateDenied,
    ComparisonSnapshotMissing,
    ProductComparisonError,
    normalize_comparison_ids,
    product_comparison_service,
)
from app.services.product_snapshot_service import product_snapshot_service
from app.services.shopping_need_service import shopping_need_service


def test_comparison_requires_two_to_four_unique_products():
    assert normalize_comparison_ids(["p1", "p1", "p2"]) == ["p1", "p2"]
    with pytest.raises(ProductComparisonError, match="2 到 4"):
        normalize_comparison_ids(["p1"])
    with pytest.raises(ProductComparisonError, match="2 到 4"):
        normalize_comparison_ids(["p1", "p2", "p3", "p4", "p5"])


@pytest.mark.asyncio
async def test_comparison_rejects_product_outside_recent_candidates(monkeypatch):
    monkeypatch.setattr(shopping_need_service, "load", AsyncMock(return_value={}))
    monkeypatch.setattr(
        shopping_need_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    snapshot = AsyncMock()
    monkeypatch.setattr(product_snapshot_service, "build_snapshot_json", snapshot)

    with pytest.raises(ComparisonCandidateDenied):
        await product_comparison_service.compare("u1", ["p1", "other"])

    snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_comparison_uses_live_price_stock_and_property_snapshots(monkeypatch):
    need = {
        "candidateProducts": [
            {
                "productId": "p1",
                "minPrice": 100,
                "sourceMessageId": 10,
            },
            {
                "productId": "p2",
                "minPrice": 200,
                "sourceMessageId": 10,
            },
        ]
    }
    monkeypatch.setattr(shopping_need_service, "load", AsyncMock(return_value=need))
    monkeypatch.setattr(
        shopping_need_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    snapshots = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "productId": "p1",
                    "productName": "耳机 A",
                    "minPrice": 120,
                    "maxPrice": 150,
                    "status": 1,
                    "inStock": True,
                    "totalStock": 8,
                    "properties": [
                        {
                            "propertyName": "降噪",
                            "propertyValues": [{"propertyValue": "支持"}],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "productId": "p2",
                    "productName": "耳机 B",
                    "minPrice": 200,
                    "status": 1,
                    "inStock": False,
                    "totalStock": 0,
                    "properties": [],
                },
                ensure_ascii=False,
            ),
        ]
    )
    monkeypatch.setattr(product_snapshot_service, "build_snapshot_json", snapshots)

    result = await product_comparison_service.compare("u1", ["p1", "p2"])
    card = json.loads(result.assistant_cards or "{}")

    assert result.biz_type == "product_comparison"
    assert card["type"] == "PRODUCT_COMPARISON"
    assert card["snapshotType"] == "REAL_TIME"
    assert card["products"][0]["minPrice"] == 120
    assert card["products"][0]["priceChanged"] is True
    assert card["products"][1]["availability"] == "OUT_OF_STOCK"
    assert "降噪" in card["dimensions"]


@pytest.mark.asyncio
async def test_comparison_fails_when_any_live_snapshot_is_missing(monkeypatch):
    monkeypatch.setattr(
        shopping_need_service,
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
        shopping_need_service,
        "allowed_candidate_ids",
        AsyncMock(return_value=["p1", "p2"]),
    )
    monkeypatch.setattr(
        product_snapshot_service,
        "build_snapshot_json",
        AsyncMock(side_effect=["{}", None]),
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
