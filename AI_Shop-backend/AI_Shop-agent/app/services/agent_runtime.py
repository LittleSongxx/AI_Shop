from __future__ import annotations

import asyncio
import json
import re
import time
from functools import lru_cache

import structlog
from opentelemetry import context as otel_context
from opentelemetry import trace

from app.config.settings import get_settings
from app.domain.intent.rules import wants_order_list_cards
from app.harness.guardrails.output_guard import strip_emojis
from app.harness.guardrails.product_text_guard import (
    should_force_product_cards,
    text_promises_product_cards,
)
from app.harness.metrics.runtime_sensors import (
    LLM_LATENCY,
    RESPONSE_VERIFIER_TOTAL,
    STREAM_CHARS,
    STREAM_TOKENS,
    observe_agent_stage,
)
from app.harness.observation import build_tool_result_observation
from app.mcp.tools import build_mcp_tools
from app.observability.llm_metrics import (
    record_llm_failure,
    record_llm_usage,
    resolve_llm_model,
)
from app.observability.telemetry import get_tracer
from app.services.badcase_service import badcase_service
from app.services.episode_service import episode_service
from app.services.judge_service import judge_service
from app.services.llm_factory import ChatLLMConfig, chat_llm_config, chat_llm_for_config
from app.services.mcp_tool_router import mcp_tool_router
from app.services.message_service import agent_message_service
from app.services.pending_action_service import pending_action_service
from app.services.redis_service import redis_service
from app.services.response_verifier import response_verifier
from app.services.shopping_profile_service import shopping_profile_service
from app.services.stream_service import stream_service
from app.utils.biz_payload import (
    build_action_confirm_payload,
    build_action_confirm_unavailable_payload,
    build_product_search_message,
    collect_act_token_ids,
    compact_product_search_intro,
    is_action_confirm_json,
    is_order_cards_json,
    is_order_selection_json,
    is_product_cards_json,
    is_support_case_cards_json,
    looks_like_aftersales_or_order_text,
    strip_embedded_product_json,
    support_case_card_type,
    trim_assistant,
)
from app.utils.product_consult import (
    is_product_consult_turn,
    parse_consult_card,
    product_consult_clarification,
)

logger = structlog.get_logger()
tracer = get_tracer()

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
) -> tuple[str, str, str, dict] | None:
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
            return assistant, biz_data, "action_confirm", candidate
        pending = candidate
        token_id = tid
        break

    if pending and token_id:
        assistant, biz_data = build_action_confirm_payload(pending, full_text)
        return assistant, biz_data, "action_confirm", pending

    # Token present but Redis has no pending — usually LLM invented 【act_xxx】
    # without calling PROPOSE_*. Never render a pre-expired confirm card.
    logger.warning(
        "action_confirm_token_missing",
        user_id=user_id,
        tokens=token_ids,
    )
    return None


async def resolve_server_action_card(
    assistant_cards: str | None,
    user_id: str,
) -> tuple[str, str, str, dict] | None:
    """Resolve a server-produced ACTION_CONFIRM card without model token echo.

    The card itself is only a reference.  We accept it after validating the
    shape of its action token and reloading the authoritative pending row for
    the authenticated user, then rebuild the payload from that row.  A model
    fabricated card or a cross-user token therefore remains fail-closed.
    """
    if not is_action_confirm_json(assistant_cards):
        return None
    try:
        card = json.loads(str(assistant_cards).strip())
    except (TypeError, json.JSONDecodeError):
        return None
    token = str(
        card.get("actionToken") or card.get("token") or ""
    ).strip()
    if not re.fullmatch(r"act_[a-f0-9]{32}", token, flags=re.IGNORECASE):
        return None
    pending = await pending_action_service.get_by_token(token)
    if not pending:
        return None
    if str(pending.get("userId") or "") != str(user_id or ""):
        logger.warning("action_confirm_card_wrong_user", user_id=user_id)
        return None
    pending_action_type = str(pending.get("actionType") or "").upper()
    card_action_type = str(card.get("actionType") or "").upper()
    if card_action_type and card_action_type != pending_action_type:
        logger.warning(
            "action_confirm_card_action_type_mismatch",
            user_id=user_id,
            token_shape=token[:8],
        )
        return None
    assistant, biz_data = build_action_confirm_payload(pending)
    return assistant, biz_data, "action_confirm", pending

