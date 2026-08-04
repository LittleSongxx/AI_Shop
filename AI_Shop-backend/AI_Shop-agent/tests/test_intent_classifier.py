import pytest

from app.domain.intent.classifier import (
    FUND_AT_RISK,
    PAYMENT_ISSUE_HINTS,
    _parse_intent_json,
    classify_intent_by_rules,
    resolve_intent,
)
from app.domain.intent.types import IntentKind, NextAction, RiskLevel, SentimentKind


def test_parse_intent_json():
    parsed = _parse_intent_json('{"intentType":"REFUND","data":"oid1"}')
    assert parsed["intentType"] == "REFUND"
    assert parsed["data"] == "oid1"

def test_rule_fallback_refund():
    intent = classify_intent_by_rules("我要退款")
    assert intent == IntentKind.REFUND

def test_rule_chat_returns_none_without_keywords():
    assert classify_intent_by_rules("你好呀") is None

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
