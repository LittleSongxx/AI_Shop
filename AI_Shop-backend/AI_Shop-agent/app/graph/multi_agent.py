"""True multi-agent fan-out for the customer graph.

The legacy graph remains in ``nodes.py``. This module deliberately does not
reuse its user-facing ReAct state: workers receive a typed task and return one
validated artifact to the root Supervisor.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Send

from app.config.settings import get_settings
from app.domain.intent.classifier import classify_request_mode
from app.domain.intent.types import IntentKind, RequestMode
from app.domain.intent.write_args import extract_review_content, extract_review_star
from app.harness.agents.contracts import (
    ActionProposal,
    AgentArtifact,
    SpecialistTask,
    SupervisorPlan,
)
from app.harness.agents.registry import AGENT_SPECS
from app.harness.guardrails.output_guard import strip_emojis
from app.harness.observation import build_tool_result_observation
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.rag.query_rewriter import normalize_policy_query
from app.services import agent_runtime as rt
from app.services.episode_service import bind_episode, episode_service
from app.services.llm_factory import create_memory_llm
from app.services.mcp_tool_router import mcp_tool_router
from app.services.shopping_mission_service import (
    mission_summary,
    shopping_mission_service,
)
from app.utils.biz_payload import (
    is_action_confirm_json,
    is_order_cards_json,
    is_product_cards_json,
    is_support_case_cards_json,
    is_visual_subject_selection_json,
)

_ACTION_TO_TOOL = {
    IntentKind.REFUND.value: "PROPOSE_REFUND",
    IntentKind.CANCEL_ORDER.value: "PROPOSE_CANCEL_ORDER",
    IntentKind.CONFIRM_RECEIPT.value: "PROPOSE_CONFIRM_RECEIPT",
    IntentKind.PRODUCT_REVIEW.value: "PROPOSE_PRODUCT_REVIEW",
    IntentKind.RECOMMENT.value: "PROPOSE_RECOMMENT",
    IntentKind.ADDRESS_CHANGE.value: "PROPOSE_CREATE_SUPPORT_CASE",
    IntentKind.INVOICE.value: "PROPOSE_CREATE_SUPPORT_CASE",
    IntentKind.DAMAGED_OR_WRONG_ITEM.value: "PROPOSE_CREATE_SUPPORT_CASE",
    IntentKind.AFTERSALES_UNKNOWN.value: "PROPOSE_CREATE_SUPPORT_CASE",
    IntentKind.COMPLAINT.value: "PROPOSE_CREATE_SUPPORT_CASE",
    IntentKind.PAYMENT_ISSUE.value: "PROPOSE_CREATE_SUPPORT_CASE",
}
_ACTION_INTENTS = frozenset(_ACTION_TO_TOOL)
_ORDER_READ_INTENTS = frozenset(
    {
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
        IntentKind.QUERY_COUPON.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.REFUND_STATUS.value,
    }
)
_ORDER_FACT_MARKERS = (
    "我的订单",
    "这个订单",
    "那个订单",
    "这单",
    "那单",
    "订单号",
    "物流",
    "快递",
    "包裹",
    "发货",
    "延迟",
    "退款进度",
    "退款状态",
    "我的优惠券",
    "有哪些优惠券",
    "可用券",
    "我的评价",
)
_POLICY_MARKERS = (
    "政策",
    "规则",
    "条件",
    "流程",
    "怎么申请",
    "如何申请",
    "能不能退",
    "能否退",
    "可以退吗",
    "能退吗",
    "无理由",
    "运费",
    "保修",
    "售后",
    "发票",
    "破损",
    "损坏",
    "坏了",
    "错发",
    "漏发",
    "投诉",
    "取消订单",
    "支付异常",
)
_ORDER_BOUND_ACTION_INTENTS = frozenset(
    {
        IntentKind.REFUND.value,
        IntentKind.CANCEL_ORDER.value,
        IntentKind.CONFIRM_RECEIPT.value,
        IntentKind.PRODUCT_REVIEW.value,
        IntentKind.RECOMMENT.value,
        IntentKind.ADDRESS_CHANGE.value,
        IntentKind.INVOICE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.AFTERSALES_UNKNOWN.value,
    }
)

_OFFER_TIME_CLAIM_RE = re.compile(
    r"报价(?:有效期|截止时间?)\s*(?:至|到|为|是)?\s*\*{0,2}"
    r"20\d{2}(?:-|年)\d{1,2}(?:-|月)\d{1,2}日?"
    r"(?:[T\s]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?"
    r"\*{0,2}"
)
_ORDER_CONTEXT_FIELDS = (
    "targetType",
    "targetId",
    "orderId",
    "orderItemId",
    "productId",
    "productName",
    "propertyInfo",
    "amount",
    "orderStatus",
    "orderStatusName",
    "orderTime",
    "orderItemStatus",
    "commentStatus",
)
_PRODUCT_CONTEXT_FIELDS = ("productId", "productName", "categoryName", "price")
_EMAIL_PATTERN = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")
_MOBILE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_LANDLINE_PATTERN = re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{7,8}(?!\d)")
_ID_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_ADDRESS_PATTERN = re.compile(
    r"(?P<label>(?:收货|配送|送货)?地址|住址)\s*(?:是|为|[:：])?\s*"
    r"[^，。；;!?！？\n]{2,100}"
)
_ACTION_TOKEN_PATTERN = re.compile(r"(?i)(?:【)?act_[a-z0-9_-]{8,}(?:】)?")
_TRUSTED_EVIDENCE_TYPES = frozenset(
    {
        "order",
        "order_item",
        "logistics",
        "refund",
        "comment",
        "coupon",
        "support_case",
        "product",
        "product_detail",
        "visual_product",
        "knowledge",
        "knowledge_chunk",
        "faq",
        "rag",
        "policy",
    }
)
_EVIDENCE_ID_KEYS = (
    "id",
    "orderId",
    "orderItemId",
    "productId",
    "caseId",
    "documentId",
    "chunkId",
    "questionId",
    "knowledgeVersion",
    "decisionId",
    "policyId",
)
_TOOL_EVIDENCE_TYPES = {
    "SEARCH_PRODUCTS": frozenset({"product", "product_detail"}),
    "GET_PRODUCT_DETAIL": frozenset({"product", "product_detail"}),
    "COMPARE_PRODUCTS": frozenset({"product", "product_detail"}),
    "SEARCH_PRODUCTS_BY_IMAGE": frozenset(
        {"product", "product_detail", "visual_product"}
    ),
    "QUERY_ORDERS": frozenset({"order", "order_item"}),
    "QUERY_LOGISTICS": frozenset({"logistics", "order"}),
    "QUERY_COMMENT": frozenset({"comment", "order"}),
    "QUERY_REFUND_STATUS": frozenset({"refund", "order", "order_item"}),
    "QUERY_USER_COUPONS": frozenset({"coupon"}),
    "CHECK_AFTER_SALES_ELIGIBILITY": frozenset({"policy", "order", "order_item"}),
    "QUERY_SUPPORT_CASES": frozenset({"support_case"}),
    "SEARCH_KNOWLEDGE": frozenset({"knowledge", "knowledge_chunk", "faq", "rag", "policy"}),
}
_SUPPORT_CASE_CARD_TERMS = (
    "工单",
    "售后申请",
    "客服记录",
    "人工客服",
    "投诉进度",
)
_TOOL_PROTOCOL_MARKERS = (
    "<｜｜DSML｜｜tool_calls>",
    "<tool_call>",
    '"tool_calls":',
)
_POLICY_FALLBACK_LINE_RE = re.compile(
    r"政策|平台规则|售后规则|(?:退款|退货|换货)(?:条件|资格)|"
    r"(?:可以|能够|不能|不可|支持|不支持).{0,10}(?:退款|退货|换货)|"
    r"(?:符合|满足|具备|不符合|不满足).{0,10}(?:退款|退货|换货)"
)


def _contains(text: str, *terms: str) -> bool:
    return any(term in text for term in terms)


def _contains_tool_protocol(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in text for marker in _TOOL_PROTOCOL_MARKERS) or (
        "dsml" in lowered and ("tool_calls" in lowered or "invoke" in lowered)
    )


def _is_trusted_evidence_ref(item: dict[str, Any]) -> bool:
    """Accept only server-shaped refs, never arbitrary model dictionaries."""
    evidence_type = str(item.get("type") or "").strip().lower()
    if evidence_type not in _TRUSTED_EVIDENCE_TYPES:
        return False
    return any(item.get(key) not in (None, "", []) for key in _EVIDENCE_ID_KEYS)


def _evidence_type_matches_tool(item: dict[str, Any], tool_name: str) -> bool:
    evidence_type = str(item.get("type") or "").strip().lower()
    return evidence_type in _TOOL_EVIDENCE_TYPES.get(tool_name, frozenset())


def _has_verified_evidence(evidence: list[dict[str, Any]]) -> bool:
    tool_results = [item for item in evidence if item.get("type") == "tool_result"]
    if any(item.get("success") is True for item in tool_results):
        return True
    # A failed tool result invalidates the unproven refs that may have been
    # placed after it by a model or a malformed integration response.
    return False


def _has_policy_evidence(evidence: list[dict[str, Any]]) -> bool:
    policy_refs = [
        item
        for item in evidence
        if _is_trusted_evidence_ref(item)
        and str(item.get("type") or "").strip().lower()
        in {"knowledge", "knowledge_chunk", "faq", "rag", "policy"}
    ]
    if not policy_refs:
        return False
    knowledge_results = [
        item
        for item in evidence
        if item.get("type") == "tool_result" and str(item.get("tool") or "") == "SEARCH_KNOWLEDGE"
    ]
    # If the specialist used the knowledge tool, a policy ref is valid only
    # when that specific call succeeded. A successful order lookup must not
    # launder a failed policy lookup into a policy conclusion.
    return any(item.get("success") is True for item in knowledge_results)


def _has_eligibility_evidence(evidence: list[dict[str, Any]]) -> bool:
    """Require both the rule-tool success and its server-issued decision ref."""
    tool_succeeded = any(
        item.get("type") == "tool_result"
        and str(item.get("tool") or "") == "CHECK_AFTER_SALES_ELIGIBILITY"
        and item.get("success") is True
        for item in evidence
    )
    decision_ref = any(
        _is_trusted_evidence_ref(item)
        and str(item.get("type") or "").lower() == "policy"
        and bool(item.get("decisionId"))
        for item in evidence
    )
    return tool_succeeded and decision_ref


def _is_write_card(value: str | None) -> bool:
    """Specialists may return read cards, but never a confirmation/write card."""
    if not value or not isinstance(value, str):
        return False
    if is_action_confirm_json(value) or _ACTION_TOKEN_PATTERN.search(value):
        return True
    try:
        parsed = json.loads(value.strip())
    except (TypeError, json.JSONDecodeError):
        return False
    candidates = parsed if isinstance(parsed, list) else [parsed]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("type") or "").upper() == "ACTION_CONFIRM":
            return True
        action_type = str(candidate.get("actionType") or "").upper()
        if action_type in {
            "REFUND",
            "CANCEL_ORDER",
            "CONFIRM_RECEIPT",
            "PRODUCT_REVIEW",
            "RECOMMENT",
            "CREATE_SUPPORT_CASE",
        }:
            return True
    return False


def _sanitize_specialist_text(value: Any, *, max_length: int) -> str:
    text = str(value or "")[:max_length]
    text = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
    text = _MOBILE_PATTERN.sub("[PHONE_REDACTED]", text)
    text = _LANDLINE_PATTERN.sub("[PHONE_REDACTED]", text)
    text = _ID_CARD_PATTERN.sub("[ID_REDACTED]", text)
    return _ADDRESS_PATTERN.sub(lambda match: f"{match.group('label')}[ADDRESS_REDACTED]", text)


def _sanitize_context_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_specialist_text(value, max_length=500)
    if isinstance(value, list):
        return [_sanitize_context_value(item) for item in value[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _sanitize_specialist_text(value, max_length=500)


def _allowlisted_context(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {
        field: _sanitize_context_value(source[field])
        for field in fields
        if source.get(field) not in (None, "", [])
    }


def _specialist_verified_context(
    state: dict[str, Any],
    *,
    agent_id: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {"intent": str(state.get("intent") or "")[:64]}
    resolution = str(state.get("order_resolution") or "")[:64]
    if resolution:
        context["orderResolution"] = resolution
    order = _allowlisted_context(state.get("verified_order_context"), _ORDER_CONTEXT_FIELDS)
    if order:
        context["order"] = order
    if agent_id == "shopping_advisor":
        product = _allowlisted_context(state.get("card"), _PRODUCT_CONTEXT_FIELDS)
        if product:
            context["product"] = product
        product_ids = [
            str(product_id)[:128]
            for product_id in state.get("comparison_product_ids") or []
            if str(product_id or "").strip()
        ][:10]
        if product_ids:
            context["comparisonProductIds"] = product_ids
    return context


def build_supervisor_plan(state: dict[str, Any]) -> SupervisorPlan:
    """Build a bounded plan from the already classified intent.

    The deterministic matrix is the safety fallback for a structured planner;
    it also makes rollout and regression tests independent of a provider.
    """

    intent = str(state.get("intent") or "")
    text = str(state.get("user_text") or "")
    try:
        resolved_intent = IntentKind(intent)
    except ValueError:
        resolved_intent = IntentKind.CHAT
    raw_mode = state.get("request_mode") or (state.get("intent_decision") or {}).get(
        "request_mode"
    )
    try:
        request_mode = (
            raw_mode if isinstance(raw_mode, RequestMode) else RequestMode(str(raw_mode))
        )
    except ValueError:
        request_mode = classify_request_mode(text, resolved_intent)
    specialists: list[str] = []
    goals: dict[str, str] = {}
    if intent == IntentKind.QUERY_ORDER.value and _contains(text, "再买一次", "再买", "复购"):
        specialists = ["shopping_advisor"]
        goals["shopping_advisor"] = "基于已验证订单中的商品信息检索当前可售商品，提供复购入口。"
    elif intent in {
        IntentKind.PRODUCT_SEARCH.value,
        IntentKind.PRODUCT_CONSULT.value,
        IntentKind.VISUAL_PRODUCT_SEARCH.value,
    }:
        specialists = ["shopping_advisor"]
        goals["shopping_advisor"] = (
            "使用已验证图片检索同图或视觉相似商品，并核对价格库存。"
            if intent == IntentKind.VISUAL_PRODUCT_SEARCH.value
            else "检索商品事实、价格库存与适配性，只返回可验证商品信息。"
        )
    else:
        needs_order = (
            intent in _ORDER_READ_INTENTS
            or (
                request_mode == RequestMode.ACTION_PROPOSAL
                and intent in _ORDER_BOUND_ACTION_INTENTS
            )
            or _contains(text, *_ORDER_FACT_MARKERS)
        )
        needs_policy = (
            bool(state.get("rag_evidence_required"))
            or _contains(text, *_POLICY_MARKERS)
            or intent
            in {
                IntentKind.REFUND.value,
                IntentKind.CANCEL_ORDER.value,
                IntentKind.COMPLAINT.value,
                IntentKind.PAYMENT_ISSUE.value,
                IntentKind.DAMAGED_OR_WRONG_ITEM.value,
                IntentKind.AFTERSALES_UNKNOWN.value,
                IntentKind.ADDRESS_CHANGE.value,
                IntentKind.INVOICE.value,
            }
        )
        if needs_order:
            specialists.append("order_fulfillment_specialist")
        if needs_policy and len(specialists) < 2:
            specialists.append("after_sales_policy_specialist")

    if "order_fulfillment_specialist" in specialists:
        goals["order_fulfillment_specialist"] = "查询订单、物流和售后状态，输出已验证的订单事实。"
    if "after_sales_policy_specialist" in specialists:
        goals["after_sales_policy_specialist"] = (
            "核对售后政策和资格条件，只输出政策证据与风险，不执行写操作。"
        )
    if (
        state.get("rag_evidence_required")
        and "after_sales_policy_specialist" not in specialists
        and len(specialists) < 2
    ):
        specialists.append("after_sales_policy_specialist")
        goals["after_sales_policy_specialist"] = (
            "检索已发布的售后政策证据，证据不足时给出保守答复。"
        )

    specialists = specialists[:2]
    action_type = _ACTION_TO_TOOL.get(intent)
    requires_action = bool(
        request_mode == RequestMode.ACTION_PROPOSAL
        and
        action_type
        and (state.get("verified_order_context") or action_type == "PROPOSE_CREATE_SUPPORT_CASE")
    )
    return SupervisorPlan(
        intent=intent or None,
        request_mode=request_mode,
        specialists=specialists,
        goals=goals,
        requires_action=requires_action,
        action_type=action_type if requires_action else None,
        fallback="PARTIAL_ARTIFACTS" if specialists else "SUPERVISOR_ONLY",
    )


async def _structured_supervisor_plan(
    state: dict[str, Any], fallback: SupervisorPlan
) -> SupervisorPlan:
    allowed = set(AGENT_SPECS) - {"supervisor"}
    llm = create_memory_llm(disable_thinking=True)
    structured = llm.with_structured_output(SupervisorPlan, method="json_mode", include_raw=True)
    response = await asyncio.wait_for(
        invoke_llm_with_metrics(
            structured,
            [
                SystemMessage(
                    content=(
                        "你是电商多智能体 Supervisor，只负责结构化路由。"
                        "最多选择两个独立的只读专家；不要回答用户，不要创建行动参数。"
                        "可用专家：shopping_advisor（商品）、"
                        "order_fulfillment_specialist（订单物流事实）、"
                        "after_sales_policy_specialist（售后政策证据）。"
                        "严格只返回一个符合下方 JSON Schema 的 JSON 对象，不得输出 Markdown。"
                        f"JSON Schema：{json.dumps(SupervisorPlan.model_json_schema(), ensure_ascii=False, separators=(',', ':'))}"
                    )
                ),
                HumanMessage(
                    content=json.dumps(
                        {
                            "question": state.get("user_text"),
                            "classifiedIntent": state.get("intent"),
                            "requestMode": fallback.request_mode.value,
                            "verifiedOrderAvailable": bool(state.get("verified_order_context")),
                            "deterministicSafetyFallback": fallback.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    )
                ),
            ],
        ),
        timeout=5,
    )
    parsed = response.get("parsed") if isinstance(response, dict) else response
    if isinstance(response, dict) and response.get("parsing_error") is not None:
        raise ValueError("SUPERVISOR_PLAN_PARSE_FAILED")
    plan = SupervisorPlan.model_validate(parsed)
    if len(plan.specialists) > 2 or not set(plan.specialists).issubset(allowed):
        raise ValueError("SUPERVISOR_PLAN_AGENT_INVALID")
    if not set(fallback.specialists).issubset(plan.specialists):
        raise ValueError("SUPERVISOR_PLAN_REQUIRED_AGENT_MISSING")
    plan.intent = fallback.intent
    plan.request_mode = fallback.request_mode
    plan.requires_action = fallback.requires_action
    plan.action_type = fallback.action_type
    plan.planner_source = "LLM_STRUCTURED"
    plan.goals = {
        agent_id: str(
            plan.goals.get(agent_id) or fallback.goals.get(agent_id) or "完成只读事实核对"
        )[:500]
        for agent_id in plan.specialists
    }
    return plan


def prepare_specialist_sends(state: dict[str, Any]) -> list[Send] | str:
    plan = SupervisorPlan.model_validate(state.get("supervisor_plan") or {})
    if not plan.specialists:
        return "multi_agent_synthesis"
    tasks = [SpecialistTask.model_validate(task) for task in state.get("specialist_tasks") or []]
    if [task.agent_id for task in tasks] != plan.specialists:
        return "multi_agent_synthesis"
    return [
        Send("specialist_runner", {"specialist_task": task.model_dump(mode="json")})
        for task in tasks
    ]


_ORDER_CONTEXT_TOOLS = frozenset(
    {
        "QUERY_ORDERS",
        "QUERY_LOGISTICS",
        "QUERY_COMMENT",
        "QUERY_REFUND_STATUS",
        "CHECK_AFTER_SALES_ELIGIBILITY",
    }
)

_ORDER_FACT_TOOLS = _ORDER_CONTEXT_TOOLS - {"CHECK_AFTER_SALES_ELIGIBILITY"}


def _after_sales_action(task: SpecialistTask) -> str:
    """Derive the rule action from the classified task, never from model text."""
    text = str(task.user_text or "")
    intent = str(task.verified_context.get("intent") or "")
    if intent == IntentKind.DAMAGED_OR_WRONG_ITEM.value or _contains(
        text, "退货", "退回", "退掉", "寄回"
    ):
        return "RETURN"
    return "REFUND"


def _tool_args(
    task: SpecialistTask,
    raw_args: dict[str, Any],
    run_id: str,
    tool_name: str | None = None,
) -> dict[str, Any]:
    args = dict(raw_args or {})
    if tool_name == "SEARCH_PRODUCTS_BY_IMAGE":
        # The model may suggest a query constraint but must never choose an
        # asset, a filesystem location or a detection box. All three values
        # below are bound to this durable specialist task by the Supervisor.
        for key in (
            "imageAssetId",
            "image_asset_id",
            "selectedSubjectId",
            "selected_subject_id",
            "queryText",
            "query_text",
            "bbox",
            "boundingBox",
            "bounding_box",
            "selectedSubject",
            "selected_subject",
            "imageUrl",
            "image_url",
            "imagePath",
            "image_path",
            "imageModerationId",
            "image_moderation_id",
        ):
            args.pop(key, None)
        image_context = task.verified_image_context
        if image_context is not None:
            args["imageAssetId"] = image_context.asset_id
            args["selectedSubjectId"] = (
                image_context.selected_subject.subject_id
                if image_context.selected_subject
                else None
            )
        args["queryText"] = task.user_text
    if tool_name == "SEARCH_KNOWLEDGE":
        args["query"] = normalize_policy_query(task.user_text)
    if tool_name == "CHECK_AFTER_SALES_ELIGIBILITY":
        # Action, order references and evidence are all server-bound. A model
        # cannot switch REFUND to RETURN, target another order, or invent a
        # proof type by editing a tool-call JSON payload.
        for key in (
            "action",
            "orderId",
            "order_id",
            "orderItemId",
            "order_item_id",
            "evidence",
        ):
            args.pop(key, None)
        args["action"] = _after_sales_action(task)
        verified_image = task.verified_image_context
        args["evidence"] = ["IMAGE"] if verified_image is not None else []
    verified_order = task.verified_context.get("order")
    if (
        tool_name in _ORDER_CONTEXT_TOOLS
        and isinstance(verified_order, dict)
        and task.agent_id
        in {
            "order_fulfillment_specialist",
            "after_sales_policy_specialist",
        }
    ):
        # A specialist may choose a read operation, but it cannot retarget that
        # operation away from the server-resolved order or item supplied by the
        # Supervisor. Ownership checks in the tool service remain a second gate.
        if "orderId" in verified_order and "orderId" in args:
            args["orderId"] = verified_order["orderId"]
        if "orderItemId" in verified_order and "orderItemId" in args:
            args["orderItemId"] = verified_order["orderItemId"]
        if not args.get("orderId") and verified_order.get("orderId"):
            args["orderId"] = verified_order["orderId"]
        if not args.get("orderItemId") and verified_order.get("orderItemId"):
            args["orderItemId"] = verified_order["orderItemId"]
        if tool_name == "CHECK_AFTER_SALES_ELIGIBILITY":
            # The eligibility tool must always receive the complete verified
            # reference, even when the model omitted optional fields.
            args["orderId"] = verified_order.get("orderId")
            args["orderItemId"] = verified_order.get("orderItemId")
    args["userId"] = task.user_id
    args["runId"] = run_id
    return args


def _required_tools_for_specialist(
    state: dict[str, Any], agent_id: str
) -> list[str]:
    scoped = _task_tools_for_specialist(state, agent_id)
    if agent_id == "after_sales_policy_specialist":
        # Eligibility is intentionally executed before policy retrieval. Both
        # calls are deterministic and leave the model only a summarisation job.
        return scoped[:2]
    return scoped[:1]


def _task_tools_for_specialist(
    state: dict[str, Any], agent_id: str
) -> list[str]:
    """Narrow a specialist's registry capabilities to this handoff only."""

    intent = str(state.get("intent") or "")
    if agent_id == "shopping_advisor":
        if intent == IntentKind.VISUAL_PRODUCT_SEARCH.value:
            return ["SEARCH_PRODUCTS_BY_IMAGE"]
        if state.get("comparison_product_ids"):
            return ["COMPARE_PRODUCTS"]
        if intent == IntentKind.PRODUCT_CONSULT.value and (state.get("card") or {}).get(
            "productId"
        ):
            return ["GET_PRODUCT_DETAIL"]
        return ["SEARCH_PRODUCTS", "GET_PRODUCT_DETAIL"]
    if agent_id == "after_sales_policy_specialist":
        user_text = str(state.get("user_text") or "")
        eligibility_intents = {
            IntentKind.REFUND.value,
            IntentKind.DAMAGED_OR_WRONG_ITEM.value,
            IntentKind.AFTERSALES_UNKNOWN.value,
        }
        asks_refund_eligibility = _contains(
            user_text,
            "能退款吗",
            "能否退款",
            "是否退款",
            "是否可以退款",
            "可以退款吗",
            "可不可以退款",
            "能退吗",
            "能不能退",
            "是否能退",
            "可以退吗",
        )
        tools: list[str] = []
        if state.get("verified_order_context") and (
            intent in eligibility_intents or asks_refund_eligibility
        ):
            tools.append("CHECK_AFTER_SALES_ELIGIBILITY")
        tools.append("SEARCH_KNOWLEDGE")
        if len(tools) < 2 and _contains(
            user_text,
            "工单",
            "售后进度",
            "投诉进度",
            "客服记录",
        ):
            tools.append("QUERY_SUPPORT_CASES")
        return tools[:2]
    if agent_id != "order_fulfillment_specialist":
        return []
    user_text = str(state.get("user_text") or "")
    if intent == IntentKind.QUERY_COUPON.value:
        return ["QUERY_USER_COUPONS"]

    # A write proposal needs the authoritative order/item state even when the
    # user's wording mentions shipping.  An unshipped order legitimately has
    # no logistics record, so making QUERY_LOGISTICS the required first call
    # would turn a valid refund/cancel flow into ORDER_EVIDENCE_INSUFFICIENT.
    if (
        str(state.get("request_mode") or "")
        == RequestMode.ACTION_PROPOSAL.value
        and state.get("verified_order_context")
    ):
        return ["QUERY_ORDERS"]

    primary = "QUERY_ORDERS"
    if intent == IntentKind.REFUND_STATUS.value:
        primary = "QUERY_REFUND_STATUS"
    elif intent == IntentKind.QUERY_COMMENT.value:
        primary = "QUERY_COMMENT"
    elif _contains(user_text, "工单", "售后进度", "投诉进度", "客服记录"):
        primary = "QUERY_SUPPORT_CASES"
    elif intent in {
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
    } or _contains(user_text, "物流", "快递", "包裹", "延迟", "不更新", "发货"):
        primary = "QUERY_LOGISTICS"

    tools = [primary]
    if primary != "QUERY_ORDERS" and state.get("verified_order_context"):
        tools.append("QUERY_ORDERS")
    return tools[:2]


