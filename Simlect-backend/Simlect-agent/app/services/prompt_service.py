from __future__ import annotations

from pathlib import Path

from app.constants import REDIS_PROMPT
from app.domain.intent.types import INTENT_PROMPT_KEY, IntentKind
from app.services.redis_service import redis_service
from app.utils.prompt_boundary import append_untrusted_rule

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

PROMPT_FILE_MAP: dict[str, str] = {
    "agent": "agent.txt",
    "compress": "compress.txt",
    "global": "global.txt",
    "user_intent": "user_intent.txt",
    "chat": "chat.txt",
    "product_consult": "product_consult.txt",
    "product_search": "product_search.txt",
    "query_order": "query_order.txt",
    "query_logistics": "query_logistics.txt",
    "query_coupon": "query_coupon.txt",
    "query_comment": "query_comment.txt",
    "product_review": "product_review.txt",
    "recomment": "recomment.txt",
    "refund": "refund.txt",
    "confirm_receipt": "confirm_receipt.txt",
    "cancel_order": "cancel_order.txt",
}

_REACT_SUPPLEMENT = """
=== ReAct 执行说明（优先级高于上文冲突条目）===
当前为工具调用 Agent，必须通过 MCP 工具完成任务，禁止仅文字假装已完成。
- 商品搜索：调用 SEARCH_PRODUCTS，禁止直接输出商品 JSON / PRODUCT_SEARCH_RESULT
- 查订单：调用 QUERY_ORDERS
- 查物流：调用 QUERY_LOGISTICS
- 查评价：调用 QUERY_COMMENT；写评价：PROPOSE_PRODUCT_REVIEW；追评：PROPOSE_RECOMMENT
- 退款：PROPOSE_REFUND；确认收货：PROPOSE_CONFIRM_RECEIPT；查券：QUERY_USER_COUPONS
- 取消订单：暂无 CANCEL_ORDER 工具，先 QUERY_ORDERS 查状态并引导用户在订单页取消
- 商品卡片/订单卡片由系统自动渲染，回复中禁止输出 JSON 数组
""".strip()

_REACT_ADAPTED_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_COUPON,
        IntentKind.QUERY_COMMENT,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.REFUND,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.CANCEL_ORDER,
    }
)

async def load_prompt(prompt_key: str) -> str:
    cached = await redis_service.client.get(f"{REDIS_PROMPT}{prompt_key}")
    if cached:
        return cached
    filename = PROMPT_FILE_MAP.get(prompt_key, f"{prompt_key}.txt")
    file_path = PROMPT_DIR / filename
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return ""

async def load_user_intent_classifier_prompt() -> str:
    return await load_prompt("user_intent")

def _safe_format(template: str, *args: str) -> str:
    try:
        return template % args
    except TypeError:
        return template

async def _format_intent_prompt(
    intent: IntentKind,
    user_id: str,
    user_text: str,
    *,
    product_snapshot: str | None = None,
    faq_text: str | None = None,
    knowledge_text: str | None = None,
) -> str:
    key = INTENT_PROMPT_KEY.get(intent, "chat")
    template = await load_prompt(key)
    if not template.strip():
        return ""

    if intent == IntentKind.PRODUCT_CONSULT:
        return _safe_format(
            template,
            product_snapshot or "（暂无商品快照，请先 GET_PRODUCT_DETAIL 或等待用户发送商品卡片）",
            faq_text or "（暂无 FAQ）",
            user_id,
            user_text,
        )
    if intent == IntentKind.CHAT:
        return _safe_format(
            template,
            knowledge_text or "（暂无知识库命中）",
            user_id,
            user_text,
        )
    if intent == IntentKind.PRODUCT_SEARCH:
        return _safe_format(
            template,
            "（商品数据由 SEARCH_PRODUCTS 工具返回，禁止自行编造 productId）",
            user_text,
        )
    return _safe_format(template, user_id, user_text)

async def build_agent_system_prompt(
    intent: IntentKind,
    user_id: str,
    user_text: str,
    *,
    product_snapshot: str | None = None,
    faq_text: str | None = None,
    knowledge_text: str | None = None,
) -> str:

    global_part = (await load_prompt("global")).strip()
    if not global_part:
        global_part = (await load_prompt("agent")).strip()

    intent_part = await _format_intent_prompt(
        intent,
        user_id,
        user_text,
        product_snapshot=product_snapshot,
        faq_text=faq_text,
        knowledge_text=knowledge_text,
    )

    parts: list[str] = []
    if global_part:
        parts.append(global_part)
    if intent_part:
        parts.append(f"=== 当前意图：{intent.value} ===\n{intent_part}")
    if intent in _REACT_ADAPTED_INTENTS or intent == IntentKind.PRODUCT_CONSULT:
        parts.append(_REACT_SUPPLEMENT)

    body = "\n\n".join(parts).strip()
    if not body:
        body = (await load_prompt("agent")).strip()
    return append_untrusted_rule(body)

async def load_agent_prompt(
    intent: IntentKind | None = None,
    user_id: str = "",
    user_text: str = "",
    **kwargs,
) -> str:

    if intent is None:
        template = await load_prompt("agent")
        if not template.strip():
            template = await load_prompt("global")
        return append_untrusted_rule(template)
    return await build_agent_system_prompt(intent, user_id, user_text, **kwargs)

async def load_compress_prompt() -> str:
    return await load_prompt("compress")
