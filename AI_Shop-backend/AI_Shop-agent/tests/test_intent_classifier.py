import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from app.config.settings import get_settings
from app.domain.intent.classifier import (
    FUND_AT_RISK,
    PAYMENT_ISSUE_HINTS,
    _parse_intent_json,
    classify_high_confidence_intent,
    classify_intent_by_llm,
    classify_intent_by_rules,
    classify_request_mode,
    extract_entities,
    resolve_intent,
)
from app.domain.intent.rules import deterministic_social_reply
from app.domain.intent.types import (
    IntentDecision,
    IntentKind,
    NextAction,
    RequestMode,
    RiskLevel,
    SentimentKind,
)
from app.services.agent_service import AgentOrchestrator


@pytest.mark.parametrize(
    ("text", "intent", "expected"),
    [
        ("退款政策是什么", IntentKind.REFUND, RequestMode.INFORMATIONAL),
        ("退款需要什么条件", IntentKind.REFUND, RequestMode.INFORMATIONAL),
        ("退款怎么申请", IntentKind.CHAT, RequestMode.INFORMATIONAL),
        ("我要退款", IntentKind.REFUND, RequestMode.ACTION_PROPOSAL),
        (
            "请为订单 SM202608050002 发起退款申请。",
            IntentKind.REFUND,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "请不要为订单 SM202608050002 发起退款申请。",
            IntentKind.REFUND,
            RequestMode.INFORMATIONAL,
        ),
        (
            "把订单 SM202608050002 退款",
            IntentKind.REFUND,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "订单 SM202608050002 能退款吗？",
            IntentKind.REFUND,
            RequestMode.INFORMATIONAL,
        ),
        ("选择耳机订单继续退款", IntentKind.REFUND, RequestMode.ACTION_PROPOSAL),
        ("取消订单怎么操作", IntentKind.CHAT, RequestMode.INFORMATIONAL),
        (
            "取消订单 SM202608050001",
            IntentKind.CANCEL_ORDER,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "先别取消订单 SM202608050001",
            IntentKind.CANCEL_ORDER,
            RequestMode.INFORMATIONAL,
        ),
        ("五星，音质很好", IntentKind.PRODUCT_REVIEW, RequestMode.ACTION_PROPOSAL),
        ("五星评价怎么写", IntentKind.PRODUCT_REVIEW, RequestMode.INFORMATIONAL),
        ("补充一下，降噪和续航都不错", IntentKind.RECOMMENT, RequestMode.ACTION_PROPOSAL),
        ("我有哪些优惠券", IntentKind.QUERY_COUPON, RequestMode.READ_QUERY),
        ("收到的商品坏了，帮我处理", IntentKind.DAMAGED_OR_WRONG_ITEM, RequestMode.ACTION_PROPOSAL),
        ("坏了以后怎么处理", IntentKind.DAMAGED_OR_WRONG_ITEM, RequestMode.INFORMATIONAL),
        (
            "请先为我生成取消订单的操作确认卡",
            IntentKind.CANCEL_ORDER,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "取消订单前只生成提案，不要实际执行",
            IntentKind.CANCEL_ORDER,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "请展示取消待付款订单的方案，等待我确认",
            IntentKind.CANCEL_ORDER,
            RequestMode.ACTION_PROPOSAL,
        ),
        (
            "远程结果未知时不要伪造成功，只保留订单 SM202608050001 的取消提案",
            IntentKind.CANCEL_ORDER,
            RequestMode.ACTION_PROPOSAL,
        ),
    ],
)
def test_request_mode_is_independent_from_business_intent(text, intent, expected):
    assert classify_request_mode(text, intent) == expected


def test_coupon_word_order_is_classified_as_personal_query():
    assert classify_intent_by_rules("我有哪些优惠券") == IntentKind.QUERY_COUPON


def test_parse_intent_json():
    parsed = _parse_intent_json('{"intentType":"REFUND","data":"oid1"}')
    assert parsed["intentType"] == "REFUND"
    assert parsed["data"] == "oid1"

def test_rule_fallback_refund():
    intent = classify_intent_by_rules("我要退款")
    assert intent == IntentKind.REFUND


