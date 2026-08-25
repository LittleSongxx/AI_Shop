from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.constants import (
    ORDER_STATUS_PAID,
    ORDER_STATUS_SHIPPED,
    ORDER_STATUS_WAIT_PAYMENT,
)
from app.domain.intent.classifier import classify_request_mode
from app.domain.intent.types import IntentKind, RequestMode
from app.domain.intent.write_args import extract_review_content, extract_review_star
from app.graph.state import AgentGraphState
from app.memory.session_memory_service import session_memory_service
from app.services.after_sales_policy_service import after_sales_policy_service
from app.services.episode_service import episode_service
from app.services.evidence_refs import (
    action_capability_ref,
    after_sales_eligibility_ref,
    order_card_fields_with_claims,
)
from app.services.java_internal_client import java_internal_client
from app.services.order_reference_resolver import (
    ORDER_ELIGIBILITY_REQUIRED_INTENTS,
    ORDER_REFERENCE_INTENTS,
    OrderReferenceOutcome,
    order_reference_resolver,
)
from app.services.order_selection_store import order_selection_store
from app.services.product_search_query import topic_terms_for_text
from app.services.redis_service import redis_service
from app.utils.order_ids import extract_order_id

_STRONG_LOGISTICS_EXCEPTION_HINTS = (
    "物流一直不动",
    "物流不动",
    "迟迟未发货",
    "一直没发货",
    "催发货",
)


