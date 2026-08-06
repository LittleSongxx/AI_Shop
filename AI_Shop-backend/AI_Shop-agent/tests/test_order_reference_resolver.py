from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.intent.classifier import classify_intent_by_rules
from app.domain.intent.types import IntentKind
from app.services.order_reference_resolver import (
    OrderReferenceOutcome,
    order_reference_resolver,
)
from app.utils.order_ids import extract_order_id, extract_order_item_id


def _order(
    order_id: str,
    item_id: str,
    product_name: str,
    *,
    status: int = 1,
    item_status: int = 1,
    order_time: str = "2026-08-05 21:00:00",
    comment_status: int = 0,
    product_id: str = "9002",
) -> dict:
    return {
        "order_id": order_id,
        "order_status": status,
        "order_time": order_time,
        "amount": 3999,
        "comment_status": comment_status,
        "items": [
            {
                "order_id": order_id,
                "order_item_id": item_id,
                "product_id": product_id,
                "product_name": product_name,
                "item_amount": 3999,
                "order_item_status": item_status,
            }
        ],
    }


@pytest.mark.asyncio
async def test_refund_resolves_unshipped_earphone_without_order_id():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼 WH-1000XM6 无线降噪耳机",
        )
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="没发货的耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.RESOLVED
    assert result.target is not None
    assert result.target["orderId"] == "SM202608050002"
    assert result.target["targetId"] == "SMITEM202608050002"


@pytest.mark.asyncio
async def test_multiple_matching_items_require_selection():
    orders = [
        _order("SM202608050002", "SMITEM202608050002", "索尼无线降噪耳机"),
        _order(
            "SM202608040001",
            "SMITEM202608040001",
            "苹果 AirPods 无线耳机",
            order_time="2026-08-04 10:00:00",
            product_id="9003",
        ),
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="没发货的耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.AMBIGUOUS
    assert [row["targetId"] for row in result.candidates] == [
        "SMITEM202608050002",
        "SMITEM202608040001",
    ]


@pytest.mark.asyncio
async def test_one_order_with_multiple_matching_items_requires_item_selection():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
    )
    order["items"].append(
        {
            "order_id": "SM202608050002",
            "order_item_id": "SMITEM202608050003",
            "product_id": "9003",
            "product_name": "苹果 AirPods 无线耳机",
            "item_amount": 1299,
            "order_item_status": 1,
        }
    )
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=[order]),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="这个订单的耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.AMBIGUOUS
    assert {row["targetId"] for row in result.candidates} == {
        "SMITEM202608050002",
        "SMITEM202608050003",
    }


@pytest.mark.asyncio
async def test_explicit_brand_beats_stale_product_page_context():
    orders = [
        _order("SM202608050002", "SMITEM202608050002", "索尼无线降噪耳机"),
        _order(
            "SM202608040001",
            "SMITEM202608040001",
            "苹果 AirPods 无线耳机",
            product_id="9003",
        ),
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="我要退苹果耳机",
            consult_card={"productId": "9002", "productName": "索尼无线降噪耳机"},
        )

    assert result.outcome == OrderReferenceOutcome.RESOLVED
    assert result.target is not None
    assert result.target["targetId"] == "SMITEM202608040001"


@pytest.mark.asyncio
async def test_property_clue_narrows_same_category_without_fuzzy_scoring():
    black = _order(
        "SM202608050002", "SMITEM202608050002", "索尼无线降噪耳机"
    )
    black["items"][0]["property_info"] = "黑色 256GB"
    white = _order(
        "SM202608040001",
        "SMITEM202608040001",
        "索尼无线降噪耳机",
        product_id="9003",
    )
    white["items"][0]["property_info"] = "白色 256GB"
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=[black, white]),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="黑色耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.RESOLVED
    assert result.target is not None
    assert result.target["targetId"] == "SMITEM202608050002"


@pytest.mark.asyncio
async def test_explicit_recent_relation_uniquely_selects_the_newest_match():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            order_time="2026-08-05 21:00:00",
        ),
        _order(
            "SM202608040001",
            "SMITEM202608040001",
            "苹果 AirPods 无线耳机",
            order_time="2026-08-04 10:00:00",
            product_id="9003",
        ),
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="最近买的耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.RESOLVED
    assert result.target is not None
    assert result.target["targetId"] == "SMITEM202608050002"


@pytest.mark.asyncio
async def test_matching_but_ineligible_refund_is_not_proposed():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            status=0,
        )
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="耳机我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.NO_ELIGIBLE
    assert "待付款" in result.reason


