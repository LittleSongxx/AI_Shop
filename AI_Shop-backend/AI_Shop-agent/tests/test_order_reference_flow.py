from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.order_reference_flow import resolve_order_reference_turn
from app.services.tool_invoke_result import ToolInvokeResult


@pytest.fixture(autouse=True)
def _legacy_direct_order_mode():
    with patch(
        "app.graph.order_reference_flow.get_settings",
        return_value=SimpleNamespace(multi_agent_enabled=False),
    ):
        yield


def _order(order_id: str, item_id: str, name: str, when: str) -> dict:
    return {
        "order_id": order_id,
        "order_status": 1,
        "order_time": when,
        "amount": 3999,
        "comment_status": 0,
        "items": [
            {
                "order_id": order_id,
                "order_item_id": item_id,
                "product_id": item_id[-4:],
                "product_name": name,
                "item_amount": 3999,
                "order_item_status": 1,
            }
        ],
    }


def _state() -> dict:
    return {
        "user_id": "u1",
        "message_id": 30,
        "user_text": "没发货的耳机我要退款",
        "intent": "REFUND",
        "intent_decision": {"entities": {}},
        "llm_messages": [],
        "card": None,
    }


@pytest.mark.asyncio
async def test_unique_target_calls_propose_before_any_llm_turn():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            "2026-08-05 21:00:00",
        )
    ]
    result = ToolInvokeResult(content="已生成退款确认卡片【act_1234567890abcdef1234567890abcdef】")
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=orders),
        ),
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(return_value=result),
        ) as invoke,
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(_state())

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert update["tools_called"] == ["PROPOSE_REFUND"]
    invoke.assert_awaited_once_with(
        "PROPOSE_REFUND",
        {"orderItemId": "SMITEM202608050002"},
        "u1",
    )


@pytest.mark.asyncio
async def test_multiple_targets_persist_an_order_selection_card():
    orders = [
        _order("SM202608050002", "SMITEM202608050002", "索尼无线耳机", "2026-08-05 21:00:00"),
        _order("SM202608040001", "SMITEM202608040001", "苹果无线耳机", "2026-08-04 10:00:00"),
    ]
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=orders),
        ),
        patch(
            "app.graph.order_reference_flow.order_selection_store.create",
            AsyncMock(return_value={"selectionId": "sel_1", "expiresAt": "2099-01-01T00:00:00"}),
        ) as create,
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(),
        ) as invoke,
    ):
        update = await resolve_order_reference_turn(_state())

    card = json.loads(update["assistant_cards"])
    assert update["route"] == "finalize"
    assert update["order_resolution"] == "AMBIGUOUS"
    assert card["type"] == "ORDER_SELECTION"
    assert len(card["candidates"]) == 2
    assert all("_searchText" not in row for row in card["candidates"])
    create.assert_awaited_once()
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_details_reuse_the_selected_order_reference():
    order = _order(
        "SM202608010001",
        "SMITEM202608010001",
        "索尼无线降噪耳机",
        "2026-08-01 20:00:00",
    )
    order["order_status"] = 3
    state = {
        **_state(),
        "user_text": "五星，音质很好",
        "intent": "PRODUCT_REVIEW",
        "pending_order_reference": {
            "intent": "PRODUCT_REVIEW",
            "targetType": "ORDER",
            "targetId": "SM202608010001",
            "orderId": "SM202608010001",
            "orderItemId": "SMITEM202608010001",
            "expiresAt": (datetime.now() + timedelta(minutes=20)).isoformat(),
        },
    }
    result = ToolInvokeResult(content="已生成评价确认卡片【act_1234567890abcdef1234567890abcdef】")
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(return_value=result),
        ) as invoke,
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    invoke.assert_awaited_once_with(
        "PROPOSE_PRODUCT_REVIEW",
        {
            "orderId": "SM202608010001",
            "commentContent": "音质很好",
            "star": 5,
        },
        "u1",
    )


@pytest.mark.asyncio
async def test_unshipped_order_answers_from_snapshot_without_querying_fake_logistics():
    state = {
        **_state(),
        "user_text": "没发货的耳机物流到哪了",
        "intent": "QUERY_LOGISTICS",
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(),
        ) as invoke,
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert "尚未发货" in update["chunks"][0]
    assert "没有物流轨迹" in update["chunks"][0]
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_agent_mode_only_verifies_order_and_never_executes_read_tool():
    state = {
        **_state(),
        "user_text": "没发货的耳机物流到哪了",
        "intent": "QUERY_LOGISTICS",
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.get_settings",
            return_value=SimpleNamespace(multi_agent_enabled=True),
        ),
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(),
        ) as invoke,
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert update["route"] == "multi_agent_plan"
    assert update["verified_order_context"]["orderId"] == "SM202608050002"
    invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_refund_rechecks_latest_status_before_proposing():
    state = {
        **_state(),
        "user_text": "选择索尼无线降噪耳机订单继续退款。",
        "pending_order_reference": {
            "intent": "REFUND",
            "targetType": "ORDER_ITEM",
            "targetId": "SMITEM202608050002",
            "orderId": "SM202608050002",
            "orderItemId": "SMITEM202608050002",
            "expiresAt": (datetime.now() + timedelta(minutes=2)).isoformat(),
        },
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 0
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.mcp_tool_router.invoke",
            AsyncMock(),
        ) as invoke,
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert "待付款" in update["chunks"][0]
    invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "text", "expected_category"),
    [
        ("ADDRESS_CHANGE", "我想修改收货地址", "ADDRESS_CHANGE"),
        ("INVOICE", "请开具发票", "INVOICE"),
        ("DAMAGED_OR_WRONG_ITEM", "收到的商品破损了", "DAMAGED"),
    ],
)
async def test_after_sales_intents_propose_owned_support_case(intent, text, expected_category):
    from app.graph.order_reference_flow import _tool_for_target

    target = {
        "orderId": "SM202608050002",
        "orderItemId": "SMITEM202608050002",
        "productName": "索尼无线降噪耳机",
    }
    tool_name, args = _tool_for_target(
        intent,
        text,
        target,
        {"message_id": 30, "after_sales_workflow": True},
    )

    assert tool_name == "PROPOSE_CREATE_SUPPORT_CASE"
    assert args["category"] == expected_category
    assert args["orderId"] == "SM202608050002"
    assert args["orderItemId"] == "SMITEM202608050002"