async def resolve_order_reference_turn(state: AgentGraphState) -> dict:
    intent = str(state.get("intent") or "")
    user_text = str(state.get("user_text") or "")
    request_mode = str(state.get("request_mode") or "")
    if not request_mode:
        try:
            request_mode = classify_request_mode(user_text, IntentKind(intent)).value
        except ValueError:
            request_mode = RequestMode.READ_QUERY.value
    if request_mode == RequestMode.HUMAN_SUPPORT.value:
        return {"route": "orchestration_router"}
    if intent not in ORDER_REFERENCE_INTENTS:
        return {"route": "orchestration_router"}
    # An order-specific informational question (for example, “订单 X 怎么还
    # 没发货”) still needs the Java snapshot. Only generic policy questions
    # should bypass order resolution and use RAG directly.
    order_specific_query_intents = {
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
        IntentKind.REFUND_STATUS.value,
    }
    has_specific_order_clue = _has_specific_order_clue(user_text)
    generic_refund_policy = (
        intent == IntentKind.REFUND.value
        and request_mode == RequestMode.INFORMATIONAL.value
        and not has_specific_order_clue
    )
    if (
        intent in order_specific_query_intents and not has_specific_order_clue
    ) or generic_refund_policy:
        return {"route": "orchestration_router"}

    decision = state.get("intent_decision") or {}
    resolution = await order_reference_resolver.resolve(
        user_id=state["user_id"],
        intent=intent,
        user_text=user_text,
        entities=decision.get("entities") or {},
        consult_card=state.get("card"),
        pending_reference=state.get("pending_order_reference"),
        enforce_action_eligibility=(
            request_mode == RequestMode.ACTION_PROPOSAL.value
            or intent in ORDER_ELIGIBILITY_REQUIRED_INTENTS
        ),
    )
    base = {
        "order_resolution": resolution.outcome.value,
        "tool_source_refs": list(resolution.source_refs or []),
    }

    def with_evidence(
        update: dict,
        *,
        route: str,
        resolved_tool: str | None = None,
        has_context: bool = False,
        deterministic_clarification: bool = False,
    ) -> dict:
        """Attach a redacted resolver proof and persist it in the Episode."""

        effective_refs = update.get("tool_source_refs")
        if not isinstance(effective_refs, list):
            effective_refs = list(resolution.source_refs or [])
        if route == "finalize":
            update = {
                **update,
                "orchestration_mode": "workflow",
                "orchestration_reason": "order_reference_deterministic_result",
                "llm_skipped": True,
                "llm_skip_reason": "order_reference_deterministic_result",
                "structured_result_finalized": True,
            }
            if deterministic_clarification:
                # This flag is only set by the fixed, source-backed branch below.
                # It permits a next-step clarification, never a policy conclusion.
                update["deterministic_clarification"] = True
        audit = {
            "outcome": resolution.outcome.value,
            "route": route,
            "resolvedTool": resolved_tool,
            "businessSourceRefCount": len(effective_refs),
            "capabilityDecisionRefCount": sum(
                isinstance(ref, dict)
                and ref.get("type")
                in {"action_capability", "after_sales_eligibility"}
                for ref in effective_refs
            ),
            "hasVerifiedOrderContext": bool(has_context),
            "matchedCandidateCount": len(
                resolution.matched_candidates or resolution.candidates or []
            ),
            "dependencyError": resolution.outcome == OrderReferenceOutcome.DEPENDENCY_ERROR,
        }
        episode_service.record_step(
            "ORDER_REFERENCE_RESOLUTION",
            node_name="order_reference",
            status="ERROR" if audit["dependencyError"] else "OK",
            output_data=audit,
        )
        # Keep this under its own key; later RAG/orchestration updates must not
        # erase the proof that a deterministic path was deliberately selected.
        episode_service.update_run(experiment={"orderReference": audit})
        if route == "finalize":
            episode_service.record_step(
                "AGENT_POLICY",
                node_name="order_reference",
                output_data={
                    "policy": "ORDER_REFERENCE_DETERMINISTIC_RESULT",
                    "route": "workflow",
                    "mode": "workflow",
                    "llmSkipped": True,
                    "llmSkipReason": "order_reference_deterministic_result",
                    "structuredResultFinalized": True,
                    "deterministicClarification": deterministic_clarification,
                    "sideEffectAllowed": False,
                },
            )
        return {**update, "order_reference_evidence": audit}

    if resolution.outcome == OrderReferenceOutcome.DEPENDENCY_ERROR:
        return with_evidence(
            {**base, "chunks": [resolution.reason], "biz_type": "agent", "route": "finalize"},
            route="finalize",
        )
    # Historical checkpoints may still contain NO_ELIGIBLE. Treat it only as
    # a verified snapshot and abstain from a capability conclusion; new
    # resolver executions no longer derive this outcome from a status table.
    if resolution.outcome == OrderReferenceOutcome.NO_ELIGIBLE:
        candidate = resolution.candidates[0] if resolution.candidates else None
        await _remember_reference(state, intent, candidate)
        return with_evidence(
            {
                **base,
                "verified_order_context": dict(candidate) if candidate else None,
                "chunks": [_capability_unavailable_text(intent, candidate)],
                "biz_type": "agent",
                "route": "finalize",
            },
            route="finalize",
            has_context=bool(candidate and resolution.source_refs),
        )
    if resolution.outcome in {OrderReferenceOutcome.AMBIGUOUS, OrderReferenceOutcome.NO_MATCH}:
        if resolution.candidates:
            card = await _selection_card(
                state,
                intent,
                resolution.reason,
                resolution.candidates,
                resolution.source_refs,
            )
            return with_evidence(
                {
                    **base,
                    "assistant_cards": json.dumps(card, ensure_ascii=False),
                    "biz_type": "order_selection",
                    "chunks": [],
                    "route": "finalize",
                },
                route="finalize",
            )
        invoice_no_match = intent == IntentKind.INVOICE.value
        return with_evidence(
            {
                **base,
                "chunks": [
                    _missing_invoice_order_clarification()
                    if invoice_no_match
                    else resolution.reason
                ],
                "biz_type": "agent",
                "route": "finalize",
            },
            route="finalize",
            deterministic_clarification=invoice_no_match,
        )
    if resolution.outcome != OrderReferenceOutcome.RESOLVED or not resolution.target:
        return with_evidence(
            {
                **base,
                "chunks": ["订单候选已失效，请重新描述商品、购买时间或订单号。"],
                "biz_type": "agent",
                "route": "finalize",
            },
            route="finalize",
        )

    target = resolution.target
    tool = _tool_for_target(intent, user_text, target, state)
    if tool is None:
        await _remember_reference(state, intent, target)
        # Missing write arguments are a deterministic, snapshot-backed
        # clarification path. Preserve the same evidence as the other
        # finalize branches so evaluation can distinguish it from a skipped
        # or failed LLM call.
        return with_evidence(
            {
                **base,
                "verified_order_context": dict(target),
                "chunks": [_missing_args_prompt(intent, target)],
                "biz_type": "agent",
                "route": "finalize",
            },
            route="finalize",
            has_context=True,
        )

    tool_name, args = tool
    if tool_name == "PROPOSE_REFUND":
        eligibility = await _refund_eligibility(state, target)
        eligibility_ref = after_sales_eligibility_ref(eligibility)
        if eligibility_ref is not None:
            base["tool_source_refs"] = [
                *base["tool_source_refs"],
                eligibility_ref,
            ]
        eligibility_decision = str(
            eligibility.get("decision") or "POLICY_UNAVAILABLE"
        ).upper()
        episode_service.record_step(
            "AFTER_SALES_ELIGIBILITY_DECISION",
            node_name="order_reference",
            status="OK"
            if eligibility_decision in {"ELIGIBLE", "INELIGIBLE", "NEEDS_EVIDENCE"}
            else "DEGRADED",
            input_data={
                "action": "REFUND",
                "orderId": target.get("orderId"),
                "orderItemId": target.get("orderItemId"),
            },
            output_data={
                "decision": eligibility_decision,
                "decisionId": eligibility.get("decisionId"),
                "policyId": eligibility.get("policyId"),
                "policyVersion": eligibility.get("policyVersion"),
            },
        )
        if (
            eligibility_decision != "ELIGIBLE"
            or request_mode != RequestMode.ACTION_PROPOSAL.value
        ):
            await _remember_reference(state, intent, target)
            return with_evidence(
                {
                    **base,
                    "order_resolution": (
                        OrderReferenceOutcome.NO_ELIGIBLE.value
                        if eligibility_decision == "INELIGIBLE"
                        else OrderReferenceOutcome.RESOLVED.value
                    ),
                    "verified_order_context": dict(target),
                    "chunks": [
                        _refund_eligibility_text(target, eligibility_decision)
                    ],
                    "biz_type": "agent",
                    "route": "finalize",
                },
                route="finalize",
                has_context=True,
            )
    if tool_name in {
        "PROPOSE_CANCEL_ORDER",
        "PROPOSE_CONFIRM_RECEIPT",
        "PROPOSE_PRODUCT_REVIEW",
        "PROPOSE_RECOMMENT",
    }:
        capability = await _order_action_capability(
            intent=intent,
            target=target,
        )
        capability_ref = action_capability_ref(capability)
        if capability_ref is not None:
            base["tool_source_refs"] = [
                *base["tool_source_refs"],
                capability_ref,
            ]
        decision = str(capability.get("decision") or "UNAVAILABLE").upper()
        episode_service.record_step(
            "ORDER_ACTION_CAPABILITY",
            node_name="order_reference",
            status="OK" if decision in {"ALLOWED", "DENIED"} else "DEGRADED",
            input_data={
                "action": capability.get("action"),
                "orderId": target.get("orderId"),
                "orderItemId": target.get("orderItemId"),
            },
            output_data={
                "decision": decision,
                "reasonCode": capability.get("reason_code")
                or capability.get("reasonCode"),
                "capabilityVersion": capability.get("capability_version")
                or capability.get("capabilityVersion"),
            },
        )
        if decision != "ALLOWED":
            await _remember_reference(state, intent, target)
            return with_evidence(
                {
                    **base,
                    "order_resolution": (
                        OrderReferenceOutcome.NO_ELIGIBLE.value
                        if decision == "DENIED"
                        else OrderReferenceOutcome.RESOLVED.value
                    ),
                    "verified_order_context": dict(target),
                    "chunks": [
                        _capability_decision_text(intent, target, decision)
                    ],
                    "biz_type": "agent",
                    "route": "finalize",
                },
                route="finalize",
                has_context=True,
            )
        if request_mode != RequestMode.ACTION_PROPOSAL.value:
            await _remember_reference(state, intent, target)
            return with_evidence(
                {
                    **base,
                    "verified_order_context": dict(target),
                    "chunks": [
                        _capability_decision_text(intent, target, "ALLOWED")
                    ],
                    "biz_type": "agent",
                    "route": "finalize",
                },
                route="finalize",
                has_context=True,
            )
    # A strong logistics exception and a verified after-sales complaint are
    # intentionally converted to a confirmation card. Do not let a generic
    # order-status explanation finalize before that proposal is issued.
    direct = (
        None
        if tool_name == "PROPOSE_CREATE_SUPPORT_CASE"
        else await _direct_response(state, intent, target)
    )
    if direct is not None:
        return with_evidence(
            {**base, **direct, "verified_order_context": dict(target)},
            route="finalize",
            has_context=True,
        )
    await _clear_reference(state)
    if (
        tool_name.startswith("PROPOSE_")
        and tool_name != "PROPOSE_CREATE_SUPPORT_CASE"
        and request_mode != RequestMode.ACTION_PROPOSAL.value
    ):
        return with_evidence(
            {
                **base,
                "verified_order_context": dict(target),
                "resolved_order_tool": None,
                "route": "orchestration_router",
            },
            route="orchestration_router",
            has_context=True,
        )
    return with_evidence(
        {
            **base,
            "verified_order_context": dict(target),
            "resolved_order_tool": {"name": tool_name, "args": args},
            "route": "orchestration_router",
        },
        route="orchestration_router",
        resolved_tool=tool_name,
        has_context=True,
    )