def test_explicit_cancel_proposal_is_high_confidence_before_llm():
    text = "远程结果未知时不要伪造成功，只保留订单 SM202608050001 的取消提案"
    assert classify_high_confidence_intent(text) == (
        IntentKind.CANCEL_ORDER,
        "SM202608050001",
    )


def test_cancel_policy_question_is_not_promoted_to_write_proposal():
    text = "订单 SM202608050001 的取消规则是什么？"
    assert classify_high_confidence_intent(text) == (None, "")
    assert classify_request_mode(text, IntentKind.CANCEL_ORDER) == RequestMode.INFORMATIONAL

def test_rule_chat_returns_none_without_keywords():
    assert classify_intent_by_rules("你好呀") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好！", "你好，我是 AI Shop 客服。请问需要查询订单、物流、优惠，还是推荐商品？"),
        ("谢谢你", "不客气，有需要可以继续告诉我。"),
        ("好的呢。", "好的。"),
        ("再见", "再见，祝你购物愉快。"),
        ("你好，今天有什么活动？", None),
        ("你好，帮我退款", None),
        ("谢谢，顺便查一下订单", None),
    ],
)
def test_deterministic_social_reply_requires_a_complete_social_utterance(text, expected):
    assert deterministic_social_reply(text) == expected


@pytest.mark.asyncio
async def test_pure_social_intent_skips_llm_and_is_high_confidence(monkeypatch):
    llm = AsyncMock(side_effect=AssertionError("pure social text must skip intent LLM"))
    monkeypatch.setattr("app.domain.intent.classifier.classify_intent_by_llm", llm)

    decision = await resolve_intent("u1", "谢谢你", allow_llm=True)

    assert decision.intent == IntentKind.CHAT
    assert decision.source == "deterministic_social"
    assert decision.confidence == 0.99
    assert decision.next_action == NextAction.ANSWER
    assert decision.request_mode == RequestMode.INFORMATIONAL
    llm.assert_not_awaited()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "我想买索尼 WH-1000XM6，预算 2000 元",
            {
                "amount": "2000",
                "brand": "索尼",
                "budget": "2000元",
                "productName": "索尼 WH-1000XM6",
            },
        ),
        (
            "帮我找 500 元以内、不要户外款的男士外套",
            {
                "amount": "500",
                "budget": "500元以内",
                "excludedStyle": "户外款",
                "productName": "男士外套",
            },
        ),
        (
            "不要苹果，推荐安卓手机",
            {"excludedBrand": "苹果", "operatingSystem": "安卓", "productName": "安卓手机"},
        ),
        (
            "少了一个配件，订单SM202608050002",
            {"orderId": "SM202608050002", "productName": "配件", "quantity": "1"},
        ),
        (
            "手机壳有没有适配 iPhone 15",
            {"compatibleModel": "iPhone 15", "productName": "手机壳"},
        ),
    ],
)
def test_extended_customer_service_slots_are_bounded_and_explainable(text, expected):
    assert extract_entities(text) == expected


def test_feature_and_bluetooth_slots_keep_original_user_spans():
    assert extract_entities("这款耳机支持蓝牙 5.4 吗") == {
        "bluetoothVersion": "5.4",
        "productName": "耳机",
    }
    assert extract_entities("这副耳机有没有主动降噪") == {
        "feature": "主动降噪",
        "productName": "耳机",
    }


