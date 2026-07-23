from __future__ import annotations

import re

import structlog
from langchain_core.messages import SystemMessage, ToolMessage

from app.config.settings import get_settings
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.rules import looks_like_category_switch, looks_like_new_product_search
from app.domain.intent.types import IntentKind
from app.graph.state import AgentGraphState
from app.harness.guardrails.output_guard import OutputGuardrail, strip_emojis
from app.memory.context_builder import context_builder
from app.memory.post_turn import post_turn_service
from app.memory.session_memory_service import session_memory_service
from app.rag.retriever import rag_retriever
from app.services import agent_runtime as rt
from app.services.mcp_tool_router import mcp_tool_router
from app.services.message_service import agent_message_service
from app.services.product_service import is_similar_or_recommend_request
from app.services.product_snapshot_service import product_snapshot_service
from app.services.redis_service import redis_service
from app.utils.biz_payload import is_order_cards_json, is_product_cards_json
from app.utils.order_ids import extract_order_id, extract_order_item_id, extract_refund_target_id
from app.utils.product_consult import is_product_consult_turn

logger = structlog.get_logger()
output_guard = OutputGuardrail()

_ORDER_LIST_UI_HINTS = (
    "我的订单",
    "最近订单",
    "最近的订单",
    "查订单",
    "订单列表",
    "买了什么",
    "买过什么",
    "最近买了",
    "最近买的",
    "最近购买",
    "再买一次",
    "复购",
    "上次买",
)


def _wants_order_list_cards(user_text: str | None) -> bool:
    """UI contract: these asks must render order cards, not markdown tables."""
    t = (user_text or "").strip()
    if not t:
        return False
    return any(k in t for k in _ORDER_LIST_UI_HINTS)

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

    _keep_intent = {
        IntentKind.QUERY_ORDER,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_COMMENT,
        IntentKind.QUERY_COUPON,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.REFUND,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.CANCEL_ORDER,
    }
    if (switching_away or category_switch_search) and intent not in _keep_intent:
        intent = IntentKind.PRODUCT_SEARCH
        intent_source = "category_switch"
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
        "react_round": 0,
        "pending_tool_calls": [],
        "route": "agent_loop",
    }


_STAR_RE = re.compile(
    r"(?:评[价分]|打)\s*([1-5])\s*星|([1-5])\s*星|星级\s*[：:]*\s*([1-5])|给.{0,8}([1-5])\s*分",
    re.I,
)
_POSITIVE_STAR_HINTS = ("好评", "很好", "不错", "可以", "满意", "推荐", "赞", "棒", "给力", "喜欢")
_NEGATIVE_STAR_HINTS = ("差评", "很差", "太差", "失望", "糟糕", "垃圾", "坑")
_NEUTRAL_STAR_HINTS = ("一般", "还行", "凑合", "普通")

_TOOL_REQUIRED_INTENTS = frozenset(
    {
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.QUERY_COUPON.value,
        IntentKind.REFUND.value,
        IntentKind.CONFIRM_RECEIPT.value,
        IntentKind.PRODUCT_REVIEW.value,
        IntentKind.RECOMMENT.value,
    }
)


def _extract_order_id(*texts: str | None) -> str | None:
    return extract_order_id(*texts)


def _extract_review_star(text: str) -> int | None:
    t = text or ""
    m = _STAR_RE.search(t)
    if m:
        for g in m.groups():
            if g:
                return int(g)
    if any(k in t for k in _NEGATIVE_STAR_HINTS):
        return 1
    if any(k in t for k in _NEUTRAL_STAR_HINTS):
        return 3
    if any(k in t for k in _POSITIVE_STAR_HINTS):
        return 5
    if "评价" in t or "打分" in t or "评星" in t:
        return 5
    return None


def _extract_review_content(text: str, order_id: str | None) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    cleaned = raw
    if order_id:
        cleaned = cleaned.replace(order_id, " ")
    cleaned = _STAR_RE.sub(" ", cleaned)

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
    if len(cleaned) >= 1:
        return cleaned[:200]
    if sentiment:
        return sentiment
    return None


def _missing_write_args_prompt(intent: str | None, intent_data: str | None, user_text: str) -> str:
    oid = (intent_data or "").strip() or _extract_order_id(user_text) or ""
    if intent == IntentKind.PRODUCT_REVIEW.value:
        if not oid:
            return "请提供要评价的订单号，并说明星级（1-5）和评价内容，例如：订单号xxx 5星 物流很快。"
        return (
            f"已识别订单 {oid}。请补充评价星级（1-5星）和评价内容，"
            "例如：「5星 包装完好物流很快」，我再为您生成确认卡片。"
        )
    if intent == IntentKind.RECOMMENT.value:
        if not oid:
            return "请提供要追评的订单号和追评内容。"
        return f"已识别订单 {oid}。请补充追评内容，我再为您生成确认卡片。"
    if intent == IntentKind.REFUND.value:
        return "请提供要退款的订单号或订单项ID，我再为您生成退款确认卡片。"
    if intent == IntentKind.CONFIRM_RECEIPT.value:
        return "请提供要确认收货的订单号。"
    return "请补充订单相关信息后重试。"


