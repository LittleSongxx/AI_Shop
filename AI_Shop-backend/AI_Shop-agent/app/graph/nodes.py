from __future__ import annotations

import time

import structlog
from langchain_core.messages import SystemMessage, ToolMessage

from app.config.settings import get_settings
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.rules import (
    looks_like_category_switch,
    looks_like_new_product_search,
    wants_order_list_cards,
)
from app.domain.intent.types import IntentDecision, IntentKind, NextAction
from app.domain.intent.write_args import TOOL_REQUIRED_INTENTS
from app.graph.forced_tools import (
    forced_order_list,
    forced_product_search,
    forced_tool_for_intent,
)
from app.graph.order_reference_flow import resolve_order_reference_turn
from app.graph.state import AgentGraphState
from app.harness.guardrails.output_guard import OutputGuardrail, strip_emojis
from app.harness.metrics.runtime_sensors import measure_agent_stage
from app.memory.context_builder import context_builder
from app.memory.post_turn import post_turn_service
from app.memory.session_memory_service import session_memory_service
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.observability.telemetry import get_tracer
from app.rag.ab_test import get_bucket
from app.rag.query_rewriter import rewrite_for_rag
from app.rag.retriever import rag_retriever
from app.services import agent_runtime as rt
from app.services.badcase_service import badcase_service
from app.services.episode_service import episode_service
from app.services.llm_factory import has_fallback_chat_llm
from app.services.mcp_tool_router import mcp_tool_router
from app.services.message_service import agent_message_service
from app.services.product_service import is_similar_or_recommend_request
from app.services.product_snapshot_service import product_snapshot_service
from app.services.redis_service import redis_service
from app.utils.biz_payload import is_order_cards_json, is_product_cards_json
from app.utils.order_ids import extract_order_id
from app.utils.product_consult import is_product_consult_turn
from app.utils.prompt_boundary import isolate_knowledge_text

logger = structlog.get_logger()

# A3：HANDOFF_SUGGESTED（REPEATED_INTENT / 低置信）时追加在回答末尾的
# 建议转人工文案。只是建议，不强制；用户可继续提问或直接说"转人工"。
_HANDOFF_SUGGEST_TEXT = "\n\n如仍未解决，可以回复“转人工”，由人工客服继续协助。"
output_guard = OutputGuardrail()

# Intent kinds for which the original intent is preserved even when a
# category-switch or new-product-search is detected.  Defined at module
# level so the frozenset is constructed once rather than on every call to
# build_context_node.
_KEEP_INTENT: frozenset = frozenset({
    IntentKind.QUERY_ORDER,
    IntentKind.QUERY_LOGISTICS,
    IntentKind.QUERY_FULFILLMENT,
    IntentKind.QUERY_COMMENT,
    IntentKind.QUERY_COUPON,
    IntentKind.PRODUCT_REVIEW,
    IntentKind.RECOMMENT,
    IntentKind.REFUND,
    IntentKind.CONFIRM_RECEIPT,
    IntentKind.CANCEL_ORDER,
})

# Policy and support intents need the same published-knowledge grounding as
# generic chat. Transactional order lookup, product search, and an explicit
# human request do not benefit from a speculative knowledge prefetch.
_RAG_PREFETCH_INTENTS = frozenset(
    {
        IntentKind.PRODUCT_CONSULT,
        IntentKind.REFUND,
        IntentKind.CANCEL_ORDER,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_COUPON,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.QUERY_COMMENT,
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.INVOICE,
        IntentKind.ADDRESS_CHANGE,
        IntentKind.REFUND_STATUS,
        IntentKind.AFTERSALES_UNKNOWN,
        IntentKind.CHAT,
    }
)


def should_prefetch_rag(intent: IntentKind, *, agentic_rag: bool) -> bool:
    return not agentic_rag and intent in _RAG_PREFETCH_INTENTS

async def entry_guard(state: AgentGraphState) -> dict:
    user_id = state["user_id"]
    message_id = state["message_id"]
    if await rt.is_cancelled(user_id, message_id):
        episode_service.record_step(
            "GUARD",
            node_name="entry",
            status="BLOCKED",
            output_data={"guard": "cancellation", "decision": "BLOCK"},
        )
        return {"cancelled": True, "finished": True, "route": "end", "outcome": "cancelled"}
    card, user_text = rt.parse_agent_message(state["agent_msg"])
    # P3-2: optional image URL forwarded by the frontend alongside the text message.
    image_url: str | None = state["agent_msg"].get("imageUrl") or None
    episode_service.record_step(
        "GUARD",
        node_name="entry",
        output_data={"guard": "cancellation", "decision": "PASS", "hasImage": bool(image_url)},
    )
    return {"card": card, "message_card": card, "user_text": user_text, "image_url": image_url, "cancelled": False}

