from __future__ import annotations

import json
from datetime import datetime, timedelta

from langchain_core.messages import ToolMessage

from app.constants import (
    ORDER_STATUS_PAID,
    ORDER_STATUS_SHIPPED,
    ORDER_STATUS_WAIT_PAYMENT,
)
from app.domain.intent.types import IntentKind
from app.domain.intent.write_args import extract_review_content, extract_review_star
from app.graph.state import AgentGraphState
from app.memory.session_memory_service import session_memory_service
from app.services.mcp_tool_router import mcp_tool_router
from app.services.order_reference_resolver import (
    ORDER_REFERENCE_INTENTS,
    OrderReferenceOutcome,
    order_reference_resolver,
)
from app.services.order_selection_store import order_selection_store
from app.services.product_search_query import topic_terms_for_text
from app.services.redis_service import redis_service
from app.utils.order_ids import extract_order_id


async def resolve_order_reference_turn(state: AgentGraphState) -> dict:
    intent = str(state.get("intent") or "")
    user_text = str(state.get("user_text") or "")
    if intent not in ORDER_REFERENCE_INTENTS:
        return {"route": "agent_loop"}
    if intent == IntentKind.QUERY_ORDER.value and not _has_specific_order_clue(user_text):
        return {"route": "agent_loop"}

    decision = state.get("intent_decision") or {}
    resolution = await order_reference_resolver.resolve(
        user_id=state["user_id"],
        intent=intent,
        user_text=user_text,
        entities=decision.get("entities") or {},
        consult_card=state.get("card"),
        pending_reference=state.get("pending_order_reference"),
    )
    base = {"order_resolution": resolution.outcome.value}

    if resolution.outcome == OrderReferenceOutcome.DEPENDENCY_ERROR:
        return {**base, "chunks": [resolution.reason], "biz_type": "agent", "route": "finalize"}
    if resolution.outcome == OrderReferenceOutcome.NO_ELIGIBLE:
        await _remember_reference(state, intent, resolution.candidates[0] if resolution.candidates else None)
        return {**base, "chunks": [resolution.reason], "biz_type": "agent", "route": "finalize"}
    if resolution.outcome in {OrderReferenceOutcome.AMBIGUOUS, OrderReferenceOutcome.NO_MATCH}:
        if resolution.candidates:
            card = await _selection_card(state, intent, resolution.reason, resolution.candidates)
            return {
                **base,
                "assistant_cards": json.dumps(card, ensure_ascii=False),
                "biz_type": "order_selection",
                "chunks": [],
                "route": "finalize",
            }
        return {**base, "chunks": [resolution.reason], "biz_type": "agent", "route": "finalize"}
    if resolution.outcome != OrderReferenceOutcome.RESOLVED or not resolution.target:
        return {
            **base,
            "chunks": ["订单候选已失效，请重新描述商品、购买时间或订单号。"],
            "biz_type": "agent",
            "route": "finalize",
        }

    target = resolution.target
    direct = await _direct_response(state, intent, target)
    if direct is not None:
        return {**base, **direct}

    tool = _tool_for_target(intent, user_text, target, state)
    if tool is None:
        await _remember_reference(state, intent, target)
        return {
            **base,
            "chunks": [_missing_args_prompt(intent, target)],
            "biz_type": "agent",
            "route": "finalize",
        }

    tool_name, args = tool
    result = await mcp_tool_router.invoke(tool_name, args, state["user_id"])
    messages = list(state.get("llm_messages") or [])
    messages.append(
        ToolMessage(
            content=result.to_tool_message() or "未查询到相关记录。",
            tool_call_id="resolved_order_reference",
        )
    )
    if not result.success:
        return {
            **base,
            "llm_messages": messages,
            "tools_called": [tool_name],
            "chunks": [result.content or "业务查询失败，请稍后重试。"],
            "biz_type": result.biz_type or "agent",
            "route": "finalize",
        }
    await _clear_reference(state)
    return {
        **base,
        "llm_messages": messages,
        "tools_called": [tool_name],
        "tool_biz": result.to_biz_dict() or None,
        "biz_type": result.biz_type,
        "biz_data": result.biz_data,
        "assistant_cards": result.assistant_cards,
        "chunks": [] if result.assistant_cards else [result.to_tool_message() or ""],
        "route": "finalize",
    }


