"""从用户自然语言里补齐写操作参数，并决定某个意图必须调哪个工具。

从 ``app/graph/nodes.py`` 拆出来的：这些逻辑既不读图状态也不写图状态，纯粹是
"意图 + 原文 → 工具名 + 参数"，放在图节点里只会让节点越长越难测。

这里的中文关键词表是有意保留的启发式：模型漏调工具时要有确定性兜底，
不能再让一次 LLM 调用决定要不要调工具。判错的代价是多问用户一句，
而不是伪造业务结果——真正的写操作仍然要走 ``PROPOSE_*`` + 用户确认。
"""

from __future__ import annotations

import re

from app.domain.intent.types import IntentKind
from app.utils.order_ids import extract_order_id, extract_order_item_id, extract_refund_target_id

_STAR_RE = re.compile(
    r"(?:评[价分]|打)\s*([1-5])\s*星|([1-5])\s*星|星级\s*[：:]*\s*([1-5])|给.{0,8}([1-5])\s*分",
    re.I,
)
_CN_STAR_RE = re.compile(r"([一二三四五壹贰叁肆伍])\s*星")
_CN_STAR_VALUES = {
    "一": 1,
    "壹": 1,
    "二": 2,
    "贰": 2,
    "三": 3,
    "叁": 3,
    "四": 4,
    "肆": 4,
    "五": 5,
    "伍": 5,
}
_POSITIVE_STAR_HINTS = ("好评", "很好", "不错", "可以", "满意", "推荐", "赞", "棒", "给力", "喜欢")
_NEGATIVE_STAR_HINTS = ("差评", "很差", "太差", "失望", "糟糕", "垃圾", "坑")
_NEUTRAL_STAR_HINTS = ("一般", "还行", "凑合", "普通")
_REVIEW_ACTION_FILLER_RE = re.compile(
    r"^(?:请帮我|帮我|我想要|我想|我要|想要|希望|麻烦|给我|请|想|要)\s*"
)

# 这些意图必须有工具结果才能回答：订单、物流、评价、券的事实只有 Java 侧知道，
# 模型自己编一段话出来就是幻觉。
TOOL_REQUIRED_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_SEARCH.value,
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.QUERY_COUPON.value,
        IntentKind.REFUND.value,
        IntentKind.CANCEL_ORDER.value,
        IntentKind.CONFIRM_RECEIPT.value,
        IntentKind.PRODUCT_REVIEW.value,
        IntentKind.RECOMMENT.value,
        IntentKind.COMPLAINT.value,
        IntentKind.PAYMENT_ISSUE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.INVOICE.value,
        IntentKind.ADDRESS_CHANGE.value,
    }
)


def extract_review_star(text: str) -> int | None:
    """从原文里取评价星级，取不到就用情感词兜底。"""
    t = text or ""
    m = _STAR_RE.search(t)
    if m:
        for g in m.groups():
            if g:
                return int(g)
    cn_match = _CN_STAR_RE.search(t)
    if cn_match:
        return _CN_STAR_VALUES[cn_match.group(1)]
    if any(k in t for k in _NEGATIVE_STAR_HINTS):
        return 1
    if any(k in t for k in _NEUTRAL_STAR_HINTS):
        return 3
    if any(k in t for k in _POSITIVE_STAR_HINTS):
        return 5
    return None