async def _selection_card(
    state: AgentGraphState,
    intent: str,
    prompt: str,
    candidates: list[dict],
    source_refs: list[dict],
) -> dict:
    public_candidates = [
        public
        for candidate in candidates
        if (public := order_card_fields_with_claims(candidate, source_refs))
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


_CAPABILITY_ACTIONS = {
    IntentKind.CANCEL_ORDER.value: "CANCEL_ORDER",
    IntentKind.CONFIRM_RECEIPT.value: "CONFIRM_RECEIPT",
    IntentKind.PRODUCT_REVIEW.value: "PRODUCT_REVIEW",
    IntentKind.RECOMMENT.value: "RECOMMENT",
}


async def _order_action_capability(*, intent: str, target: dict) -> dict:
    action = _CAPABILITY_ACTIONS.get(intent)
    order_id = str(target.get("orderId") or "")
    if not action or not order_id:
        return {
            "decision": "UNAVAILABLE",
            "action": action,
            "order_id": order_id,
            "reason_code": "MISSING_ACTION_TARGET",
        }
    try:
        return await java_internal_client.get_order_action_capability(
            action,
            order_id,
            order_item_id=target.get("orderItemId"),
        )
    except Exception:
        return {
            "decision": "UNAVAILABLE",
            "action": action,
            "order_id": order_id,
            "order_item_id": target.get("orderItemId"),
            "reason_code": "CAPABILITY_SERVICE_UNAVAILABLE",
        }


async def _refund_eligibility(state: AgentGraphState, target: dict) -> dict:
    try:
        return await after_sales_policy_service.evaluate(
            user_id=state["user_id"],
            action="REFUND",
            order_id=target.get("orderId"),
            order_item_id=target.get("orderItemId"),
            evidence=["IMAGE"] if state.get("verified_image_context") else [],
        )
    except Exception:
        return {
            "decision": "POLICY_UNAVAILABLE",
            "action": "REFUND",
            "orderId": target.get("orderId"),
            "orderItemId": target.get("orderItemId"),
            "reason": "资格服务暂时不可用",
        }
def _capability_decision_text(intent: str, target: dict | None, decision: str) -> str:
    target = target or {}
    product = target.get("productName") or "该订单"
    order_id = target.get("orderId") or "未知"
    status = target.get("orderStatusName") or "未知"
    action = {
        IntentKind.CANCEL_ORDER.value: "取消订单",
        IntentKind.CONFIRM_RECEIPT.value: "确认收货",
        IntentKind.PRODUCT_REVIEW.value: "首次评价",
        IntentKind.RECOMMENT.value: "追评",
    }.get(intent, "办理该操作")
    prefix = f"已核验“{product}”（订单 {order_id}）当前状态为“{status}”。"
    if decision == "ALLOWED":
        return f"{prefix}业务系统的本次资格核验结果为可{action}；尚未创建确认卡，如需办理请明确回复要执行的操作。"
    if decision == "DENIED":
        return f"{prefix}业务系统的本次资格核验结果为不可{action}；如状态刚发生变化，请刷新后重试或转人工。"
    if decision == "MANUAL_REVIEW":
        return f"{prefix}{action}需要人工复核，请回复“转人工”继续处理。"
    return f"{prefix}资格服务暂时无法给出可核验结论，因此不会生成{action}确认卡；请稍后重试或转人工。"


def _capability_unavailable_text(intent: str, target: dict | None) -> str:
    return _capability_decision_text(intent, target, "UNAVAILABLE")


def _refund_eligibility_text(target: dict, decision: str) -> str:
    product = target.get("productName") or "该订单商品"
    order_id = target.get("orderId") or "未知"
    status = target.get("orderStatusName") or "未知"
    prefix = f"已核验“{product}”（订单 {order_id}）当前状态为“{status}”。"
    if decision == "ELIGIBLE":
        return f"{prefix}版本化售后规则的本次核验结果为可申请退款；尚未创建确认卡，如需办理请明确回复申请退款。"
    if decision == "INELIGIBLE":
        return f"{prefix}版本化售后规则的本次核验结果为不符合退款资格；如订单刚发生变化，请刷新后重试或转人工。"
    if decision == "NEEDS_EVIDENCE":
        return f"{prefix}退款资格仍需补充可核验凭证，本次不会生成退款确认卡。"
    if decision == "CONFLICT":
        return f"{prefix}当前售后规则存在冲突，需要人工复核，请回复“转人工”。"
    return f"{prefix}售后资格服务暂时无法给出可核验结论，本次不会生成退款确认卡；请稍后重试或转人工。"


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
    if (
        not legacy_reference
        and intent in {
            IntentKind.QUERY_LOGISTICS.value,
            IntentKind.QUERY_FULFILLMENT.value,
        }
        and _requires_logistics_support_case(user_text)
    ):
        return _support_case_tool(target, intent, user_text, state)
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
        return _support_case_tool(target, intent, user_text, state)
    return None


def _requires_logistics_support_case(user_text: str) -> bool:
    return any(marker in str(user_text or "") for marker in _STRONG_LOGISTICS_EXCEPTION_HINTS)


def _support_case_tool(
    target: dict,
    intent: str,
    user_text: str,
    state: AgentGraphState,
) -> tuple[str, dict]:
    from app.services.support_case_service import support_case_service

    image_context = dict(state.get("verified_image_context") or {})
    args = {
        "category": support_case_service.category_for_intent(intent, user_text),
        "description": user_text[:4000],
        "orderId": target.get("orderId") or None,
        "orderItemId": target.get("orderItemId") or None,
        "sourceMessageId": state.get("message_id"),
        "runId": (state.get("agent_msg") or {}).get("runId"),
    }
    if image_context:
        args.update(
            {
                "imageAssetId": image_context.get("asset_id"),
                "imageUnderstanding": state.get("image_understanding"),
                "imageUnderstandingStatus": (
                    "SUCCESS" if state.get("image_understanding") else "NOT_REQUESTED"
                ),
            }
        )
    return "PROPOSE_CREATE_SUPPORT_CASE", args


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
                "如需催发货或进一步核查，可以回复“转人工”继续处理。"
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


def _missing_invoice_order_clarification() -> str:
    """Ask for a verifiable invoice target without asserting invoice policy."""

    return (
        "我需要先定位具体订单才能继续处理开票请求。仅凭金额无法唯一匹配订单，"
        "请补充订单号或商品信息；如需人工帮助可回复“转人工”。"
    )


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
