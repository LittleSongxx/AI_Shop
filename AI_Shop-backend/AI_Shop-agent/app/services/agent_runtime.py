from __future__ import annotations

import json
import re
import time
from functools import lru_cache

import structlog

from app.domain.intent.rules import wants_order_list_cards
from app.harness.guardrails.output_guard import strip_emojis
from app.harness.guardrails.product_text_guard import (
    should_force_product_cards,
    text_promises_product_cards,
)
from app.harness.metrics.runtime_sensors import LLM_LATENCY, STREAM_TOKENS
from app.mcp.tools import build_mcp_tools
from app.services.llm_factory import ChatLLMConfig, chat_llm_config, chat_llm_for_config
from app.services.mcp_tool_router import mcp_tool_router
from app.services.message_service import agent_message_service
from app.services.pending_action_service import pending_action_service
from app.services.redis_service import redis_service
from app.services.stream_service import stream_service
from app.utils.biz_payload import (
    build_action_confirm_payload,
    build_action_confirm_unavailable_payload,
    build_product_search_message,
    collect_act_token_ids,
    compact_product_search_intro,
    is_action_confirm_json,
    is_order_cards_json,
    is_product_cards_json,
    looks_like_aftersales_or_order_text,
    strip_embedded_product_json,
    trim_assistant,
)
from app.utils.product_consult import is_product_consult_turn, parse_consult_card

logger = structlog.get_logger()

def chunk_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""

async def is_cancelled(user_id: str, message_id: int) -> bool:
    return await redis_service.is_cancelled(user_id, message_id)

async def resolve_action_confirm(
    full_text: str, messages: list, user_id: str
) -> tuple[str, str, str] | None:
    token_ids = collect_act_token_ids(full_text, messages)
    if not token_ids:
        return None

    pending = None
    token_id = None
    for tid in token_ids:
        candidate = await pending_action_service.get_by_token(tid)
        if not candidate:
            continue
        if candidate.get("userId") != user_id:
            assistant, biz_data = build_action_confirm_unavailable_payload(
                tid, full_text, reason="wrong_user"
            )
            return assistant, biz_data, "action_confirm"
        pending = candidate
        token_id = tid
        break

    if pending and token_id:
        assistant, biz_data = build_action_confirm_payload(pending, full_text)
        return assistant, biz_data, "action_confirm"

    # Token present but Redis has no pending — usually LLM invented 【act_xxx】
    # without calling PROPOSE_*. Never render a pre-expired confirm card.
    logger.warning(
        "action_confirm_token_missing",
        user_id=user_id,
        tokens=token_ids,
    )
    return None

async def stream_llm_turn(
    llm,
    messages: list,
    user_id: str,
    message_id: int,
    user_message: str | None,
    chunks: list[str],
):
    started = time.perf_counter()
    gathered = None
    sent_visible = ""
    try:
        async for chunk in llm.astream(messages):
            if await is_cancelled(user_id, message_id):
                return None
            gathered = chunk if gathered is None else gathered + chunk
            visible = strip_embedded_product_json(
                strip_emojis(chunk_text(gathered.content))
            )
            delta = visible[len(sent_visible) :]
            sent_visible = visible
            if not delta:
                continue
            chunks.append(delta)
            STREAM_TOKENS.inc(len(delta))
            await stream_service.push_chunk(user_id, message_id, delta, user_message)
        return gathered
    finally:
        LLM_LATENCY.observe(max(0.0, time.perf_counter() - started))