async def _required_tool_for_intent(
    intent: str | None,
    intent_data: str | None,
    user_text: str,
    user_id: str,
) -> tuple[str, dict] | None:
    """Return (tool_name, args) that must run for this intent, or None."""
    if intent == IntentKind.QUERY_ORDER.value:
        args: dict = {}
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        if oid:
            args["orderId"] = oid
        return "QUERY_ORDERS", args
    if intent == IntentKind.QUERY_LOGISTICS.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        if not oid:
            return None
        return "QUERY_LOGISTICS", {"orderId": oid}
    if intent == IntentKind.QUERY_COMMENT.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        if not oid:
            return None
        return "QUERY_COMMENT", {"orderId": oid}
    if intent == IntentKind.QUERY_COUPON.value:
        return "QUERY_USER_COUPONS", {}
    if intent == IntentKind.CANCEL_ORDER.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        if not oid:
            return None
        return "QUERY_ORDERS", {"orderId": oid}
    if intent == IntentKind.CONFIRM_RECEIPT.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        if not oid:
            return None
        return "PROPOSE_CONFIRM_RECEIPT", {"orderId": oid}
    if intent == IntentKind.REFUND.value:
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
        if len(refundable) > 1:
            return "QUERY_ORDERS", {"orderId": order_id}
        return "PROPOSE_REFUND", {"orderItemId": raw_id}
    if intent == IntentKind.PRODUCT_REVIEW.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        star = _extract_review_star(user_text)
        content = _extract_review_content(user_text, oid)
        if not oid or star is None or not content:
            return None
        return "PROPOSE_PRODUCT_REVIEW", {
            "orderId": oid,
            "commentContent": content,
            "star": star,
        }
    if intent == IntentKind.RECOMMENT.value:
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        content = _extract_review_content(user_text, oid)
        if not oid or not content:
            return None
        return "PROPOSE_RECOMMENT", {"orderId": oid, "reCommentContent": content}
    return None


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
    intent_name = state.get("intent")
    intent_data = state.get("intent_data")
    tool_required_first_turn = (
        bool(settings.force_mcp_on_llm_skip)
        and state.get("react_round", 0) == 0
        and intent_name in _TOOL_REQUIRED_INTENTS
        and not state.get("search_fallback_done")
    )
    try:
        if similar_first_turn or category_switch_first_turn or tool_required_first_turn:
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

    if tool_required_first_turn and not tool_calls:
        forced = await _required_tool_for_intent(intent_name, intent_data, user_text, user_id)
        if forced:
            tool_name, tool_args = forced
            result = await mcp_tool_router.invoke(tool_name, tool_args, user_id)
            biz_dict = result.to_biz_dict() or {}
            tool_text = result.to_tool_message() or ""
            messages.append(
                ToolMessage(content=tool_text or "未查询到相关记录。", tool_call_id="forced_mcp")
            )
            logger.warning(
                "forced_mcp_after_llm_skip",
                user_id=user_id,
                intent=intent_name,
                tool=tool_name,
                has_cards=bool(result.assistant_cards),
                has_act_token="act_" in tool_text.lower(),
            )
            if result.assistant_cards and result.assistant_cards.strip() not in ("", "[]"):
                chunks_out: list[str] = []
            else:
                chunks_out = [tool_text or "未查询到相关记录。"]
            if intent_name == IntentKind.CANCEL_ORDER.value:
                guide = (
                    "客服侧暂不支持直接取消订单，请到「我的订单」页面自行取消。"
                )
                if chunks_out:
                    chunks_out = [guide + "\n" + chunks_out[0]]
                else:
                    chunks_out = [guide]
            biz_type = result.biz_type
            if not biz_type:
                if tool_name == "QUERY_ORDERS":
                    biz_type = "query_order"
                elif tool_name == "QUERY_LOGISTICS":
                    biz_type = "query_logistics"
                elif tool_name == "QUERY_COMMENT":
                    biz_type = "query_comment"
                elif tool_name == "QUERY_USER_COUPONS":
                    biz_type = "query_coupon"
                elif tool_name.startswith("PROPOSE_"):
                    biz_type = "action_confirm"
            return {
                "llm_messages": messages,
                "tools_called": [tool_name],
                "tool_biz": biz_dict or None,
                "biz_type": biz_type,
                "biz_data": result.biz_data,
                "assistant_cards": result.assistant_cards,
                "search_tool_hint": result.to_tool_message() if tool_name == "SEARCH_PRODUCTS" else None,
                "search_fallback_done": True,
                "chunks": chunks_out,
                "pending_tool_calls": [],
                "route": "finalize",
            }

    if (
        state.get("react_round", 0) == 0
        and not tool_calls
        and not state.get("search_fallback_done")
        and (
            intent_name == IntentKind.QUERY_ORDER.value
            or _wants_order_list_cards(user_text)
        )
    ):
        oid = (intent_data or "").strip() or _extract_order_id(user_text)
        tool_args = {"orderId": oid} if oid else {}
        result = await mcp_tool_router.invoke("QUERY_ORDERS", tool_args, user_id)
        biz_dict = result.to_biz_dict() or {}
        tool_text = result.to_tool_message() or ""
        messages.append(
            ToolMessage(content=tool_text or "未查询到相关订单。", tool_call_id="forced_orders_ui")
        )
        logger.warning(
            "forced_query_orders_for_cards",
            user_id=user_id,
            intent=intent_name,
            has_cards=bool(result.assistant_cards),
        )
        return {
            "llm_messages": messages,
            "tools_called": ["QUERY_ORDERS"],
            "tool_biz": biz_dict or None,
            "biz_type": result.biz_type or "query_order",
            "biz_data": result.biz_data,
            "assistant_cards": result.assistant_cards,
            "search_tool_hint": None,
            "search_fallback_done": True,
            "chunks": [],
            "pending_tool_calls": [],
            "route": "finalize",
        }

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