def _required_tool_args(task: SpecialistTask, tool_name: str) -> dict[str, Any]:
    order = task.verified_context.get("order") or {}
    product = task.verified_context.get("product") or {}
    if tool_name == "SEARCH_PRODUCTS":
        return {"keyword": task.user_text}
    if tool_name == "SEARCH_PRODUCTS_BY_IMAGE":
        return {}
    if tool_name == "GET_PRODUCT_DETAIL":
        return {"productId": product.get("productId")}
    if tool_name == "COMPARE_PRODUCTS":
        return {
            "productIds": task.verified_context.get("comparisonProductIds") or []
        }
    if tool_name == "SEARCH_KNOWLEDGE":
        return {"query": normalize_policy_query(task.user_text)}
    if tool_name == "CHECK_AFTER_SALES_ELIGIBILITY":
        return {
            "action": _after_sales_action(task),
            "orderId": order.get("orderId"),
            "orderItemId": order.get("orderItemId"),
            "evidence": ["IMAGE"] if task.verified_image_context is not None else [],
        }
    if tool_name == "QUERY_REFUND_STATUS":
        return {
            "orderItemId": order.get("orderItemId"),
            "orderId": order.get("orderId"),
        }
    if tool_name in {"QUERY_ORDERS", "QUERY_LOGISTICS", "QUERY_COMMENT"}:
        return {"orderId": order.get("orderId")}
    return {}


