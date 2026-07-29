from __future__ import annotations

import json
import re

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.config.settings import get_settings
from app.domain.intent.types import (
    IntentDecision,
    IntentKind,
    NextAction,
    RiskLevel,
    SentimentKind,
    UrgencyKind,
)
from app.harness.metrics.runtime_sensors import INTENT_TOTAL
from app.services.llm_factory import create_memory_llm
from app.services.prompt_service import load_user_intent_classifier_prompt
from app.utils.order_ids import extract_order_id, extract_order_item_id
from app.utils.product_consult import is_product_consult_turn, normalize_consult_card

logger = structlog.get_logger()

_HUMAN_HINTS = (
    "转人工",
    "人工客服",
    "找客服",
    "真人客服",
    "人工处理",
    "人工介入",
    "找你们主管",
)
_VERY_NEGATIVE_HINTS = (
    "诈骗",
    "骗子",
    "报警",
    "起诉",
    "曝光",
    "消费者协会",
    "12315",
    "太垃圾",
    "气死",
    "再也不买",
)
_NEGATIVE_HINTS = (
    "生气",
    "愤怒",
    "失望",
    "不满意",
    "差劲",
    "糟糕",
    "投诉",
    "没用",
    "没解决",
    "一直不",
    "怎么还",
    "烦",
)
_POSITIVE_HINTS = ("谢谢", "满意", "很好", "不错", "喜欢", "解决了")

# ---------------------------------------------------------------------------
# 支付类词汇：单一事实源
#
# 这里原先是两张表：意图分支内联一份支付词，风险判定用另一份 _FUND_RISK_HINTS。
# 两份各自增删，于是漏成了这样——
#   「同一笔订单重复支付了两次」意图表收了「重复支付」，风险表没收 → 判成 PAYMENT_ISSUE
#     但 risk=MEDIUM，不转人工，机器人自己去解释一笔重复付款；
#   「支付失败了但是钱扣了」风险表有「扣了钱」但没有「钱扣了」，词序一换就漏；
#   「订单已经取消了为什么还扣款」两张表都只有「扣款了」，连意图都没判出来，落到 CHAT/0.4。
# 而「我的钱被盗刷了」是唯一正确转人工的，原因只是「盗刷」恰好被两张表都收了。
#
# 所以问题不是"漏了几个词"，是同一件事有两处说法。改成分级派生：
#   FUND_AT_RISK    钱已经动了/该退没退 → RiskLevel.HIGH → FUND_DISPUTE 转人工
#   PAYMENT_BLOCKED 支付走不通但钱没动 → 仍是 PAYMENT_ISSUE，但不必然转人工
# 意图分支读派生出的 PAYMENT_ISSUE_HINTS，因此任何资金词都不可能只被风险表认得。
# 分级的作用：「支付失败了怎么办」这类纯操作咨询不该占用人工坐席，而任何一句提到钱
# 已经被扣走的都该走人工——这两件事必须能分开表达。
#
# 取舍：「扣款」按裸词收，不写死「重复扣款」「扣款了」这些具体说法。代价是
# 「什么时候扣款」这类售前咨询也会升级；收益是任何"钱被扣了"的说法都不会漏。
# 资金问题上多转一次人工的成本远低于漏转一次。
# ---------------------------------------------------------------------------
FUND_AT_RISK = (
    "扣款",
    "扣了钱",
    "钱扣了",
    "重复支付",
    "钱没退",
    "退款没到账",
    "支付成功没订单",
    "资金",
    "盗刷",
)
PAYMENT_BLOCKED = (
    "支付失败",
    "付款失败",
    "支付异常",
)
PAYMENT_ISSUE_HINTS = FUND_AT_RISK + PAYMENT_BLOCKED
_UNRESOLVED_HINTS = ("还是没解决", "没有解决", "又不行", "还是不行", "说了没用", "重复问")