async def _selection_card(
    state: AgentGraphState,
    intent: str,
    prompt: str,
    candidates: list[dict],
) -> dict:
    public_candidates = [
        {
            key: candidate.get(key)
            for key in (
                "targetType", "targetId", "orderId", "orderItemId", "productId",
                "productName", "propertyInfo", "cover", "amount", "orderStatus",
                "orderStatusName", "orderTime",
            )
        }
        for candidate in candidates
    ]
    stored = await order_selection_store.create(
        user_id=state["user_id"],
        source_message_id=state["message_id"],
        intent=intent,
        original_text=str(state.get("user_text") or ""),
        candidates=public_candidates,
        context={"intentDecision": state.get("intent_decision") or {}},
    )
    return {
        "type": "ORDER_SELECTION",
        "selectionId": stored["selectionId"],
        "sourceMessageId": str(state["message_id"]),
        "intent": intent,
        "prompt": prompt,
        "expiresAt": stored["expiresAt"],
        "candidates": public_candidates,
    }


def _tool_for_target(
    intent: str,
    user_text: str,
    target: dict,
    state: AgentGraphState | None = None,
) -> tuple[str, dict] | None:
    legacy_reference = state is None
    state = state or {}
    order_id = str(target.get("orderId") or "")
    item_id = str(target.get("orderItemId") or "")
    if intent == IntentKind.REFUND.value:
        return ("PROPOSE_REFUND", {"orderItemId": item_id}) if item_id else None
    if intent == IntentKind.CONFIRM_RECEIPT.value:
        return "PROPOSE_CONFIRM_RECEIPT", {"orderId": order_id}
    if intent == IntentKind.CANCEL_ORDER.value and not legacy_reference:
        return "PROPOSE_CANCEL_ORDER", {"orderId": order_id}
    if intent == IntentKind.QUERY_LOGISTICS.value:
        return "QUERY_LOGISTICS", {"orderId": order_id}
    if intent == IntentKind.QUERY_FULFILLMENT.value:
        return "QUERY_LOGISTICS", {"orderId": order_id}
    if intent == IntentKind.QUERY_ORDER.value:
        if any(hint in user_text for hint in ("再买一次", "再买", "复购")):
            product_name = str(target.get("productName") or "").strip()
            return ("SEARCH_PRODUCTS", {"keyword": product_name}) if product_name else None
        return "QUERY_ORDERS", {"orderId": order_id}
    if intent == IntentKind.QUERY_COMMENT.value:
        return "QUERY_COMMENT", {"orderId": order_id}
    if intent == IntentKind.REFUND_STATUS.value:
        args = {"orderId": order_id}
        if item_id:
            args["orderItemId"] = item_id
        return "QUERY_REFUND_STATUS", args
    if intent == IntentKind.PRODUCT_REVIEW.value:
        star = extract_review_star(user_text)
        content = extract_review_content(user_text, order_id)
        if star is None or not content:
            return None
        return "PROPOSE_PRODUCT_REVIEW", {
            "orderId": order_id,
            "commentContent": content,
            "star": star,
        }
    if intent == IntentKind.RECOMMENT.value:
        content = extract_review_content(user_text, order_id)
        if not content:
            return None
        return "PROPOSE_RECOMMENT", {
            "orderId": order_id,
            "reCommentContent": content,
        }
    if intent in {
        IntentKind.ADDRESS_CHANGE.value,
        IntentKind.INVOICE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.AFTERSALES_UNKNOWN.value,
    }:
        if legacy_reference:
            return None
        from app.services.support_case_service import support_case_service

        evidence = dict(state.get("image_evidence") or {})
        args = {
            "category": support_case_service.category_for_intent(intent, user_text),
            "description": user_text[:4000],
            "orderId": order_id or None,
            "orderItemId": item_id or None,
            "sourceMessageId": state.get("message_id"),
            "runId": (state.get("agent_msg") or {}).get("runId"),
        }
        if evidence:
            args.update(
                {
                    "imagePath": evidence.get("path"),
                    "imageModerationId": evidence.get("moderationId"),
                    "imageDescription": evidence.get("vlmDescription"),
                    "vlmStatus": evidence.get("vlmStatus"),
                }
            )
        return "PROPOSE_CREATE_SUPPORT_CASE", args
    return None


