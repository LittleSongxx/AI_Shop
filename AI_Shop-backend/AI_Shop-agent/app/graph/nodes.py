from __future__ import annotations

import time

import structlog
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.config.settings import get_settings
from app.domain.intent.classifier import (
    is_ambiguous_payment_failure,
    is_confirmed_no_deduction_payment_failure,
    is_pre_authorization_payment_retry,
    resolve_intent,
)
from app.domain.intent.rules import (
    deterministic_social_reply,
    looks_like_category_switch,
    looks_like_new_product_search,
    wants_order_list_cards,
)
from app.domain.intent.types import (
    IntentDecision,
    IntentKind,
    NextAction,
    RequestMode,
    RiskLevel,
)
from app.domain.intent.write_args import TOOL_REQUIRED_INTENTS
from app.domain.tool_policy import READ_TOOLS
from app.graph.forced_tools import (
    forced_named_product_comparison,
    forced_order_list,
    forced_product_search,
    forced_tool_for_intent,
    invoke_deterministic_tool,
)
from app.graph.orchestration_policy import fast_support_eligible, select_orchestration
from app.graph.order_reference_flow import resolve_order_reference_turn
from app.graph.state import AgentGraphState
from app.harness.guardrails.output_guard import OutputGuardrail, strip_emojis
from app.harness.metrics.runtime_sensors import measure_agent_stage
from app.harness.observation import (
    CONTAMINATED_CONTENT_PLACEHOLDER,
    build_tool_result_observation,
)
from app.memory.context_builder import context_builder
from app.memory.post_turn import post_turn_service
from app.memory.session_memory_service import session_memory_service
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.observability.telemetry import get_tracer
from app.rag.ab_test import get_bucket
from app.rag.prompt_builder import (
    RAG_REFUSAL_TEXT,
    build_grounding_prompt,
    deterministic_grounding_policy_fallback,
    deterministic_policy_evidence_fallback,
    grounding_repair_reason,
)
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
from app.services.response_verifier import (
    has_dynamic_order_authority,
    has_trusted_capability_decision,
)
from app.utils.biz_payload import (
    is_order_cards_json,
    is_product_cards_json,
    is_support_case_cards_json,
    support_case_card_type,
)
from app.utils.order_ids import extract_order_id
from app.utils.product_consult import (
    bounded_display_technology_explanation,
    elliptical_product_search_needs_category,
    is_product_consult_turn,
    named_product_comparison_requested,
    normalize_consult_card,
    product_consult_clarification,
)
from app.utils.prompt_boundary import isolate_knowledge_text
from app.utils.refund_policy import asks_refund_conditions, cited_refund_conditions

logger = structlog.get_logger()

# A3：HANDOFF_SUGGESTED（REPEATED_INTENT / 低置信）时追加在回答末尾的
# 建议转人工文案。只是建议，不强制；用户可继续提问或直接说"转人工"。
_HANDOFF_SUGGEST_TEXT = "\n\n如仍未解决，可以回复“转人工”，由人工客服继续协助。"
output_guard = OutputGuardrail()
_WRITE_PROPOSAL_TOOLS = frozenset(
    {
        "PROPOSE_REFUND",
        "PROPOSE_CANCEL_ORDER",
        "PROPOSE_CONFIRM_RECEIPT",
        "PROPOSE_CREATE_SUPPORT_CASE",
        "PROPOSE_PRODUCT_REVIEW",
        "PROPOSE_RECOMMENT",
    }
)


def _rag_fact_ref(
    state: AgentGraphState,
    fact_id: str,
) -> tuple[int, dict] | None:
    for citation, ref in enumerate(state.get("rag_source_refs") or [], start=1):
        if not isinstance(ref, dict):
            continue
        values = ref.get("factIds") or ref.get("fact_ids") or []
        if isinstance(values, str):
            values = [values]
        if fact_id in {str(value) for value in values}:
            return citation, ref
    return None


def _cited_payment_guidance(kind: str, citation: int) -> str:
    marker = f"[{citation}]"
    if kind == "preauthorization":
        return (
            "尚未输入支付密码且付款页卡住时，应先退出卡住页面并确认没有扣款。"
            f"{marker} 若订单仍为待支付，可从订单页重新发起一次支付。{marker} "
            f"不要连续快速重复提交；若出现扣款、重复扣款、非本人支付或状态不一致，"
            f"请停止重试并联系人工客服核查。{marker}"
        )
    return (
        f"根据你的描述，本次支付失败且没有扣款。{marker} "
        f"请先查看订单或支付记录的最终状态。{marker} "
        f"若仍为待支付，可退出失败页面，再从订单页重新发起一次支付。{marker} "
        f"不要连续快速重复提交；若之后出现扣款、重复扣款、非本人支付或状态不一致，"
        f"请停止重试并联系人工客服核查。{marker}"
    )


def _feedback_only_without_handoff(user_text: str) -> bool:
    text = str(user_text or "")
    feedback_only = any(
        marker in text
        for marker in ("只反馈", "反馈一下", "先反馈", "只是反馈", "就是反馈")
    )
    declines_handoff = any(
        marker in text
        for marker in ("不用转人工", "不要转人工", "不需要转人工", "先别转人工")
    )
    return feedback_only and declines_handoff


def _allow_llm_fallback_retry(
    state: AgentGraphState,
    *,
    non_stream_turn: bool,
    turn_chunks: list[str],
) -> bool:
    """Fallback is limited to idempotent reads with no visible partial output."""

    if not non_stream_turn or turn_chunks or state.get("input_security_flags"):
        return False
    if str(state.get("request_mode") or "") not in {
        RequestMode.READ_QUERY.value,
        RequestMode.INFORMATIONAL.value,
    }:
        return False
    called = {str(tool) for tool in state.get("tools_called") or []}
    pending = {
        str(call.get("name") or "")
        for call in state.get("pending_tool_calls") or []
        if isinstance(call, dict)
    }
    return not bool((called | pending) & _WRITE_PROPOSAL_TOOLS)