def _verified_order_evidence(task: SpecialistTask) -> dict[str, Any] | None:
    order = task.verified_context.get("order")
    if not isinstance(order, dict) or not order.get("orderId"):
        return None
    return {
        "type": "order",
        **{
            field: order[field]
            for field in _ORDER_CONTEXT_FIELDS
            if order.get(field) not in (None, "", [])
        },
    }


async def specialist_runner_node(state: dict[str, Any]) -> dict[str, Any]:
    raw_task = state.get("specialist_task") or {}
    try:
        task = SpecialistTask.model_validate(raw_task)
        spec = AGENT_SPECS.get(task.agent_id)
        if spec is None:
            raise ValueError("SPECIALIST_AGENT_UNKNOWN")
        if not task.tool_scope or not set(task.tool_scope).issubset(spec.tool_allowlist):
            raise ValueError("SPECIALIST_TASK_TOOL_SCOPE_INVALID")
        if not set(task.required_tools).issubset(task.tool_scope):
            raise ValueError("SPECIALIST_TASK_REQUIRED_TOOL_INVALID")
    except Exception:
        # The task was normally created and persisted by supervisor_plan_node.
        # Keep the graph contract total even if a handoff is tampered with or
        # malformed before the worker starts: a child run must not remain QUEUED.
        error_code = "SPECIALIST_TASK_INVALID"
        if isinstance(raw_task, dict):
            # Preserve the more specific preflight failure for trace and
            # interview replay while keeping the public error contract stable.
            candidate_agent = str(raw_task.get("agent_id") or "")
            if candidate_agent and candidate_agent not in AGENT_SPECS:
                error_code = "SPECIALIST_AGENT_UNKNOWN"
            elif candidate_agent:
                error_code = "SPECIALIST_TASK_TOOL_SCOPE_INVALID"
        return _failed_specialist_result(
            raw_task,
            error_code=error_code,
        )
    with bind_episode(
        task.child_run_id,
        message_id=None,
        user_id=task.user_id,
        force_keep=True,
    ):
        return await _execute_specialist_task(task)


