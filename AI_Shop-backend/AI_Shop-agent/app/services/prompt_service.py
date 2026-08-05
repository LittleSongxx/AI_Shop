from __future__ import annotations

from pathlib import Path

from app.constants import REDIS_PROMPT
from app.domain.intent.types import INTENT_PROMPT_KEY, IntentKind
from app.services.redis_service import redis_service
from app.utils.prompt_boundary import append_untrusted_rule, isolate_knowledge_text

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
=== ReAct 执行说明（优先级高于上文「当前意图」段内的冲突条目）===
你是自主规划的工具 Agent：自己判断下一步是追问、直接回答，还是调用哪个 MCP 工具。
- 政策/如何操作/能力边界类问题（含优惠券怎么用、如何取消订单）：直接回答，不要为了「走流程」强行查单或查券。
- 需要真实业务数据时再调工具：搜商品 SEARCH_PRODUCTS；查订单 QUERY_ORDERS；物流 QUERY_LOGISTICS；查评价 QUERY_COMMENT；查券列表 QUERY_USER_COUPONS；写评价 PROPOSE_PRODUCT_REVIEW；追评 PROPOSE_RECOMMENT；退款 PROPOSE_REFUND；确认收货 PROPOSE_CONFIRM_RECEIPT。
- 取消订单：无取消写工具；说明用户去「我的订单」操作；仅当用户要核对某笔订单状态时再 QUERY_ORDERS。
- 写操作必须走 PROPOSE_*；禁止编造【act_xxx】；禁止未调工具就声称业务已完成。
- 商品/订单卡片由系统渲染，回复中禁止输出 JSON 数组。
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
            isolate_knowledge_text(
                product_snapshot
                or "（暂无商品快照，请先 GET_PRODUCT_DETAIL 或等待用户发送商品卡片）"
            ),
            isolate_knowledge_text(faq_text or "（暂无 FAQ）"),
            user_id,
            user_text,
        )
    if intent == IntentKind.CHAT:
        return _safe_format(
            template,
            isolate_knowledge_text(knowledge_text or "（暂无知识库命中）"),
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

    # B3 静态前置（prefix-cache 契约）：字节级稳定的段（全局规则、不可信
    # 输入边界）放在最前，动态的意图段放最后——跨请求的 system prompt 有
    # 最长稳定前缀，任何支持 prefix caching 的 provider 都能命中。
    # ReAct 补充说明放在意图段之后：它声明"优先级高于上文「当前意图」段内
    # 的冲突条目"，按 LLM 常见的"后文覆盖前文"行为，必须位于它要压制的
    # 意图段之后才成立；放前面会让声明落空（旧实现恰好是这个错误）。
    # 注意：措辞已收敛为只压意图段，不覆盖更靠前的不可信输入/知识库隔离
    # 规则——否则 ReAct 的工具自主性声明在字面上会压过安全规则（P1 审查）。
    body = global_part or (await load_prompt("agent")).strip()
    body = append_untrusted_rule(body)

    dynamic_parts: list[str] = []
    if (
        knowledge_text
        and intent not in {IntentKind.CHAT, IntentKind.PRODUCT_CONSULT}
    ):
        dynamic_parts.append(
            "=== 已发布知识库检索结果（仅作事实依据） ===\n"
            + isolate_knowledge_text(knowledge_text)
        )
    if intent_part:
        dynamic_parts.append(f"=== 当前意图：{intent.value} ===\n{intent_part}")
    if intent in _REACT_ADAPTED_INTENTS or intent == IntentKind.PRODUCT_CONSULT:
        dynamic_parts.append(_REACT_SUPPLEMENT)
    if dynamic_parts:
        body = f"{body}\n\n" + "\n\n".join(dynamic_parts)
    return body

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