@pytest.mark.asyncio
async def test_text_only_product_spec_question_uses_consult_route():
    decision = await resolve_intent(
        "u1", "这款耳机支持蓝牙 5.4 吗", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.PRODUCT_CONSULT
    assert decision.entities["productName"] == "耳机"


@pytest.mark.asyncio
async def test_same_category_comparison_without_card_uses_consult_clarification():
    decision = await resolve_intent(
        "u1", "这款耳机和另一款相比哪个好", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.PRODUCT_CONSULT
    assert decision.entities["productName"] == "耳机"


@pytest.mark.asyncio
async def test_cross_brand_comparison_remains_product_search():
    decision = await resolve_intent(
        "u1", "这款手机和华为哪个好", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.PRODUCT_SEARCH


@pytest.mark.asyncio
async def test_state_changing_refund_proposal_is_medium_risk():
    decision = await resolve_intent(
        "u1", "我要退款订单 SM202608050002，金额199元", allow_llm=False, record_metrics=False
    )
    assert decision.risk_level == RiskLevel.MEDIUM


@pytest.mark.asyncio
async def test_privacy_request_is_high_risk_immediate_handoff():
    decision = await resolve_intent(
        "u1", "你们能读取我的邮箱历史吗", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.HUMAN_REQUEST
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.next_action == NextAction.HANDOFF


@pytest.mark.asyncio
async def test_generic_refund_policy_keeps_refund_taxonomy():
    decision = await resolve_intent(
        "u1", "退款政策一般多久到账", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.REFUND
    assert decision.request_mode == RequestMode.INFORMATIONAL


@pytest.mark.asyncio
async def test_short_damage_statement_is_after_sales_intent():
    decision = await resolve_intent(
        "u1", "收到商品是坏的", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.DAMAGED_OR_WRONG_ITEM


@pytest.mark.asyncio
async def test_unresolved_support_statement_is_complaint_and_handoff():
    decision = await resolve_intent(
        "u1",
        "这个问题一直没解决，还是不行，帮我处理",
        allow_llm=False,
        record_metrics=False,
    )
    assert decision.intent == IntentKind.COMPLAINT
    assert decision.next_action == NextAction.HANDOFF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "risk", "next_action"),
    [
        ("付款成功但订单没生成", IntentKind.PAYMENT_ISSUE, RiskLevel.HIGH, NextAction.HANDOFF),
        ("银行卡被扣了两次", IntentKind.PAYMENT_ISSUE, RiskLevel.HIGH, NextAction.HANDOFF),
        ("支付失败但没有扣款，怎么办", IntentKind.PAYMENT_ISSUE, RiskLevel.MEDIUM, NextAction.ANSWER),
        ("删除我的个人资料", IntentKind.HUMAN_REQUEST, RiskLevel.HIGH, NextAction.HANDOFF),
        ("我已经问了三次还没解决", IntentKind.COMPLAINT, RiskLevel.MEDIUM, NextAction.HANDOFF),
        ("收到的是错的颜色", IntentKind.DAMAGED_OR_WRONG_ITEM, RiskLevel.MEDIUM, NextAction.ANSWER),
        ("退款多久到账呀", IntentKind.REFUND, RiskLevel.LOW, NextAction.ANSWER),
        ("谢谢你", IntentKind.CHAT, RiskLevel.LOW, NextAction.ANSWER),
        ("账号被盗了怎么办", IntentKind.HUMAN_REQUEST, RiskLevel.HIGH, NextAction.HANDOFF),
        ("我想投诉但先别转人工", IntentKind.COMPLAINT, RiskLevel.MEDIUM, NextAction.ANSWER),
        ("我的退款还没到账但客服说已完成", IntentKind.REFUND_STATUS, RiskLevel.HIGH, NextAction.HANDOFF),
    ],
)
async def test_customer_service_boundary_cases_are_safe(text, intent, risk, next_action):
    decision = await resolve_intent("boundary", text, allow_llm=False, record_metrics=False)
    assert decision.intent == intent
    assert decision.risk_level == risk
    assert decision.next_action == next_action


@pytest.mark.asyncio
async def test_product_search_constraints_beat_consult_and_keep_spans_bounded():
    search = await resolve_intent(
        "boundary", "有没有适合学生的平板，预算2000元", allow_llm=False, record_metrics=False
    )
    assert search.intent == IntentKind.PRODUCT_SEARCH
    assert search.entities["amount"] == "2000"
    assert search.entities["productName"] == "平板"
    assert search.entities["audience"] == "学生"
    assert search.entities["budget"] == "2000元"

    consult = await resolve_intent(
        "boundary", "这款手机续航怎么样", allow_llm=False, record_metrics=False
    )
    assert consult.intent == IntentKind.PRODUCT_CONSULT
    assert consult.entities["productName"] == "手机"


@pytest.mark.asyncio
async def test_negated_handoff_and_comparison_product_span_are_bounded():
    decision = await resolve_intent(
        "boundary", "这款手机和华为哪个好", allow_llm=False, record_metrics=False
    )
    assert decision.intent == IntentKind.PRODUCT_SEARCH
    assert decision.entities["productName"] == "手机"


@pytest.mark.parametrize(
    "text",
    [
        "办公室采购旺旺雪饼和汽水",
        "采购一批可乐和芬达",
    ],
)
def test_bulk_snack_procurement_routes_to_product_search(text):
    assert classify_intent_by_rules(text) == IntentKind.PRODUCT_SEARCH


@pytest.mark.parametrize(
    "text",
    [
        "新房除甲醛空气净化器怎么选",
        "净水器和空气净化器哪个好",
    ],
)
def test_category_selection_routes_to_product_search_without_buy_verb(text):
    assert classify_intent_by_rules(text) == IntentKind.PRODUCT_SEARCH


def test_bag_budget_revision_continues_product_search():
    assert (
        classify_intent_by_rules(
            "预算提高到 1000 元，请继续推荐适合上班通勤的包，并说明适合谁、不适合谁和主要取舍。",
            session_intent=IntentKind.PRODUCT_SEARCH.value,
        )
        == IntentKind.PRODUCT_SEARCH
    )


def test_verified_shopping_image_routes_deterministically_to_visual_search():
    decision = IntentDecision(
        intent=IntentKind.CHAT,
        confidence=0.4,
        next_action=NextAction.ANSWER,
    )

    routed = AgentOrchestrator._route_verified_image(
        decision, "帮我找图中红色商品的同款"
    )

    assert routed.intent == IntentKind.VISUAL_PRODUCT_SEARCH
    assert routed.next_action == NextAction.TOOL
    assert routed.request_mode == RequestMode.READ_QUERY
    assert routed.source == "verified_image_route"


@pytest.mark.parametrize(
    "text",
    [
        "收到的商品破损了，怎么退货",
        "这是物流面单，快递为什么不更新",
        "订单里的商品发错了",
    ],
)
def test_after_sales_images_are_not_misrouted_to_visual_search(text):
    decision = IntentDecision(
        intent=IntentKind.DAMAGED_OR_WRONG_ITEM,
        confidence=0.9,
        next_action=NextAction.TOOL,
    )

    assert AgentOrchestrator._route_verified_image(decision, text) is decision


def test_review_details_continue_the_selected_order_review_flow():
    assert (
        classify_intent_by_rules(
            "五星，音质很好", session_intent=IntentKind.PRODUCT_REVIEW.value
        )
        == IntentKind.PRODUCT_REVIEW
    )


def test_recomment_content_continues_the_selected_order_flow():
    assert (
        classify_intent_by_rules(
            "补充一下，降噪和续航都不错", session_intent=IntentKind.RECOMMENT.value
        )
        == IntentKind.RECOMMENT
    )


@pytest.mark.asyncio
async def test_llm_intent_prefers_provider_structured_output(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()

    class StructuredLlm:
        async def ainvoke(self, _messages):
            return {
                "raw": AIMessage(content=""),
                "parsed": IntentDecision(
                    intent=IntentKind.QUERY_LOGISTICS,
                    confidence=0.91,
                    data="oid-1",
                    entities={"orderId": "oid-1"},
                    next_action=NextAction.TOOL,
                ),
                "parsing_error": None,
            }

    class FakeLlm:
        def with_structured_output(self, schema, *, include_raw):
            assert schema is IntentDecision
            assert include_raw is True
            return StructuredLlm()

        async def ainvoke(self, _messages):
            raise AssertionError("text JSON fallback must not run after schema success")

    async def prompt():
        return "用户 %s 的问题是 %s"

    monkeypatch.setattr("app.domain.intent.classifier.load_user_intent_classifier_prompt", prompt)
    monkeypatch.setattr(
        "app.domain.intent.classifier.create_memory_llm",
        lambda **kwargs: FakeLlm(),
    )

    try:
        decision = await classify_intent_by_llm("u1", "查物流")
    finally:
        get_settings.cache_clear()

    assert decision is not None
    assert decision.intent == IntentKind.QUERY_LOGISTICS
    assert decision.source == "llm_structured"
    assert decision.entities["orderId"] == "oid-1"


@pytest.mark.asyncio
async def test_llm_intent_falls_back_to_text_json_when_schema_unsupported(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()

    class FakeLlm:
        def with_structured_output(self, *_args, **_kwargs):
            raise NotImplementedError("provider has no schema mode")

        async def ainvoke(self, _messages):
            return AIMessage(
                content='{"intentType":"QUERY_COUPON","confidence":0.82,"data":""}'
            )

    async def prompt():
        return "用户 %s 的问题是 %s"

    monkeypatch.setattr("app.domain.intent.classifier.load_user_intent_classifier_prompt", prompt)
    monkeypatch.setattr(
        "app.domain.intent.classifier.create_memory_llm",
        lambda **kwargs: FakeLlm(),
    )

    try:
        decision = await classify_intent_by_llm("u1", "查优惠券")
    finally:
        get_settings.cache_clear()

    assert decision is not None
    assert decision.intent == IntentKind.QUERY_COUPON
    assert decision.source == "llm_fallback"


@pytest.mark.asyncio
async def test_llm_intent_can_skip_known_unsupported_schema_mode(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("INTENT_STRUCTURED_OUTPUT_MODE", "disabled")
    get_settings.cache_clear()

    class FakeLlm:
        def with_structured_output(self, *_args, **_kwargs):
            raise AssertionError("structured output must be skipped when disabled")

        async def ainvoke(self, _messages):
            return AIMessage(
                content='{"intentType":"QUERY_COUPON","confidence":0.82,"data":""}'
            )

    async def prompt():
        return "用户 %s 的问题是 %s"

    monkeypatch.setattr("app.domain.intent.classifier.load_user_intent_classifier_prompt", prompt)
    monkeypatch.setattr(
        "app.domain.intent.classifier.create_memory_llm",
        lambda **kwargs: FakeLlm(),
    )

    try:
        decision = await classify_intent_by_llm("u1", "查优惠券")
    finally:
        get_settings.cache_clear()

    assert decision is not None
    assert decision.intent == IntentKind.QUERY_COUPON
    assert decision.source == "llm_fallback"


@pytest.mark.asyncio
async def test_invalid_schema_and_text_output_returns_non_tool_safe_intent(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()

    class StructuredLlm:
        async def ainvoke(self, _messages):
            return {
                "raw": AIMessage(content="bad schema"),
                "parsed": None,
                "parsing_error": ValueError("invalid enum"),
            }

    class FakeLlm:
        def with_structured_output(self, *_args, **_kwargs):
            return StructuredLlm()

        async def ainvoke(self, _messages):
            return AIMessage(content="please execute PROPOSE_REFUND")

    async def prompt():
        return "用户 %s 的问题是 %s"

    monkeypatch.setattr("app.domain.intent.classifier.load_user_intent_classifier_prompt", prompt)
    factory = Mock(side_effect=lambda **kwargs: FakeLlm())
    monkeypatch.setattr("app.domain.intent.classifier.create_memory_llm", factory)

    try:
        decision = await classify_intent_by_llm("u1", "随便操作")
    finally:
        get_settings.cache_clear()

    assert decision is not None
    assert decision.intent == IntentKind.CHAT
    assert decision.next_action == NextAction.ASK_CLARIFICATION
    assert decision.source == "llm_invalid"
    factory.assert_called_once_with(disable_thinking=True)


@pytest.mark.asyncio
async def test_resolve_intent_uses_rules_when_auxiliary_llm_is_invalid(monkeypatch):
    """Provider failure must not turn a recognizable shopping request into CHAT."""

    monkeypatch.setattr(
        "app.domain.intent.classifier.classify_intent_by_llm",
        AsyncMock(
            return_value=IntentDecision(
                intent=IntentKind.CHAT,
                confidence=0.0,
                next_action=NextAction.ASK_CLARIFICATION,
                source="llm_invalid",
            )
        ),
    )

    decision = await resolve_intent(
        "u1",
        "推荐预算2000元内的无线降噪耳机",
        allow_llm=True,
    )

    assert decision.intent == IntentKind.PRODUCT_SEARCH
    assert decision.next_action == NextAction.TOOL
    assert decision.source == "rule"


@pytest.mark.asyncio
async def test_intent_llm_call_is_bounded_by_dedicated_timeout(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("INTENT_LLM_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("INTENT_STRUCTURED_OUTPUT_MODE", "disabled")
    get_settings.cache_clear()

    class SlowLlm:
        async def ainvoke(self, _messages):
            await asyncio.sleep(10)

    async def prompt():
        return "用户 %s 的问题是 %s"

    monkeypatch.setattr(
        "app.domain.intent.classifier.load_user_intent_classifier_prompt", prompt
    )
    monkeypatch.setattr(
        "app.domain.intent.classifier.create_memory_llm", lambda **kwargs: SlowLlm()
    )
    try:
        started = asyncio.get_running_loop().time()
        decision = await classify_intent_by_llm("u1", "查优惠券")
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        get_settings.cache_clear()

    assert elapsed < 3
    assert decision is not None
    assert decision.source == "llm_invalid"


@pytest.mark.asyncio
async def test_missing_llm_key_uses_explicit_unavailable_fallback(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "")
    get_settings.cache_clear()

    def fail_create(**_kwargs):
        raise AssertionError("LLM factory must not run without credentials")

    monkeypatch.setattr(
        "app.domain.intent.classifier.create_memory_llm",
        fail_create,
    )
    try:
        decision = await classify_intent_by_llm("u1", "帮我看看这个")
    finally:
        get_settings.cache_clear()

    assert decision is not None
    assert decision.intent == IntentKind.CHAT
    assert decision.next_action == NextAction.ASK_CLARIFICATION
    assert decision.source == "llm_unavailable"

@pytest.mark.asyncio
async def test_resolve_intent_llm_primary(monkeypatch):
    async def fake_llm(*args, **kwargs):
        from app.domain.intent.types import IntentDecision

        return IntentDecision(
            intent=IntentKind.QUERY_LOGISTICS,
            confidence=0.88,
            data="oid123",
            source="llm",
        )

    async def fake_load():
        return "分类 %s %s"

    monkeypatch.setattr("app.domain.intent.classifier.load_user_intent_classifier_prompt", fake_load)
    monkeypatch.setattr("app.domain.intent.classifier.classify_intent_by_llm", fake_llm)

    decision = await resolve_intent("u1", "快递到哪了")
    assert decision.intent == IntentKind.QUERY_LOGISTICS
    assert decision.source == "llm"
    assert decision.entities["orderId"] == "oid123"

@pytest.mark.asyncio
async def test_resolve_intent_structural_product_consult(monkeypatch):
    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM should not run for structural consult")

    monkeypatch.setattr("app.domain.intent.classifier.classify_intent_by_llm", fail_llm)
    consult = {"productId": "1", "productName": "FG800"}
    decision = await resolve_intent(
        "u1",
        "这款内存多大",
        from_product=True,
        consult_card=consult,
    )
    assert decision.intent == IntentKind.PRODUCT_CONSULT
    assert decision.source == "structural"


@pytest.mark.asyncio
async def test_negative_fund_dispute_is_handoff():
    decision = await resolve_intent(
        "u1", "钱扣了但是退款没到账，我要投诉", allow_llm=False
    )
    assert decision.next_action == NextAction.HANDOFF
    assert decision.sentiment in {SentimentKind.NEGATIVE, SentimentKind.VERY_NEGATIVE}


def test_fund_terms_are_a_subset_of_payment_intent_terms():
    """资金词必须同时是支付意图词。

    这条断言就是"单一事实源"本身。原先意图分支内联一份支付词、风险判定用另一份，
    结果「重复支付」只被意图表收了（判成 PAYMENT_ISSUE 但不转人工），
    「订单已经取消了为什么还扣款」两张表都不认（落到 CHAT/0.4）。
    只要 PAYMENT_ISSUE_HINTS 仍从 FUND_AT_RISK 派生，这条就恒成立；
    哪天有人把它改回手写清单，这里立刻红。
    """
    missing = [term for term in FUND_AT_RISK if term not in PAYMENT_ISSUE_HINTS]
    assert not missing, (
        f"这些资金词不在支付意图词表里：{missing}。"
        "它们会被风险判定认出但意图分支认不出，导致连 PAYMENT_ISSUE 都判不到。"
    )


@pytest.mark.parametrize("term", FUND_AT_RISK)
@pytest.mark.asyncio
async def test_every_fund_term_escalates_to_fund_dispute(term):
    """逐个资金词都要能独立触发 FUND_DISPUTE，不依赖上下文里其他词凑出来。

    参数化而不是挑几个代表：往 FUND_AT_RISK 里加词的人不需要记得回来加用例，
    加进去就自动被覆盖。
    """
    decision = await resolve_intent("u1", f"我的订单{term}了", allow_llm=False)
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "FUND_DISPUTE"


@pytest.mark.asyncio
async def test_payment_blocked_without_fund_loss_is_not_fund_dispute():
    """支付走不通但钱没动，不该占用人工坐席。

    分级的意义全在这条：如果只要判成 PAYMENT_ISSUE 就一律转人工，这类纯操作咨询
    会把资金争议队列冲淡。
    """
    decision = await resolve_intent("u1", "支付异常是什么原因", allow_llm=False)
    assert decision.intent == IntentKind.PAYMENT_ISSUE
    assert decision.risk_level != RiskLevel.HIGH
    assert decision.handoff_reason != "FUND_DISPUTE"


@pytest.mark.asyncio
async def test_hypothetical_demo_payment_policy_is_answered_without_handoff():
    decision = await resolve_intent(
        "u1",
        "演示支付会不会发生真实扣款？",
        allow_llm=False,
    )

    assert decision.intent == IntentKind.CHAT
    assert decision.risk_level == RiskLevel.LOW
    assert decision.next_action == NextAction.ANSWER
    assert decision.handoff_reason is None


@pytest.mark.asyncio
async def test_observed_demo_payment_charge_remains_a_fund_dispute():
    decision = await resolve_intent(
        "u1",
        "演示支付已经扣款了，请处理",
        allow_llm=False,
    )

    assert decision.intent == IntentKind.PAYMENT_ISSUE
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "FUND_DISPUTE"


@pytest.mark.asyncio
async def test_repeated_intent_handoff_triggers_on_current_third_turn():
    decision = await resolve_intent(
        "u1",
        "物流到哪了",
        allow_llm=False,
        recent_intents=["QUERY_LOGISTICS", "QUERY_LOGISTICS"],
    )

    assert decision.intent == IntentKind.QUERY_LOGISTICS
    assert decision.next_action == NextAction.HANDOFF_SUGGESTED
    assert decision.handoff_reason == "REPEATED_INTENT"


@pytest.mark.asyncio
async def test_repeated_intent_handoff_does_not_trigger_on_second_turn():
    decision = await resolve_intent(
        "u1",
        "物流到哪了",
        allow_llm=False,
        recent_intents=["QUERY_LOGISTICS"],
    )

    assert decision.intent == IntentKind.QUERY_LOGISTICS
    assert decision.next_action == NextAction.TOOL
    assert decision.handoff_reason is None


@pytest.mark.asyncio
async def test_second_low_confidence_turn_does_not_force_handoff():
    decision = await resolve_intent(
        "u1", "支付方式有哪些", allow_llm=False, unresolved_count=1
    )

    assert decision.next_action == NextAction.HANDOFF_SUGGESTED
    assert decision.handoff_reason == "LOW_CONFIDENCE"


@pytest.mark.asyncio
async def test_first_payment_method_question_answers_without_handoff_suggestion():
    decision = await resolve_intent(
        "u1", "支付方式有哪些", allow_llm=False, unresolved_count=0
    )

    assert decision.intent == IntentKind.CHAT
    assert decision.next_action == NextAction.ANSWER
    assert decision.handoff_reason is None


@pytest.mark.asyncio
async def test_third_consecutive_low_confidence_turn_forces_handoff():
    decision = await resolve_intent(
        "u1", "支付方式有哪些", allow_llm=False, unresolved_count=2
    )

    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "REPEATED_UNRESOLVED"


@pytest.mark.asyncio
async def test_explicit_unresolved_feedback_still_hands_off_immediately():
    decision = await resolve_intent(
        "u1", "还是没解决", allow_llm=False, unresolved_count=0
    )

    assert decision.next_action == NextAction.HANDOFF
    assert decision.handoff_reason == "REPEATED_UNRESOLVED"