def _failed_specialist_result(raw_task: Any, *, error_code: str) -> dict[str, Any]:
    """Close a child handoff when validation fails before the worker loop."""
    if not isinstance(raw_task, dict):
        return {"specialist_artifacts": []}
    child_run_id = str(raw_task.get("child_run_id") or "")
    if not child_run_id:
        return {"specialist_artifacts": []}
    handoff_id = str(raw_task.get("handoff_id") or uuid.uuid4().hex)
    parent_run_id = str(raw_task.get("parent_run_id") or "") or None
    agent_id = str(raw_task.get("agent_id") or "unknown")[:64]
    artifact = AgentArtifact(
        status="FAILED",
        agent_id=agent_id,
        next_step="FALLBACK",
        confidence=0.0,
        warnings=[error_code],
        handoff_id=handoff_id,
        latency_ms=0,
    ).model_dump(mode="json")
    episode_service.record_step(
        "SPECIALIST_ARTIFACT",
        node_name="specialist_runner",
        status="ERROR",
        output_data=artifact,
        artifact_type="AgentArtifact",
        agent_id=agent_id,
        handoff_id=handoff_id,
        run_id=child_run_id,
        error_code=error_code,
    )
    episode_service.record_handoff(
        handoff_id=handoff_id,
        parent_run_id=parent_run_id,
        child_run_id=child_run_id,
        source_agent="supervisor",
        target_agent=agent_id,
        status="FAILED",
        artifact=artifact,
        latency_ms=0,
        error_code=error_code,
    )
    episode_service.finish_run(
        "specialist_failed",
        run_id=child_run_id,
        latency_ms=0,
        force_keep=True,
    )
    return {"specialist_artifacts": [artifact]}


def _record_eligibility_trace(
    *, task: SpecialistTask, result: Any, run_id: str
) -> None:
    """Emit a compact Chinese-readable rule decision without raw order data."""
    if getattr(result, "biz_type", None) != "after_sales_eligibility":
        return
    payload: dict[str, Any] = {}
    try:
        decoded = json.loads(result.biz_data or "{}")
        if isinstance(decoded, dict):
            payload = decoded
    except (TypeError, json.JSONDecodeError):
        pass
    output = {
        "label": "售后资格已核验" if result.success else "售后资格核验降级",
        "decision": payload.get("decision"),
        "decisionId": payload.get("decisionId"),
        "policyId": payload.get("policyId"),
        "policyVersion": payload.get("policyVersion"),
        "specificity": payload.get("specificity"),
        "missingEvidence": list(payload.get("missingEvidence") or [])[:10],
        "reason": str(payload.get("reason") or result.error_code or "")[:300],
        "nextStep": payload.get("nextStep"),
    }
    episode_service.record_step(
        "AFTER_SALES_ELIGIBILITY_DECISION",
        node_name="after_sales_policy_specialist",
        status="OK" if result.success else "DEGRADED",
        output_data=output,
        tool_name="CHECK_AFTER_SALES_ELIGIBILITY",
        agent_id=task.agent_id,
        handoff_id=task.handoff_id,
        run_id=run_id,
        error_code=None if result.success else result.error_code,
    )


