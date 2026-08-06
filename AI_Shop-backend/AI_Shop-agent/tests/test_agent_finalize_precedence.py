from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent_runtime import finalize_agent_response


@pytest.mark.asyncio
async def test_valid_order_cards_beat_an_earlier_failed_write_attempt():
    cards = json.dumps(
        [
            {
                "orderId": "SM202608050002",
                "orderStatus": 1,
                "orderStatusName": "已付款，待发货",
                "orderItemList": [],
            }
        ],
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 30, "userMessage": "耳机退款"},
            ["未能生成有效确认卡片"],
            [],
            assistant_cards=cards,
            tools_called=["PROPOSE_REFUND", "QUERY_ORDERS"],
            user_text="耳机退款",
        )

    assert complete.await_args.args[1] == cards
    assert complete.await_args.args[2] == "query_order"


@pytest.mark.asyncio
async def test_order_selection_is_persisted_as_the_terminal_payload():
    selection = json.dumps(
        {
            "type": "ORDER_SELECTION",
            "selectionId": "sel_1",
            "sourceMessageId": "30",
            "intent": "REFUND",
            "prompt": "请选择订单",
            "expiresAt": "2099-01-01T00:00:00",
            "candidates": [{"targetType": "ORDER", "targetId": "o1", "orderId": "o1"}],
        },
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 30, "userMessage": "退款"},
            [],
            [],
            assistant_cards=selection,
            biz_type="order_selection",
            tools_called=[],
            user_text="退款",
        )

    assert complete.await_args.args[1] == selection
    assert complete.await_args.args[2] == "order_selection"


@pytest.mark.asyncio
async def test_product_comparison_card_is_the_terminal_payload():
    comparison = json.dumps(
        {
            "type": "PRODUCT_COMPARISON",
            "snapshotType": "REAL_TIME",
            "products": [
                {"productId": "p1", "productName": "A", "minPrice": 100},
                {"productId": "p2", "productName": "B", "minPrice": 120},
            ],
        },
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 31, "userMessage": "比较这两个"},
            ["已经比较好了"],
            [],
            assistant_cards=comparison,
            biz_type="product_comparison",
            tools_called=["COMPARE_PRODUCTS"],
            user_text="比较这两个",
        )

    assert complete.await_args.args[1] == comparison
    assert complete.await_args.args[2] == "product_comparison"