def _strip_emojis_from_assistant(assistant: str) -> str:
    text = (assistant or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                from app.utils.biz_payload import PRODUCT_SEARCH_RESULT_TYPE

                if obj.get("type") == PRODUCT_SEARCH_RESULT_TYPE:
                    if obj.get("intro"):
                        obj["intro"] = strip_emojis(str(obj["intro"]))
                    return json.dumps(obj, ensure_ascii=False)
                if obj.get("intro"):
                    obj["intro"] = strip_emojis(str(obj["intro"]))
                    return json.dumps(obj, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return strip_emojis(text)

async def _resolve_product_cards_json(
    assistant_cards: str | None,
    tool_biz: dict | None,
    tools_called: list[str] | None,
    search_tool_hint: str | None = None,
) -> tuple[str | None, str | None]:
    called = list(tools_called or [])
    # Only treat as product search when SEARCH_PRODUCTS actually ran.
    # Do NOT infer from search_tool_hint alone (other tools used to misuse that field).

    if assistant_cards and is_product_cards_json(assistant_cards):
        return assistant_cards, "product_search"

    if "SEARCH_PRODUCTS" in called:
        if (tool_biz or {}).get("productIds"):
            logger.error(
                "mcp_product_result_missing_cards",
                product_count=len((tool_biz or {}).get("productIds") or []),
            )
        return None, "product_search"
    return None, None

_NON_PRODUCT_TOOLS = frozenset({
    "QUERY_ORDERS",
    "QUERY_LOGISTICS",
    "QUERY_COMMENT",
    "QUERY_USER_COUPONS",
    "PROPOSE_REFUND",
    "PROPOSE_CONFIRM_RECEIPT",
    "PROPOSE_PRODUCT_REVIEW",
    "PROPOSE_RECOMMENT",
    "GET_PRODUCT_DETAIL",
})

_WRITE_PROPOSE_TOOLS = frozenset({
    "PROPOSE_REFUND",
    "PROPOSE_CONFIRM_RECEIPT",
    "PROPOSE_PRODUCT_REVIEW",
    "PROPOSE_RECOMMENT",
})

def _recover_order_cards(
    assistant_cards: str | None,
    tools_called: list[str] | None,
) -> str | None:
    """Use only the order cards returned by MCP; never re-query the order service."""
    if is_order_cards_json(assistant_cards):
        return assistant_cards
    if (assistant_cards or "").strip() == "[]":
        return "[]"
    if "QUERY_ORDERS" in (tools_called or []):
        logger.error("mcp_order_result_missing_cards")
    return None

async def finalize_agent_response(
    agent_msg: dict,
    chunks: list[str],
    messages: list,
    biz_type: str | None = None,
    biz_data: str | None = None,
    assistant_cards: str | None = None,
    tools_called: list[str] | None = None,
    tool_biz: dict | None = None,
    search_tool_hint: str | None = None,
    source_refs: list[dict] | dict | None = None,
    user_text: str | None = None,
    consult_card: dict | None = None,
    message_card: dict | None = None,
) -> None:
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    full_text = "".join(chunks)
    is_consult_turn = is_product_consult_turn(
        user_text,
        message_card,
        consult_card,
        from_product=bool(agent_msg.get("fromProduct")),
    )

    resolved = await resolve_action_confirm(full_text, messages, user_id)
    if resolved:
        assistant, biz_data, biz_type = resolved
    else:
        # Drop fabricated act tokens so they never surface as "expired" cards.
        full_text = re.sub(r"【act_[a-f0-9]{32}】", "", full_text or "", flags=re.I).strip()
        full_text = re.sub(r"act_[a-f0-9]{32}", "", full_text, flags=re.I).strip()
        # LLM sometimes dumps a fake ACTION_CONFIRM JSON — never render it.
        if is_action_confirm_json(full_text) or is_action_confirm_json(assistant_cards):
            logger.warning("fabricated_action_confirm_json_dropped", user_id=user_id)
            full_text = "操作确认卡片无效，请重新说明退款/评价/收货需求后重试。"
            assistant_cards = None

        called = list(tools_called or [])
        wants_orders = "QUERY_ORDERS" in called or wants_order_list_cards(user_text)
        if any(t in _WRITE_PROPOSE_TOOLS for t in called):
            # PROPOSE ran but Redis token missing / invalid — do not fall into product UI.
            assistant = trim_assistant(full_text) or (
                "未能生成有效确认卡片。请确认订单号/订单项、评价星级与内容后重试。"
            )
            if is_action_confirm_json(assistant):
                assistant = "未能生成有效确认卡片。请确认订单号/订单项、评价星级与内容后重试。"
            biz_type = biz_type or "agent"
        elif wants_orders:
            # Always render order cards — never leave LLM markdown/HTML tables as the UI.
            cards = _recover_order_cards(assistant_cards, called)
            if is_order_cards_json(cards):
                assistant = cards or "[]"
                biz_type = "query_order"
            elif (cards or "").strip() == "[]":
                assistant = "[]"
                biz_type = "query_order"
            else:
                logger.warning("query_orders_without_cards", user_id=user_id)
                assistant = trim_assistant(full_text) or "未查询到相关订单。"
                assistant = re.sub(r"【act_[a-f0-9]{32}】", "", assistant, flags=re.I).strip()
                biz_type = biz_type or "query_order"
        elif any(t in called for t in _NON_PRODUCT_TOOLS) and "SEARCH_PRODUCTS" not in called:
            # Logistics / coupon / write tools: keep tool text, never rewrite as product search.
            assistant = trim_assistant(full_text) or ""
            assistant = re.sub(r"【act_[a-f0-9]{32}】", "", assistant, flags=re.I).strip()
            assistant = re.sub(r"【act_(?![a-f0-9]{32})[^】]*】", "", assistant, flags=re.I).strip()
            if is_action_confirm_json(assistant):
                assistant = "操作未能完成，请稍后重试或补充订单信息。"
            if not biz_type:
                if "QUERY_LOGISTICS" in called:
                    biz_type = "query_logistics"
                elif "QUERY_COMMENT" in called:
                    biz_type = "query_comment"
                elif "QUERY_USER_COUPONS" in called:
                    biz_type = "query_coupon"
                else:
                    biz_type = "agent"
        else:
            # Prefer Agent text. Only block product-search hijack for clear aftersales.
            if looks_like_aftersales_or_order_text(user_text) and "SEARCH_PRODUCTS" not in called:
                assistant = trim_assistant(full_text) or ""
                if is_action_confirm_json(assistant):
                    assistant = "请补充必要信息后重试，或说明你想办理的具体操作。"
                biz_type = biz_type or "agent"
            else:
                cards_json, forced_biz = await _resolve_product_cards_json(
                    assistant_cards, tool_biz, tools_called, search_tool_hint
                )
                if not cards_json and should_force_product_cards(
                    full_text,
                    None,
                    tool_biz,
                    consult_card,
                    assistant_cards,
                    is_consult_turn=is_consult_turn,
                    tools_called=called,
                ):
                    if "SEARCH_PRODUCTS" not in called:
                        keyword = (user_text or full_text or "").strip()[:120]
                        result = await mcp_tool_router.invoke(
                            "SEARCH_PRODUCTS",
                            {"keyword": keyword},
                            user_id,
                        )
                        called.append("SEARCH_PRODUCTS")
                        cards_json = result.assistant_cards
                        forced_biz = result.biz_type or "product_search"
                        search_tool_hint = result.to_tool_message()
                        biz_data = result.biz_data or biz_data
                        tool_biz_update = result.to_biz_dict()
                        if tool_biz_update:
                            tool_biz = {**tool_biz, **tool_biz_update}
                if cards_json and is_product_cards_json(cards_json):
                    intro = compact_product_search_intro(full_text, search_tool_hint)
                    if text_promises_product_cards(intro) and cards_json == "[]":
                        intro = "未找到相关商品，请换个关键词试试。"
                    assistant = build_product_search_message(intro, cards_json)
                    biz_type = biz_type or forced_biz
                elif assistant_cards and assistant_cards.strip().startswith("{"):
                    if is_action_confirm_json(assistant_cards):
                        assistant = "操作确认卡片无效，请重新发起。"
                        biz_type = biz_type or "agent"
                    else:
                        assistant = assistant_cards
                        biz_type = biz_type or "product_search"
                elif forced_biz == "product_search" and "QUERY_ORDERS" not in called:
                    intro = compact_product_search_intro(full_text, search_tool_hint)
                    if text_promises_product_cards(intro):
                        intro = "未找到相关商品，请换个关键词试试。"
                    assistant = build_product_search_message(intro or "未找到相关商品，请换个关键词试试。", "[]")
                    biz_type = biz_type or forced_biz
                else:
                    assistant = trim_assistant(full_text) or ""
                    assistant = re.sub(r"【act_[a-f0-9]{32}】", "", assistant, flags=re.I).strip()
                    assistant = re.sub(r"【act_(?![a-f0-9]{32})[^】]*】", "", assistant, flags=re.I).strip()
                    if is_action_confirm_json(assistant):
                        assistant = "操作确认卡片无效，请重新发起。"
                    biz_type = biz_type or "agent"

    assistant = _strip_emojis_from_assistant(assistant)

    await stream_service.push_done(
        user_id, message_id, assistant, biz_type, agent_msg.get("userMessage")
    )
    await agent_message_service.complete_message(
        message_id,
        assistant,
        biz_type,
        biz_data,
        source_refs,
    )

async def push_chat_error(agent_msg: dict, prompt_type: str, partial: str = "") -> None:
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    await stream_service.push_error(user_id, message_id, "服务暂时不可用，请稍后重试", prompt_type)
    await agent_message_service.complete_message(
        message_id, partial or "服务异常", prompt_type, None
    )

@lru_cache(maxsize=8)
def _agent_llm_with_tools(config: ChatLLMConfig):
    return chat_llm_for_config(config).bind_tools(build_mcp_tools())


def bind_agent_llm(*, fallback: bool = False):
    """The tool-bound LLM for one ReAct round.

    Both halves of this are pure functions of the settings: building the tool list
    and converting every args_schema to an OpenAI function schema costs the same
    work on round 1 and round 6. Keying on the resolved config means a settings
    change still produces a fresh binding.
    """
    return _agent_llm_with_tools(chat_llm_config(fallback=fallback))

def parse_agent_message(agent_msg: dict) -> tuple[dict | None, str]:
    return parse_consult_card(agent_msg.get("userMessage") or "")

async def resolve_consult_card(
    user_id: str,
    message_card: dict | None = None,
    memory_state: dict | None = None,
    from_product: bool | None = None,
) -> dict | None:
    from app.utils.product_consult import resolve_consult_card as _resolve

    return await _resolve(user_id, message_card, memory_state, from_product=from_product)