# Intent kinds for which the original intent is preserved even when a
# category-switch or new-product-search is detected.  Defined at module
# level so the frozenset is constructed once rather than on every call to
# build_context_node.
_KEEP_INTENT: frozenset = frozenset({
    # A product noun in an after-sales/security sentence is not a category
    # switch.  Preserve these intents before the shopping-memory heuristic
    # gets a chance to reinterpret the turn as a new product search.
    IntentKind.DAMAGED_OR_WRONG_ITEM,
    IntentKind.AFTERSALES_UNKNOWN,
    IntentKind.INVOICE,
    IntentKind.ADDRESS_CHANGE,
    IntentKind.PAYMENT_ISSUE,
    IntentKind.COMPLAINT,
    IntentKind.HUMAN_REQUEST,
    IntentKind.PRODUCT_CONSULT,
    IntentKind.QUERY_ORDER,
    IntentKind.QUERY_LOGISTICS,
    IntentKind.QUERY_FULFILLMENT,
    IntentKind.QUERY_COMMENT,
    IntentKind.QUERY_COUPON,
    IntentKind.PRODUCT_REVIEW,
    IntentKind.RECOMMENT,
    IntentKind.REFUND,
    IntentKind.REFUND_STATUS,
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


_RAG_POLICY_MARKERS = (
    "规则",
    "政策",
    "条件",
    "几天",
    "多久",
    "期限",
    "流程",
    "怎么办",
    "如何",
    "怎么",
    "申请",
    "是否支持",
    "能不能",
    "能否",
    "可不可以",
    "无理由",
    "运费",
    "保修",
    "发票",
    "取消",
    "在哪用",
)
_RAG_COMPLEX_MARKERS = ("同时", "另外", "以及", "并且", "还想", "区别", "分别")
_RAG_BUSINESS_MARKERS = (
    "订单",
    "退款",
    "退货",
    "换货",
    "物流",
    "优惠券",
    "售后",
    "发票",
    "地址",
)


def should_prefetch_rag(
    intent: IntentKind,
    *,
    rag_mode: str | None = None,
    agentic_rag: bool | None = None,
) -> bool:
    if rag_mode is None:
        rag_mode = "agentic" if agentic_rag else "prefetch"
    return rag_mode in {"prefetch", "conditional"} and intent in _RAG_PREFETCH_INTENTS


def requires_rag_evidence(user_text: str, intent: IntentKind) -> bool:
    text = str(user_text or "")
    has_policy_marker = any(marker in text for marker in _RAG_POLICY_MARKERS)
    return has_policy_marker and (
        intent in _RAG_PREFETCH_INTENTS
        or any(marker in text for marker in _RAG_BUSINESS_MARKERS)
    )


def _rag_query_variants_for_turn(user_text: str, rewritten_query: str) -> list[str]:
    """Add bounded retrieval variants for a known composite policy question."""

    text = str(user_text or "")
    variants = [str(rewritten_query or "").strip()]
    if "自动" in text and "收货" in text and "售后" in text:
        variants.extend(
            [
                "确认收货 订单完成状态 未实际收到不要提前确认",
                "售后资格 订单状态 实时规则核验",
            ]
        )
    return list(dict.fromkeys(value for value in variants if value))[:3]


def _citation_for_fact(
    source_refs: list[dict] | None,
    fact_ids: set[str] | frozenset[str],
) -> int | None:
    for index, ref in enumerate(source_refs or [], start=1):
        if not isinstance(ref, dict):
            continue
        values = ref.get("factIds") or ref.get("fact_ids") or []
        if isinstance(values, str):
            values = [values]
        nested = ref.get("ref")
        if isinstance(nested, dict):
            nested_values = nested.get("factIds") or nested.get("fact_ids") or []
            if isinstance(nested_values, str):
                nested_values = [nested_values]
            values = [*values, *nested_values]
        if fact_ids.intersection(str(value) for value in values):
            return index
    return None


def _auto_receipt_aftersales_policy_answer(state: AgentGraphState) -> str | None:
    text = str(state.get("user_text") or "")
    if not (
        str(state.get("intent") or "") == IntentKind.CONFIRM_RECEIPT.value
        and "自动" in text
        and "收货" in text
        and "售后" in text
    ):
        return None
    refs = list(state.get("rag_source_refs") or [])
    receipt_citation = _citation_for_fact(refs, {"logistics.confirm_receipt"})
    if receipt_citation is None:
        return None
    eligibility_citation = _citation_for_fact(
        refs,
        {
            "aftersales.rule_engine_authoritative",
            "aftersales.request_and_refund_boundary",
        },
    )
    if eligibility_citation is not None:
        return (
            f"自动确认收货会把订单推进到完成状态。[{receipt_citation}] "
            "是否还能办理售后不能只凭“已自动确认”统一下结论，"
            f"仍要按具体订单状态和实时售后规则核验。[{eligibility_citation}] "
            f"如果尚未实际收到商品或包裹异常，不要提前手动确认。[{receipt_citation}] "
            "请提供订单号，我可以继续核验该订单。"
        )
    return (
        f"自动确认收货会把订单推进到完成状态。[{receipt_citation}] "
        "当前可见规则不足以对所有订单统一判断完成后是否仍可售后；"
        "请提供订单号，以便按该订单的实时状态核验。"
        f"如果尚未实际收到商品或包裹异常，不要提前手动确认。[{receipt_citation}]"
    )


def _coupon_checkout_unavailable_answer(
    state: AgentGraphState, tool_update: dict
) -> str | None:
    text = str(state.get("user_text") or "")
    if not (
        str(state.get("intent") or "") == IntentKind.QUERY_COUPON.value
        and "优惠券" in text
        and "结算" in text
        and any(marker in text for marker in ("不可用", "不能用", "用不了"))
    ):
        return None
    citation = _citation_for_fact(
        list(state.get("rag_source_refs") or []),
        {"coupon.single_per_order_and_revalidate"},
    )
    if citation is None:
        return None
    refs = list(tool_update.get("tool_source_refs") or [])
    no_match = any(
        isinstance(ref, dict)
        and str(ref.get("type") or "").lower() == "coupon"
        and ref.get("matched") is False
        for ref in refs
    ) or "没有符合条件" in "".join(tool_update.get("chunks") or [])
    lookup = (
        "本次账户查询没有返回当前符合条件的优惠券；"
        if no_match
        else "已查询当前账户的优惠券记录；"
    )
    return (
        lookup
        + "但仅凭这次列表查询，不能定位某张券具体是哪项校验未通过。"
        + "提交订单时会按当前商品金额重新校验优惠券的门槛和归属，"
        + f"购物车预估优惠不代表结算时一定可用。[{citation}] "
        + "请补充券名称和门槛、结算商品金额及页面提示，以便继续核对具体券的可用状态；"
        + "不要提供支付密码。"
    )


def is_complex_rag_question(user_text: str, intent: IntentKind) -> bool:
    text = str(user_text or "")
    return bool(
        len(text) >= 80
        or text.count("？") + text.count("?") >= 2
        or sum(marker in text for marker in _RAG_COMPLEX_MARKERS) >= 1
        or (requires_rag_evidence(text, intent) and "订单" in text)
    )


def should_open_agentic_rag(
    *,
    rag_mode: str,
    user_text: str,
    intent: IntentKind,
    prefetched: bool,
    has_evidence: bool,
) -> bool:
    relevant = intent in _RAG_PREFETCH_INTENTS or requires_rag_evidence(
        user_text, intent
    )
    if rag_mode == "agentic":
        return relevant
    if rag_mode != "conditional" or not relevant:
        return False
    return (
        not prefetched
        or not has_evidence
        or is_complex_rag_question(user_text, intent)
    )

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
    image_context = state.get("verified_image_context")
    episode_service.record_step(
        "GUARD",
        node_name="entry",
        output_data={
            "guard": "cancellation",
            "decision": "PASS",
            "hasImage": bool(image_context),
        },
    )
    return {
        "card": card,
        "message_card": card,
        "user_text": user_text,
        "cancelled": False,
    }

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
                after_sales_workflow=True,
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
    settings = get_settings()
    rag_mode = settings.rag_mode
    episode_service.update_run(
        intent=intent.value,
        scenario=intent.value.lower(),
        experiment={"bucket": ab_bucket, "ragMode": rag_mode},
    )
    episode_service.record_step(
        "INTENT_DECISION",
        node_name="build_context",
        output_data={
            "intent": intent.value,
            "requestMode": decision.request_mode.value,
            "confidence": decision.confidence,
            "source": decision.source,
            "nextAction": decision.next_action.value,
        },
    )

    image_context = dict(state.get("verified_image_context") or {}) or None
    image_understanding = state.get("image_understanding")

    faq_text = ""
    knowledge_text = ""
    rag_source_refs: list[dict] = []
    rag_trace: dict | None = None
    rag_evidence_state = "INSUFFICIENT"
    rag_evidence_items: list[dict] = []
    rag_safe_business_query = user_text
    grounding_system_messages: list[SystemMessage] = []
    rag_queries: list[str] = []
    rag_retrieval_count = 0
    social_reply = (
        deterministic_social_reply(user_text)
        if decision.source == "deterministic_social"
        else None
    )
    rag_evidence_required = (
        False if social_reply is not None else requires_rag_evidence(user_text, intent)
    )
    prefetched = social_reply is None and should_prefetch_rag(intent, rag_mode=rag_mode)
    if prefetched:
        rag_started = time.perf_counter()
        with get_tracer().start_as_current_span("agent.rag.retrieve") as span:
            span.set_attribute("agent.rag.mode", rag_mode)
            span.set_attribute("agent.rag.phase", "prefetch")
            span.set_attribute("agent.rag.bucket", ab_bucket)
            with measure_agent_stage("rag"):
                rag_query = await rewrite_for_rag(user_text, memory)
                if image_understanding:
                    rag_query = f"{image_understanding} {rag_query}".strip()
                _cat_map = get_settings().rag_intent_category_map
                category_filter = (_cat_map.get(intent.value) or None) if _cat_map else None
                rag_result = await rag_retriever.search_faq_with_trace(
                    user_text,
                    category_filter=category_filter,
                    bucket=ab_bucket,
                    query_variants=_rag_query_variants_for_turn(
                        user_text, rag_query
                    ),
                    security_flags=list(
                        (state.get("agent_msg") or {}).get("inputSecurityFlags") or []
                    ),
                )
            rag_queries.append(rag_retriever.query_key(user_text))
            rag_retrieval_count = 1
            faq_text = str(rag_result.get("text") or "")
            rag_source_refs = list(rag_result.get("source_refs") or [])
            grounding_prompt = build_grounding_prompt(
                str((rag_result.get("queryPlan") or {}).get("safeBusinessQuery") or user_text),
                evidence_state=str(rag_result.get("evidenceState") or "INSUFFICIENT"),
                evidence_items=list(rag_result.get("evidenceItems") or []),
            )
            rag_evidence_state = grounding_prompt.evidence_state.value
            rag_evidence_items = list(rag_result.get("evidenceItems") or [])
            rag_safe_business_query = str(
                (rag_result.get("queryPlan") or {}).get("safeBusinessQuery")
                or user_text
            )
            grounding_system_messages = grounding_prompt.production_system_messages()
            retrieval_trace = dict(rag_result.get("trace") or {})
            retrieval_trace.update({"retrievalNo": 1, "phase": "prefetch"})
            rag_trace = {
                **retrieval_trace,
                "ragMode": rag_mode,
                "retrievals": [retrieval_trace],
            }
            # Production and evaluation use the same numbered grounding
            # contract. The legacy text/source_refs fields remain available for
            # UI and trace compatibility.
            # Grounding rules and numbered evidence are injected as separate
            # SystemMessages below. Do not pass them through the intent template,
            # which would escape the rules into the untrusted knowledge block and
            # repeat the user query.
            knowledge_text = ""
            faq_text = ""
            episode_service.record_step(
                "RAG_RETRIEVAL",
                node_name="build_context",
                input_data={
                    "queryHash": rag_retriever.query_key(user_text),
                    "categoryFilter": category_filter,
                },
                output_data={
                    "trace": rag_trace,
                    "sourceRefs": rag_source_refs,
                    "hasEvidence": bool(rag_source_refs),
                },
                latency_ms=round((time.perf_counter() - rag_started) * 1_000),
            )

    rag_agentic_allowed = False
    if social_reply is None:
        rag_agentic_allowed = should_open_agentic_rag(
            rag_mode=rag_mode,
            user_text=user_text,
            intent=intent,
            prefetched=prefetched,
            has_evidence=bool(rag_source_refs),
        )

    selected_fragments: list[dict] = []
    messages, working_turns, working_oldest_id = await context_builder.build_agent_messages(
        user_id,
        user_text,
        memory,
        intent=intent,
        product_snapshot=snapshot,
        faq_text=faq_text,
        knowledge_text=knowledge_text,
        grounding_system_messages=grounding_system_messages,
        selection_out=selected_fragments,
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

    # D 工作线：记录本次实际选用的 prompt 片段（含管理端 Redis 覆盖是否
    # 生效的 source 字段），让"提示词是怎么组出来的"可观测。
    episode_service.record_step(
        "CONTEXT_BUILT",
        node_name="build_context",
        input_data={"intent": intent.value, "intentSource": intent_source},
        output_data={
            "selectedFragments": selected_fragments,
            "systemPromptChars": sum(
                int(fragment.get("chars") or 0) for fragment in selected_fragments
            ),
        },
    )

    remaining_retrievals = max(0, 2 - rag_retrieval_count)
    if rag_agentic_allowed and remaining_retrievals:
        messages.append(
            SystemMessage(
                content=(
                    "【RAG 编排】本轮允许按需调用 SEARCH_KNOWLEDGE；"
                    f"剩余检索次数 {remaining_retrievals}。每次 query 必须独立完整且不可重复；"
                    "只能依据工具返回的通过证据门禁内容形成政策结论。"
                )
            )
        )
    else:
        messages.append(
            SystemMessage(
                content=(
                    "【RAG 编排】本轮不开放额外 SEARCH_KNOWLEDGE 调用。"
                    "只使用已注入且带来源的知识；没有依据时不得给出确定政策结论。"
                )
            )
        )

    # P2 无证据拒答强化：当本轮没有任何通过证据门禁的知识片段时，明确告知模型
    # 不得根据通用训练知识推断商品参数、价格、政策等具体数据。
    # 条件：已做了预取（说明这类意图需要知识支撑），但证据为空；
    # 且不是商品搜索/推荐意图（那些依赖工具结果而非 RAG 证据）。
    _EVIDENCE_REQUIRED_INTENTS = frozenset(
        {IntentKind.CHAT, IntentKind.PRODUCT_CONSULT, IntentKind.QUERY_LOGISTICS}
    )
    if (
        prefetched
        and not rag_source_refs
        and rag_evidence_required
        and intent in _EVIDENCE_REQUIRED_INTENTS
    ):
        messages.append(
            SystemMessage(
                content=(
                    "[无证据提示]本轮未检索到相关知识库内容，证据库为空。"
                    "你必须诚实告知用户：暂无相关信息，建议查看商品详情页或联系人工客服。"
                    "禁止根据通用训练知识推断或猜测商品价格、规格、售后政策等具体数据。"
                    "若涉及账号、订单或物流，请引导用户提供订单号后通过工具查询。"
                )
            )
        )

    selected_comparison_ids = list(state.get("comparison_product_ids") or [])
    if selected_comparison_ids:
        messages.append(
            SystemMessage(
                content=(
                    "【商品比较选择】用户已在界面选择商品Id："
                    + ", ".join(selected_comparison_ids)
                    + "。需要比较时调用 COMPARE_PRODUCTS；不得替换或追加其他商品Id。"
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
        "request_mode": decision.request_mode.value,
        "verified_image_context": image_context,
        "image_understanding": image_understanding,
        "rag_source_refs": rag_source_refs,
        "rag_trace": rag_trace,
        "rag_evidence_state": rag_evidence_state,
        "rag_evidence_items": rag_evidence_items,
        "rag_safe_business_query": rag_safe_business_query,
        "rag_mode": rag_mode,
        "rag_queries": rag_queries,
        "rag_retrieval_count": rag_retrieval_count,
        "rag_agentic_allowed": rag_agentic_allowed,
        "rag_evidence_required": rag_evidence_required,
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

    intent_name = str(state.get("intent") or "")
    user_text = str(state.get("user_text") or "")
    intent_decision = state.get("intent_decision") or {}
    decision_risk = str(
        intent_decision.get("risk_level")
        or intent_decision.get("riskLevel")
        or ""
    ).upper()
    decision_action = str(
        intent_decision.get("next_action")
        or intent_decision.get("nextAction")
        or ""
    ).upper()
    has_consult_identity = bool(
        normalize_consult_card(state.get("card"))
        or normalize_consult_card(state.get("message_card"))
    )
    has_product_evidence = any(
        tool in {"GET_PRODUCT_DETAIL", "COMPARE_PRODUCTS"}
        for tool in state.get("tools_called") or []
    )
    auto_receipt_answer = _auto_receipt_aftersales_policy_answer(state)
    if auto_receipt_answer is not None:
        episode_service.record_step(
            "AUTO_RECEIPT_AFTERSALES_BOUNDARY",
            node_name="agent_loop",
            status="OK",
            output_data={"reason": "visible_policy_evidence"},
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "AUTO_RECEIPT_AFTERSALES_BOUNDARY",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "auto_receipt_aftersales_boundary",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [auto_receipt_answer],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "auto_receipt_aftersales_boundary",
            "structured_result_finalized": True,
            "route": "finalize",
        }
    if (
        intent_name == IntentKind.COMPLAINT.value
        and _feedback_only_without_handoff(user_text)
    ):
        answer = (
            "收到你对物流速度偏慢的反馈。本次不会发起人工转接，也不会查询或修改订单；"
            "如果之后需要核查具体订单，再提供订单号即可。"
        )
        episode_service.record_step(
            "FEEDBACK_ONLY_ACKNOWLEDGEMENT",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={
                "reason": "feedback_only_acknowledgement",
                "handoffStarted": False,
                "sideEffectAllowed": False,
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "FEEDBACK_ONLY_ACKNOWLEDGEMENT",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "feedback_only_acknowledgement",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [answer],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "feedback_only_acknowledgement",
            "deterministic_clarification": True,
            "structured_result_finalized": True,
            "route": "finalize",
        }
    refund_conditions = (
        cited_refund_conditions(state.get("rag_source_refs") or [])
        if intent_name == IntentKind.REFUND.value
        and asks_refund_conditions(user_text)
        else None
    )
    if refund_conditions is not None:
        episode_service.record_step(
            "REFUND_CONDITIONS_EVIDENCE_ANSWER",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={
                "reason": "visible_policy_evidence",
                "citation": refund_conditions["citation"],
                "sourceId": refund_conditions.get("sourceId"),
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "REFUND_CONDITIONS_EVIDENCE_ANSWER",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "refund_conditions_evidence_answer",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [str(refund_conditions["answer"])],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "refund_conditions_evidence_answer",
            "structured_result_finalized": True,
            "route": "finalize",
        }
    display_requested = (
        bounded_display_technology_explanation(user_text)
        if intent_name == IntentKind.PRODUCT_CONSULT.value
        else None
    )
    display_fact = _rag_fact_ref(state, "product.display_technology_boundary")
    display_explanation = None
    if display_requested is not None:
        display_explanation = (
            bounded_display_technology_explanation(
                user_text,
                citation=display_fact[0],
            )
            if display_fact is not None
            else RAG_REFUSAL_TEXT
        )
    if display_explanation is not None:
        episode_service.record_step(
            "BOUNDED_DISPLAY_TECHNOLOGY_EXPLANATION",
            node_name="agent_loop",
            status="OK",
            output_data={
                "reason": (
                    "visible_published_technology_source"
                    if display_fact is not None
                    else "published_technology_source_missing"
                ),
                "citation": display_fact[0] if display_fact is not None else None,
                "sourceId": display_fact[1].get("id") if display_fact is not None else None,
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "BOUNDED_DISPLAY_TECHNOLOGY_EXPLANATION",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "bounded_display_technology_explanation",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [display_explanation],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "bounded_display_technology_explanation",
            "structured_result_finalized": True,
            "route": "finalize",
        }
    if (
        intent_name == IntentKind.PRODUCT_CONSULT.value
        and not has_consult_identity
        and not has_product_evidence
        and not state.get("comparison_product_ids")
        and named_product_comparison_requested(user_text)
    ):
        update = await forced_named_product_comparison(
            messages=list(state.get("llm_messages") or []),
            user_id=state["user_id"],
            message_id=state["message_id"],
            keyword=user_text,
        )
        episode_service.record_step(
            "NAMED_PRODUCT_COMPARISON",
            node_name="agent_loop",
            status="OK",
            output_data={
                "reason": "catalog_identity_resolution",
                "tools": list(update.get("tools_called") or []),
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "NAMED_PRODUCT_COMPARISON",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "named_product_comparison",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            **update,
            "llm_skipped": True,
            "llm_skip_reason": "named_product_comparison",
            "structured_result_finalized": True,
        }
    if (
        intent_name == IntentKind.PRODUCT_CONSULT.value
        and not has_consult_identity
        and not has_product_evidence
        and not state.get("comparison_product_ids")
    ):
        clarification = product_consult_clarification(user_text)
        episode_service.record_step(
            "PRODUCT_CONSULT_CLARIFICATION",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={"reason": "missing_authoritative_product_identity"},
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "PRODUCT_CONSULT_IDENTITY_CLARIFICATION",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "missing_authoritative_product_identity",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [clarification],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "missing_authoritative_product_identity",
            "structured_result_finalized": True,
            "route": "finalize",
        }

    if (
        intent_name == IntentKind.PRODUCT_SEARCH.value
        and elliptical_product_search_needs_category(user_text)
    ):
        clarification = (
            "我已记录“小户型、静音”这两个条件，但当前对话里没有可核验的上一批商品类别。"
            "请补充要重选的品类（例如空气净化器、风扇或其他家电），我再只返回该品类中有静音依据的商品。"
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "ELLIPTICAL_SEARCH_CATEGORY_CLARIFICATION",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "missing_followup_product_category",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [clarification],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "missing_followup_product_category",
            "deterministic_clarification": True,
            "structured_result_finalized": True,
            "route": "finalize",
        }

    payment_guidance_allowed = (
        intent_name == IntentKind.PAYMENT_ISSUE.value
        and str(state.get("request_mode") or "")
        != RequestMode.HUMAN_SUPPORT.value
        and decision_risk != RiskLevel.HIGH.value
        and decision_action != NextAction.HANDOFF.value
    )
    payment_fact = _rag_fact_ref(state, "payment.safe_retry_guidance")
    if payment_guidance_allowed and is_pre_authorization_payment_retry(user_text):
        payment_answer = (
            _cited_payment_guidance("preauthorization", payment_fact[0])
            if payment_fact is not None
            else RAG_REFUSAL_TEXT
        )
        episode_service.record_step(
            "PAYMENT_PREAUTH_RETRY_GUIDANCE",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={
                "reason": "credential_not_entered",
                "citation": payment_fact[0] if payment_fact is not None else None,
                "sourceId": payment_fact[1].get("id") if payment_fact is not None else None,
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "PAYMENT_PREAUTH_RETRY_GUIDANCE",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "payment_preauth_retry_guidance",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [payment_answer],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "payment_preauth_retry_guidance",
            "deterministic_clarification": payment_fact is None,
            "structured_result_finalized": True,
            "route": "finalize",
        }

    if payment_guidance_allowed and is_confirmed_no_deduction_payment_failure(
        user_text
    ):
        payment_answer = (
            _cited_payment_guidance("no_deduction", payment_fact[0])
            if payment_fact is not None
            else RAG_REFUSAL_TEXT
        )
        episode_service.record_step(
            "PAYMENT_FAILURE_NO_DEDUCTION_GUIDANCE",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={
                "reason": "funds_not_deducted",
                "citation": payment_fact[0] if payment_fact is not None else None,
                "sourceId": payment_fact[1].get("id") if payment_fact is not None else None,
            },
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "PAYMENT_FAILURE_NO_DEDUCTION_GUIDANCE",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "funds_not_deducted",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [payment_answer],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "funds_not_deducted",
            "deterministic_clarification": payment_fact is None,
            "structured_result_finalized": True,
            "route": "finalize",
        }

    if payment_guidance_allowed and is_ambiguous_payment_failure(user_text):
        clarification = (
            "请先确认支付账户是否已有扣款记录。若已扣款、重复扣款或非本人支付，"
            "请回复“转人工”以进入人工核查；若未扣款，请补充支付页面提示或支付方式。"
        )
        episode_service.record_step(
            "PAYMENT_FAILURE_STATE_CLARIFICATION",
            node_name="agent_loop",
            status="OK",
            input_data={"intent": intent_name},
            output_data={"reason": "funds_state_not_confirmed"},
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "PAYMENT_FAILURE_STATE_CLARIFICATION",
                "route": "finalize",
                "llmSkipped": True,
                "llmSkipReason": "funds_state_not_confirmed",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "chunks": [clarification],
            "pending_tool_calls": [],
            "biz_type": "agent",
            "llm_skipped": True,
            "llm_skip_reason": "funds_state_not_confirmed",
            "deterministic_clarification": True,
            "structured_result_finalized": True,
            "route": "finalize",
        }

    consult = state.get("card")
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
    grounded_answer_turn = bool(
        state.get("rag_evidence_required")
        and state.get("rag_evidence_state") == "SUPPORTED"
        and state.get("rag_evidence_items")
    )
    # A simple policy question with prevalidated evidence has no reason to
    # enter a second tool-calling round. Keeping tools enabled here can cause
    # redundant retrieval, extra context, and an avoidable token-budget miss.
    bounded_grounded_turn = grounded_answer_turn and not state.get("rag_agentic_allowed")
    if bounded_grounded_turn:
        tool_required_first_turn = False
    fast_support_turn = bool(
        getattr(settings, "agent_fast_support_mode", False)
        and fast_support_eligible(state)
    )
    if fast_support_turn:
        # Keep the experiment visible in the authoritative episode instead of
        # relying on an environment variable that is easy to lose in replay.
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="agent_loop",
            output_data={
                "policy": "FAST_SUPPORT_FIRST_TURN",
                "deterministicSocialReply": False,
                "maxTokens": getattr(settings, "agent_fast_support_max_tokens", 512),
                "disableThinking": True,
            },
        )
    non_stream_turn = non_stream_turn or grounded_answer_turn
    llm_options = {
        "tools_enabled": not bounded_grounded_turn,
        "max_tokens": (
            384
            if bounded_grounded_turn
            else getattr(settings, "agent_fast_support_max_tokens", 512)
            if fast_support_turn
            else None
        ),
        "disable_thinking": bounded_grounded_turn or fast_support_turn,
    }
    # A proposal path must never hide a provider failure behind an SDK retry.
    # The deterministic capability and pending-action guards can be retried by
    # the user with a fresh business snapshot instead.  Keep the option absent
    # on normal turns so the LLM factory's configured retry policy still owns
    # those calls.
    if (
        str(state.get("request_mode") or "")
        == RequestMode.ACTION_PROPOSAL.value
    ):
        llm_options["max_retries"] = 0
    llm = rt.bind_agent_llm(**llm_options)
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
        retry_is_safe = _allow_llm_fallback_retry(
            state, non_stream_turn=non_stream_turn, turn_chunks=turn_chunks
        )
        can_retry = retry_is_safe and has_fallback_chat_llm()
        # A4：失败的调用也计入 LLM_CALL_TOTAL（成功/失败都要可观测，
        # 只看成功数算不出失败率）。已部分流式输出的不算 fallback 机会，
        # 但那次调用本身已经失败，照记。
        if not non_stream_turn and not getattr(
            primary_error, "_aishop_llm_metrics_recorded", False
        ):
            rt.record_llm_failure(
                settings.llm_model,
                fallback=False,
                missing_reason=(
                    "call_deadline_exceeded_before_usage"
                    if isinstance(primary_error, TimeoutError)
                    else "provider_error_before_usage"
                ),
            )
        logger.warning(
            "llm_turn_failed",
            error=str(primary_error),
            error_type=type(primary_error).__name__,
            retry_fallback=can_retry,
        )
        if can_retry:
            configured_fallback_retries = getattr(
                settings, "llm_fallback_max_retries", None
            )
            fallback_retry_budget = (
                0
                if configured_fallback_retries is None
                else max(0, int(configured_fallback_retries))
            )
            try:
                fallback_llm = rt.bind_agent_llm(
                    fallback=True,
                    allowed_tools=READ_TOOLS,
                    **{**llm_options, "max_retries": fallback_retry_budget},
                )
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
                if not non_stream_turn and not getattr(
                    fallback_error, "_aishop_llm_metrics_recorded", False
                ):
                    rt.record_llm_failure(
                        settings.llm_fallback_model,
                        fallback=True,
                        missing_reason=(
                            "call_deadline_exceeded_before_usage"
                            if isinstance(fallback_error, TimeoutError)
                            else "provider_error_before_usage"
                        ),
                    )
                logger.warning(
                    "llm_fallback_failed",
                    error=str(fallback_error),
                    error_type=type(fallback_error).__name__,
                )
        elif has_fallback_chat_llm():
            episode_service.record_step(
                "LLM_FALLBACK_SUPPRESSED",
                node_name="agent_loop",
                status="SKIPPED",
                output_data={
                    "reason": (
                        "non_idempotent_or_write_path"
                        if str(state.get("request_mode") or "")
                        not in {
                            RequestMode.READ_QUERY.value,
                            RequestMode.INFORMATIONAL.value,
                        }
                        else "streaming_partial_or_security_boundary"
                    ),
                    "requestMode": state.get("request_mode"),
                    "nonStreamTurn": non_stream_turn,
                    "visiblePartial": bool(turn_chunks),
                },
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
            request_mode=state.get("request_mode"),
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

    repair_attempted = bool(state.get("rag_repair_attempted"))
    repair_reason = None
    repair_failed = False
    deterministic_fallback: dict | None = None
    if grounded_answer_turn and not repair_attempted:
        initial_text = strip_emojis(
            rt.chunk_text(getattr(response, "content", "") or "")
        )
        evidence_items = list(state.get("rag_evidence_items") or [])
        repair_reason = grounding_repair_reason(
            initial_text,
            evidence_state=str(state.get("rag_evidence_state") or "INSUFFICIENT"),
            evidence_count=len(evidence_items),
            evidence_items=evidence_items,
        )
        if repair_reason:
            repair_attempted = True
            repair_prompt = build_grounding_prompt(
                str(state.get("rag_safe_business_query") or user_text),
                evidence_state=str(state.get("rag_evidence_state") or "INSUFFICIENT"),
                evidence_items=evidence_items,
                repair_reason=repair_reason,
            )
            try:
                repair_llm = rt.bind_agent_llm(
                    max_tokens=256,
                    disable_thinking=True,
                    tools_enabled=False,
                )
                repaired = await invoke_llm_with_metrics(
                    repair_llm,
                    repair_prompt.messages(),
                    model=settings.llm_model,
                )
                repaired_text = strip_emojis(
                    rt.chunk_text(getattr(repaired, "content", "") or "")
                )
                remaining = grounding_repair_reason(
                    repaired_text,
                    evidence_state=str(
                        state.get("rag_evidence_state") or "INSUFFICIENT"
                    ),
                    evidence_count=len(evidence_items),
                    evidence_items=evidence_items,
                )
                if repaired_text and not remaining:
                    response = repaired
                    turn_chunks = [repaired_text]
                else:
                    repair_failed = True
                    repair_reason = (
                        f"{repair_reason}；修复后仍失败：{remaining or '空答案'}"
                    )
                episode_service.record_step(
                    "RAG_GENERATION_REPAIR",
                    node_name="agent_loop",
                    status="OK" if repaired_text and not remaining else "FAILED",
                    input_data={
                        "reason": repair_reason,
                        "initialAnswer": initial_text,
                        "evidenceCount": len(evidence_items),
                    },
                    output_data={"repairedAnswer": repaired_text},
                )
            except Exception as exc:
                repair_failed = True
                repair_reason = f"{repair_reason}；修复调用失败：{type(exc).__name__}"
                episode_service.record_step(
                    "RAG_GENERATION_REPAIR",
                    node_name="agent_loop",
                    status="ERROR",
                    input_data={
                        "reason": repair_reason,
                        "initialAnswer": initial_text,
                        "evidenceCount": len(evidence_items),
                    },
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )

        if repair_failed:
            deterministic_fallback = deterministic_grounding_policy_fallback(
                str(state.get("rag_safe_business_query") or user_text),
                evidence_state=str(state.get("rag_evidence_state") or "INSUFFICIENT"),
                evidence_items=evidence_items,
            )
            if deterministic_fallback:
                fallback_text = str(deterministic_fallback["answer"])
                response = AIMessage(content=fallback_text)
                turn_chunks = [fallback_text]
                repair_reason = (
                    f"{repair_reason or '模型修复未通过'}；deterministicFallback="
                    f"{deterministic_fallback['event']}"
                )
                episode_service.record_step(
                    deterministic_fallback["event"],
                    node_name="agent_loop",
                    status="OK",
                    input_data={
                        "query": str(
                            state.get("rag_safe_business_query") or user_text
                        ),
                        "evidenceState": str(
                            state.get("rag_evidence_state") or "INSUFFICIENT"
                        ),
                        "evidenceCount": len(evidence_items),
                        "factId": deterministic_fallback["factId"],
                    },
                    output_data={
                        "answer": fallback_text,
                        "citation": deterministic_fallback["citation"],
                        "usageAdded": False,
                    },
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
        "rag_repair_attempted": repair_attempted,
        "rag_repair_reason": repair_reason,
    }


async def order_reference_node(state: AgentGraphState) -> dict:
    if state.get("cancelled") or state.get("finished"):
        return {"route": "end"}
    return await resolve_order_reference_turn(state)


async def dynamic_handoff_node(state: AgentGraphState) -> dict:
    """Perform a handoff justified by facts learned after initial routing."""

    if state.get("cancelled"):
        return {"finished": True, "route": "end", "outcome": "cancelled"}
    # Lazy import avoids agent_service -> graph.runner -> graph.builder -> nodes
    # import cycles during worker startup.
    from app.services.agent_service import agent_orchestrator

    decision = dict(state.get("intent_decision") or {})
    decision.update(
        {
            "intent": str(state.get("intent") or decision.get("intent") or "CHAT"),
            "next_action": NextAction.HANDOFF.value,
            "request_mode": RequestMode.HUMAN_SUPPORT.value,
            "handoff_reason": str(
                state.get("dynamic_handoff_reason") or "STATE_CONFLICT"
            ),
        }
    )
    verified_refs = dict(state.get("dynamic_handoff_order_refs") or {})
    episode_service.record_step(
        "AGENT_POLICY",
        node_name="human_handoff",
        output_data={
            "policy": "POST_RESOLUTION_HUMAN_HANDOFF",
            "reason": decision["handoff_reason"],
            "hasVerifiedOrderReference": bool(verified_refs),
            "llmSkipped": True,
            "sideEffectAllowed": True,
        },
    )
    try:
        await agent_orchestrator._transfer_to_support(
            state["agent_msg"],
            str(state.get("user_text") or ""),
            decision,
            verified_order_refs=verified_refs,
            finish_episode=False,
        )
    finally:
        await redis_service.clear_bound_message_id(state["user_id"])
    return {
        "finished": True,
        "route": "end",
        "outcome": "human_support",
        "biz_type": "human_support",
        "llm_skipped": True,
        "llm_skip_reason": "post_resolution_human_handoff",
        "structured_result_finalized": True,
    }


async def orchestration_router_node(state: AgentGraphState) -> dict:
    settings = get_settings()
    decision = select_orchestration(
        state,
        configured_mode=settings.orchestration_mode,
        multi_agent_enabled=settings.multi_agent_enabled,
    )
    detail = {
        "mode": decision.mode,
        "reason": decision.reason,
        "configuredMode": settings.orchestration_mode,
        "multiAgentAvailable": settings.multi_agent_enabled,
    }
    episode_service.record_step(
        "ORCHESTRATION_DECISION",
        node_name="orchestration_router",
        output_data=detail,
    )
    episode_service.update_run(experiment={"orchestration": detail})
    return {
        "orchestration_mode": decision.mode,
        "orchestration_reason": decision.reason,
        "route": decision.route,
    }


async def deterministic_workflow_node(state: AgentGraphState) -> dict:
    messages = list(state.get("llm_messages") or [])
    user_id = state["user_id"]
    intent = state.get("intent")
    resolved = state.get("resolved_order_tool") or {}
    tool_name = str(resolved.get("name") or "")
    tool_args = resolved.get("args")
    decision = state.get("intent_decision")
    social_reply = (
        deterministic_social_reply(str(state.get("user_text") or ""))
        if intent == IntentKind.CHAT.value
        and isinstance(decision, dict)
        and decision.get("source") == "deterministic_social"
        else None
    )
    if social_reply is not None:
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="deterministic_workflow",
            output_data={
                "policy": "DETERMINISTIC_SOCIAL_REPLY",
                "deterministicSocialReply": True,
                "llmSkipped": True,
                "ragSkipped": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "llm_messages": messages,
            "tools_called": [],
            "biz_type": "agent",
            "chunks": [social_reply],
            "pending_tool_calls": [],
            "llm_skipped": True,
            "llm_skip_reason": "deterministic_social_reply",
            "structured_result_finalized": True,
            "route": "finalize",
        }
    user_text = str(state.get("user_text") or "")
    if (
        intent == IntentKind.PRODUCT_SEARCH.value
        and elliptical_product_search_needs_category(user_text)
    ):
        clarification = (
            "我已记录“小户型、静音”这两个条件，但当前对话里没有可核验的上一批商品类别。"
            "请补充要重选的品类（例如空气净化器、风扇或其他家电），我再只返回该品类中有静音依据的商品。"
        )
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="deterministic_workflow",
            output_data={
                "policy": "ELLIPTICAL_SEARCH_CATEGORY_CLARIFICATION",
                "route": "workflow",
                "mode": "workflow",
                "llmSkipped": True,
                "llmSkipReason": "missing_followup_product_category",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            "llm_messages": messages,
            "tools_called": [],
            "biz_type": "agent",
            "chunks": [clarification],
            "pending_tool_calls": [],
            "llm_skipped": True,
            "llm_skip_reason": "missing_followup_product_category",
            "deterministic_clarification": True,
            "structured_result_finalized": True,
            "route": "finalize",
        }
    if tool_name and isinstance(tool_args, dict):
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="deterministic_workflow",
            output_data={
                "policy": "VERIFIED_ORDER_WORKFLOW",
                "route": "workflow",
                "mode": "workflow",
                "llmSkipped": True,
                "llmSkipReason": "verified_order_workflow",
                "structuredResultFinalized": True,
                "sideEffectAllowed": tool_name.startswith("PROPOSE_"),
            },
        )
        update = await invoke_deterministic_tool(
            messages=messages,
            user_id=user_id,
            tool_name=tool_name,
            tool_args=tool_args,
            intent=intent,
            call_id=f"workflow:{state['message_id']}",
            prior_source_refs=list(state.get("tool_source_refs") or []),
        )
        return {
            **update,
            "llm_skipped": True,
            "llm_skip_reason": "verified_order_workflow",
            "structured_result_finalized": True,
        }

    update = await forced_tool_for_intent(
        messages=messages,
        user_id=user_id,
        intent=intent,
        intent_data=state.get("intent_data"),
        user_text=str(state.get("user_text") or ""),
        request_mode=state.get("request_mode"),
    )
    if update is not None:
        coupon_answer = _coupon_checkout_unavailable_answer(state, update)
        if coupon_answer is not None:
            update = {**update, "chunks": [coupon_answer]}
        episode_service.record_step(
            "AGENT_POLICY",
            node_name="deterministic_workflow",
            output_data={
                "policy": (
                    "COUPON_CHECKOUT_REVALIDATION_EXPLANATION"
                    if coupon_answer is not None
                    else "DETERMINISTIC_BUSINESS_TOOL"
                ),
                "route": "workflow",
                "mode": "workflow",
                "llmSkipped": True,
                "llmSkipReason": "deterministic_business_tool",
                "structuredResultFinalized": True,
                "sideEffectAllowed": False,
            },
        )
        return {
            **update,
            "llm_skipped": True,
            "llm_skip_reason": "deterministic_business_tool",
            "structured_result_finalized": True,
        }

    # A deterministic path must never guess missing business parameters. Hand
    # the request to one agent for clarification and preserve that fallback in
    # the trace so workflow-only ablations cannot silently count it as workflow.
    episode_service.record_step(
        "ORCHESTRATION_FALLBACK",
        node_name="deterministic_workflow",
        status="FALLBACK",
        output_data={"from": "workflow", "to": "single_agent", "reason": "missing_args"},
    )
    return {
        "orchestration_mode": "single_agent",
        "orchestration_reason": "workflow_missing_args",
        "route": "agent_loop",
    }


def _rag_rejection_code(
    state: AgentGraphState,
    *,
    query_key: str,
    retrieval_count: int,
    seen_queries: list[str],
) -> str | None:
    if not query_key:
        return "RAG_EMPTY_QUERY"
    if query_key in seen_queries:
        return "RAG_DUPLICATE_QUERY"
    if retrieval_count >= 2:
        return "RAG_RETRIEVAL_LIMIT"
    if state.get("rag_mode") == "prefetch" or not state.get("rag_agentic_allowed"):
        return "RAG_NOT_ALLOWED"
    return None


def _merge_rag_sources(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        identity = str(
            item.get("chunkId")
            or item.get("documentId")
            or item.get("questionId")
            or item.get("id")
            or item.get("source")
            or ""
        )
        version = str(item.get("knowledgeVersion") or item.get("version") or "")
        key = (identity, version)
        if not identity or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _merge_rag_trace(
    existing: dict | None,
    incoming: dict | None,
    *,
    rag_mode: str,
    retrieval_no: int,
    source_count: int,
) -> dict:
    entry = dict(incoming or {})
    entry.update({"retrievalNo": retrieval_no, "phase": "agentic"})
    retrievals = list((existing or {}).get("retrievals") or [])
    if existing and not retrievals:
        retrievals.append(
            {key: value for key, value in existing.items() if key != "retrievals"}
        )
    retrievals.append(entry)
    return {
        **entry,
        "ragMode": rag_mode,
        "hit": any(bool(item.get("hit")) for item in retrievals),
        "sourceCount": source_count,
        "retrievals": retrievals,
    }


def _quarantined_rag_trace(incoming: dict | None, matched_rules: tuple[str, ...]) -> dict:
    """只保留数值型检索元数据和规则名，绝不把污染正文写入 Trace。

    contamination 是安全结构（A2 约定：只含 doc id/source/规则名，无正文），
    保留它才能定位被投毒文档；其余任意字段（可能含原文）一律丢弃。
    """
    raw = incoming or {}
    trace: dict = {
        "hit": False,
        "quarantined": True,
        "quarantineCount": max(int(raw.get("quarantineCount") or 0), 1),
        "matchedRules": list(matched_rules),
    }
    contamination = raw.get("contamination") or []
    if isinstance(contamination, list) and contamination:
        trace["contamination"] = contamination
    for key in ("candidateCount", "latencyMs", "knowledgeVersion"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            trace[key] = value
    return trace


async def _capture_rag_rejection(state: AgentGraphState, code: str) -> None:
    episode_service.record_step(
        "RAG_QUERY_REJECTED",
        node_name="tools",
        status="BLOCKED",
        output_data={
            "code": code,
            "ragMode": state.get("rag_mode"),
            "retrievalCount": state.get("rag_retrieval_count", 0),
        },
    )
    try:
        await badcase_service.add_candidate(
            int(state["message_id"]),
            "RAG_QUERY_REJECTED",
            code,
            run_id=(state.get("agent_msg") or {}).get("runId"),
            source="VERIFIER",
            severity="MEDIUM",
            snapshot={
                "code": code,
                "ragMode": state.get("rag_mode"),
                "retrievalCount": state.get("rag_retrieval_count", 0),
            },
        )
    except Exception as exc:
        logger.warning(
            "rag_rejection_badcase_capture_failed",
            message_id=state["message_id"],
            error=type(exc).__name__,
        )

async def tools_node(state: AgentGraphState) -> dict:
    user_id = state["user_id"]
    message_id = state["message_id"]
    messages = list(state.get("llm_messages") or [])
    called: list[str] = []
    tool_biz = dict(state.get("tool_biz") or {})
    tool_source_refs = list(state.get("tool_source_refs") or [])
    biz_type = state.get("biz_type")
    biz_data = state.get("biz_data")
    assistant_cards = state.get("assistant_cards")
    search_tool_hint = state.get("search_tool_hint")
    rag_mode = str(state.get("rag_mode") or get_settings().rag_mode)
    rag_queries = list(state.get("rag_queries") or [])
    rag_retrieval_count = int(state.get("rag_retrieval_count") or 0)
    rag_source_refs = list(state.get("rag_source_refs") or [])
    rag_trace = dict(state.get("rag_trace") or {}) or None
    rag_evidence_state = str(state.get("rag_evidence_state") or "INSUFFICIENT")
    rag_evidence_items = list(state.get("rag_evidence_items") or [])
    rag_safe_business_query = str(
        state.get("rag_safe_business_query") or state.get("user_text") or ""
    )
    quarantined_result = False
    # OpenAI-compatible providers require every ToolMessage to immediately
    # follow the assistant message that declared its tool_calls. RAG's
    # system-role grounding rule is therefore queued and inserted before that
    # assistant message after all pending calls have their ToolMessages.
    tool_call_message_index = max(0, len(messages) - 1)
    pending_grounding_rules: list[SystemMessage] = []

    for tc in state.get("pending_tool_calls") or []:
        if await rt.is_cancelled(user_id, message_id):
            return {"cancelled": True, "finished": True, "route": "end", "outcome": "cancelled"}
        if (
            tc["name"] in _WRITE_PROPOSAL_TOOLS
            and str(state.get("request_mode") or "")
            in {
                RequestMode.READ_QUERY.value,
                RequestMode.INFORMATIONAL.value,
            }
        ):
            # Model-selected tools are untrusted.  A read/informational turn
            # cannot silently escalate into even a confirmation-stage write
            # proposal.  Preverified exception workflows (for example a
            # strong logistics incident) bypass this model-tool path and keep
            # their separately audited deterministic capability decision.
            episode_service.record_step(
                "TOOL_POLICY_DENIED",
                node_name="tools",
                status="BLOCKED",
                input_data={
                    "tool": tc["name"],
                    "requestMode": state.get("request_mode"),
                },
                output_data={
                    "reason": "WRITE_PROPOSAL_REQUIRES_ACTION_PROPOSAL_MODE",
                    "sideEffectAllowed": False,
                },
            )
            messages.append(
                ToolMessage(
                    content=(
                        "【工具调用被安全策略拒绝】当前请求仅为查询或说明，"
                        "不能生成写操作确认卡。请使用已有事实与知识证据直接回答；"
                        "如用户明确要求执行，再重新进入操作提案流程。"
                    ),
                    tool_call_id=tc["id"],
                )
            )
            continue
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
        tool_args = dict(tc.get("args") or {})
        if tc["name"] == "COMPARE_PRODUCTS" and state.get("comparison_product_ids"):
            tool_args["productIds"] = list(state["comparison_product_ids"])
        if tc["name"] == "SEARCH_KNOWLEDGE":
            query_key = rag_retriever.query_key(str(tool_args.get("query") or ""))
            rejection = _rag_rejection_code(
                state,
                query_key=query_key,
                retrieval_count=rag_retrieval_count,
                seen_queries=rag_queries,
            )
            if rejection:
                await _capture_rag_rejection(
                    {
                        **state,
                        "rag_mode": rag_mode,
                        "rag_retrieval_count": rag_retrieval_count,
                    },
                    rejection,
                )
                messages.append(
                    ToolMessage(
                        content=(
                            "【知识检索被拒绝】"
                            f"{rejection}。请使用已有证据；无证据时保守回答或转人工。"
                        ),
                        tool_call_id=tc["id"],
                    )
                )
                continue
            category_map = get_settings().rag_intent_category_map
            category_filter = category_map.get(str(state.get("intent") or "")) or None
            if category_filter:
                tool_args["_categoryFilter"] = category_filter
        if tc["name"] == "PROPOSE_CREATE_SUPPORT_CASE":
            image_context = dict(state.get("verified_image_context") or {})
            for key in (
                "imageAssetId",
                "imageUnderstanding",
                "imageUnderstandingStatus",
            ):
                tool_args.pop(key, None)
            if image_context:
                tool_args.update(
                    {
                        "imageAssetId": image_context.get("asset_id"),
                        "imageUnderstanding": state.get("image_understanding"),
                        "imageUnderstandingStatus": (
                            "SUCCESS" if state.get("image_understanding") else "NOT_REQUESTED"
                        ),
                    }
                )
            tool_args["sourceMessageId"] = message_id
            tool_args["runId"] = (state.get("agent_msg") or {}).get("runId")

        verified_image_context = None
        source_message_id = None
        if tc["name"] == "SEARCH_PRODUCTS_BY_IMAGE":
            # Tool-call arguments are model controlled. The verified asset and
            # optional selected subject are taken only from the persisted
            # message state created by the upload/selection endpoints.
            image_context = dict(state.get("verified_image_context") or {})
            for key in (
                "imageAssetId",
                "image_asset_id",
                "selectedSubjectId",
                "selected_subject_id",
                "queryText",
                "query_text",
            ):
                tool_args.pop(key, None)
            tool_args["queryText"] = str(state.get("user_text") or "")
            verified_image_context = image_context or None
            source_message_id = message_id

        if state.get("request_id"):
            tool_args.setdefault("requestId", state["request_id"])
        if state.get("run_id"):
            tool_args.setdefault("runId", state["run_id"])

        result = await mcp_tool_router.invoke(
            tc["name"],
            tool_args,
            user_id,
            call_id=tc.get("id"),
            verified_image_context=verified_image_context,
            source_message_id=source_message_id,
        )
        # Legacy integrations and test doubles may not expose the optional
        # structured evidence fields.  Missing fields mean no evidence, not a
        # failed agent turn; production ToolInvokeResult objects expose them.
        result_source_refs = list(getattr(result, "source_refs", None) or [])
        result_retrieval_trace = getattr(result, "retrieval_trace", None)
        result_grounding = getattr(result, "grounding", None)
        called.append(tc["name"])
        # Observation 层：工具结果进上下文前统一脱敏/裁剪/污染扫描，
        # 治理痕迹进 trace（A2：命中注入话术时 contaminated 标记）。
        obs = build_tool_result_observation(result)
        messages.append(ToolMessage(content=obs.text, tool_call_id=tc["id"]))
        if obs.redacted_count or obs.truncated or obs.contaminated:
            episode_service.record_step(
                "TOOL_OBSERVED",
                node_name="tools",
                input_data={"tool": tc["name"]},
                output_data=obs.as_dict(),
            )

        if obs.contaminated:
            # 同一结果里的卡片、业务载荷和来源引用也属于不可信通道，不能因为
            # 绕过 ToolMessage 而直达前端或 Supervisor。
            if tc["name"] == "SEARCH_KNOWLEDGE":
                rag_retrieval_count += 1
                rag_queries.append(query_key)
                # 检疫不是"没检索"：污染痕迹（quarantineCount/contamination 规则名）
                # 同样要进 trace——否则 agentic 路径整包被隔离时无法定位被投毒文档。
                if result_retrieval_trace:
                    safe_trace = _quarantined_rag_trace(
                        result_retrieval_trace,
                        obs.matched_rules,
                    )
                    rag_trace = _merge_rag_trace(
                        rag_trace,
                        safe_trace,
                        rag_mode=rag_mode,
                        retrieval_no=rag_retrieval_count,
                        source_count=0,
                    )
                    episode_service.record_step(
                        "RAG_RETRIEVAL",
                        node_name="tools",
                        input_data={"queryHash": query_key},
                        output_data={
                            "trace": safe_trace,
                            "sourceRefs": [],
                            "hasEvidence": False,
                            "quarantined": True,
                        },
                    )
            quarantined_result = True
            continue

        if tc["name"] == "SEARCH_KNOWLEDGE":
            rag_retrieval_count += 1
            rag_queries.append(query_key)
            rag_source_refs = _merge_rag_sources(
                rag_source_refs, result_source_refs
            )
            rag_trace = _merge_rag_trace(
                rag_trace,
                result_retrieval_trace,
                rag_mode=rag_mode,
                retrieval_no=rag_retrieval_count,
                source_count=len(rag_source_refs),
            )
            grounding = result_grounding or {}
            incoming_state = str(grounding.get("evidenceState") or "INSUFFICIENT")
            if incoming_state == "SUPPORTED":
                rag_evidence_state = "SUPPORTED"
            elif incoming_state == "QUARANTINED" and rag_evidence_state != "SUPPORTED":
                rag_evidence_state = "QUARANTINED"
            grounding_prompt = build_grounding_prompt(
                str((grounding.get("queryPlan") or {}).get("safeBusinessQuery") or ""),
                evidence_state=incoming_state,
                evidence_items=list(grounding.get("evidenceItems") or []),
            )
            # A ToolMessage is untrusted by definition; the shared behavioral
            # contract must still be a true system-role message. It cannot be
            # inserted between an AI tool-call message and its ToolMessages,
            # because OpenAI-compatible providers reject that message order.
            pending_grounding_rules.append(
                grounding_prompt.production_system_messages()[0]
            )
            for item in grounding.get("evidenceItems") or []:
                if not isinstance(item, dict):
                    continue
                identity = str(
                    (item.get("ref") or {}).get("id")
                    or (item.get("ref") or {}).get("chunkId")
                    or (item.get("ref") or {}).get("questionId")
                    or ""
                )
                existing_ids = {
                    str(
                        (row.get("ref") or {}).get("id")
                        or (row.get("ref") or {}).get("chunkId")
                        or (row.get("ref") or {}).get("questionId")
                        or ""
                    )
                    for row in rag_evidence_items
                    if isinstance(row, dict)
                }
                if identity and identity not in existing_ids:
                    rag_evidence_items.append(item)
            rag_safe_business_query = str(
                (grounding.get("queryPlan") or {}).get("safeBusinessQuery")
                or rag_safe_business_query
            )
            episode_service.record_step(
                "RAG_RETRIEVAL",
                node_name="tools",
                input_data={"queryHash": query_key},
                output_data={
                    "trace": result_retrieval_trace,
                    "sourceRefs": result_source_refs,
                    "hasEvidence": bool(result_source_refs),
                },
            )
        elif result_source_refs:
            # Business evidence is carried separately from RAG evidence. This
            # prevents an order/offer snapshot from being mistaken for policy
            # grounding while still making the final HTTP envelope auditable.
            tool_source_refs = _merge_rag_sources(tool_source_refs, result_source_refs)

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
            search_tool_hint = obs.text
        if tc["name"] == "GET_PRODUCT_DETAIL":
            product_id = (tc.get("args") or {}).get("productId") or (tc.get("args") or {}).get("product_id")
            if product_id:
                await product_snapshot_service.ensure_consult_snapshot(user_id, str(product_id))

    if pending_grounding_rules:
        messages[tool_call_message_index:tool_call_message_index] = pending_grounding_rules

    rag_update = {
        "rag_mode": rag_mode,
        "rag_queries": rag_queries,
        "rag_retrieval_count": rag_retrieval_count,
        "rag_source_refs": rag_source_refs,
        "tool_source_refs": tool_source_refs,
        "rag_trace": rag_trace,
        "rag_evidence_state": rag_evidence_state,
        "rag_evidence_items": rag_evidence_items,
        "rag_safe_business_query": rag_safe_business_query,
    }
    if is_order_cards_json(assistant_cards) and "QUERY_ORDERS" in called:
        logger.info("finalize_after_order_cards", user_id=user_id)
        return {
            **rag_update,
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
            **rag_update,
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
    if is_support_case_cards_json(assistant_cards) and "QUERY_SUPPORT_CASES" in called:
        return {
            **rag_update,
            "llm_messages": messages,
            "tools_called": called,
            "pending_tool_calls": [],
            "tool_biz": tool_biz or None,
            "biz_type": (
                "support_case_list"
                if support_case_card_type(assistant_cards) == "SUPPORT_CASE_LIST"
                else "support_case_detail"
            ),
            "biz_data": biz_data,
            "assistant_cards": assistant_cards,
            "search_tool_hint": search_tool_hint,
            "chunks": [],
            "route": "finalize",
        }

    next_route = (
        "agent_loop"
        if state.get("react_round", 0) < get_settings().graph_max_react_rounds
        else "finalize"
    )
    update = {
        **rag_update,
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
    if next_route == "finalize" and quarantined_result:
        update["chunks"] = [CONTAMINATED_CONTENT_PLACEHOLDER]
    return update

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

        verifier_fallback = state.get("verifier_fallback")
        # A verified order snapshot or a server-built action card already owns
        # the answer. Preparing a generic RAG fallback here is misleading when
        # it will never be applied, and makes trace readers think policy RAG
        # participated in a dynamic-business response.
        business_refs = list(state.get("tool_source_refs") or [])
        has_dynamic_business_authority = (
            str(state.get("order_resolution") or "").upper()
            in {"RESOLVED", "NO_ELIGIBLE"}
            and has_dynamic_order_authority(business_refs)
        ) or has_trusted_capability_decision(business_refs)
        # A card's mere presence is not authority.  Order selection fields and
        # action cards are accepted only through their field-level snapshot or
        # durable pending-action verification in ``finalize_agent_response``.
        if (
            not verifier_fallback
            and not has_dynamic_business_authority
            and not state.get("deterministic_clarification")
            and state.get("rag_evidence_required")
            and str(state.get("rag_evidence_state") or "").upper() == "SUPPORTED"
        ):
            fallback = deterministic_policy_evidence_fallback(
                str(state.get("user_text") or ""),
                intent=str(state.get("intent") or ""),
                evidence_state=str(state.get("rag_evidence_state") or "INSUFFICIENT"),
                source_refs=list(state.get("rag_source_refs") or []),
            )
            if fallback:
                verifier_fallback = str(fallback["answer"])
                episode_service.record_step(
                    "RAG_POLICY_FALLBACK_PREPARED",
                    node_name="finalize",
                    status="OK",
                    input_data={
                        "intent": str(state.get("intent") or ""),
                        "evidenceState": str(state.get("rag_evidence_state") or ""),
                        "sourceId": fallback.get("sourceId"),
                    },
                    output_data={
                        "event": fallback.get("event"),
                        "citation": fallback.get("citation"),
                        "factId": fallback.get("factId"),
                    },
                )

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
            # Keep one backwards-compatible envelope for persistence/UI, while
            # making the evidence domain explicit.  The verifier consumes only
            # ragSources for policy citations; business snapshots remain
            # auditable but cannot launder an unsupported policy claim.
            source_refs={
                "trace": state.get("rag_trace"),
                "ragSources": list(state.get("rag_source_refs") or []),
                "businessSources": list(state.get("tool_source_refs") or []),
                "sources": [
                    *(state.get("rag_source_refs") or []),
                    *(state.get("tool_source_refs") or []),
                ],
            },
            user_text=state.get("user_text"),
            consult_card=state.get("card"),
            message_card=state.get("message_card"),
            order_resolution=state.get("order_resolution"),
            rag_evidence_required=bool(state.get("rag_evidence_required")),
            rag_evidence_state=str(state.get("rag_evidence_state") or "INSUFFICIENT"),
            deterministic_clarification=bool(
                state.get("deterministic_clarification")
            ),
            deterministic_consult_resolution=str(
                state.get("llm_skip_reason") or ""
            )
            in {
                "bounded_display_technology_explanation",
                "named_product_comparison",
            },
            verifier_fallback=verifier_fallback,
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
            assistant_cards=state.get("assistant_cards"),
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
