from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.java_internal_client import java_internal_client
from app.services.support_service import SupportService


@pytest.mark.asyncio
async def test_handoff_context_separates_model_hints_from_java_authority(monkeypatch):
    get_order = AsyncMock(
        return_value={
            "order_id": "order-1",
            "user_id": "u1",
            "order_status": 2,
            "order_time": "2026-08-17 10:00:00",
            "subject": "蓝牙耳机",
            "items": [
                {
                    "order_item_id": "item-1",
                    "product_id": "product-1",
                    "product_name": "降噪耳机",
                    "property_info": "黑色",
                    "buy_count": 1,
                    "order_item_status": 1,
                }
            ],
        }
    )
    monkeypatch.setattr(java_internal_client, "get_order", get_order)
    monkeypatch.setattr(
        java_internal_client, "get_order_item", AsyncMock(return_value=None)
    )
    history = [
        {"role": "user", "content": f"第{i}条，手机号13800138000"}
        for i in range(8)
    ]

    context = await SupportService().build_handoff_context(
        "u1",
        "订单坏了，地址：广东省深圳市南山区科技园，联系13800138000",
        {
            "intent": "DAMAGED_OR_WRONG_ITEM",
            "confidence": 0.91,
            "sentiment": "NEGATIVE",
            "urgency": "HIGH",
            "risk_level": "MEDIUM",
            "handoff_reason": "POLICY_REQUIRES_HUMAN",
            "entities": {"orderId": "order-1", "phone": "13800138000"},
        },
        history=history,
    )

    assert context["schemaVersion"] == "aishop-support-handoff/v1"
    assert len(context["recentConversation"]) == 6
    assert all(
        len(item["content"]) <= 200 for item in context["recentConversation"]
    )
    assert "13800138000" not in json.dumps(context, ensure_ascii=False)
    assert "科技园" not in context["request"]
    assert context["unverifiedHints"]["orderId"] == "order-1"
    assert "authority" not in context["unverifiedHints"]
    assert context["authoritativeOrders"] == [
        {
            "authority": "JAVA_ORDER_SERVICE",
            "ownershipVerified": True,
            "orderId": "order-1",
            "orderStatus": 2,
            "orderTime": "2026-08-17 10:00:00",
            "subject": "蓝牙耳机",
            "items": [
                {
                    "orderItemId": "item-1",
                    "productId": "product-1",
                    "productName": "降噪耳机",
                    "propertyInfo": "黑色",
                    "quantity": 1,
                    "orderItemStatus": 1,
                }
            ],
        }
    ]
    get_order.assert_awaited_once_with("order-1")


@pytest.mark.asyncio
async def test_handoff_context_never_marks_cross_user_order_as_authoritative(
    monkeypatch,
):
    monkeypatch.setattr(
        java_internal_client,
        "get_order",
        AsyncMock(
            return_value={
                "order_id": "order-other",
                "user_id": "other-user",
                "order_status": 1,
                "items": [],
            }
        ),
    )

    context = await SupportService().build_handoff_context(
        "u1",
        "帮我查这个订单",
        {"entities": {"orderId": "order-other"}},
    )

    assert context["unverifiedHints"] == {"orderId": "order-other"}
    assert context["authoritativeOrders"] == []


@pytest.mark.asyncio
async def test_handoff_context_fails_closed_when_java_order_service_is_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        java_internal_client,
        "get_order",
        AsyncMock(side_effect=OSError("unavailable")),
    )

    context = await SupportService().build_handoff_context(
        "u1", "查订单", {"entities": {"orderId": "order-1"}}
    )

    assert context["authoritativeOrders"] == []
    assert context["unverifiedHints"] == {"orderId": "order-1"}


@pytest.mark.asyncio
async def test_verified_order_item_takes_priority_over_model_order_hints(monkeypatch):
    monkeypatch.setattr(
        java_internal_client,
        "get_order_item",
        AsyncMock(return_value={"order_id": "verified-order"}),
    )

    async def get_order(order_id: str):
        return {
            "order_id": order_id,
            "user_id": "u1",
            "order_status": 1,
            "items": [],
        }

    lookup = AsyncMock(side_effect=get_order)
    monkeypatch.setattr(java_internal_client, "get_order", lookup)

    context = await SupportService().build_handoff_context(
        "u1",
        "处理这个订单项",
        {
            "entities": {
                "orders": [
                    {"orderId": "model-1"},
                    {"orderId": "model-2"},
                    {"orderId": "model-3"},
                ]
            }
        },
        verified_order_refs={"orderItemId": "verified-item"},
    )

    assert [
        row["orderId"] for row in context["authoritativeOrders"]
    ] == ["verified-order", "model-1", "model-2"]


def test_public_session_omits_context_and_admin_session_exposes_it():
    context = {
        "schemaVersion": "aishop-support-handoff/v1",
        "authoritativeOrders": [],
    }
    row = {
        "session_id": "s1",
        "user_id": "u1",
        "status": "QUEUED",
        "context_json": json.dumps(context),
    }

    assert "handoffContext" not in SupportService.public_session(row)
    assert SupportService._admin_session(row)["handoffContext"] == context


def test_handoff_context_has_a_hard_serialized_size_limit():
    with pytest.raises(ValueError, match="安全上限"):
        SupportService._encode_context({"payload": "x" * 9000})
