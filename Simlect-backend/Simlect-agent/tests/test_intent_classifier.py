import pytest

from app.domain.intent.classifier import (
    _parse_intent_json,
    classify_intent_by_rules,
    resolve_intent,
)
from app.domain.intent.types import IntentKind, NextAction, SentimentKind


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
