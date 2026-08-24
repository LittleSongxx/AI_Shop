from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.order_reference_flow import resolve_order_reference_turn


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
async def test_unique_target_prepares_verified_proposal_before_any_llm_turn():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            "2026-08-05 21:00:00",
        )
    ]
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=orders),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(_state())

    assert update["route"] == "orchestration_router"
    assert update["order_resolution"] == "RESOLVED"
    assert update["verified_order_context"]["orderId"] == "SM202608050002"
    assert update["resolved_order_tool"] == {
        "name": "PROPOSE_REFUND",
        "args": {"orderItemId": "SMITEM202608050002"},
    }


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
    ):
        update = await resolve_order_reference_turn(_state())

    card = json.loads(update["assistant_cards"])
    assert update["route"] == "finalize"
    assert update["order_resolution"] == "AMBIGUOUS"
    assert card["type"] == "ORDER_SELECTION"
    assert len(card["candidates"]) == 2
    assert all("_searchText" not in row for row in card["candidates"])
    create.assert_awaited_once()


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
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert update["resolved_order_tool"] == {
        "name": "PROPOSE_PRODUCT_REVIEW",
        "args": {
            "orderId": "SM202608010001",
            "commentContent": "音质很好",
            "star": 5,
        },
    }


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
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert "尚未发货" in update["chunks"][0]
    assert "没有物流轨迹" in update["chunks"][0]


@pytest.mark.asyncio
async def test_order_resolution_is_independent_from_serving_mode():
    state = {
        **_state(),
        "user_text": "已发货的耳机物流到哪了",
        "intent": "QUERY_LOGISTICS",
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 2
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"


@pytest.mark.asyncio
async def test_no_eligible_keeps_verified_snapshot_without_proposing_write():
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
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "CANCEL_ORDER",
                "user_text": "取消订单 SM202608050002",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert update["verified_order_context"]["orderStatusName"] == "已付款,待发货"
    assert update["order_reference_evidence"] == {
        "outcome": "NO_ELIGIBLE",
        "route": "finalize",
        "resolvedTool": None,
        "businessSourceRefCount": 1,
        "hasVerifiedOrderContext": True,
        "matchedCandidateCount": 1,
        "dependencyError": False,
    }


@pytest.mark.asyncio
async def test_informational_damaged_item_still_checks_order_eligibility() -> None:
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
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "DAMAGED_OR_WRONG_ITEM",
                "request_mode": "INFORMATIONAL",
                "user_text": "商家发错商品了，订单 SM202608050002 怎么处理？",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert "当前不能发起破损/错发售后" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert "assistant_cards" not in update
    assert update["order_reference_evidence"]["hasVerifiedOrderContext"] is True


@pytest.mark.asyncio
async def test_recomment_without_body_stays_a_snapshot_backed_clarification() -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 3
    order["comment_status"] = 1
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "RECOMMENT",
                "request_mode": "ACTION_PROPOSAL",
                "user_text": "我想追评订单 SM202608050002",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "想追加的评价内容" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert update["verified_order_context"]["orderId"] == "SM202608050002"
    assert update["order_reference_evidence"]["hasVerifiedOrderContext"] is True


@pytest.mark.asyncio
async def test_read_only_refund_question_checks_eligibility_without_proposing_write():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 3
    state = {
        **_state(),
        "user_text": "订单号SM202608050002为什么延迟，现在能否退款？",
        "request_mode": "READ_QUERY",
    }
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert update["route"] == "finalize"
    assert "当前不能申请退款" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert update["verified_order_context"]["orderId"] == "SM202608050002"


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
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert "待付款" in update["chunks"][0]


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
