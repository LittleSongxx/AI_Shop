from __future__ import annotations

import json
import re

import structlog
from app.services.llm_factory import create_chat_llm

from app.harness.guardrails.output_guard import strip_emojis
from app.harness.guardrails.product_text_guard import (
    build_consult_product_cards_json,
    collect_known_product_names,
    name_mentioned_in_text,
    should_force_product_cards,
    text_contains_product_info,
    text_promises_product_cards,
)
from app.harness.metrics.runtime_sensors import STREAM_TOKENS
from app.mcp.tools import build_mcp_tools
from app.services.message_service import agent_message_service
from app.services.pending_action_service import pending_action_service
from app.services.product_service import (
    derive_search_keyword,
    format_search_tool_message,
    product_service,
)
from app.services.redis_service import redis_service
from app.services.stream_service import stream_service
from app.utils.biz_payload import (
    build_action_confirm_payload,
    build_action_confirm_unavailable_payload,
    build_product_payload,
    build_product_search_message,
    collect_act_token_ids,
    compact_product_search_intro,
    is_order_cards_json,
    is_product_cards_json,
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

    assistant, biz_data = build_action_confirm_unavailable_payload(
        token_ids[0], full_text, reason="not_found"
    )
    return assistant, biz_data, "action_confirm"

async def stream_llm_turn(
    llm,
    messages: list,
    user_id: str,
    message_id: int,
    user_message: str | None,
    chunks: list[str],
):
    gathered = None
    sent_visible = ""
    async for chunk in llm.astream(messages):
        if await is_cancelled(user_id, message_id):
            return None
        gathered = chunk if gathered is None else gathered + chunk
        visible = strip_embedded_product_json(strip_emojis(chunk_text(gathered.content)))
        delta = visible[len(sent_visible) :]
        sent_visible = visible
        if not delta:
            continue
        chunks.append(delta)
        STREAM_TOKENS.inc(len(delta))
        await stream_service.push_chunk(user_id, message_id, delta, user_message)
    return gathered

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
    if search_tool_hint and "SEARCH_PRODUCTS" not in called:
        called.append("SEARCH_PRODUCTS")

    if assistant_cards and is_product_cards_json(assistant_cards):
        return assistant_cards, "product_search"

    ids = (tool_biz or {}).get("productIds") or []
    if ids:
        products = await product_service._load_products_by_ids([str(i) for i in ids])
        cards_json, _ = build_product_payload(products)
        if cards_json and cards_json != "[]":
            return cards_json, "product_search"

    if "SEARCH_PRODUCTS" in called:
        return None, "product_search"
    return None, None

async def _ensure_product_search_cards(
    user_id: str,
    user_text: str | None,
    full_text: str | None,
    cards_json: str | None,
    called: list[str],
    consult_card: dict | None,
    from_product: bool,
) -> tuple[str | None, str | None, str | None, str | None]:

    from app.domain.intent.rules import looks_like_new_product_search

    has_cards = bool(cards_json and cards_json != "[]" and is_product_cards_json(cards_json))
    if has_cards:
        return None, None, None, None

    should_search = False
    if looks_like_new_product_search(user_text or ""):
        should_search = True
    elif text_promises_product_cards(full_text) and "SEARCH_PRODUCTS" not in called:
        should_search = True
    elif "SEARCH_PRODUCTS" in called:
        should_search = True

    if not should_search:
        return None, None, None, None

    keyword = (user_text or "").strip() or (full_text or "").strip()[:40]
    consult = consult_card if from_product else None
    cards_json, biz_data, _, products, source = await product_service.search_products(
        user_id,
        keyword,
        user_text=user_text or "",
        consult_product=consult,
    )
    hint = format_search_tool_message(keyword or "", consult, products, source)
    return cards_json, biz_data, hint, "product_search"

async def _resolve_cards_when_text_mentions_products(
    user_id: str,
    user_text: str | None,
    full_text: str | None,
    tool_biz: dict | None,
    assistant_cards: str | None,
    consult_card: dict | None,
    *,
    is_consult_turn: bool = False,
) -> tuple[str | None, str | None, str | None]:
    if is_consult_turn or is_order_cards_json(assistant_cards):
        return None, None, None
    if assistant_cards and is_product_cards_json(assistant_cards):
        return assistant_cards, None, None

    text = full_text or ""
    consult = consult_card
    if consult and consult.get("productId"):
        name = consult.get("productName") or consult.get("product_name") or ""
        if name_mentioned_in_text(name, text):
            cards = build_consult_product_cards_json(consult)
            if cards:
                return cards, None, None

    lines = [ln.strip() for ln in re.split(r"[\n\r]+", text) if ln.strip()]
    price_lines = sum(1 for ln in lines if re.search(r"(?:¥|[￥])[\d,]+|\d[\d,]*(?:\.\d+)?\s*元", ln))
    listing = any(re.search(r".{2,80}[—\-–]\s*\d[\d,]*(?:\.\d+)?\s*元", ln) for ln in lines)
    if price_lines < 2 and not listing and consult and consult.get("productId"):
        cards = build_consult_product_cards_json(consult)
        if cards and text_contains_product_info(text, collect_known_product_names(tool_biz, consult, assistant_cards)):
            return cards, None, None

    if not consult:
        cached = await redis_service.get_consult_product(user_id)
        if cached and await redis_service.is_consult_active(user_id):
            consult = cached
    exclude = str(consult["productId"]) if consult and consult.get("productId") else None
    keyword = derive_search_keyword(user_text or text, consult)
    cards_json, biz_data, _, products, source = await product_service.search_products(
        user_id,
        keyword,
        user_text=user_text or "",
        consult_product=consult,
        exclude_product_id=exclude,
    )
    if not products:
        return None, None, None
    hint = format_search_tool_message(keyword or "", consult, products, source)
    return cards_json, hint, biz_data

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
        called = list(tools_called or [])
        if "QUERY_ORDERS" in called and is_order_cards_json(assistant_cards):
            assistant = assistant_cards or "[]"
            biz_type = biz_type or "query_order"
        elif "QUERY_ORDERS" in called and (assistant_cards or "").strip() == "[]":
            assistant = "[]"
            biz_type = biz_type or "query_order"
        else:
            cards_json, forced_biz = await _resolve_product_cards_json(
                assistant_cards, tool_biz, tools_called, search_tool_hint
            )
            backfill_cards, backfill_biz, backfill_hint, backfill_forced = await _ensure_product_search_cards(
                user_id,
                user_text,
                full_text,
                cards_json,
                called,
                consult_card,
                bool(agent_msg.get("fromProduct")),
            )
            if backfill_forced:
                cards_json = backfill_cards
                forced_biz = backfill_forced
                if backfill_hint:
                    search_tool_hint = backfill_hint
                if backfill_biz:
                    biz_data = backfill_biz
            if not cards_json and should_force_product_cards(
                full_text,
                None,
                tool_biz,
                consult_card,
                assistant_cards,
                is_consult_turn=is_consult_turn,
                tools_called=called,
            ):
                extra_cards, extra_hint, extra_biz = await _resolve_cards_when_text_mentions_products(
                    user_id,
                    user_text,
                    full_text,
                    tool_biz,
                    assistant_cards,
                    consult_card,
                    is_consult_turn=is_consult_turn,
                )
                if extra_cards:
                    cards_json = extra_cards
                    forced_biz = "product_search"
                    if extra_hint:
                        search_tool_hint = extra_hint
                    if extra_biz:
                        biz_data = extra_biz
            if cards_json and is_product_cards_json(cards_json):
                intro = compact_product_search_intro(full_text, search_tool_hint)
                if text_promises_product_cards(intro) and cards_json == "[]":
                    intro = "未找到相关商品，请换个关键词试试。"
                assistant = build_product_search_message(intro, cards_json)
                biz_type = biz_type or forced_biz
            elif assistant_cards and assistant_cards.strip().startswith("{"):
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
                biz_type = biz_type or "agent"
                if should_force_product_cards(
                    full_text,
                    assistant,
                    tool_biz,
                    consult_card,
                    assistant_cards,
                    is_consult_turn=is_consult_turn,
                    tools_called=called,
                ):
                    extra_cards, extra_hint, extra_biz = await _resolve_cards_when_text_mentions_products(
                        user_id,
                        user_text,
                        full_text,
                        tool_biz,
                        assistant_cards,
                        consult_card,
                        is_consult_turn=is_consult_turn,
                    )
                    if extra_cards:
                        intro = compact_product_search_intro(full_text, extra_hint or search_tool_hint)
                        assistant = build_product_search_message(intro, extra_cards)
                        biz_type = "product_search"
                        if extra_biz:
                            biz_data = extra_biz

    assistant = _strip_emojis_from_assistant(assistant)

    await stream_service.push_done(
        user_id, message_id, assistant, biz_type, agent_msg.get("userMessage")
    )
    await agent_message_service.complete_message(message_id, assistant, biz_type, biz_data)

async def push_chat_error(agent_msg: dict, prompt_type: str, partial: str = "") -> None:
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    await stream_service.push_error(user_id, message_id, "服务暂时不可用，请稍后重试", prompt_type)
    await agent_message_service.complete_message(
        message_id, partial or "服务异常", prompt_type, None
    )

def bind_agent_llm():
    return create_chat_llm().bind_tools(build_mcp_tools())

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