async def build_context_node(state: AgentGraphState) -> dict:
    if state.get("cancelled"):
        return {"route": "end", "finished": True}

    user_id = state["user_id"]
    message_id = state["message_id"]
    user_text = state["user_text"]
    card = state.get("card")
    from_product = state.get("from_product", False)

    memory = await session_memory_service.load(user_id, redis_service.client)
    episode_service.record_step(
        "MEMORY_READ",
        node_name="build_context",
        output_data={
            "turnCount": memory.turn_count,
            "stateKeys": sorted(memory.state.keys()),
            "hasSummary": bool(memory.summary),
        },
    )
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

    raw_decision = state["agent_msg"].get("intentDecision")
    try:
        decision = IntentDecision.model_validate(raw_decision) if raw_decision else None
    except (TypeError, ValueError):
        decision = None
    if decision is None:
        # A2/A3：会话级意图延续与死循环检测的输入（send_message 已算过决策的
        # 情况下走不到这里；只有 agent_msg 没带 intentDecision 时才补查）。
        recent_intents = await agent_message_service.get_recent_intents(user_id)
        with measure_agent_stage("intent"):
            decision = await resolve_intent(
                user_id,
                user_text,
                from_product=from_product,
                consult_card=consult_card,
                message_card=card,
                unresolved_count=int(state["agent_msg"].get("unresolvedCount") or 0),
                session_intent=recent_intents[0] if recent_intents else None,
                recent_intents=recent_intents,
            )
    intent = decision.intent
    intent_source = decision.source
    intent_data = decision.data

    if (switching_away or category_switch_search) and intent not in _KEEP_INTENT:
        intent = IntentKind.PRODUCT_SEARCH
        intent_source = "category_switch"
        decision = decision.model_copy(
            update={
                "intent": intent,
                "source": intent_source,
                "next_action": NextAction.TOOL,
            }
        )
    # P2-3: stable A/B bucket for this user. Overrides (rag_top_k / rerank_top_n)
    # are applied inside rag_retriever (single source), which also folds the
    # bucket into the semantic cache key so strategies never share cached results.
    ab_bucket = get_bucket(user_id)
    episode_service.update_run(
        intent=intent.value,
        scenario=intent.value.lower(),
        experiment={"bucket": ab_bucket},
    )

    # P3-2: describe the user's image (if any) before the retrieval step so
    # the description enriches both the RAG query and the LLM context window.
    image_url = state.get("image_url")
    image_desc: str | None = None
    if image_url:
        from app.rag.image_describer import describe_image
        image_desc = await describe_image(image_url)
        if image_desc:
            logger.debug("user_image_described", user_id=user_id, length=len(image_desc))

    faq_text = ""
    knowledge_text = ""
    rag_source_refs: list[dict] = []
    rag_trace: dict | None = None
    # P3-1: when agentic_rag=True the LLM calls SEARCH_KNOWLEDGE itself;
    # skip the fixed prefetch so the context window stays clean.
    if should_prefetch_rag(intent, agentic_rag=get_settings().agentic_rag):
        rag_started = time.perf_counter()
        with get_tracer().start_as_current_span("agent.rag.retrieve") as span:
            span.set_attribute("agent.rag.mode", "prefetch")
            span.set_attribute("agent.rag.bucket", ab_bucket)
            with measure_agent_stage("rag"):
                rag_query = await rewrite_for_rag(user_text, memory)
                # P3-2: prepend image description to retrieval query when available.
                if image_desc:
                    rag_query = f"{image_desc} {rag_query}".strip()
                _cat_map = get_settings().rag_intent_category_map
                category_filter = (_cat_map.get(intent.value) or None) if _cat_map else None
                rag_result = await rag_retriever.search_faq_with_trace(
                    rag_query,
                    category_filter=category_filter,
                    bucket=ab_bucket,
                )
            faq_text = str(rag_result.get("text") or "")
            rag_source_refs = list(rag_result.get("source_refs") or [])
            rag_trace = rag_result.get("trace")
            knowledge_text = faq_text
            episode_service.record_step(
                "RAG_RETRIEVAL",
                node_name="build_context",
                input_data={"query": rag_query, "categoryFilter": category_filter},
                output_data={
                    "trace": rag_trace,
                    "sourceRefs": rag_source_refs,
                    "hasEvidence": bool(faq_text and rag_source_refs),
                },
                latency_ms=round((time.perf_counter() - rag_started) * 1_000),
            )

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
        ab_bucket=ab_bucket,
    )

    # P3-2: inject VLM image description so the LLM understands visual context.
    if image_desc:
        messages.append(
            SystemMessage(
                content=(
                    "## 用户发送了图片\n"
                    f"图片描述（内部参考，勿直接引用原文）：{image_desc}"
                )
            )
        )

    if card and card.get("productId") and snapshot and intent != IntentKind.PRODUCT_CONSULT:
        messages.append(
            SystemMessage(
                content=f"## 当前咨询商品详情\n{isolate_knowledge_text(snapshot)}"
            )
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
                    "若需要真实商品列表，请调用 SEARCH_PRODUCTS 后再回复；"
                    "不要编造商品名或价格；有结果时引导查看下方卡片。"
                )
            )
        )

    if category_switch_search:
        messages.append(
            SystemMessage(
                content=(
                    "【系统提示】用户可能已切换品类或发起新的商品搜索。"
                    "请按最新意图作答；需要商品列表时调用 SEARCH_PRODUCTS"
                    "（keyword 用品类/品牌/特征），不要强行围绕旧咨询商品拒绝切换。"
                )
            )
        )

    await redis_service.bind_message_id(user_id, message_id)
    if intent == IntentKind.QUERY_ORDER:
        messages.append(
            SystemMessage(
                content=(
                    "【系统提示】本轮更像查订单。"
                    "若要陈述用户订单事实，请先调用 QUERY_ORDERS；"
                    "政策/如何查看订单类问题可直接说明入口。"
                )
            )
        )
    elif intent == IntentKind.QUERY_LOGISTICS:
        messages.append(
            SystemMessage(
                content=(
                    "【系统提示】本轮更像查物流。"
                    "若要陈述物流轨迹，请先调用 QUERY_LOGISTICS；"
                    "缺订单号时先追问，不要编造轨迹。"
                )
            )
        )

    return {
        "llm_messages": messages,
        "working_turns": working_turns,
        "working_oldest_id": working_oldest_id,
        "card": consult_card or card,
        "message_card": card,
        "category_switch_search": category_switch_search,
        "intent": intent.value,
        "intent_data": intent_data or None,
        "intent_decision": decision.model_dump(mode="json"),
        "rag_source_refs": rag_source_refs,
        "rag_trace": rag_trace,
        "pending_order_reference": (
            state.get("selected_order_reference")
            or memory.state.get("pendingOrderReference")
        ),
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
        return {"cancelled": True, "finished": True, "route": "end", "outcome": "cancelled"}

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
    intent_name = state.get("intent")
    intent_data = state.get("intent_data")
    tool_required_first_turn = (
        bool(settings.force_mcp_on_llm_skip)
        and state.get("react_round", 0) == 0
        and intent_name in TOOL_REQUIRED_INTENTS
        and not state.get("search_fallback_done")
    )
    non_stream_turn = similar_first_turn or category_switch_first_turn or tool_required_first_turn
    try:
        if non_stream_turn:
            response = await invoke_llm_with_metrics(
                llm, messages, model=settings.llm_model
            )
        else:
            response = await rt.stream_llm_turn(
                llm,
                messages,
                user_id,
                message_id,
                agent_msg.get("userMessage"),
                turn_chunks,
                model=settings.llm_model,
            )
    except Exception as primary_error:
        response = None
        can_retry = not turn_chunks and has_fallback_chat_llm()
        # A4：失败的调用也计入 LLM_CALL_TOTAL（成功/失败都要可观测，
        # 只看成功数算不出失败率）。已部分流式输出的不算 fallback 机会，
        # 但那次调用本身已经失败，照记。
        if not non_stream_turn:
            rt.record_llm_failure(settings.llm_model, fallback=False)
        logger.warning(
            "llm_turn_failed",
            error=str(primary_error),
            error_type=type(primary_error).__name__,
            retry_fallback=can_retry,
        )
        if can_retry:
            try:
                fallback_llm = rt.bind_agent_llm(fallback=True)
                if non_stream_turn:
                    response = await invoke_llm_with_metrics(
                        fallback_llm,
                        messages,
                        fallback=True,
                        model=settings.llm_fallback_model,
                    )
                else:
                    response = await rt.stream_llm_turn(
                        fallback_llm,
                        messages,
                        user_id,
                        message_id,
                        agent_msg.get("userMessage"),
                        turn_chunks,
                        fallback=True,
                        model=settings.llm_fallback_model,
                    )
                logger.info(
                    "llm_fallback_succeeded",
                    fallback_model=settings.llm_fallback_model,
                )
            except Exception as fallback_error:
                if not non_stream_turn:
                    rt.record_llm_failure(
                        settings.llm_fallback_model, fallback=True
                    )
                logger.warning(
                    "llm_fallback_failed",
                    error=str(fallback_error),
                    error_type=type(fallback_error).__name__,
                )
        if response is None:
            partial = "".join((state.get("chunks") or []) + turn_chunks)
            await rt.push_chat_error(agent_msg, "agent", partial)
            await redis_service.clear_bound_message_id(user_id)
            return {"finished": True, "route": "end", "outcome": "llm_error"}

    if response is None:
        partial = "".join((state.get("chunks") or []) + turn_chunks)
        if partial:
            await agent_message_service.interrupt_message(user_id, message_id, partial, "agent")
        await redis_service.clear_bound_message_id(user_id)
        return {"cancelled": True, "finished": True, "route": "end", "outcome": "cancelled"}

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

    if (similar_first_turn or category_switch_first_turn) and not tool_calls:
        return await forced_product_search(
            messages=messages,
            user_id=user_id,
            keyword=user_text,
            llm_body=strip_emojis(rt.chunk_text(getattr(response, "content", "") or "")),
            # 找相似要排除当前咨询商品，切品类不排除。
            exclude_product_id=(consult or {}).get("productId") if similar_first_turn else None,
            log_event=(
                "search_fallback_after_llm_skip"
                if similar_first_turn
                else "category_switch_search_fallback"
            ),
        )

    if tool_required_first_turn and not tool_calls:
        forced = await forced_tool_for_intent(
            messages=messages,
            user_id=user_id,
            intent=intent_name,
            intent_data=intent_data,
            user_text=user_text,
        )
        if forced:
            return forced

    if (
        state.get("react_round", 0) == 0
        and not tool_calls
        and not state.get("search_fallback_done")
        and (
            intent_name == IntentKind.QUERY_ORDER.value
            or wants_order_list_cards(user_text)
        )
    ):
        return await forced_order_list(
            messages=messages,
            user_id=user_id,
            intent=intent_name,
            order_id=(intent_data or "").strip() or extract_order_id(user_text),
        )

    messages.append(response)
    if not turn_chunks:
        llm_body = strip_emojis(rt.chunk_text(getattr(response, "content", "") or ""))
        if llm_body:
            turn_chunks = [llm_body]
    return {
        "llm_messages": messages,
        "chunks": turn_chunks,
        "pending_tool_calls": [],
        "route": "finalize",
    }


async def order_reference_node(state: AgentGraphState) -> dict:
    if state.get("cancelled") or state.get("finished"):
        return {"route": "end"}
    return await resolve_order_reference_turn(state)

async def tools_node(state: AgentGraphState) -> dict:
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
            return {"cancelled": True, "finished": True, "route": "end", "outcome": "cancelled"}
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
        result = await mcp_tool_router.invoke(
            tc["name"], tc.get("args") or {}, user_id, call_id=tc.get("id")
        )
        called.append(tc["name"])
        messages.append(ToolMessage(content=result.to_tool_message(), tool_call_id=tc["id"]))

        biz_dict = result.to_biz_dict()
        if biz_dict:
            tool_biz.update(biz_dict)
        if result.assistant_cards:
            assistant_cards = result.assistant_cards
            biz_type = result.biz_type or biz_type
            biz_data = result.biz_data or biz_data
        if tc["name"] == "QUERY_ORDERS":
            biz_type = result.biz_type or biz_type or "query_order"
            if not result.assistant_cards:
                logger.warning("query_orders_missing_cards_in_tools_node", user_id=user_id)
        if tc["name"] == "SEARCH_PRODUCTS":
            search_tool_hint = result.to_tool_message()
        if tc["name"] == "GET_PRODUCT_DETAIL":
            product_id = (tc.get("args") or {}).get("productId") or (tc.get("args") or {}).get("product_id")
            if product_id:
                await product_snapshot_service.ensure_consult_snapshot(user_id, str(product_id))

    settings = get_settings()
    if is_order_cards_json(assistant_cards) and "QUERY_ORDERS" in called:
        logger.info("finalize_after_order_cards", user_id=user_id)
        return {
            "llm_messages": messages,
            "tools_called": called,
            "pending_tool_calls": [],
            "tool_biz": tool_biz or None,
            "biz_type": biz_type or "query_order",
            "biz_data": biz_data,
            "assistant_cards": assistant_cards,
            "search_tool_hint": search_tool_hint,
            "chunks": [],
            "route": "finalize",
        }
    if is_product_cards_json(assistant_cards) and "SEARCH_PRODUCTS" in called:
        return {
            "llm_messages": messages,
            "tools_called": called,
            "pending_tool_calls": [],
            "tool_biz": tool_biz or None,
            "biz_type": biz_type or "product_search",
            "biz_data": biz_data,
            "assistant_cards": assistant_cards,
            "search_tool_hint": search_tool_hint,
            "chunks": [],
            "route": "finalize",
        }

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

    try:
        if state.get("cancelled"):
            return {"finished": True, "route": "end", "outcome": "cancelled"}

        chunks = list(state.get("chunks") or [])
        messages = list(state.get("llm_messages") or [])
        full_text = "".join(chunks)
        tools_called = state.get("tools_called") or []
        guarded = output_guard.validate_no_false_completion(full_text, tools_called)
        if guarded != full_text:
            episode_service.record_step(
                "GUARD",
                node_name="finalize",
                status="REPAIRED",
                input_data={"assistantText": full_text},
                output_data={"assistantText": guarded, "rule": "false_completion"},
            )
            chunks = [guarded]
            full_text = guarded
            try:
                await badcase_service.add_candidate(
                    int(state["message_id"]),
                    "GUARD_BLOCK",
                    "输出 Guard 修复了不受支持的完成或能力声明",
                    run_id=agent_msg.get("runId"),
                    source="VERIFIER",
                    severity="HIGH",
                    snapshot={"rule": "false_completion"},
                )
            except Exception as exc:
                logger.warning(
                    "guard_badcase_capture_failed",
                    message_id=state["message_id"],
                    error=type(exc).__name__,
                )
        else:
            episode_service.record_step(
                "GUARD",
                node_name="finalize",
                output_data={"guard": "false_completion", "decision": "PASS"},
            )

        # A3：HANDOFF_SUGGESTED（REPEATED_INTENT / 低置信）此前只被计数、
        # 用户看不到任何提示。这里在回答末尾补一句可见的建议转人工文案——
        # 只是建议，不强制（强制转人工是 HANDOFF 的职责）。
        decision = agent_msg.get("intentDecision") or {}
        # 键名兼容：IntentDecision.model_dump 输出 snake_case 的 next_action
        # （死代码修复：旧写法只查 nextAction，全链路从不产生该键，A3 文案
        # 从未生效——P1 审查）。
        if (
            decision.get("next_action") == "HANDOFF_SUGGESTED"
            or decision.get("nextAction") == "HANDOFF_SUGGESTED"
        ):
            chunks = list(chunks) + [_HANDOFF_SUGGEST_TEXT]
            full_text = "".join(chunks)

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
            source_refs=(
                {
                    "trace": state.get("rag_trace"),
                    "sources": state.get("rag_source_refs") or [],
                }
                if state.get("rag_trace")
                else state.get("rag_source_refs")
            ),
            user_text=state.get("user_text"),
            consult_card=state.get("card"),
            message_card=state.get("message_card"),
            order_resolution=state.get("order_resolution"),
        )
    except Exception as e:
        logger.exception("graph_finalize_failed", error=str(e))
        await rt.push_chat_error(agent_msg, "agent", "".join(state.get("chunks") or []))
        return {"finished": True, "route": "end", "outcome": "graph_error"}
    finally:
        await redis_service.clear_bound_message_id(user_id)

    return {"finished": True, "route": "post_turn", "outcome": "ok"}

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
        episode_service.record_step(
            "MEMORY_WRITE",
            node_name="post_turn",
            output_data={
                "toolsCalled": state.get("tools_called") or [],
                "hasCard": bool(card),
            },
        )
    except Exception as e:
        logger.exception("post_turn_failed", user_id=user_id, error=str(e))

    return {"finished": True}

async def cleanup_node(state: AgentGraphState) -> dict:
    return {"finished": True}