async def stream_llm_turn(
    llm,
    messages: list,
    user_id: str,
    message_id: int,
    user_message: str | None,
    chunks: list[str],
    *,
    fallback: bool = False,
    model: str | None = None,
    timeout_seconds: float | None = None,
):
    started = time.perf_counter()
    resolved_model = resolve_llm_model(llm, model)
    effective_timeout = (
        get_settings().agent_llm_call_deadline_seconds
        if timeout_seconds is None
        else float(timeout_seconds)
    )
    if effective_timeout <= 0:
        raise ValueError("LLM stream timeout_seconds must be positive")
    gathered = None
    sent_visible = ""
    first_token_observed = False
    usage: dict = {}
    episode_status = "OK"
    episode_error: Exception | None = None
    span = tracer.start_span("agent.llm.stream")
    context_token = otel_context.attach(trace.set_span_in_context(span))
    span.set_attribute("gen_ai.request.model", resolved_model)
    span.set_attribute("agent.llm.fallback", bool(fallback))
    try:
        async with asyncio.timeout(effective_timeout):
            async for chunk in llm.astream(messages):
                if await is_cancelled(user_id, message_id):
                    episode_status = "CANCELLED"
                    record_llm_failure(resolved_model, fallback=fallback)
                    return None
                gathered = chunk if gathered is None else gathered + chunk
                visible = strip_embedded_product_json(
                    strip_emojis(chunk_text(gathered.content))
                )
                delta = visible[len(sent_visible) :]
                sent_visible = visible
                if not delta:
                    continue
                if not first_token_observed:
                    observe_agent_stage("first_token", time.perf_counter() - started)
                    episode_service.observe_first_token()
                    first_token_observed = True
                chunks.append(delta)
                # 字符数（真实口径）；旧指标同步累计保持面板兼容。
                STREAM_CHARS.inc(len(delta))
                STREAM_TOKENS.inc(len(delta))
                await stream_service.push_chunk(
                    user_id, message_id, delta, user_message
                )
        if await is_cancelled(user_id, message_id):
            episode_status = "CANCELLED"
            record_llm_failure(resolved_model, fallback=fallback)
            return None
        if gathered is None:
            raise RuntimeError("LLM stream ended without a response")
        usage = record_llm_usage(
            gathered, fallback=fallback, model=resolved_model
        )
        return gathered
    except asyncio.CancelledError:
        episode_status = "CANCELLED"
        record_llm_failure(resolved_model, fallback=fallback)
        raise
    except Exception as exc:
        episode_status = "ERROR"
        episode_error = exc
        span.record_exception(exc)
        raise
    finally:
        elapsed = max(0.0, time.perf_counter() - started)
        LLM_LATENCY.observe(elapsed)
        observe_agent_stage("generation", elapsed)
        usage_evidence = dict(usage)
        if not usage_evidence:
            usage_evidence = {
                "providerCalls": 1,
                "pricedCalls": 0,
                "unpricedCalls": 0,
                "missingUsageCalls": 1,
                "costCny": None,
                "costStatus": "MISSING_USAGE",
                "usageReported": False,
                "usageSource": "none",
                "missingReason": (
                    "cancelled_before_usage"
                    if episode_status == "CANCELLED"
                    else "call_deadline_exceeded_before_usage"
                    if isinstance(episode_error, TimeoutError)
                    else "provider_error_before_usage"
                    if episode_status == "ERROR"
                    else "provider_omitted_usage"
                ),
            }
        episode_service.record_step(
            "LLM_CALL",
            node_name="llm",
            status=episode_status,
            input_data={
                "messages": messages,
                "fallback": fallback,
                "stream": True,
                "hardDeadlineSeconds": effective_timeout,
            },
            output_data={
                **usage_evidence,
                "visibleChars": len(sent_visible),
                "hasToolCalls": bool(getattr(gathered, "tool_calls", None)),
            },
            model_name=resolved_model,
            error_code=type(episode_error).__name__ if episode_error else None,
            error_message=str(episode_error) if episode_error else None,
            latency_ms=round(elapsed * 1_000),
        )
        otel_context.detach(context_token)
        span.end()

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
    "QUERY_REFUND_STATUS",
    "QUERY_USER_COUPONS",
    "PROPOSE_REFUND",
    "PROPOSE_CANCEL_ORDER",
    "PROPOSE_CONFIRM_RECEIPT",
    "PROPOSE_CREATE_SUPPORT_CASE",
    "QUERY_SUPPORT_CASES",
    "PROPOSE_PRODUCT_REVIEW",
    "PROPOSE_RECOMMENT",
    "GET_PRODUCT_DETAIL",
})

