from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, ToolMessage

from app.config.settings import get_settings
from app.graph.state import AgentGraphState
from app.harness.guardrails.output_guard import OutputGuardrail, strip_emojis
from app.memory.context_builder import context_builder
from app.memory.post_turn import post_turn_service
from app.memory.session_memory_service import session_memory_service
from app.services import agent_runtime as rt
from app.services.message_service import agent_message_service
from app.services.mcp_tool_router import mcp_tool_router
from app.services.product_service import is_similar_or_recommend_request
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.types import IntentKind
from app.domain.intent.rules import looks_like_category_switch, looks_like_new_product_search
from app.rag.retriever import rag_retriever
from app.utils.product_consult import is_product_consult_turn
from app.services.product_snapshot_service import product_snapshot_service
from app.services.redis_service import redis_service

logger = structlog.get_logger()
output_guard = OutputGuardrail()

async def entry_guard(state: AgentGraphState) -> dict:
    user_id = state["user_id"]
    message_id = state["message_id"]
    if await rt.is_cancelled(user_id, message_id):
        return {"cancelled": True, "finished": True, "route": "end"}
    card, user_text = rt.parse_agent_message(state["agent_msg"])
    return {"card": card, "message_card": card, "user_text": user_text, "cancelled": False}

async def build_context_node(state: AgentGraphState) -> dict:
    if state.get("cancelled"):
        return {"route": "end", "finished": True}

    user_id = state["user_id"]
    message_id = state["message_id"]
    user_text = state["user_text"]
    card = state.get("card")
    from_product = state.get("from_product", False)

    memory = await session_memory_service.load(user_id, redis_service.client)
    consult_card = await rt.resolve_consult_card(
        user_id, card, memory.state, from_product=from_product
    )

    consult_name = (consult_card or {}).get("productName") or (consult_card or {}).get("product_name")
    switching_away = (
        consult_card
        and not (card and card.get("productId"))
        and (
            looks_like_category_switch(user_text, consult_name)
            or looks_like_new_product_search(user_text)
        )
        and not is_similar_or_recommend_request(user_text)
    )
    category_switch_search = switching_away or (
        looks_like_new_product_search(user_text)
        and not is_product_consult_turn(
            user_text, card, consult_card, from_product=from_product
        )
    )
    if switching_away:
        await redis_service.clear_consult(user_id)
        memory.state.pop("consultProduct", None)
        consult_card = None

    snapshot = None
    if consult_card and consult_card.get("productId"):
        snapshot = await product_snapshot_service.resolve_active_snapshot(user_id, consult_card)

    if card and card.get("productId"):
        memory.state["consultProduct"] = {
            "productId": str(card["productId"]),
            "productName": card.get("productName"),
            "minPrice": card.get("minPrice"),
            "cover": card.get("cover"),
            "categoryId": card.get("categoryId"),
        }

    intent, intent_source, intent_data = await resolve_intent(
        user_id,
        user_text,
        from_product=from_product,
        consult_card=consult_card,
        message_card=card,
    )
    if switching_away or category_switch_search:
        intent = IntentKind.PRODUCT_SEARCH

    faq_text = ""
    knowledge_text = ""
    if intent in (IntentKind.PRODUCT_CONSULT, IntentKind.CHAT):
        faq_text = await rag_retriever.search_faq(user_text)
    if intent == IntentKind.CHAT:
        knowledge_text = faq_text

    messages, working_turns, working_oldest_id = await context_builder.build_agent_messages(
        user_id,
        user_text,
        memory,
        intent=intent,
        product_snapshot=snapshot,
        faq_text=faq_text,
        knowledge_text=knowledge_text,
    )

    logger.info(
        "agent_intent_resolved",
        user_id=user_id,
        message_id=message_id,
        intent=intent.value,
        source=intent_source,
        intent_data=intent_data or None,
    )

    if card and card.get("productId") and snapshot and intent != IntentKind.PRODUCT_CONSULT:
        messages.append(
            SystemMessage(content=f"## 当前咨询商品详情\n{snapshot}")
        )

    if (
        consult_card
        and consult_card.get("productId")
        and is_similar_or_recommend_request(user_text)
        and not is_product_consult_turn(
            user_text, card, consult_card, from_product=from_product
        )
    ):
        messages.append(
            SystemMessage(
                content=(
                    "【系统提示】用户可能在找类似/推荐商品。"
                    "请先调用 SEARCH_PRODUCTS 获取结果后再回复；"
                    "禁止自行编造商品名或价格，回复时引导查看下方卡片。"
                )
            )
        )

    if category_switch_search:
        messages.append(
            SystemMessage(
                content=(
                    "【系统提示】用户已切换品类或发起新的商品搜索。"
                    "请调用 SEARCH_PRODUCTS（keyword 用用户意图中的品类/品牌/特征），"
                    "禁止再围绕旧咨询商品作答，禁止拒绝搜索。"
                )
            )
        )

    await redis_service.bind_message_id(user_id, message_id)
    return {
        "llm_messages": messages,
        "working_turns": working_turns,
        "working_oldest_id": working_oldest_id,
        "card": consult_card or card,
        "message_card": card,
        "category_switch_search": category_switch_search,
        "react_round": 0,
        "pending_tool_calls": [],
        "route": "agent_loop",
    }