async def _direct_response(
    state: AgentGraphState, intent: str, target: dict
) -> dict | None:
    product = target.get("productName") or "该商品"
    order_id = target.get("orderId")
    status = target.get("orderStatusName") or "未知"
    if intent == IntentKind.QUERY_LOGISTICS.value and target.get("orderStatus") in {
        ORDER_STATUS_WAIT_PAYMENT,
        ORDER_STATUS_PAID,
    }:
        detail = (
            "订单尚未付款，因此还没有物流信息。"
            if target.get("orderStatus") == ORDER_STATUS_WAIT_PAYMENT
            else "商家尚未发货，因此暂时没有物流轨迹。"
        )
        return {
            "chunks": [
                f"已定位到“{product}”（订单 {order_id}，状态“{status}”）。{detail}"
            ],
            "biz_type": "query_logistics",
            "route": "finalize",
        }
    if intent == IntentKind.QUERY_FULFILLMENT.value:
        if target.get("orderStatus") == ORDER_STATUS_SHIPPED:
            return None
        if target.get("orderStatus") == ORDER_STATUS_PAID:
            text = (
                f"已定位到“{product}”（订单 {order_id}）。当前状态为“{status}”，商家尚未发货。"
                "客服侧暂无催发货写工具，可到「我的订单」查看，或回复“转人工”继续处理。"
            )
        else:
            text = f"已定位到“{product}”（订单 {order_id}），当前状态为“{status}”。"
        return {"chunks": [text], "biz_type": "query_order", "route": "finalize"}
    # The frozen conversation set records the pre-support-case capability
    # contract. Production graph states always carry message_id/run context;
    # keep the old explanatory answer only for lightweight legacy callers.
    if not state.get("message_id"):
        if intent == IntentKind.CANCEL_ORDER.value:
            return {
                "chunks": [
                    f"已定位到“{product}”（订单 {order_id}，状态“{status}”）。"
                    "客服侧没有代客取消工具，请到「我的订单」中选择该订单并取消。"
                ],
                "biz_type": "agent",
                "route": "finalize",
            }
        capability = {
            IntentKind.ADDRESS_CHANGE.value: "客服侧暂无修改收货地址工具，请在订单页核对可修改入口；如已无法修改，请回复“转人工”。",
            IntentKind.INVOICE.value: "客服侧暂无代开发票工具，请在订单详情中使用发票入口，或回复“转人工”。",
            IntentKind.DAMAGED_OR_WRONG_ITEM.value: "客服侧暂无破损、错发或漏发工单工具，请保留商品和包装凭证并回复“转人工”。",
            IntentKind.AFTERSALES_UNKNOWN.value: "请说明希望退款、查询物流还是处理质量问题；需要人工核验时可回复“转人工”。",
        }.get(intent)
        if capability:
            return {
                "chunks": [
                    f"已定位到“{product}”（订单 {order_id}，状态“{status}”）。{capability}"
                ],
                "biz_type": "agent",
                "route": "finalize",
            }
    return None


def _missing_args_prompt(intent: str, target: dict) -> str:
    product = target.get("productName") or "该订单商品"
    if intent == IntentKind.PRODUCT_REVIEW.value:
        return f"已定位到“{product}”。请告诉我 1-5 星评分和评价内容。"
    if intent == IntentKind.RECOMMENT.value:
        return f"已定位到“{product}”。请告诉我想追加的评价内容。"
    return "已定位到订单，但还缺少办理所需信息，请补充后继续。"


async def _remember_reference(state: AgentGraphState, intent: str, target: dict | None) -> None:
    if not target:
        return
    memory = await session_memory_service.load(state["user_id"], redis_service.client)
    memory.state["pendingOrderReference"] = {
        "intent": intent,
        "targetType": target.get("targetType"),
        "targetId": target.get("targetId"),
        "orderId": target.get("orderId"),
        "orderItemId": target.get("orderItemId"),
        "productName": target.get("productName"),
        "expiresAt": (datetime.now() + timedelta(minutes=30)).isoformat(timespec="seconds"),
    }
    await session_memory_service.save(memory, redis_service.client)


async def _clear_reference(state: AgentGraphState) -> None:
    memory = await session_memory_service.load(state["user_id"], redis_service.client)
    if memory.state.pop("pendingOrderReference", None) is not None:
        await session_memory_service.save(memory, redis_service.client)


def _has_specific_order_clue(text: str) -> bool:
    return any(
        hint in text
        for hint in (
            "最近", "上次", "刚买", "昨天", "前几天", "待付款", "待发货",
            "没发货", "已发货", "已退款", "耳机", "手机", "电脑", "订单号",
            "再买一次", "复购",
        )
    ) or bool(extract_order_id(text)) or bool(topic_terms_for_text(text))