async def _execute_specialist_task(task: SpecialistTask) -> dict[str, Any]:
    child_run_id = task.child_run_id
    spec = AGENT_SPECS[task.agent_id]
    if not task.tool_scope or not set(task.tool_scope).issubset(spec.tool_allowlist):
        raise ValueError("SPECIALIST_TASK_TOOL_SCOPE_INVALID")
    if not set(task.required_tools).issubset(task.tool_scope):
        raise ValueError("SPECIALIST_TASK_REQUIRED_TOOL_INVALID")
    episode_service.record_step(
        "SPECIALIST_STARTED",
        node_name="specialist_runner",
        status="OK",
        output_data={
            "goal": task.goal,
            "toolScope": sorted(task.tool_scope),
            "requiredTools": task.required_tools,
            "maxRounds": task.max_rounds,
            "timeoutSeconds": task.timeout_seconds,
        },
        agent_id=task.agent_id,
        handoff_id=task.handoff_id,
        run_id=child_run_id,
    )
    started = time.perf_counter()
    messages = [
        SystemMessage(
            content=(
                f"你是电商内部专家 {task.agent_id}。只处理目标：{task.goal}\n"
                f"职责：{spec.instructions}\n"
                f"本任务获准的只读工具：{', '.join(sorted(task.tool_scope)) or '无'}。\n"
                "不得执行写操作，不得编造订单或政策事实。最终只返回内部事实摘要，"
                "不得使用 markdown 表格，不得输出用户确认卡片。"
            )
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "goal": task.goal,
                    "userQuestion": task.user_text,
                    "sessionSummary": task.session_summary,
                    "shoppingMission": task.shopping_mission,
                    "verifiedContext": task.verified_context,
                },
                ensure_ascii=False,
            )
        ),
    ]
    called: list[str] = []
    evidence: list[dict] = []
    verified_tool_outputs: list[str] = []
    assistant_cards: str | None = None
    tool_biz: dict[str, Any] = {}
    biz_type: str | None = None
    biz_data: str | None = None
    search_tool_hint: str | None = None
    retrieval_trace: dict | None = None
    warnings: list[str] = []
    draft = ""
    status = "SUCCESS"
    error_code: str | None = None
    try:
        async with asyncio.timeout(task.timeout_seconds):
            for required_tool in task.required_tools:
                required_result = await mcp_tool_router.invoke(
                    required_tool,
                    _tool_args(
                        task,
                        _required_tool_args(task, required_tool),
                        child_run_id,
                        required_tool,
                    ),
                    task.user_id,
                    call_id=f"required-{required_tool.lower()}",
                    verified_image_context=(
                        task.verified_image_context
                        if required_tool == "SEARCH_PRODUCTS_BY_IMAGE"
                        else None
                    ),
                    source_message_id=(
                        task.source_message_id
                        if required_tool == "SEARCH_PRODUCTS_BY_IMAGE"
                        else None
                    ),
                )
                called.append(required_tool)
                required_observation = build_tool_result_observation(required_result)
                required_usable = required_result.success and not required_observation.contaminated
                if required_observation.contaminated:
                    warnings.append(f"TOOL_RESULT_QUARANTINED:{required_tool}")
                if required_usable:
                    _record_eligibility_trace(
                        task=task, result=required_result, run_id=child_run_id
                    )
                evidence.append(
                    {
                        "type": "tool_result",
                        "tool": required_tool,
                        "success": required_usable,
                        "errorCode": (
                            "TOOL_RESULT_QUARANTINED"
                            if required_observation.contaminated
                            else required_result.error_code
                        ),
                    }
                )
                if required_usable and required_result.source_refs:
                    evidence.extend(required_result.source_refs)
                if required_usable and required_tool in _ORDER_CONTEXT_TOOLS:
                    order_evidence = _verified_order_evidence(task)
                    if order_evidence:
                        evidence.append(order_evidence)
                if required_usable and required_result.assistant_cards:
                    assistant_cards = required_result.assistant_cards
                if required_result.success:
                    required_text = required_observation.text
                    if required_usable:
                        verified_tool_outputs.append(
                            f"{required_tool}: {required_text}"[:3000]
                        )
                    required_call_id = f"required-{required_tool.lower()}"
                    messages.append(
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "id": required_call_id,
                                    "name": required_tool,
                                    "args": _required_tool_args(task, required_tool),
                                }
                            ],
                        )
                    )
                    messages.append(
                        ToolMessage(
                            content=required_text,
                            tool_call_id=required_call_id,
                        )
                    )
                required_biz = (
                    required_result.to_biz_dict() or {} if required_usable else {}
                )
                for key in ("productIds", "productNames", "orderIds"):
                    values = [
                        str(value)
                        for value in required_biz.get(key) or []
                        if str(value or "").strip()
                    ]
                    if values:
                        tool_biz[key] = list(
                            dict.fromkeys([*(tool_biz.get(key) or []), *values])
                        )[:50]
                if required_usable:
                    biz_type = required_result.biz_type or biz_type
                    biz_data = required_result.biz_data or biz_data
                    retrieval_trace = required_result.retrieval_trace or retrieval_trace
                if required_usable and required_tool == "SEARCH_PRODUCTS":
                    search_tool_hint = required_observation.text
                episode_service.record_step(
                    "SPECIALIST_TOOL",
                    node_name="specialist_runner",
                    status="OK" if required_usable else "ERROR",
                    output_data={
                        "tool": required_tool,
                        "required": True,
                        "success": required_usable,
                        "sourceCount": (
                            len(required_result.source_refs or [])
                            if required_usable
                            else 0
                        ),
                        "quarantined": required_observation.contaminated,
                    },
                    agent_id=task.agent_id,
                    handoff_id=task.handoff_id,
                    run_id=child_run_id,
                )
            for round_no in range(task.max_rounds):
                remaining_tools = frozenset(task.tool_scope) - frozenset(called)
                llm = rt.bind_agent_llm(
                    allowed_tools=remaining_tools,
                    max_tokens=task.max_tokens,
                    disable_thinking=True,
                    tools_enabled=bool(remaining_tools),
                )
                response = await invoke_llm_with_metrics(
                    llm, messages, model=getattr(llm, "model_name", None)
                )
                messages.append(response)
                tool_calls = list(getattr(response, "tool_calls", None) or [])
                if not tool_calls:
                    candidate = strip_emojis(
                        rt.chunk_text(getattr(response, "content", "") or "")
                    )
                    if _contains_tool_protocol(candidate):
                        successful_required_tools = {
                            str(item.get("tool") or "")
                            for item in evidence
                            if item.get("type") == "tool_result"
                            and item.get("success") is True
                        }
                        required_tools_complete = bool(task.required_tools) and set(
                            task.required_tools
                        ).issubset(successful_required_tools)
                        if (
                            required_tools_complete
                            and not remaining_tools
                            and verified_tool_outputs
                        ):
                            # Some providers may emit their private tool syntax
                            # even after tools have been disabled. The required
                            # read-only work is already complete, so keep the
                            # run successful and build the internal artifact
                            # only from server-verified tool results.
                            warnings.append("MODEL_SUMMARY_PROTOCOL_SANITIZED")
                        else:
                            warnings.append("SPECIALIST_TOOL_PROTOCOL_REJECTED")
                            status = "DEGRADED"
                        draft = "\n".join(verified_tool_outputs)[-4000:]
                    else:
                        draft = candidate
                    break
                for call in tool_calls:
                    name = str(call.get("name") or "")
                    if name in called:
                        warnings.append(f"TOOL_DUPLICATE_DENIED:{name}")
                        messages.append(
                            ToolMessage(
                                content="该工具本任务已调用，不允许重复调用。",
                                tool_call_id=call.get("id") or "duplicate",
                            )
                        )
                        continue
                    if name not in task.tool_scope or name.startswith("PROPOSE_"):
                        warnings.append(f"TOOL_SCOPE_DENIED:{name}")
                        messages.append(
                            ToolMessage(
                                content="工具权限拒绝。", tool_call_id=call.get("id") or "denied"
                            )
                        )
                        continue
                    result = await mcp_tool_router.invoke(
                        name,
                        _tool_args(task, call.get("args") or {}, child_run_id, name),
                        task.user_id,
                        call_id=call.get("id"),
                        verified_image_context=(
                            task.verified_image_context
                            if name == "SEARCH_PRODUCTS_BY_IMAGE"
                            else None
                        ),
                        source_message_id=(
                            task.source_message_id
                            if name == "SEARCH_PRODUCTS_BY_IMAGE"
                            else None
                        ),
                    )
                    called.append(name)
                    observation = build_tool_result_observation(result)
                    result_usable = result.success and not observation.contaminated
                    if observation.contaminated:
                        warnings.append(f"TOOL_RESULT_QUARANTINED:{name}")
                    if result_usable:
                        _record_eligibility_trace(
                            task=task, result=result, run_id=child_run_id
                        )
                    evidence.append(
                        {
                            "type": "tool_result",
                            "tool": name,
                            "success": result_usable,
                            "errorCode": (
                                "TOOL_RESULT_QUARANTINED"
                                if observation.contaminated
                                else result.error_code
                            ),
                        }
                    )
                    if result_usable and result.source_refs:
                        evidence.extend(result.source_refs)
                    if result_usable and name in _ORDER_CONTEXT_TOOLS:
                        order_evidence = _verified_order_evidence(task)
                        if order_evidence:
                            evidence.append(order_evidence)
                    if result_usable and result.assistant_cards:
                        assistant_cards = result.assistant_cards
                    result_biz = result.to_biz_dict() or {} if result_usable else {}
                    for key in ("productIds", "productNames", "orderIds"):
                        values = [
                            str(value)
                            for value in result_biz.get(key) or []
                            if str(value or "").strip()
                        ]
                        if values:
                            tool_biz[key] = list(
                                dict.fromkeys([*(tool_biz.get(key) or []), *values])
                            )[:50]
                    if result_usable:
                        biz_type = result.biz_type or biz_type
                        biz_data = result.biz_data or biz_data
                        retrieval_trace = result.retrieval_trace or retrieval_trace
                    if result_usable and name == "SEARCH_PRODUCTS":
                        search_tool_hint = observation.text
                    tool_message = observation.text
                    if result_usable:
                        verified_tool_outputs.append(f"{name}: {tool_message}"[:3000])
                    messages.append(
                        ToolMessage(
                            content=tool_message, tool_call_id=call.get("id") or name
                        )
                    )
                    episode_service.record_step(
                        "SPECIALIST_TOOL",
                        node_name="specialist_runner",
                        status="OK" if result_usable else "ERROR",
                        output_data={
                            "tool": name,
                            "success": result_usable,
                            "sourceCount": (
                                len(result.source_refs or []) if result_usable else 0
                            ),
                            "quarantined": observation.contaminated,
                        },
                        agent_id=task.agent_id,
                        handoff_id=task.handoff_id,
                        run_id=child_run_id,
                    )
            if not draft:
                # Tool rounds and the artifact turn are separate budgets. The
                # final model call has no tools, so it can only summarize the
                # verified results already present in this isolated branch.
                llm = rt.bind_agent_llm(
                    allowed_tools=frozenset(),
                    max_tokens=task.max_tokens,
                    disable_thinking=True,
                    tools_enabled=False,
                )
                messages.append(
                    SystemMessage(
                        content=(
                            "工具阶段已结束。只根据上方工具结果输出内部事实摘要；"
                            "不得继续调用工具，不得输出 DSML、XML、JSON 工具协议。"
                        )
                    )
                )
                response = await invoke_llm_with_metrics(
                    llm, messages, model=getattr(llm, "model_name", None)
                )
                final_tool_calls = list(getattr(response, "tool_calls", None) or [])
                final_text = strip_emojis(
                    rt.chunk_text(getattr(response, "content", "") or "")
                )
                if final_tool_calls or _contains_tool_protocol(final_text):
                    warnings.append("SPECIALIST_ROUND_LIMIT")
                    status = "DEGRADED"
                    draft = "\n".join(verified_tool_outputs)[-4000:]
                else:
                    draft = final_text
    except TimeoutError:
        error_code = "SPECIALIST_TIMEOUT"
        warnings.append("SPECIALIST_TIMEOUT")
        if verified_tool_outputs:
            status = "DEGRADED"
            draft = "\n".join(verified_tool_outputs)[-4000:]
            warnings.append("专家总结超时，已保留超时前取得的可信工具证据。")
        else:
            status = "FAILED"
            warnings.append("专家在取得可信证据前超时，Supervisor 将使用其他分支降级回答。")
    except Exception as exc:
        successful_tools = {
            str(item.get("tool") or "")
            for item in evidence
            if item.get("type") == "tool_result" and item.get("success") is True
        }
        # A credential-free local stack deliberately has no chat model. Once
        # every required deterministic tool has succeeded, its sanitized
        # observations are sufficient for a typed artifact and the root
        # Supervisor can still create a confirmation-gated proposal. Do not
        # apply this fallback to a configured model: provider failures must
        # remain visible as specialist failures.
        if (
            not get_settings().llm_api_key.strip()
            and verified_tool_outputs
            and set(task.required_tools).issubset(successful_tools)
        ):
            status = "SUCCESS"
            error_code = None
            draft = "\n".join(verified_tool_outputs)[-4000:]
            warnings.append("DETERMINISTIC_SPECIALIST_SUMMARY")
        else:
            status = "FAILED"
            error_code = type(exc).__name__
            warnings.append("专家调用失败，Supervisor 将基于其他证据回答。")

    if not draft and status == "SUCCESS":
        status = "DEGRADED"
        warnings.append("SPECIALIST_EMPTY_ARTIFACT")
    artifact = AgentArtifact(
        status=status,
        agent_id=task.agent_id,
        facts=[draft[:2000]] if draft else [],
        evidence=evidence[:20],
        draft_answer=draft[:4000],
        assistant_cards=assistant_cards,
        tool_biz=tool_biz,
        biz_type=biz_type,
        biz_data=biz_data,
        search_tool_hint=search_tool_hint,
        retrieval_trace=retrieval_trace,
        confidence=0.75 if draft and evidence else 0.35 if draft else 0.0,
        next_step="FINALIZE" if draft else "FALLBACK",
        warnings=warnings,
        tool_calls=called,
        handoff_id=task.handoff_id,
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    # Persist and hand off the same validated artifact that Supervisor will
    # consume. Otherwise a child Trace can retain an untrusted model draft
    # even though the root silently rebuilt it from verified business cards.
    artifact = _validate_artifact(artifact.model_dump(mode="json"))
    status = artifact.status
    artifact_data = artifact.model_dump(mode="json")
    episode_service.record_step(
        "SPECIALIST_ARTIFACT",
        node_name="specialist_runner",
        status="OK" if status == "SUCCESS" else "DEGRADED",
        output_data=artifact_data,
        artifact_type="AgentArtifact",
        agent_id=task.agent_id,
        handoff_id=task.handoff_id,
        run_id=child_run_id,
    )
    episode_service.record_handoff(
        handoff_id=task.handoff_id,
        parent_run_id=task.parent_run_id,
        child_run_id=child_run_id,
        source_agent="supervisor",
        target_agent=task.agent_id,
        status="SUCCEEDED" if status == "SUCCESS" else "FALLBACK",
        artifact=artifact_data,
        latency_ms=artifact.latency_ms,
        error_code=error_code,
    )
    outcome = (
        "ok" if status == "SUCCESS" else "degraded" if status == "DEGRADED" else "specialist_failed"
    )
    episode_service.finish_run(
        outcome,
        run_id=child_run_id,
        latency_ms=artifact.latency_ms,
        force_keep=True,
    )
    return {"specialist_artifacts": [artifact_data]}


def _action_args(state: dict[str, Any], tool: str) -> dict[str, Any] | None:
    target = dict(state.get("verified_order_context") or {})
    order_id = str(target.get("orderId") or target.get("order_id") or "")
    item_id = str(target.get("orderItemId") or target.get("order_item_id") or "")
    run_id = str((state.get("agent_msg") or {}).get("runId") or "") or None
    if tool == "PROPOSE_REFUND" and item_id:
        return {"orderItemId": item_id, "runId": run_id}
    if tool in {"PROPOSE_CANCEL_ORDER", "PROPOSE_CONFIRM_RECEIPT"} and order_id:
        return {"orderId": order_id, "runId": run_id}
    user_text = str(state.get("user_text") or "")
    if tool == "PROPOSE_PRODUCT_REVIEW" and order_id:
        star = extract_review_star(user_text)
        content = extract_review_content(user_text, order_id)
        if star is not None and content:
            return {
                "orderId": order_id,
                "commentContent": content,
                "star": star,
                "runId": run_id,
            }
    if tool == "PROPOSE_RECOMMENT" and order_id:
        content = extract_review_content(user_text, order_id)
        if content:
            return {"orderId": order_id, "reCommentContent": content, "runId": run_id}
    if tool == "PROPOSE_CREATE_SUPPORT_CASE":
        from app.services.support_case_service import support_case_service

        args: dict[str, Any] = {
            "category": support_case_service.category_for_intent(
                str(state.get("intent") or ""), user_text
            ),
            "description": user_text[:4000],
            "orderId": order_id or None,
            "orderItemId": item_id or None,
            "sourceMessageId": state.get("message_id"),
            "runId": run_id,
        }
        image_context = dict(state.get("verified_image_context") or {})
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
        return args
    return None


def _validate_artifact(raw: dict[str, Any]) -> AgentArtifact:
    artifact = AgentArtifact.model_validate(raw)
    spec = AGENT_SPECS.get(artifact.agent_id)
    warnings = list(artifact.warnings)
    if spec is None or not set(artifact.tool_calls).issubset(spec.tool_allowlist):
        raise ValueError("ARTIFACT_TOOL_SCOPE_INVALID")
    if artifact.proposed_action is not None:
        artifact.proposed_action = None
        warnings.append("SPECIALIST_ACTION_DROPPED")
    raw_evidence = [item for item in artifact.evidence if isinstance(item, dict)]
    artifact.evidence = []
    saw_tool_result = False
    last_tool_name = ""
    last_tool_succeeded = False
    for item in raw_evidence[:20]:
        if item.get("type") == "tool_result":
            tool_name = str(item.get("tool") or "")
            if tool_name in spec.tool_allowlist and isinstance(item.get("success"), bool):
                artifact.evidence.append(item)
                saw_tool_result = True
                last_tool_name = tool_name
                last_tool_succeeded = item["success"] is True
            else:
                warnings.append("UNTRUSTED_EVIDENCE_DROPPED")
            continue
        if (
            _is_trusted_evidence_ref(item)
            and saw_tool_result
            and last_tool_succeeded
            and _evidence_type_matches_tool(item, last_tool_name)
        ):
            artifact.evidence.append(item)
        else:
            warnings.append("UNTRUSTED_EVIDENCE_DROPPED")
    if _is_write_card(artifact.assistant_cards):
        artifact.assistant_cards = None
        warnings.append("SPECIALIST_ACTION_CARD_DROPPED")
    has_verified_result = _has_verified_evidence(artifact.evidence)
    successful_tools = {
        str(item.get("tool") or "")
        for item in artifact.evidence
        if item.get("type") == "tool_result" and item.get("success") is True
    }
    clean_tool_biz: dict[str, list[str]] = {}
    for key in ("productIds", "productNames", "orderIds"):
        values = [
            str(value)[:500]
            for value in artifact.tool_biz.get(key) or []
            if str(value or "").strip()
        ]
        if values:
            clean_tool_biz[key] = list(dict.fromkeys(values))[:50]
    artifact.tool_biz = clean_tool_biz if has_verified_result else {}
    if artifact.assistant_cards:
        card_has_matching_tool = (
            is_product_cards_json(artifact.assistant_cards)
            and bool(
                successful_tools
                & {"SEARCH_PRODUCTS", "SEARCH_PRODUCTS_BY_IMAGE", "COMPARE_PRODUCTS"}
            )
            or is_visual_subject_selection_json(artifact.assistant_cards)
            and "SEARCH_PRODUCTS_BY_IMAGE" in successful_tools
            or is_order_cards_json(artifact.assistant_cards)
            and "QUERY_ORDERS" in successful_tools
            or is_support_case_cards_json(artifact.assistant_cards)
            and "QUERY_SUPPORT_CASES" in successful_tools
        )
        if not card_has_matching_tool:
            artifact.assistant_cards = None
            warnings.append("UNVERIFIED_ASSISTANT_CARD_DROPPED")
        elif is_product_cards_json(artifact.assistant_cards):
            card_facts = _verified_product_card_facts(artifact.assistant_cards)
            if card_facts:
                artifact.facts = card_facts
                artifact.draft_answer = "\n".join(card_facts)[:4000]
                artifact.confidence = max(artifact.confidence, 0.9)
                artifact.next_step = "FINALIZE"
                warnings.append("SHOPPING_FACTS_REBUILT_FROM_VERIFIED_CARDS")
    if not has_verified_result:
        artifact.biz_type = None
        artifact.biz_data = None
    elif artifact.biz_data:
        artifact.biz_data = artifact.biz_data[:20_000]
    if not successful_tools & {"SEARCH_PRODUCTS", "SEARCH_PRODUCTS_BY_IMAGE"}:
        artifact.search_tool_hint = None
    elif artifact.search_tool_hint:
        artifact.search_tool_hint = artifact.search_tool_hint[:4000]
    if not successful_tools & {"SEARCH_KNOWLEDGE", "SEARCH_PRODUCTS_BY_IMAGE"}:
        artifact.retrieval_trace = None
    if artifact.draft_answer and not has_verified_result:
        artifact.status = "BLOCKED"
        artifact.facts = []
        artifact.draft_answer = ""
        artifact.confidence = 0.0
        artifact.next_step = "FALLBACK"
        warnings.append("UNVERIFIED_FACTS_DROPPED")
    has_knowledge_evidence = _has_policy_evidence(artifact.evidence)
    has_eligibility = _has_eligibility_evidence(artifact.evidence)
    if (
        artifact.agent_id == "after_sales_policy_specialist"
        and artifact.status != "FAILED"
    ):
        if has_eligibility and not has_knowledge_evidence:
            # A rule decision is still useful as a structured fact, but the
            # model must not turn it into a broader policy promise without a
            # citation from the knowledge tool.
            artifact.status = "DEGRADED"
            artifact.draft_answer = _eligibility_safe_summary(artifact)
            artifact.facts = [artifact.draft_answer]
            artifact.confidence = min(artifact.confidence, 0.65)
            artifact.next_step = "FINALIZE"
            warnings.append("POLICY_TEXT_EVIDENCE_MISSING")
        elif not has_eligibility and not has_knowledge_evidence:
            # Neither a concrete eligibility decision nor policy text can
            # support a customer-facing qualification answer.
            artifact.status = "DEGRADED"
            artifact.facts = []
            artifact.draft_answer = "未找到可引用的售后政策证据，也无法完成具体订单资格核验，建议人工核验。"
            artifact.confidence = 0.0
            artifact.next_step = "HUMAN_HANDOFF"
            warnings.extend(("POLICY_EVIDENCE_MISSING", "ELIGIBILITY_EVIDENCE_MISSING"))
        elif (
            not has_eligibility
            and "CHECK_AFTER_SALES_ELIGIBILITY" in artifact.tool_calls
        ):
            # Policy text can explain a general rule, but it cannot establish
            # that this user's order satisfies it.
            artifact.status = "DEGRADED"
            artifact.facts = []
            artifact.draft_answer = "已找到售后政策说明，但缺少当前订单的资格核验结果，暂不能确认是否符合。"
            artifact.confidence = min(artifact.confidence, 0.35)
            artifact.next_step = "HUMAN_HANDOFF"
            warnings.append("ELIGIBILITY_EVIDENCE_MISSING")
    artifact.warnings = list(dict.fromkeys(warnings))
    return artifact


def _action_evidence_failure(
    plan: SupervisorPlan,
    artifacts: list[AgentArtifact],
) -> str | None:
    if plan.action_type == "PROPOSE_CREATE_SUPPORT_CASE":
        return None
    order_artifact = next(
        (artifact for artifact in artifacts if artifact.agent_id == "order_fulfillment_specialist"),
        None,
    )
    order_result_verified = bool(
        order_artifact
        and _has_verified_evidence(order_artifact.evidence)
        and any(
            item.get("type") == "tool_result"
            and item.get("success") is True
            and str(item.get("tool") or "") in _ORDER_FACT_TOOLS
            for item in order_artifact.evidence
        )
    )
    if order_artifact is None or order_artifact.status != "SUCCESS" or not order_result_verified:
        return "ORDER_EVIDENCE_INSUFFICIENT"
    if plan.action_type == "PROPOSE_REFUND" and "after_sales_policy_specialist" in plan.specialists:
        policy_artifact = next(
            (
                artifact
                for artifact in artifacts
                if artifact.agent_id == "after_sales_policy_specialist"
            ),
            None,
        )
        has_policy_source = bool(
            policy_artifact
            and policy_artifact.status == "SUCCESS"
            and _has_policy_evidence(policy_artifact.evidence)
            and _has_eligibility_evidence(policy_artifact.evidence)
        )
        if not has_policy_source:
            return "POLICY_EVIDENCE_INSUFFICIENT"
        if str(_eligibility_payload(policy_artifact).get("decision") or "") != "ELIGIBLE":
            return "AFTER_SALES_NOT_ELIGIBLE"
    return None


def _action_failure_answer(answer: str) -> str:
    base = str(answer or "").strip().rstrip("。")
    suffix = "操作确认未创建，请稍后重试或转人工处理。"
    return f"{base}。{suffix}" if base else suffix


def _sanitize_offer_time_claims(answer: str) -> str:
    """Keep exact offer expiry in the structured card, where timezone is explicit."""
    return _OFFER_TIME_CLAIM_RE.sub(
        "报价短期有效，具体截止时间以商品卡片为准",
        str(answer or ""),
    )


def _verified_product_card_facts(cards: str | None) -> list[str]:
    """Build replayable facts from server-produced cards, never from model prose."""
    try:
        payload = json.loads(str(cards or ""))
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("products")
    if not isinstance(payload, list):
        return []
    facts: list[str] = []
    for raw in payload[:6]:
        if not isinstance(raw, dict):
            continue
        product_id = str(raw.get("productId") or "").strip()
        product_name = str(raw.get("productName") or "").strip()
        if not product_id or not product_name:
            continue
        fields = [f"商品={product_name}", f"productId={product_id}"]
        sku_key = str(raw.get("skuKey") or "").strip()
        if sku_key:
            fields.append(f"skuKey={sku_key}")
        base_price = raw.get("basePrice")
        payable = raw.get("estimatedPayable")
        if base_price is not None:
            fields.append(f"原价={base_price}元")
        if payable is not None:
            fields.append(f"预计到手价={payable}元")
        if raw.get("totalStock") is not None:
            fields.append(f"库存={raw['totalStock']}")
        availability = str(raw.get("availability") or "").strip()
        if availability:
            fields.append(f"可售状态={availability}")
        snapshot_id = str(raw.get("offerSnapshotId") or "").strip()
        if snapshot_id:
            fields.append(f"报价快照={snapshot_id}")
        recommendation = raw.get("recommendation")
        if isinstance(recommendation, dict):
            for label, key in (
                ("适合", "bestFor"),
                ("不适合", "notIdealFor"),
                ("取舍", "tradeoff"),
            ):
                value = str(recommendation.get(key) or "").strip()
                if value:
                    fields.append(f"{label}={value[:300]}")
        facts.append(" | ".join(fields)[:2000])
    return facts


def _eligibility_payload(artifact: AgentArtifact) -> dict[str, Any]:
    if artifact.biz_type != "after_sales_eligibility" or not artifact.biz_data:
        return {}
    try:
        payload = json.loads(artifact.biz_data)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _eligibility_safe_summary(artifact: AgentArtifact) -> str:
    """Render only rule-engine fields when policy prose is unavailable."""
    payload = _eligibility_payload(artifact)
    decision = str(payload.get("decision") or "POLICY_UNAVAILABLE")
    labels = {
        "ELIGIBLE": "规则引擎判定当前订单满足该售后动作的业务前置条件",
        "INELIGIBLE": "规则引擎判定当前订单不满足该售后动作的业务前置条件",
        "NEEDS_EVIDENCE": "规则引擎需要补充凭证后才能继续核验",
        "CONFLICT": "规则或订单事实存在冲突，暂时无法确认资格",
        "POLICY_UNAVAILABLE": "当前没有可用的已发布售后规则",
    }
    text = labels.get(decision, "当前无法确认售后资格")
    reason = str(payload.get("reason") or "").strip()
    if reason:
        text += f"：{reason}"
    missing = [str(item) for item in payload.get("missingEvidence") or [] if str(item).strip()]
    if missing:
        text += f"。待补凭证：{', '.join(missing)}"
    version = str(payload.get("policyVersion") or "").strip()
    if version:
        text += f"（规则版本 {version}）"
    return text[:2000]


def _policy_safe_fallback(artifacts: list[AgentArtifact]) -> str:
    lines: list[str] = []
    for artifact in artifacts:
        if artifact.agent_id == "after_sales_policy_specialist":
            continue
        if artifact.status != "SUCCESS" or not _has_verified_evidence(artifact.evidence):
            continue
        text = artifact.draft_answer or "\n".join(artifact.facts)
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line and not _POLICY_FALLBACK_LINE_RE.search(line):
                lines.append(raw_line.rstrip())
    grounded = "\n".join(lines).strip()[:3500]
    abstention = (
        "未找到可引用的售后政策证据，因此无法确认具体售后资格；"
        "本次未执行任何业务操作。"
    )
    return f"{grounded}\n\n{abstention}" if grounded else abstention


async def supervisor_synthesis_node(state: dict[str, Any]) -> dict[str, Any]:
    plan = SupervisorPlan.model_validate(state.get("supervisor_plan") or {})
    artifacts: list[AgentArtifact] = []
    specialist_rank = {agent_id: index for index, agent_id in enumerate(plan.specialists)}
    raw_artifacts = list(state.get("specialist_artifacts") or [])
    raw_artifacts.sort(
        key=lambda raw: (
            specialist_rank.get(
                str(raw.get("agent_id") or "") if isinstance(raw, dict) else "",
                len(specialist_rank),
            ),
            str(raw.get("handoff_id") or "") if isinstance(raw, dict) else "",
        )
    )
    for raw in raw_artifacts:
        raw_agent_id = raw.get("agent_id") if isinstance(raw, dict) else None
        raw_handoff_id = raw.get("handoff_id") if isinstance(raw, dict) else None
        try:
            artifact = _validate_artifact(raw)
            artifacts.append(artifact)
            validation_status = "OK"
            validation_reason = None
        except Exception as exc:
            validation_status = "BLOCKED"
            validation_reason = str(exc) or type(exc).__name__
        episode_service.record_step(
            "ARTIFACT_VALIDATION",
            node_name="artifact_validator",
            status=validation_status,
            output_data={
                "agentId": raw_agent_id,
                "reason": validation_reason,
            },
            agent_id="supervisor",
            handoff_id=raw_handoff_id,
            run_id=(state.get("agent_msg") or {}).get("runId"),
        )
    degraded_artifacts = [
        item
        for item in artifacts
        if item.status in {"DEGRADED", "FAILED", "BLOCKED"}
        or any(warning.startswith(("SPECIALIST_", "POLICY_EVIDENCE_")) for warning in item.warnings)
    ]
    timeout_artifacts = [
        item for item in degraded_artifacts if "SPECIALIST_TIMEOUT" in item.warnings
    ]
    if timeout_artifacts:
        episode_service.record_step(
            "FANOUT_TIMEOUT",
            node_name="supervisor_synthesis",
            status="DEGRADED",
            output_data={
                "completedArtifacts": len(artifacts),
                "timedOutArtifacts": len(timeout_artifacts),
            },
            agent_id="supervisor",
            run_id=(state.get("agent_msg") or {}).get("runId"),
        )
    if degraded_artifacts:
        episode_service.record_step(
            "FANOUT_DEGRADED",
            node_name="supervisor_synthesis",
            status="DEGRADED",
            output_data={
                "completedArtifacts": len(artifacts),
                "degradedArtifacts": len(degraded_artifacts),
                "warnings": sorted(
                    {warning for item in degraded_artifacts for warning in item.warnings}
                )[:20],
            },
            agent_id="supervisor",
            run_id=(state.get("agent_msg") or {}).get("runId"),
        )
    artifact_payload = [item.model_dump(mode="json") for item in artifacts]
    policy_evidence_missing = any(
        item.agent_id == "after_sales_policy_specialist"
        and not _has_policy_evidence(item.evidence)
        for item in artifacts
    )
    policy_instruction = (
        "售后政策专家没有提供可引用证据。必须明确说明未找到政策证据、无法确认资格；"
        "保留其他专家已经核实的订单和物流事实，但不得推断可以或不可以退款。"
        if policy_evidence_missing
        else "政策结论只能来自 artifact 中可引用的已发布知识证据。"
    )
    prompt = json.dumps(
        {
            "question": state.get("user_text"),
            "plan": plan.model_dump(mode="json"),
            "artifacts": artifact_payload,
        },
        ensure_ascii=False,
    )
    answer = ""
    try:
        llm = rt.bind_agent_llm(
            allowed_tools=frozenset(),
            disable_thinking=True,
            tools_enabled=False,
        )
        response = await asyncio.wait_for(
            invoke_llm_with_metrics(
                llm,
                [
                    SystemMessage(
                        content=(
                            "你是电商 Supervisor。根据已验证的内部专家 artifact 回答用户。"
                            "不得补造 artifact 中不存在的事实；冲突时明确说明并建议人工。"
                            f"{policy_instruction}"
                            "购物报价时间戳不得换算、截断或改成日期；正文只说明报价短期有效，"
                            "具体截止时间以结构化商品卡片为准。"
                            "只返回最终面向用户的简洁中文答复，不要输出内部规划或 SQL。"
                        )
                    ),
                    HumanMessage(content=prompt),
                ],
            ),
            timeout=8,
        )
        answer = strip_emojis(rt.chunk_text(getattr(response, "content", "") or ""))
    except Exception:
        answer = "；".join(item.draft_answer for item in artifacts if item.draft_answer)[:4000]
    if not answer:
        answer = "暂时没有足够的已验证信息，请补充订单信息或转人工处理。"
    if any(
        artifact.biz_type == "shopping_decision_v2" and artifact.assistant_cards
        for artifact in artifacts
    ):
        answer = _sanitize_offer_time_claims(answer)

    readonly_tools = list(
        dict.fromkeys(tool for artifact in artifacts for tool in artifact.tool_calls)
    )
    merged_tool_biz: dict[str, list[str]] = {}
    for artifact in artifacts:
        for key in ("productIds", "productNames", "orderIds"):
            values = [
                str(value)
                for value in artifact.tool_biz.get(key) or []
                if str(value or "").strip()
            ]
            if values:
                merged_tool_biz[key] = list(
                    dict.fromkeys([*(merged_tool_biz.get(key) or []), *values])
                )[:50]
    source_candidates = [
        *(state.get("rag_source_refs") or []),
        *[
            evidence
            for artifact in artifacts
            for evidence in artifact.evidence
            if evidence.get("type") != "tool_result"
        ],
    ]
    source_refs: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    for evidence in source_candidates:
        if not isinstance(evidence, dict):
            continue
        identity = str(
            evidence.get("chunkId")
            or evidence.get("documentId")
            or evidence.get("questionId")
            or evidence.get("productId")
            or evidence.get("orderId")
            or evidence.get("id")
            or ""
        )
        key = (str(evidence.get("type") or ""), identity)
        if not identity or key in source_keys:
            continue
        source_keys.add(key)
        source_refs.append(evidence)
    artifact_with_cards = next(
        (artifact for artifact in artifacts if artifact.assistant_cards), None
    )
    primary_artifact = artifact_with_cards or next(
        (artifact for artifact in artifacts if artifact.biz_type), None
    )
    rag_traces = [
        artifact.retrieval_trace
        for artifact in artifacts
        if artifact.retrieval_trace
    ]

    result: dict[str, Any] = {
        "chunks": [answer],
        "tools_called": readonly_tools,
        "tool_biz": merged_tool_biz or None,
        "biz_type": primary_artifact.biz_type if primary_artifact else None,
        "biz_data": primary_artifact.biz_data if primary_artifact else None,
        "search_tool_hint": next(
            (artifact.search_tool_hint for artifact in artifacts if artifact.search_tool_hint),
            None,
        ),
        "rag_source_refs": source_refs[:30],
        "rag_trace": (
            {"ragMode": state.get("rag_mode"), "retrievals": rag_traces}
            if rag_traces
            else state.get("rag_trace")
        ),
        "route": "finalize",
        "supervisor_plan": plan.model_dump(mode="json"),
    }
    if policy_evidence_missing:
        result["verifier_fallback"] = _policy_safe_fallback(artifacts)
    if artifacts:
        first_cards = next(
            (item.assistant_cards for item in artifacts if item.assistant_cards), None
        )
        support_cards_unrequested = is_support_case_cards_json(first_cards) and not _contains(
            str(state.get("user_text") or ""), *_SUPPORT_CASE_CARD_TERMS
        )
        order_cards_unrequested = (
            is_order_cards_json(first_cards)
            and plan.intent != IntentKind.QUERY_ORDER.value
        )
        if first_cards and not support_cards_unrequested and not order_cards_unrequested:
            result["assistant_cards"] = first_cards

    # The only write-capable step in the multi-agent path. It runs after all
    # read-only artifacts have joined and still creates a pending confirmation.
    if plan.requires_action and plan.action_type:
        args = _action_args(state, plan.action_type)
        evidence_refs = [
            evidence
            for artifact in artifacts
            for evidence in artifact.evidence
            if evidence.get("type") != "tool_result"
        ][:20]
        evidence_failure = _action_evidence_failure(plan, artifacts)
        if evidence_failure:
            proposal_contract = ActionProposal(
                tool=plan.action_type,
                arguments=args or {},
                success=False,
                evidence_refs=evidence_refs,
                reason=evidence_failure,
            )
            result["action_proposal"] = proposal_contract.model_dump(mode="json")
            result["chunks"] = [
                answer.rstrip("。") + "。当前核验依据不足，暂不创建操作确认，请转人工处理。"
            ]
            episode_service.record_step(
                "ACTION_POLICY_DECISION",
                node_name="action_executor",
                status="BLOCKED",
                tool_name=plan.action_type,
                output_data={
                    "proposal": proposal_contract.model_dump(mode="json"),
                    "success": False,
                    "reason": evidence_failure,
                    "requiresConfirmation": True,
                },
                run_id=(state.get("agent_msg") or {}).get("runId"),
            )
        elif args:
            try:
                proposal = await mcp_tool_router.invoke(
                    plan.action_type,
                    args,
                    str(state.get("user_id") or ""),
                    call_id="supervisor-action",
                )
            except Exception as exc:
                proposal_contract = ActionProposal(
                    tool=plan.action_type,
                    arguments=args,
                    success=False,
                    evidence_refs=evidence_refs,
                    reason="ACTION_EXECUTOR_FAILED",
                )
                result["action_proposal"] = proposal_contract.model_dump(mode="json")
                result["chunks"] = [_action_failure_answer(answer)]
                episode_service.record_step(
                    "ACTION_POLICY_DECISION",
                    node_name="action_executor",
                    status="ERROR",
                    tool_name=plan.action_type,
                    error_code=type(exc).__name__,
                    output_data={
                        "proposal": proposal_contract.model_dump(mode="json"),
                        "success": False,
                        "reason": proposal_contract.reason,
                        "requiresConfirmation": True,
                    },
                    run_id=(state.get("agent_msg") or {}).get("runId"),
                )
            else:
                proposal_observation = build_tool_result_observation(proposal)
                proposal_usable = proposal.success and not proposal_observation.contaminated
                result["tools_called"] = list(
                    dict.fromkeys([*readonly_tools, plan.action_type])
                )
                proposal_contract = ActionProposal(
                    tool=plan.action_type,
                    arguments=args,
                    success=proposal_usable,
                    evidence_refs=evidence_refs,
                    reason=(
                        None
                        if proposal_usable
                        else "TOOL_RESULT_QUARANTINED"
                        if proposal_observation.contaminated
                        else proposal.error_code or "ACTION_REJECTED"
                    ),
                )
                result["action_proposal"] = proposal_contract.model_dump(mode="json")
                messages = list(state.get("llm_messages") or [])
                messages.append(
                    ToolMessage(
                        content=proposal_observation.text,
                        tool_call_id="supervisor-action",
                    )
                )
                result["llm_messages"] = messages
                if proposal_usable and proposal.assistant_cards:
                    result["assistant_cards"] = proposal.assistant_cards
                if not proposal_usable:
                    result["chunks"] = [_action_failure_answer(answer)]
                episode_service.record_step(
                    "ACTION_POLICY_DECISION",
                    node_name="action_executor",
                    status="OK" if proposal_usable else "BLOCKED",
                    tool_name=plan.action_type,
                    output_data={
                        "proposal": proposal_contract.model_dump(mode="json"),
                        "success": proposal_usable,
                        "reason": proposal_contract.reason,
                        "requiresConfirmation": True,
                        "quarantined": proposal_observation.contaminated,
                    },
                    run_id=(state.get("agent_msg") or {}).get("runId"),
                )
        else:
            proposal_contract = ActionProposal(
                tool=plan.action_type,
                success=False,
                evidence_refs=evidence_refs,
                reason="VERIFIED_ACTION_ARGS_MISSING",
            )
            result["action_proposal"] = proposal_contract.model_dump(mode="json")
            result["chunks"] = [_action_failure_answer(answer)]
            episode_service.record_step(
                "ACTION_POLICY_DECISION",
                node_name="action_executor",
                status="BLOCKED",
                tool_name=plan.action_type,
                output_data={
                    "proposal": proposal_contract.model_dump(mode="json"),
                    "success": False,
                    "reason": "VERIFIED_ACTION_ARGS_MISSING",
                    "requiresConfirmation": True,
                },
                run_id=(state.get("agent_msg") or {}).get("runId"),
            )
    episode_service.record_step(
        "SUPERVISOR_SYNTHESIS",
        node_name="supervisor_synthesis",
        status="OK",
        output_data={"artifactCount": len(artifacts), "answerChars": len(answer)},
        agent_id="supervisor",
        run_id=(state.get("agent_msg") or {}).get("runId"),
    )
    return result


async def supervisor_plan_node(state: dict[str, Any]) -> dict[str, Any]:
    fallback = build_supervisor_plan(state)
    try:
        plan = await _structured_supervisor_plan(state, fallback)
    except Exception:
        plan = fallback
    run_id = (state.get("agent_msg") or {}).get("runId")
    shopping_mission: dict[str, Any] = {}
    shopping_summary = ""
    if "shopping_advisor" in plan.specialists:
        try:
            current_mission = await shopping_mission_service.load(
                str(state.get("user_id") or "")
            )
            shopping_mission = shopping_mission_service.specialist_context(
                current_mission
            )
            shopping_summary = mission_summary(current_mission)[:1000]
        except Exception as exc:
            episode_service.record_step(
                "SPECIALIST_CONTEXT",
                node_name="supervisor_plan",
                status="DEGRADED",
                error_code=type(exc).__name__,
                output_data={"context": "shoppingMission", "available": False},
                agent_id="supervisor",
                run_id=run_id,
            )
    tasks: list[dict[str, Any]] = []
    for agent_id in plan.specialists:
        spec = AGENT_SPECS[agent_id]
        identity = f"{run_id}:{state.get('message_id')}:{agent_id}"
        handoff_id = uuid.uuid5(uuid.NAMESPACE_URL, "handoff:" + identity).hex
        child_run_id = uuid.uuid5(uuid.NAMESPACE_URL, "child:" + identity).hex
        verified_context = _specialist_verified_context(
            state,
            agent_id=agent_id,
        )
        task_tool_scope = _task_tools_for_specialist(state, agent_id)
        task = SpecialistTask(
            handoff_id=handoff_id,
            child_run_id=child_run_id,
            parent_run_id=run_id,
            agent_id=agent_id,
            agent_version=spec.version,
            goal=_sanitize_specialist_text(
                plan.goals.get(agent_id, str(state.get("user_text") or "")),
                max_length=500,
            ),
            user_id=str(state.get("user_id") or ""),
            user_text=_sanitize_specialist_text(state.get("user_text"), max_length=4000),
            source_message_id=(
                int(state["message_id"]) if state.get("message_id") is not None else None
            ),
            session_summary=(
                _sanitize_specialist_text(shopping_summary, max_length=1000)
                if agent_id == "shopping_advisor"
                else ""
            ),
            verified_context=verified_context,
            shopping_mission=(
                shopping_mission if agent_id == "shopping_advisor" else {}
            ),
            verified_image_context=(
                state.get("verified_image_context")
                if agent_id in {"shopping_advisor", "after_sales_policy_specialist"}
                else None
            ),
            tool_scope=task_tool_scope,
            required_tools=_required_tools_for_specialist(state, agent_id),
            max_rounds=min(get_settings().multi_agent_specialist_max_rounds, spec.max_rounds),
            max_tokens=spec.token_budget,
            timeout_seconds=min(
                spec.timeout_seconds,
                get_settings().multi_agent_specialist_timeout_seconds,
            ),
        )
        task_data = task.model_dump(mode="json")
        tasks.append(task_data)
        episode_service.start_child_run(
            run_id=child_run_id,
            parent_run_id=run_id,
            handoff_id=handoff_id,
            message_id=None,
            user_id=task.user_id,
            session_id=(state.get("agent_msg") or {}).get("sessionId"),
            agent_id=agent_id,
            agent_version=spec.version,
            actor_type="USER",
            intent=state.get("intent"),
        )
        episode_service.record_handoff(
            handoff_id=handoff_id,
            parent_run_id=run_id,
            child_run_id=child_run_id,
            source_agent="supervisor",
            target_agent=agent_id,
            status="STARTED",
            envelope=task_data,
        )
    episode_service.record_step(
        "SUPERVISOR_PLAN",
        node_name="supervisor_plan",
        status="OK",
        output_data=plan.model_dump(mode="json"),
        agent_id="supervisor",
        run_id=run_id,
    )
    return {
        "supervisor_plan": plan.model_dump(mode="json"),
        "specialist_tasks": tasks,
        "route": "multi_agent_fanout",
    }