async def agent_loop_node(state: AgentGraphState) -> dict:
    if state.get("cancelled") or state.get("finished"):
        return {"route": "end"}

    agent_msg = state["agent_msg"]
    user_id = state["user_id"]
    message_id = state["message_id"]
    messages = list(state.get("llm_messages") or [])
    turn_chunks: list[str] = []

    settings = get_settings()
    if state.get("react_round", 0) >= settings.graph_max_react_rounds:
        return {"route": "finalize"}

    if await rt.is_cancelled(user_id, message_id):
        partial = "".join(state.get("chunks") or [])
        if partial:
            await agent_message_service.interrupt_message(user_id, message_id, partial, "agent")
        await redis_service.clear_bound_message_id(user_id)
        return {"cancelled": True, "finished": True, "route": "end"}

    llm = rt.bind_agent_llm()
    consult = state.get("card")
    user_text = state.get("user_text") or ""
    from_product = state.get("from_product", False)
    tools_called = state.get("tools_called") or []
    similar_first_turn = (
        state.get("react_round", 0) == 0
        and not state.get("search_fallback_done")
        and is_similar_or_recommend_request(user_text)
        and not is_product_consult_turn(
            user_text, state.get("message_card"), consult, from_product=from_product
        )
        and consult
        and consult.get("productId")
        and "SEARCH_PRODUCTS" not in tools_called
    )
    category_switch_first_turn = (
        state.get("react_round", 0) == 0
        and not state.get("search_fallback_done")
        and state.get("category_switch_search")
        and not is_product_consult_turn(
            user_text, state.get("message_card"), consult, from_product=from_product
        )
        and "SEARCH_PRODUCTS" not in tools_called
    )
    try:
        if similar_first_turn or category_switch_first_turn:
            response = await llm.ainvoke(messages)
        else:
            response = await rt.stream_llm_turn(
                llm,
                messages,
                user_id,
                message_id,
                agent_msg.get("userMessage"),
                turn_chunks,
            )
    except Exception as e:
        logger.warning("llm_turn_failed", error=str(e), error_type=type(e).__name__)
        await rt.push_chat_error(agent_msg, "agent", "".join(state.get("chunks") or []))
        await redis_service.clear_bound_message_id(user_id)
        return {"finished": True, "route": "end"}

    if response is None:
        partial = "".join((state.get("chunks") or []) + turn_chunks)
        if partial:
            await agent_message_service.interrupt_message(user_id, message_id, partial, "agent")
        await redis_service.clear_bound_message_id(user_id)
        return {"cancelled": True, "finished": True, "route": "end"}

    tool_calls = getattr(response, "tool_calls", None) or []
    if tool_calls:
        pending = [
            {"id": tc["id"], "name": tc["name"], "args": tc.get("args") or {}}
            for tc in tool_calls
        ]
        messages.append(response)
        return {
            "llm_messages": messages,
            "pending_tool_calls": pending,
            "react_round": state.get("react_round", 0) + 1,
            "route": "tools",
        }

    if similar_first_turn and not tool_calls:
        llm_body = strip_emojis(rt.chunk_text(getattr(response, "content", "") or ""))
        fallback_chunks = [llm_body] if llm_body else []
        search_args: dict = {"keyword": user_text, "excludeProductId": str(consult["productId"])}
        result = await mcp_tool_router.invoke("SEARCH_PRODUCTS", search_args, user_id)
        biz_dict = result.to_biz_dict() or {}
        logger.info(
            "search_fallback_after_llm_skip",
            user_id=user_id,
            product_id=consult.get("productId"),
            has_cards=bool(result.assistant_cards),
        )
        return {
            "llm_messages": messages,
            "tools_called": ["SEARCH_PRODUCTS"],
            "tool_biz": biz_dict or None,
            "biz_type": result.biz_type,
            "biz_data": result.biz_data,
            "assistant_cards": result.assistant_cards,
            "search_tool_hint": result.to_tool_message(),
            "search_fallback_done": True,
            "chunks": fallback_chunks,
            "pending_tool_calls": [],
            "route": "finalize",
        }

    if category_switch_first_turn and not tool_calls:
        llm_body = strip_emojis(rt.chunk_text(getattr(response, "content", "") or ""))
        fallback_chunks = [llm_body] if llm_body else []
        search_args = {"keyword": user_text}
        result = await mcp_tool_router.invoke("SEARCH_PRODUCTS", search_args, user_id)
        biz_dict = result.to_biz_dict() or {}
        logger.info(
            "category_switch_search_fallback",
            user_id=user_id,
            keyword=user_text,
            has_cards=bool(result.assistant_cards),
        )
        return {
            "llm_messages": messages,
            "tools_called": ["SEARCH_PRODUCTS"],
            "tool_biz": biz_dict or None,
            "biz_type": result.biz_type,
            "biz_data": result.biz_data,
            "assistant_cards": result.assistant_cards,
            "search_tool_hint": result.to_tool_message(),
            "search_fallback_done": True,
            "chunks": fallback_chunks,
            "pending_tool_calls": [],
            "route": "finalize",
        }

    messages.append(response)
    return {
        "llm_messages": messages,
        "chunks": turn_chunks,
        "pending_tool_calls": [],
        "route": "finalize",
    }