_WRITE_PROPOSE_TOOLS = frozenset({
    "PROPOSE_REFUND",
    "PROPOSE_CANCEL_ORDER",
    "PROPOSE_CONFIRM_RECEIPT",
    "PROPOSE_CREATE_SUPPORT_CASE",
    "PROPOSE_PRODUCT_REVIEW",
    "PROPOSE_RECOMMENT",
})


def _source_channels(
    source_refs: list[dict] | dict | None,
) -> tuple[list[dict], list[dict]]:
    """Return explicit RAG and business evidence channels.

    The v3 envelope carries both channels.  ``sources`` and a bare list are
    retained as a legacy shape and treated as RAG-compatible only for callers
    that have not migrated yet; production graph paths always pass the
    explicit fields below.
    """

    if isinstance(source_refs, dict):
        has_explicit = "ragSources" in source_refs or "businessSources" in source_refs
        if has_explicit:
            rag = [
                item
                for item in source_refs.get("ragSources") or []
                if isinstance(item, dict)
            ]
            business = [
                item
                for item in source_refs.get("businessSources") or []
                if isinstance(item, dict)
            ]
            return rag, business
        legacy = [
            item for item in source_refs.get("sources") or [] if isinstance(item, dict)
        ]
        return legacy, []
    if isinstance(source_refs, list):
        return [item for item in source_refs if isinstance(item, dict)], []
    return [], []