def extract_review_content(text: str, order_id: str | None) -> str | None:
    """剥掉订单号、星级和操作动词后剩下的就是评价正文。"""
    raw = (text or "").strip()
    if not raw:
        return None
    cleaned = raw
    if order_id:
        cleaned = cleaned.replace(order_id, " ")
    cleaned = _STAR_RE.sub(" ", cleaned)
    cleaned = _CN_STAR_RE.sub(" ", cleaned)

    sentiment = ""
    for k in _POSITIVE_STAR_HINTS + _NEGATIVE_STAR_HINTS + _NEUTRAL_STAR_HINTS:
        if k in cleaned and k not in ("可以",):
            sentiment = k
            break
    if "可以" in cleaned and not sentiment:
        sentiment = "可以"
    cleaned = re.sub(
        r"(请?帮我)?(申请)?退款|(确认收货)|评价一下|评价|好评|差评|追评|打分|评星|星级|订单号|订单",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。、：:;；~～")
    # A turn such as “我想追评订单 X” contains only an operation request,
    # not review content. Strip leading action fillers before deciding whether
    # a proposal is safe; otherwise the write card would contain “我想”.
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _REVIEW_ACTION_FILLER_RE.sub("", cleaned, count=1).strip(
            " ，。、：:;；~～"
        )
    if cleaned in {"", "一下", "一下吧", "吧", "呢"}:
        cleaned = ""
    if len(cleaned) >= 1:
        return cleaned[:200]
    if sentiment:
        return sentiment
    return None


async def _refund_tool_call(
    intent_data: str | None,
    user_text: str,
    user_id: str,
) -> tuple[str, dict] | None:
    """退款要先把用户说的 ID 归一到订单项：一个订单可能有多个可退项。"""
    from app.services.order_service import order_service

    raw_id = (
        extract_order_item_id(user_text, intent_data)
        or extract_refund_target_id(intent_data, user_text)
        or ""
    ).strip()
    if not raw_id:
        return None
    item = await order_service.get_order_item(raw_id)
    if item and item.get("order_item_id"):
        return "PROPOSE_REFUND", {"orderItemId": str(item["order_item_id"])}
    order_id = extract_order_id(raw_id) or raw_id
    refundable = await order_service.list_refundable_items(user_id, order_id)
    if len(refundable) == 1 and refundable[0].get("order_item_id"):
        return "PROPOSE_REFUND", {"orderItemId": str(refundable[0]["order_item_id"])}
    # 多个可退项时先把订单摆给用户看，让用户挑，不替用户选。
    if len(refundable) > 1:
        return "QUERY_ORDERS", {"orderId": order_id}
    # A1（Verified-Action）：ID 既解析不到订单项、订单也没有可退项时，
    # 不能拿原始 ID 硬发退款提案——那是在对不可验证的业务状态做出承诺。
    # 正确行为是返回 None，把"确认这是哪一单/哪一项"交回给模型追问用户。
    return None


async def required_tool_for_intent(
    intent: str | None,
    intent_data: str | None,
    user_text: str,
    user_id: str,
    *,
    after_sales_workflow: bool = False,
) -> tuple[str, dict] | None:
    """返回该意图必须执行的 (工具名, 参数)；参数不全或无需工具时返回 None。"""
    def order_id() -> str | None:
        return (intent_data or "").strip() or extract_order_id(user_text)

    if intent == IntentKind.PRODUCT_SEARCH.value:
        # Search is a read-only, authoritative tool path. The query parser in
        # the search service owns constraint extraction; passing the original
        # text preserves brands, model numbers, budgets, and exclusions.
        keyword = (user_text or "").strip()
        return ("SEARCH_PRODUCTS", {"keyword": keyword}) if keyword else None

    if intent == IntentKind.QUERY_ORDER.value:
        oid = order_id()
        return "QUERY_ORDERS", ({"orderId": oid} if oid else {})
    if intent == IntentKind.QUERY_COUPON.value:
        return "QUERY_USER_COUPONS", {}
    if intent == IntentKind.REFUND.value:
        return await _refund_tool_call(intent_data, user_text, user_id)

    # 剩下的都是"没有订单号就没法查/没法发起"的意图。
    simple_tools = {
        IntentKind.QUERY_LOGISTICS.value: "QUERY_LOGISTICS",
        IntentKind.QUERY_COMMENT.value: "QUERY_COMMENT",
        # A raw cancellation request still needs an order lookup in the
        # legacy deterministic fallback.  The production graph resolves and
        # verifies the order first, then emits PROPOSE_CANCEL_ORDER.
        IntentKind.CANCEL_ORDER.value: (
            "PROPOSE_CANCEL_ORDER"
            if after_sales_workflow
            else "QUERY_ORDERS"
        ),
        IntentKind.CONFIRM_RECEIPT.value: "PROPOSE_CONFIRM_RECEIPT",
    }
    if intent in simple_tools:
        oid = order_id()
        return (simple_tools[intent], {"orderId": oid}) if oid else None

    if intent == IntentKind.PRODUCT_REVIEW.value:
        oid = order_id()
        star = extract_review_star(user_text)
        content = extract_review_content(user_text, oid)
        if not oid or star is None or not content:
            return None
        return "PROPOSE_PRODUCT_REVIEW", {"orderId": oid, "commentContent": content, "star": star}
    if intent == IntentKind.RECOMMENT.value:
        oid = order_id()
        content = extract_review_content(user_text, oid)
        if not oid or not content:
            return None
        return "PROPOSE_RECOMMENT", {"orderId": oid, "reCommentContent": content}
    if after_sales_workflow and intent in {
        IntentKind.COMPLAINT.value,
        IntentKind.PAYMENT_ISSUE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.INVOICE.value,
        IntentKind.ADDRESS_CHANGE.value,
    }:
        from app.services.support_case_service import support_case_service

        return "PROPOSE_CREATE_SUPPORT_CASE", {
            "category": support_case_service.category_for_intent(intent, user_text),
            "description": (user_text or "售后问题").strip()[:4000],
        }
    return None