async def tools_node(state: AgentGraphState) -> dict:
    agent_msg = state["agent_msg"]
    user_id = state["user_id"]
    message_id = state["message_id"]
    messages = list(state.get("llm_messages") or [])
    called: list[str] = []
    tool_biz = dict(state.get("tool_biz") or {})
    biz_type = state.get("biz_type")
    biz_data = state.get("biz_data")
    assistant_cards = state.get("assistant_cards")
    search_tool_hint = state.get("search_tool_hint")

    for tc in state.get("pending_tool_calls") or []:
        if await rt.is_cancelled(user_id, message_id):
            return {"cancelled": True, "finished": True, "route": "end"}
        if tc["name"] == "SEARCH_PRODUCTS" and is_product_consult_turn(
            state.get("user_text"),
            state.get("message_card"),
            state.get("card"),
            from_product=state.get("from_product", False),
        ):
            messages.append(
                ToolMessage(
                    content="【系统提示】当前为商品咨询，请勿搜索其他商品；围绕当前咨询商品作答。",
                    tool_call_id=tc["id"],
                )
            )
            continue
        result = await mcp_tool_router.invoke(tc["name"], tc.get("args") or {}, user_id)
        called.append(tc["name"])
        messages.append(ToolMessage(content=result.to_tool_message(), tool_call_id=tc["id"]))

        biz_dict = result.to_biz_dict()
        if biz_dict:
            tool_biz.update(biz_dict)
        if result.assistant_cards:
            assistant_cards = result.assistant_cards
            biz_type = result.biz_type or biz_type
            biz_data = result.biz_data or biz_data
        if tc["name"] == "SEARCH_PRODUCTS":
            search_tool_hint = result.to_tool_message()
        if tc["name"] == "GET_PRODUCT_DETAIL":
            product_id = (tc.get("args") or {}).get("productId") or (tc.get("args") or {}).get("product_id")
            if product_id:
                await product_snapshot_service.ensure_consult_snapshot(user_id, str(product_id))

    settings = get_settings()
    next_route = "agent_loop" if state.get("react_round", 0) < settings.graph_max_react_rounds else "finalize"
    return {
        "llm_messages": messages,
        "tools_called": called,
        "pending_tool_calls": [],
        "tool_biz": tool_biz or None,
        "biz_type": biz_type,
        "biz_data": biz_data,
        "assistant_cards": assistant_cards,
        "search_tool_hint": search_tool_hint,
        "route": next_route,
    }

async def finalize_node(state: AgentGraphState) -> dict:
    agent_msg = state["agent_msg"]
    user_id = state["user_id"]
    message_id = state["message_id"]

    try:
        if state.get("cancelled"):
            return {"finished": True, "route": "end"}

        chunks = list(state.get("chunks") or [])
        messages = list(state.get("llm_messages") or [])
        full_text = "".join(chunks)
        tools_called = state.get("tools_called") or []
        guarded = output_guard.validate_no_false_completion(full_text, tools_called)
        if guarded != full_text:
            chunks = [guarded]
            full_text = guarded

        await rt.finalize_agent_response(
            agent_msg,
            chunks,
            messages,
            biz_type=state.get("biz_type"),
            biz_data=state.get("biz_data"),
            assistant_cards=state.get("assistant_cards"),
            tools_called=tools_called,
            tool_biz=state.get("tool_biz"),
            search_tool_hint=state.get("search_tool_hint"),
            user_text=state.get("user_text"),
            consult_card=state.get("card"),
            message_card=state.get("message_card"),
        )
    except Exception as e:
        logger.exception("graph_finalize_failed", error=str(e))
        await rt.push_chat_error(agent_msg, "agent", "".join(state.get("chunks") or []))
    finally:
        await redis_service.clear_bound_message_id(user_id)

    return {"finished": True, "route": "post_turn"}

async def post_turn_node(state: AgentGraphState) -> dict:
    if state.get("cancelled"):
        return {"finished": True}

    user_id = state["user_id"]
    message_id = state["message_id"]
    user_text = state["user_text"]
    card = state.get("card")
    assistant_text = "".join(state.get("chunks") or []) or (state.get("assistant_cards") or "")

    try:
        await post_turn_service.run(
            user_id=user_id,
            message_id=message_id,
            user_text=user_text,
            assistant_text=assistant_text,
            tools_called=state.get("tools_called") or [],
            tool_biz=state.get("tool_biz"),
            card=card,
            working_turns=state.get("working_turns") or [],
            working_oldest_id=state.get("working_oldest_id"),
        )
    except Exception as e:
        logger.exception("post_turn_failed", user_id=user_id, error=str(e))

    return {"finished": True}

async def cleanup_node(state: AgentGraphState) -> dict:
    return {"finished": True}