def _consult_cards_match(
    cards_json: str | None,
    consult_card: dict | None,
) -> bool:
    """Allow product cards in a consult turn only for the selected product."""

    selected_id = str((consult_card or {}).get("productId") or "").strip()
    if not selected_id or not cards_json or not is_product_cards_json(cards_json):
        return False
    try:
        payload = json.loads(cards_json)
    except (TypeError, json.JSONDecodeError):
        return False
    if isinstance(payload, dict):
        payload = payload.get("products")
    if not isinstance(payload, list):
        return False
    ids = {
        str(item.get("productId") or item.get("product_id") or "").strip()
        for item in payload
        if isinstance(item, dict)
    }
    return selected_id in ids

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
    order_resolution: str | None = None,
    rag_evidence_required: bool = False,
    rag_evidence_state: str = "INSUFFICIENT",
    verifier_fallback: str | None = None,
) -> None:
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    full_text = "".join(chunks)
    route_intent = str(
        (agent_msg.get("intentDecision") or {}).get("intent")
        or agent_msg.get("intent")
        or ""
    ).upper()
    is_consult_turn = is_product_consult_turn(
        user_text,
        message_card,
        consult_card,
        from_product=bool(agent_msg.get("fromProduct")),
    ) or route_intent == "PRODUCT_CONSULT"
    called = list(tools_called or [])

    resolved = await resolve_action_confirm(full_text, messages, user_id)
    if resolved is None:
        # Structured tool results may reach this function without the model
        # echoing the credential.  Re-validate the server card before using it.
        resolved = await resolve_server_action_card(assistant_cards, user_id)
    pending_for_verifier: dict | None = None
    clarification_applied = False
    if resolved:
        assistant, biz_data, biz_type, pending_for_verifier = resolved
    elif is_order_selection_json(assistant_cards):
        assistant = assistant_cards or ""
        biz_type = "order_selection"
    elif is_support_case_cards_json(assistant_cards):
        assistant = assistant_cards or "{}"
        biz_type = (
            "support_case_list"
            if support_case_card_type(assistant_cards) == "SUPPORT_CASE_LIST"
            else "support_case_detail"
        )
    else:
        # Drop fabricated act tokens so they never surface as "expired" cards.
        full_text = re.sub(r"【act_[a-f0-9]{32}】", "", full_text or "", flags=re.I).strip()
        full_text = re.sub(r"act_[a-f0-9]{32}", "", full_text, flags=re.I).strip()
        # LLM sometimes dumps a fake ACTION_CONFIRM JSON — never render it.
        if is_action_confirm_json(full_text) or is_action_confirm_json(assistant_cards):
            logger.warning("fabricated_action_confirm_json_dropped", user_id=user_id)
            full_text = "操作确认卡片无效，请重新说明退款/评价/收货需求后重试。"
            assistant_cards = None

        wants_orders = "QUERY_ORDERS" in called or wants_order_list_cards(user_text)
        recovered_order_cards = _recover_order_cards(assistant_cards, called) if wants_orders else None
        if wants_orders and (
            is_order_cards_json(recovered_order_cards)
            or (recovered_order_cards or "").strip() == "[]"
        ):
            assistant = recovered_order_cards or "[]"
            biz_type = "query_order"
        elif any(t in _WRITE_PROPOSE_TOOLS for t in called):
            # PROPOSE ran but Redis token missing / invalid — do not fall into product UI.
            assistant = trim_assistant(full_text) or (
                "未能生成有效确认卡片。请确认订单号/订单项、评价星级与内容后重试。"
            )
            if is_action_confirm_json(assistant):
                assistant = "未能生成有效确认卡片。请确认订单号/订单项、评价星级与内容后重试。"
            biz_type = biz_type or "agent"
        elif wants_orders:
            # Always render order cards — never leave LLM markdown/HTML tables as the UI.
            cards = recovered_order_cards
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
                if cards_json and is_consult_turn and not _consult_cards_match(
                    cards_json, consult_card or message_card
                ):
                    # A model/tool may widen a property question into a fresh
                    # shelf search.  Showing those cards is worse than asking
                    # for the missing product identity: it creates a concrete
                    # but unrelated recommendation and makes dynamic facts
                    # impossible to audit.  Keep the selected-product path
                    # available when the returned card contains its ID.
                    logger.warning(
                        "consult_product_card_mismatch_blocked",
                        user_id=user_id,
                        selected_product_id=str(
                            (consult_card or message_card or {}).get("productId") or ""
                        ),
                    )
                    cards_json = None
                    forced_biz = None
                    search_tool_hint = None
                    assistant_cards = None
                    full_text = (
                        "请提供具体商品名称、型号或商品卡片，我才能核对该商品的规格与兼容性。"
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
                        observation = build_tool_result_observation(result)
                        if observation.contaminated:
                            cards_json = None
                            forced_biz = "product_search"
                            search_tool_hint = None
                        else:
                            cards_json = result.assistant_cards
                            forced_biz = result.biz_type or "product_search"
                            search_tool_hint = observation.text
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

    # A PRODUCT_CONSULT request without an authoritative product/card cannot
    # be answered by broad retrieval.  Replace a refusal or accidental generic
    # text with a field-specific identity question; this keeps the next turn
    # actionable and avoids unrelated recommendations.
    if (
        is_consult_turn
        and not (consult_card or message_card)
        and not any(tool in called for tool in ("GET_PRODUCT_DETAIL", "COMPARE_PRODUCTS"))
    ):
        assistant = product_consult_clarification(user_text)
        clarification_applied = True
        assistant_cards = None
        biz_type = "agent"
        episode_service.record_step(
            "PRODUCT_CONSULT_CLARIFICATION",
            node_name="finalize",
            status="OK",
            input_data={"intent": route_intent},
            output_data={"reason": "missing_authoritative_product_identity"},
        )

    assistant = _strip_emojis_from_assistant(assistant)

    recommendation_constraints: dict | None = None
    recommendation_candidates: list[dict] | None = None
    if "SEARCH_PRODUCTS" in called and is_product_cards_json(assistant_cards):
        try:
            parsed_candidates = json.loads(assistant_cards or "[]")
            effective_profile = await shopping_profile_service.get_effective_profile(
                user_id
            )
            recommendation_constraints = {
                "budgetMin": effective_profile.get("budgetMin"),
                "budgetMax": effective_profile.get("budgetMax"),
                "excludedBrands": effective_profile.get("excludedBrands") or [],
                "requiredBrands": (
                    effective_profile.get("brands") or []
                    if effective_profile.get("acceptSubstitute") is False
                    else []
                ),
            }
            recommendation_candidates = (
                parsed_candidates if isinstance(parsed_candidates, list) else None
            )
        except Exception as exc:
            logger.warning(
                "recommendation_verifier_context_failed",
                user_id=user_id,
                error=type(exc).__name__,
            )

    support_case_for_verifier = None
    if (
        pending_for_verifier
        and pending_for_verifier.get("actionType") == "CREATE_SUPPORT_CASE"
    ):
        try:
            support_case_for_verifier = json.loads(
                pending_for_verifier.get("paramsJson") or "{}"
            )
        except (TypeError, json.JSONDecodeError):
            support_case_for_verifier = {}

    rag_sources, business_sources = _source_channels(source_refs)
    rag_supported = str(rag_evidence_state).upper() == "SUPPORTED" and bool(rag_sources)
    dynamic_authority = (
        str(order_resolution or "").upper() in {"RESOLVED", "NO_ELIGIBLE"}
        and bool(business_sources)
        and (
            biz_type
            in {
                "query_order",
                "query_logistics",
                "query_comment",
                "query_coupon",
                "support_case_list",
                "support_case_detail",
                "action_confirm",
            }
            # NO_ELIGIBLE is emitted as a plain explanatory answer by the
            # resolver. Its business snapshot is still authoritative even
            # though no specialist biz card is rendered.
            or str(order_resolution or "").upper() == "NO_ELIGIBLE"
        )
    )
    # A Java order snapshot is sufficient for a dynamic status/capability
    # response.  It must not be forced through the public-policy citation gate;
    # unsupported policy claims are still rejected independently by the
    # verifier's claim regex below.
    policy_gate_required = (
        rag_evidence_required and not clarification_applied and not dynamic_authority
    )
    rag_gate_required = policy_gate_required and rag_supported
    verification = response_verifier.verify(
        assistant=assistant,
        biz_type=biz_type,
        tools_called=called,
        source_refs=source_refs,
        has_pending_action=resolved is not None,
        order_resolution=order_resolution,
        recommendation_constraints=recommendation_constraints,
        recommendation_candidates=recommendation_candidates,
        support_case=support_case_for_verifier,
        # A missing product identity is handled by a deterministic
        # clarification, not a policy answer.  Do not let the generic RAG
        # evidence gate replace that safe next-step question with a refusal.
        policy_evidence_required=policy_gate_required,
        rag_citation_required=rag_gate_required,
        rag_evidence_state=rag_evidence_state,
        rag_source_refs=rag_sources,
        safe_fallback=verifier_fallback,
    )
    verifier_fallback_applied = bool(
        not verification.passed and verification.fallback_verified
    )
    RESPONSE_VERIFIER_TOTAL.labels(
        result="pass" if verification.passed else verification.action.lower(),
        rule=verification.issues[0].code if verification.issues else "NONE",
    ).inc()
    episode_service.update_run(
        quality=verification.quality(),
        reward_signals={
            "verifier": {
                "passed": verification.passed,
                "action": verification.action,
                "issueCodes": [issue.code for issue in verification.issues],
            }
        },
    )
    episode_service.record_step(
        "RESPONSE_VERIFIER",
        node_name="finalize",
        status="OK" if verification.passed else "BLOCKED",
        input_data={
            "bizType": biz_type,
            "toolsCalled": called,
            "hasPendingAction": resolved is not None,
            "hasSources": bool(rag_sources or business_sources),
            "ragSourceCount": len(rag_sources),
            "businessSourceCount": len(business_sources),
        },
        output_data={
            **verification.quality(),
            "safeFallbackApplied": verifier_fallback_applied,
            "clarificationApplied": clarification_applied,
        },
    )
    if not verification.passed:
        assistant = verification.assistant
        biz_type = "agent"
        biz_data = None
        try:
            primary_issue = verification.issues[0]
            await badcase_service.add_candidate(
                int(message_id),
                (
                    "RAG_NO_EVIDENCE"
                    if primary_issue.code == "POLICY_WITHOUT_CITATION"
                    else "VERIFIER_FAILURE"
                ),
                primary_issue.detail,
                run_id=agent_msg.get("runId"),
                source="VERIFIER",
                severity=primary_issue.severity,
                snapshot={
                    "action": verification.action,
                    "issues": [issue.public() for issue in verification.issues],
                },
            )
        except Exception as exc:
            logger.warning(
                "verifier_badcase_capture_failed",
                message_id=message_id,
                error=type(exc).__name__,
            )

    await agent_message_service.complete_message(
        message_id,
        assistant,
        biz_type,
        biz_data,
        source_refs,
    )
    await stream_service.push_done(
        user_id,
        message_id,
        assistant,
        biz_type,
        agent_msg.get("userMessage"),
        source_refs,
    )
    judge_service.enqueue(
        run_id=agent_msg.get("runId"),
        message_id=int(message_id),
        user_text=user_text,
        assistant=assistant,
        intent=(agent_msg.get("intentDecision") or {}).get("intent")
        or agent_msg.get("intent"),
        tools_called=called,
        source_refs=source_refs,
        verifier_passed=verification.passed,
    )

async def push_chat_error(agent_msg: dict, prompt_type: str, partial: str = "") -> None:
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    await agent_message_service.complete_message(
        message_id, partial or "服务异常", prompt_type, None
    )
    await stream_service.push_error(user_id, message_id, "服务暂时不可用，请稍后重试", prompt_type)


async def push_budget_error(agent_msg: dict) -> None:
    """Persist a controlled terminal response when a run reaches its safety cap."""
    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    message = "本次请求已达到安全执行上限，请缩小问题范围后重试。"
    await agent_message_service.complete_message(
        message_id, message, "agent_budget", None
    )
    await stream_service.push_error(user_id, message_id, message, "agent_budget")


@lru_cache(maxsize=32)
def _agent_llm_with_tools(
    config: ChatLLMConfig,
    tool_scope: tuple[str, ...] | None,
):
    allowed = None if tool_scope is None else set(tool_scope)
    return chat_llm_for_config(config).bind_tools(build_mcp_tools(allowed))


def bind_agent_llm(
    *,
    fallback: bool = False,
    allowed_tools: set[str] | frozenset[str] | None = None,
    max_tokens: int | None = None,
    disable_thinking: bool = False,
    tools_enabled: bool = True,
):
    """The tool-bound LLM for one ReAct round.

    Both halves of this are pure functions of the settings: building the tool list
    and converting every args_schema to an OpenAI function schema costs the same
    work on round 1 and round 6. Keying on the resolved config means a settings
    change still produces a fresh binding.
    """
    config = chat_llm_config(
        fallback=fallback, disable_thinking=disable_thinking
    )
    if tools_enabled:
        scope = None if allowed_tools is None else tuple(sorted(allowed_tools))
        llm = _agent_llm_with_tools(config, scope)
    else:
        llm = chat_llm_for_config(config)
    return llm.bind(max_tokens=max_tokens) if max_tokens is not None else llm

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
