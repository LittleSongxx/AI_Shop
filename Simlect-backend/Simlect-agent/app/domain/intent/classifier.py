from __future__ import annotations

import json

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.domain.intent.types import IntentKind
from app.harness.metrics.runtime_sensors import INTENT_TOTAL
from app.services.llm_factory import create_memory_llm
from app.services.prompt_service import load_user_intent_classifier_prompt
from app.utils.order_ids import extract_order_id
from app.utils.product_consult import is_product_consult_turn, normalize_consult_card

logger = structlog.get_logger()

def _structural_intent(
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> IntentKind | None:

    if is_product_consult_turn(
        user_text, message_card, consult_card, from_product=from_product
    ):
        return IntentKind.PRODUCT_CONSULT
    return None

def classify_intent_by_rules(
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> IntentKind | None:

    from app.domain.intent.rules import (
        looks_like_category_switch,
        looks_like_new_product_search,
    )
    from app.services.product_service import is_similar_or_recommend_request

    t = (user_text or "").strip()
    if not t:
        return IntentKind.CHAT

    structural = _structural_intent(
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural:
        return structural

    _howto = any(
        k in t
        for k in (
            "如何",
            "怎么",
            "怎样",
            "步骤",
            "方法",
            "教程",
            "在哪用",
            "哪里用",
            "怎么用",
            "如何用",
            "怎么使用",
            "如何使用",
            "在哪使用",
            "在哪里用",
            "在哪看",
            "哪里看",
        )
    )
    # 操作说明类：交给 Agent 直接答，勿强行查库
    if _howto and any(
        k in t
        for k in (
            "取消",
            "优惠券",
            "优惠卷",
            "用券",
            "退款",
            "退货",
            "评价",
            "收货",
            "物流",
            "快递",
            "订单",
        )
    ):
        return IntentKind.CHAT

    if any(k in t for k in ("追评", "再评", "二次评价")):
        return IntentKind.RECOMMENT
    if any(k in t for k in ("退款", "退货", "退钱")):
        return IntentKind.REFUND
    if any(k in t for k in ("确认收货", "已收到", "收货确认")):
        return IntentKind.CONFIRM_RECEIPT
    if any(k in t for k in ("取消这个订单", "不要这个订单", "帮我取消", "给我取消")) or (
        "取消订单" in t
    ):
        return IntentKind.CANCEL_ORDER
    if any(k in t for k in ("物流", "快递", "到哪了", "运单", "包裹")):
        return IntentKind.QUERY_LOGISTICS
    if any(k in t for k in ("查看评价", "评价内容", "写了什么评价", "我的评价")):
        return IntentKind.QUERY_COMMENT
    if any(k in t for k in ("评价", "好评", "差评", "打分", "评星", "星级")) and any(
        k in t for k in ("订单", "给", "写", "提交")
    ):
        return IntentKind.PRODUCT_REVIEW
    if any(k in t for k in ("我的优惠券", "查优惠券", "有哪些券", "还有几张券", "可用券", "未使用券")):
        return IntentKind.QUERY_COUPON
    if any(k in t for k in ("优惠券", "优惠卷")) and any(
        k in t for k in ("查", "看看", "有没有", "还有", "几张", "列表")
    ):
        return IntentKind.QUERY_COUPON
    # 「取消订单」含「订单」字样，勿误判为查单
    if "取消" not in t and any(
        k in t
        for k in (
            "我的订单",
            "查订单",
            "买了什么",
            "买过什么",
            "最近买",
            "最近购买",
            "最近订单",
            "订单列表",
        )
    ):
        return IntentKind.QUERY_ORDER
    # bare 订单 太宽，留给 LLM；规则不抢

    if is_similar_or_recommend_request(t) or looks_like_new_product_search(t):
        return IntentKind.PRODUCT_SEARCH

    consult_name = (consult_card or {}).get("productName") or (consult_card or {}).get("product_name")
    if consult_card and looks_like_category_switch(t, consult_name):
        return IntentKind.PRODUCT_SEARCH

    if any(k in t for k in ("搜索", "找", "买", "推荐", "热销", "爆款")) and len(t) <= 40:
        return IntentKind.PRODUCT_SEARCH
    return None


def classify_high_confidence_order_intent(user_text: str) -> IntentKind | None:
    intent, _ = classify_high_confidence_intent(user_text)
    return intent


def classify_high_confidence_intent(user_text: str) -> tuple[IntentKind | None, str]:
    """Minimal anti-misfire rules only.

    Do NOT encode full business policy here — Agent + LLM intent classifier decide
    refund/review/cancel/howto. Rules only beat clear PRODUCT_SEARCH misfires.
    """
    t = (user_text or "").strip()
    if not t:
        return None, ""
    oid = extract_order_id(t) or ""

    if oid and any(k in t for k in ("到哪里", "到哪了", "物流", "快递", "运单", "包裹", "轨迹")):
        return IntentKind.QUERY_LOGISTICS, oid
    if any(
        k in t
        for k in (
            "我的订单",
            "最近的订单",
            "最近订单",
            "查订单",
            "订单列表",
            "买了什么",
            "买过什么",
            "最近买了",
            "最近买的",
            "最近购买",
        )
    ):
        return IntentKind.QUERY_ORDER, oid
    return None, ""


def _parse_intent_json(raw: str) -> tuple[IntentKind | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None, ""
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None, ""
    if not isinstance(obj, dict):
        return None, ""
    key = (obj.get("intentType") or obj.get("intent_type") or "").strip().upper()
    data = (obj.get("data") or obj.get("keyword") or "").strip()
    try:
        return IntentKind(key), data
    except ValueError:
        return None, data

def _build_intent_context(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> str:
    consult = normalize_consult_card(consult_card) or normalize_consult_card(message_card)
    lines = [
        "=== 当前上下文 ===",
        f"商品详情通道(fromProduct)：{'是' if from_product else '否'}",
        f"消息含商品卡片：{'是' if normalize_consult_card(message_card) else '否'}",
    ]
    if consult:
        lines.append(
            f"咨询中商品：{consult.get('productName')}（ID={consult.get('productId')}）"
        )
    else:
        lines.append("咨询中商品：无")
    if extract_order_id(user_text or ""):
        lines.append("用户消息含订单号样式文本：是")
    return "\n".join(lines)

async def classify_intent_by_llm(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> tuple[IntentKind | None, str]:
    template = await load_user_intent_classifier_prompt()
    if not template.strip():
        return None, ""

    context = _build_intent_context(
        user_id,
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    try:
        base = template % (user_id, user_text)
    except TypeError:
        base = f"{template}\n\n用户ID：{user_id}\n用户问题：{user_text}"

    prompt = f"{base}\n\n{context}"

    try:
        llm = create_memory_llm()
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是电商客服意图分类器。"
                        "只输出一行 JSON：{\"intentType\":\"类型\",\"data\":\"订单号或搜索关键词或空\"}。"
                        "禁止解释、禁止 markdown。"
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content or "")
        intent, data = _parse_intent_json(content)
        if intent is None:
            logger.warning("intent_llm_parse_failed", raw=content[:200])
        return intent, data
    except Exception as e:
        logger.warning("intent_llm_failed", error=str(e))
        return None, ""

async def resolve_intent(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> tuple[IntentKind, str, str]:

    structural = _structural_intent(
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural is not None:
        INTENT_TOTAL.labels(intent=structural.value, source="structural").inc()
        return structural, "structural", ""

    hi_intent, hi_data = classify_high_confidence_intent(user_text)
    if hi_intent is not None:
        INTENT_TOTAL.labels(intent=hi_intent.value, source="rule_priority").inc()
        return hi_intent, "rule_priority", hi_data

    settings = get_settings()
    intent_data = ""
    if settings.intent_use_llm:
        llm_intent, intent_data = await classify_intent_by_llm(
            user_id,
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
        )
        if llm_intent is not None:
            INTENT_TOTAL.labels(intent=llm_intent.value, source="llm").inc()
            return llm_intent, "llm", intent_data

    if settings.intent_rule_fallback:
        ruled = classify_intent_by_rules(
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
        )
        if ruled is not None:
            INTENT_TOTAL.labels(intent=ruled.value, source="rule").inc()
            return ruled, "rule", ""

    INTENT_TOTAL.labels(intent=IntentKind.CHAT.value, source="default").inc()
    return IntentKind.CHAT, "default", ""