_TOOL_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.REFUND,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_COUPON,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.QUERY_COMMENT,
        IntentKind.REFUND_STATUS,
        IntentKind.CANCEL_ORDER,  # 查到订单后引导用户自行取消；需要工具结果才能响应
    }
)


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

    text = (user_text or "").strip()
    lower = text.lower()
    if not text:
        return IntentKind.CHAT

    structural = _structural_intent(
        text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural:
        return structural

    if any(hint in text for hint in _HUMAN_HINTS):
        return IntentKind.HUMAN_REQUEST
    if any(k in text for k in ("退款进度", "退款到哪", "退款到账", "退款什么时候", "退款状态")):
        return IntentKind.REFUND_STATUS
    if any(k in text for k in PAYMENT_ISSUE_HINTS):
        return IntentKind.PAYMENT_ISSUE
    if any(k in text for k in ("破损", "损坏", "碎了", "错发", "发错", "漏发", "少发", "缺件", "质量问题", "假货")):
        return IntentKind.DAMAGED_OR_WRONG_ITEM

    # 操作方法/如何/怎么类 → CHAT（必须在 INVOICE/ADDRESS_CHANGE 等专项分支之前执行，
    # 否则「发票怎么申请」「确认收货在哪里点」会被专项分支抢走，导致错误路由或触发
    # PROPOSE_CONFIRM_RECEIPT 等副作用）。
    howto = any(
        k in text
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
            "在哪里",  # 覆盖「确认收货在哪里点」「在哪里取消」等格式
            "在哪看",
            "哪里看",
        )
    )
    if howto and any(
        k in text
        for k in (
            "取消",
            "优惠券",
            "优惠卷",
            "用券",
            "退款",
            "退货",
            "评价",
            "追评",   # 「追评怎么写」
            "收货",
            "物流",
            "快递",
            "订单",
            "发票",
            "地址",
        )
    ):
        return IntentKind.CHAT

    if any(k in text for k in ("开发票", "发票", "抬头", "税号")):
        return IntentKind.INVOICE
    if any(k in text for k in ("修改地址", "改地址", "收货地址错", "地址填错", "换地址")):
        return IntentKind.ADDRESS_CHANGE
    if "投诉" in text or any(k in text for k in _VERY_NEGATIVE_HINTS):
        return IntentKind.COMPLAINT

    if any(k in text for k in ("追评", "再评", "二次评价")):
        return IntentKind.RECOMMENT
    if any(k in text for k in ("退款", "退货", "退钱")):
        return IntentKind.REFUND
    if any(k in text for k in ("确认收货", "已收到", "收货确认")):
        return IntentKind.CONFIRM_RECEIPT
    if any(k in text for k in ("取消这个订单", "不要这个订单", "帮我取消", "给我取消", "取消订单")):
        return IntentKind.CANCEL_ORDER
    if any(k in text for k in ("物流", "快递", "到哪了", "运单", "包裹")):
        return IntentKind.QUERY_LOGISTICS
    if any(k in text for k in ("查看评价", "评价内容", "写了什么评价", "我的评价")):
        return IntentKind.QUERY_COMMENT
    if any(k in text for k in ("评价", "好评", "差评", "打分", "评星", "星级")) and any(
        k in text for k in ("订单", "给", "写", "提交")
    ):
        return IntentKind.PRODUCT_REVIEW
    if any(k in text for k in ("我的优惠券", "查优惠券", "有哪些券", "还有几张券", "可用券", "未使用券", "这张券", "那张券")):
        return IntentKind.QUERY_COUPON
    # 含「订单」的复合句（如「帮我看看订单，顺便查一下优惠券」）主意图是 QUERY_ORDER，
    # 不应被「优惠券 + 看看」触发的宽泛规则抢走。
    if (
        any(k in text for k in ("优惠券", "优惠卷"))
        and any(k in text for k in ("查", "看看", "有没有", "还有", "几张", "列表"))
        and "订单" not in text
    ):
        return IntentKind.QUERY_COUPON
    if "取消" not in text and any(
        k in text
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

    if is_similar_or_recommend_request(text) or looks_like_new_product_search(text):
        return IntentKind.PRODUCT_SEARCH

    consult_name = (consult_card or {}).get("productName") or (consult_card or {}).get(
        "product_name"
    )
    if consult_card and looks_like_category_switch(text, consult_name):
        return IntentKind.PRODUCT_SEARCH
    if any(k in text for k in ("搜索", "找", "买", "推荐", "热销", "爆款")) and len(text) <= 40:
        return IntentKind.PRODUCT_SEARCH
    if any(k in text for k in ("售后", "退换", "商品有问题", "订单有问题")):
        return IntentKind.AFTERSALES_UNKNOWN
    if lower in {"你好", "您好", "hello", "hi", "在吗", "谢谢"}:
        return IntentKind.CHAT
    return None


def classify_high_confidence_order_intent(user_text: str) -> IntentKind | None:
    intent, _ = classify_high_confidence_intent(user_text)
    return intent


def classify_high_confidence_intent(user_text: str) -> tuple[IntentKind | None, str]:
    text = (user_text or "").strip()
    if not text:
        return None, ""
    order_id = extract_order_id(text) or ""

    ruled = classify_intent_by_rules(text)
    if ruled in {
        IntentKind.HUMAN_REQUEST,
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND_STATUS,
        IntentKind.INVOICE,
        IntentKind.ADDRESS_CHANGE,
    }:
        return ruled, order_id
    if order_id and any(
        k in text for k in ("到哪里", "到哪了", "物流", "快递", "运单", "包裹", "轨迹")
    ):
        return IntentKind.QUERY_LOGISTICS, order_id
    if any(
        k in text
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
        return IntentKind.QUERY_ORDER, order_id
    return None, ""


def analyze_sentiment(user_text: str) -> SentimentKind:
    text = (user_text or "").strip()
    if any(k in text for k in _VERY_NEGATIVE_HINTS):
        return SentimentKind.VERY_NEGATIVE
    negative_hits = sum(1 for k in _NEGATIVE_HINTS if k in text)
    if negative_hits >= 2 or ("!" in text and negative_hits):
        return SentimentKind.VERY_NEGATIVE
    if negative_hits:
        return SentimentKind.NEGATIVE
    if any(k in text for k in _POSITIVE_HINTS):
        return SentimentKind.POSITIVE
    return SentimentKind.NEUTRAL


def extract_entities(user_text: str, data: str = "") -> dict[str, str]:
    text = user_text or ""
    entities: dict[str, str] = {}
    order_item_id = extract_order_item_id(text, data)
    order_id = extract_order_id(text, data)
    if order_item_id:
        entities["orderItemId"] = order_item_id
    if order_id:
        entities["orderId"] = order_id
    amount = re.search(r"(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)\s*元", text)
    if amount:
        entities["amount"] = amount.group(1)
    product_id = re.search(r"(?:商品(?:ID|id)?)[：:\s]*([A-Za-z0-9_-]{3,32})", text)
    if product_id:
        entities["productId"] = product_id.group(1)
    return entities


def _parse_intent_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


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
) -> IntentDecision | None:
    template = await load_user_intent_classifier_prompt()
    if not template.strip():
        return None

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
                        "你是电商客服意图分类器。只输出一行 JSON，字段为："
                        "intentType、confidence、data、entities、sentiment、urgency、"
                        "riskLevel、nextAction、handoffReason。禁止解释、禁止 markdown。"
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = (
            response.content
            if isinstance(response.content, str)
            else str(response.content or "")
        )
        obj = _parse_intent_json(content)
        if not obj:
            logger.warning("intent_llm_parse_failed", raw=content[:200])
            return None
        try:
            intent = IntentKind(
                str(obj.get("intentType") or obj.get("intent_type") or "").upper()
            )
        except ValueError:
            logger.warning("intent_llm_unknown_intent", raw=content[:200])
            return None
        data = str(obj.get("data") or obj.get("keyword") or "").strip()
        entities = obj.get("entities") if isinstance(obj.get("entities"), dict) else {}
        entities = {str(k): str(v) for k, v in entities.items() if v not in (None, "")}
        return IntentDecision(
            intent=intent,
            confidence=_clamp_confidence(obj.get("confidence"), 0.75),
            entities={**extract_entities(user_text, data), **entities},
            sentiment=_enum_or_default(
                SentimentKind, obj.get("sentiment"), analyze_sentiment(user_text)
            ),
            urgency=_enum_or_default(UrgencyKind, obj.get("urgency"), UrgencyKind.NORMAL),
            risk_level=_enum_or_default(
                RiskLevel, obj.get("riskLevel") or obj.get("risk_level"), RiskLevel.LOW
            ),
            next_action=_enum_or_default(
                NextAction, obj.get("nextAction") or obj.get("next_action"), NextAction.ANSWER
            ),
            handoff_reason=str(obj.get("handoffReason") or "").strip() or None,
            source="llm",
            data=data,
        )
    except Exception as exc:
        logger.warning("intent_llm_failed", error=str(exc))
        return None


async def resolve_intent(
    user_id: str,
    user_text: str,
    *,
    from_product: bool = False,
    consult_card: dict | None = None,
    message_card: dict | None = None,
    unresolved_count: int = 0,
    allow_llm: bool = True,
) -> IntentDecision:
    structural = _structural_intent(
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=message_card,
    )
    if structural is not None:
        decision = _build_decision(
            structural, user_text, confidence=0.99, source="structural"
        )
        return _record_and_apply(decision, user_text, unresolved_count)

    high_intent, high_data = classify_high_confidence_intent(user_text)
    if high_intent is not None:
        decision = _build_decision(
            high_intent,
            user_text,
            confidence=0.96,
            source="rule_priority",
            data=high_data,
        )
        return _record_and_apply(decision, user_text, unresolved_count)

    settings = get_settings()
    if allow_llm and settings.intent_use_llm:
        llm_decision = await classify_intent_by_llm(
            user_id,
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
        )
        if llm_decision is not None:
            return _record_and_apply(llm_decision, user_text, unresolved_count)

    if settings.intent_rule_fallback:
        ruled = classify_intent_by_rules(
            user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=message_card,
        )
        if ruled is not None:
            confidence = 0.9 if ruled != IntentKind.AFTERSALES_UNKNOWN else 0.65
            decision = _build_decision(
                ruled, user_text, confidence=confidence, source="rule"
            )
            return _record_and_apply(decision, user_text, unresolved_count)

    decision = _build_decision(
        IntentKind.CHAT,
        user_text,
        confidence=0.4,
        source="default",
        next_action=NextAction.ASK_CLARIFICATION,
    )
    return _record_and_apply(decision, user_text, unresolved_count)


def _build_decision(
    intent: IntentKind,
    user_text: str,
    *,
    confidence: float,
    source: str,
    data: str = "",
    next_action: NextAction | None = None,
) -> IntentDecision:
    sentiment = analyze_sentiment(user_text)
    risk = RiskLevel.HIGH if any(k in user_text for k in FUND_AT_RISK) else RiskLevel.LOW
    if risk == RiskLevel.LOW and intent in {
        IntentKind.PAYMENT_ISSUE,
        IntentKind.COMPLAINT,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
    }:
        risk = RiskLevel.MEDIUM
    urgency = UrgencyKind.NORMAL
    if sentiment == SentimentKind.VERY_NEGATIVE or risk == RiskLevel.HIGH:
        urgency = UrgencyKind.CRITICAL
    elif sentiment == SentimentKind.NEGATIVE or intent in {
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND_STATUS,
    }:
        urgency = UrgencyKind.HIGH

    if next_action is None:
        next_action = NextAction.TOOL if intent in _TOOL_INTENTS else NextAction.ANSWER
        if intent == IntentKind.AFTERSALES_UNKNOWN:
            next_action = NextAction.ASK_CLARIFICATION

    return IntentDecision(
        intent=intent,
        confidence=confidence,
        entities=extract_entities(user_text, data),
        sentiment=sentiment,
        urgency=urgency,
        risk_level=risk,
        next_action=next_action,
        source=source,
        data=data,
    )


def _record_and_apply(
    decision: IntentDecision, user_text: str, unresolved_count: int
) -> IntentDecision:
    entities = {
        **extract_entities(user_text, decision.data),
        **decision.entities,
    }
    if (
        decision.data
        and "orderId" not in entities
        and decision.intent
        in {
            IntentKind.QUERY_ORDER,
            IntentKind.QUERY_LOGISTICS,
            IntentKind.REFUND_STATUS,
            IntentKind.CANCEL_ORDER,
            IntentKind.CONFIRM_RECEIPT,
        }
    ):
        entities["orderId"] = decision.data
    if entities != decision.entities:
        decision = decision.model_copy(update={"entities": entities})
    decision = _apply_handoff_policy(decision, user_text, unresolved_count)
    INTENT_TOTAL.labels(intent=decision.intent.value, source=decision.source).inc()
    return decision


def _apply_handoff_policy(
    decision: IntentDecision, user_text: str, unresolved_count: int
) -> IntentDecision:
    explicit_human = decision.intent == IntentKind.HUMAN_REQUEST or any(
        hint in user_text for hint in _HUMAN_HINTS
    )
    threshold = get_settings().intent_handoff_confidence
    current_unresolved = (
        decision.confidence < threshold
        or decision.next_action == NextAction.ASK_CLARIFICATION
    )
    unresolved = (
        unresolved_count >= 1 and current_unresolved
    ) or any(k in user_text for k in _UNRESOLVED_HINTS)
    severe = decision.sentiment == SentimentKind.VERY_NEGATIVE
    fund_dispute = decision.risk_level == RiskLevel.HIGH

    if explicit_human:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "USER_REQUEST",
                "urgency": UrgencyKind.HIGH,
            }
        )
    if fund_dispute:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "FUND_DISPUTE",
                "urgency": UrgencyKind.CRITICAL,
            }
        )
    if severe and decision.intent in {
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.REFUND,
        IntentKind.REFUND_STATUS,
    }:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "SEVERE_NEGATIVE_SENTIMENT",
            }
        )
    if unresolved:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF,
                "handoff_reason": "REPEATED_UNRESOLVED",
                "urgency": UrgencyKind.HIGH,
            }
        )
    if decision.confidence < threshold:
        return decision.model_copy(
            update={
                "next_action": NextAction.HANDOFF_SUGGESTED,
                "handoff_reason": "LOW_CONFIDENCE",
            }
        )
    return decision


def _clamp_confidence(value, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _enum_or_default(enum_type, value, default):
    try:
        return enum_type(str(value).upper())
    except (TypeError, ValueError):
        return default
