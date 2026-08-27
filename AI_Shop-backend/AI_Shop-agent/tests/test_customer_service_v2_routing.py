import pytest

from app.domain.intent.classifier import (
    classify_intent_by_rules,
    classify_request_mode,
    extract_entities,
    resolve_intent,
)
from app.domain.intent.types import IntentKind, NextAction, RequestMode, RiskLevel


@pytest.mark.parametrize(
    ("text", "intent", "request_mode"),
    [
        (
            "订单 A20260823001 还没发货，收货地址怎么改",
            IntentKind.ADDRESS_CHANGE,
            RequestMode.READ_QUERY,
        ),
        ("人民币199元的订单怎么开电子发票", IntentKind.INVOICE, RequestMode.INFORMATIONAL),
        ("满 300 减 40 的券在哪里领", IntentKind.QUERY_COUPON, RequestMode.INFORMATIONAL),
        (
            "这件预售商品大概几天能发货",
            IntentKind.QUERY_FULFILLMENT,
            RequestMode.INFORMATIONAL,
        ),
        (
            "这个商品页面不让我追评，能告诉我追评入口吗",
            IntentKind.PRODUCT_REVIEW,
            RequestMode.INFORMATIONAL,
        ),
    ],
)
def test_informational_howto_retains_its_business_domain(text, intent, request_mode):
    assert classify_intent_by_rules(text) == intent
    assert classify_request_mode(text, intent) == request_mode


@pytest.mark.asyncio
async def test_conditional_handoff_retains_business_intent_and_hands_off():
    decision = await resolve_intent(
        "u-v2",
        "这个订单误下了两次，帮我取消其中一单，拿不准就转人工",
        allow_llm=False,
        record_metrics=False,
    )

    assert decision.intent == IntentKind.CANCEL_ORDER
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "USER_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["找真人客服帮我处理", "找真人处理"])
async def test_generic_human_request_does_not_invent_business_risk(text):
    decision = await resolve_intent(
        "u-v2", text, allow_llm=False, record_metrics=False
    )

    assert decision.intent == IntentKind.HUMAN_REQUEST
    assert decision.risk_level == RiskLevel.LOW
    assert decision.next_action == NextAction.HANDOFF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("你们把我的收货地址发给陌生人了", "PRIVACY_INCIDENT"),
        ("账户疑似被盗，不要让我再输支付信息", "ACCOUNT_SECURITY"),
        ("充电宝鼓包而且发烫，我已经停止使用", "SAFETY_INCIDENT"),
        ("不要把钱退到陌生卡里", "ACCOUNT_SECURITY"),
    ],
)
async def test_non_fund_safety_incidents_are_high_risk_with_specific_reason(text, reason):
    decision = await resolve_intent(
        "u-v2", text, allow_llm=False, record_metrics=False
    )

    assert decision.risk_level == RiskLevel.HIGH
    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "包裹已经出库了还能改到公司地址吗",
        "我没收到货，千万别给我自动确认收货",
    ],
)
async def test_order_state_conflicts_fail_closed_to_handoff(text):
    decision = await resolve_intent(
        "u-v2", text, allow_llm=False, record_metrics=False
    )

    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "STATE_CONFLICT"


def test_contextual_order_token_does_not_generalize_to_arbitrary_numbers():
    assert extract_entities("订单 20260823008 当前是什么状态")["orderId"] == "20260823008"
    assert "orderId" not in extract_entities("预算 20260823008 元，推荐一款耳机")


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("人民币199元的订单怎么开电子发票", "人民币199元"),
        ("￥１，２９９．００ 这笔能开公司抬头吗", "￥１，２９９．００"),
        ("支付显示失败但银行卡扣了 ¥88", "¥88"),
        ("预算一千二，想买个能拍照的手机", "一千二"),
    ],
)
def test_amount_entity_preserves_the_original_visible_span(text, amount):
    assert extract_entities(text)["amount"] == amount


def test_recommendation_refinement_does_not_reuse_followup_review_intent():
    assert (
        classify_intent_by_rules("上一批都不适合小户型，重新给几个静音的")
        == IntentKind.PRODUCT_SEARCH
    )
    assert classify_intent_by_rules("我要追评订单 SM202608050002") == IntentKind.RECOMMENT