@pytest.mark.asyncio
async def test_explicit_foreign_order_id_is_verified_with_current_user():
    list_orders = AsyncMock(return_value=[])
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        list_orders,
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.REFUND.value,
            user_text="SM202608050099 这个订单我要退款",
        )

    assert result.outcome == OrderReferenceOutcome.NO_MATCH
    assert "你的订单" in result.reason
    list_orders.assert_awaited_once_with(
        "u1",
        order_id="SM202608050099",
        time_start=None,
        time_end=None,
        limit=30,
    )


@pytest.mark.asyncio
async def test_dependency_failure_is_not_reported_as_no_order():
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(side_effect=TimeoutError("order timeout")),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=IntentKind.QUERY_LOGISTICS.value,
            user_text="查一下耳机物流",
        )

    assert result.outcome == OrderReferenceOutcome.DEPENDENCY_ERROR
    assert "暂时不可用" in result.reason


@pytest.mark.parametrize(
    ("intent", "text", "status", "comment_status", "expected"),
    [
        (IntentKind.CONFIRM_RECEIPT, "确认收货耳机订单", 2, 0, OrderReferenceOutcome.RESOLVED),
        (IntentKind.CONFIRM_RECEIPT, "确认收货耳机订单", 1, 0, OrderReferenceOutcome.NO_ELIGIBLE),
        (IntentKind.CANCEL_ORDER, "取消耳机订单", 0, 0, OrderReferenceOutcome.RESOLVED),
        (IntentKind.CANCEL_ORDER, "取消耳机订单", 1, 0, OrderReferenceOutcome.NO_ELIGIBLE),
        (IntentKind.PRODUCT_REVIEW, "我要评价耳机", 3, 0, OrderReferenceOutcome.RESOLVED),
        (IntentKind.RECOMMENT, "我要追评耳机", 3, 1, OrderReferenceOutcome.RESOLVED),
        (IntentKind.QUERY_COMMENT, "查看耳机评价", 3, 0, OrderReferenceOutcome.NO_ELIGIBLE),
        (IntentKind.DAMAGED_OR_WRONG_ITEM, "我收到的耳机坏了", 2, 0, OrderReferenceOutcome.RESOLVED),
        (IntentKind.DAMAGED_OR_WRONG_ITEM, "我收到的耳机坏了", 0, 0, OrderReferenceOutcome.NO_ELIGIBLE),
    ],
)
@pytest.mark.asyncio
async def test_intent_specific_order_eligibility(
    intent: IntentKind,
    text: str,
    status: int,
    comment_status: int,
    expected: OrderReferenceOutcome,
):
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            status=status,
            comment_status=comment_status,
        )
    ]
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=orders),
    ):
        result = await order_reference_resolver.resolve(
            user_id="u1",
            intent=intent.value,
            user_text=text,
        )

    assert result.outcome == expected


def test_demo_order_ids_are_part_of_the_deterministic_contract():
    assert extract_order_id("SM202608050002号订单") == "SM202608050002"
    assert extract_order_item_id("退 SMITEM202608050002") == "SMITEM202608050002"
    assert extract_order_id("退 SMITEM202608050002") == "SM202608050002"


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("没发货的耳机我要退款", IntentKind.REFUND),
        ("刚买的手机怎么还没发货", IntentKind.QUERY_FULFILLMENT),
        ("查一下耳机那单的评价", IntentKind.QUERY_COMMENT),
        ("我要改没发货耳机的地址", IntentKind.ADDRESS_CHANGE),
        ("我收到的耳机坏了", IntentKind.DAMAGED_OR_WRONG_ITEM),
        ("催一下没发货的耳机", IntentKind.QUERY_FULFILLMENT),
        ("没发货的耳机物流到哪了", IntentKind.QUERY_LOGISTICS),
        ("退款一般多久到账", IntentKind.CHAT),
        ("我的退款到哪了", IntentKind.REFUND_STATUS),
    ],
)
def test_order_aftersales_beats_product_search(text: str, intent: IntentKind):
    assert classify_intent_by_rules(text) == intent


@pytest.mark.parametrize(
    "text",
    [
        "这个没发货我要退款",
        "这个订单物流到哪了",
        "这个坏了怎么办",
        "我想给它五星好评",
        "这个订单取消掉",
    ],
)
def test_product_context_does_not_steal_order_action(text: str):
    consult = {"productId": "9002", "productName": "索尼无线降噪耳机"}
    result = classify_intent_by_rules(
        text,
        from_product=True,
        consult_card=consult,
        message_card=consult,
    )
    assert result != IntentKind.PRODUCT_CONSULT
    assert result != IntentKind.PRODUCT_SEARCH
