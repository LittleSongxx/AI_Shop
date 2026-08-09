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

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Send

from app.config.settings import get_settings
from app.domain.intent.types import IntentKind
from app.domain.intent.write_args import extract_review_content, extract_review_star
from app.harness.agents.contracts import (
    ActionProposal,
    AgentArtifact,
    SpecialistTask,
    SupervisorPlan,
)
from app.harness.agents.registry import AGENT_SPECS
from app.harness.guardrails.output_guard import strip_emojis
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services import agent_runtime as rt
from app.services.episode_service import bind_episode, episode_service
from app.services.llm_factory import create_memory_llm
from app.services.mcp_tool_router import mcp_tool_router
from app.services.shopping_profile_service import shopping_profile_service
from app.utils.biz_payload import (
    is_action_confirm_json,
    is_order_cards_json,
    is_support_case_cards_json,
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
_SHOPPING_PROFILE_FIELDS = (
    "category",
    "budgetMin",
    "budgetMax",
    "brands",
    "excludedBrands",
    "scenarios",
    "features",
    "acceptSubstitute",
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
)
_TOOL_EVIDENCE_TYPES = {
    "SEARCH_PRODUCTS": frozenset({"product", "product_detail"}),
    "GET_PRODUCT_DETAIL": frozenset({"product", "product_detail"}),
    "COMPARE_PRODUCTS": frozenset({"product", "product_detail"}),
    "QUERY_ORDERS": frozenset({"order", "order_item"}),
    "QUERY_LOGISTICS": frozenset({"logistics", "order"}),
    "QUERY_COMMENT": frozenset({"comment", "order"}),
    "QUERY_REFUND_STATUS": frozenset({"refund", "order", "order_item"}),
    "QUERY_USER_COUPONS": frozenset({"coupon"}),
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
    return any(marker in text for marker in _TOOL_PROTOCOL_MARKERS)


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
    shopping_profile: dict[str, Any],
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
        if shopping_profile:
            context["shoppingProfile"] = {
                field: _sanitize_context_value(value) for field, value in shopping_profile.items()
            }
    return context


def build_supervisor_plan(state: dict[str, Any]) -> SupervisorPlan:
    """Build a bounded plan from the already classified intent.

    The deterministic matrix is the safety fallback for a structured planner;
    it also makes rollout and regression tests independent of a provider.
    """

    intent = str(state.get("intent") or "")
    text = str(state.get("user_text") or "")
    specialists: list[str] = []
    goals: dict[str, str] = {}
    if intent == IntentKind.QUERY_ORDER.value and _contains(text, "再买一次", "再买", "复购"):
        specialists = ["shopping_advisor"]
        goals["shopping_advisor"] = "基于已验证订单中的商品信息检索当前可售商品，提供复购入口。"
    elif intent in {
        IntentKind.PRODUCT_SEARCH.value,
        IntentKind.PRODUCT_CONSULT.value,
    }:
        specialists = ["shopping_advisor"]
        goals["shopping_advisor"] = "检索商品事实、价格库存与适配性，只返回可验证商品信息。"
    elif intent in {
        IntentKind.COMPLAINT.value,
        IntentKind.PAYMENT_ISSUE.value,
    }:
        specialists = ["after_sales_policy_specialist"]
        goals["after_sales_policy_specialist"] = "核对售后政策、工单路径和所需材料，不执行写操作。"
    elif intent in _ACTION_INTENTS or intent in {
        IntentKind.REFUND_STATUS.value,
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.QUERY_COUPON.value,
    }:
        specialists = ["order_fulfillment_specialist"]
        goals["order_fulfillment_specialist"] = "查询订单、物流和售后状态，输出已验证的订单事实。"
        if intent in _ACTION_INTENTS or _contains(text, "政策", "规则", "能不能退", "符合"):
            specialists.append("after_sales_policy_specialist")
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
        action_type
        and (state.get("verified_order_context") or action_type == "PROPOSE_CREATE_SUPPORT_CASE")
    )
    return SupervisorPlan(
        intent=intent or None,
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
    }
)


def _tool_args(
    task: SpecialistTask,
    raw_args: dict[str, Any],
    run_id: str,
    tool_name: str | None = None,
) -> dict[str, Any]:
    args = dict(raw_args or {})
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
    args["userId"] = task.user_id
    args["runId"] = run_id
    return args


async def specialist_runner_node(state: dict[str, Any]) -> dict[str, Any]:
    raw_task = state.get("specialist_task") or {}
    try:
        task = SpecialistTask.model_validate(raw_task)
        spec = AGENT_SPECS.get(task.agent_id)
        if spec is None:
            raise ValueError("SPECIALIST_AGENT_UNKNOWN")
        if frozenset(task.tool_scope) != spec.tool_allowlist:
            raise ValueError("SPECIALIST_TASK_TOOL_SCOPE_INVALID")
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


async def _execute_specialist_task(task: SpecialistTask) -> dict[str, Any]:
    child_run_id = task.child_run_id
    spec = AGENT_SPECS[task.agent_id]
    if frozenset(task.tool_scope) != spec.tool_allowlist:
        raise ValueError("SPECIALIST_TASK_TOOL_SCOPE_INVALID")
    episode_service.record_step(
        "SPECIALIST_STARTED",
        node_name="specialist_runner",
        status="OK",
        output_data={
            "goal": task.goal,
            "toolScope": sorted(task.tool_scope),
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
                f"允许的只读工具：{', '.join(sorted(task.tool_scope)) or '无'}。\n"
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
    warnings: list[str] = []
    draft = ""
    status = "SUCCESS"
    error_code: str | None = None
    try:
        async with asyncio.timeout(task.timeout_seconds):
            for round_no in range(task.max_rounds):
                llm = rt.bind_agent_llm(
                    allowed_tools=spec.tool_allowlist,
                    max_tokens=task.max_tokens,
                    disable_thinking=True,
                )
                response = await invoke_llm_with_metrics(
                    llm, messages, model=getattr(llm, "model_name", None)
                )
                messages.append(response)
                tool_calls = list(getattr(response, "tool_calls", None) or [])
                if not tool_calls:
                    draft = strip_emojis(rt.chunk_text(getattr(response, "content", "") or ""))
                    break
                for call in tool_calls:
                    name = str(call.get("name") or "")
                    if name not in spec.tool_allowlist or name.startswith("PROPOSE_"):
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
                    )
                    called.append(name)
                    evidence.append(
                        {
                            "type": "tool_result",
                            "tool": name,
                            "success": result.success,
                            "errorCode": result.error_code,
                        }
                    )
                    if result.success and result.source_refs:
                        evidence.extend(result.source_refs)
                    if result.success and result.assistant_cards:
                        assistant_cards = result.assistant_cards
                    tool_message = result.to_tool_message()
                    if result.success:
                        verified_tool_outputs.append(f"{name}: {tool_message}"[:3000])
                    messages.append(
                        ToolMessage(
                            content=tool_message, tool_call_id=call.get("id") or name
                        )
                    )
                    episode_service.record_step(
                        "SPECIALIST_TOOL",
                        node_name="specialist_runner",
                        status="OK" if result.success else "ERROR",
                        output_data={
                            "tool": name,
                            "success": result.success,
                            "sourceCount": len(result.source_refs or []),
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
        status = "FAILED"
        error_code = "SPECIALIST_TIMEOUT"
        warnings.extend(["SPECIALIST_TIMEOUT", "专家超时，已使用其他可用证据降级回答。"])
    except Exception as exc:
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
        confidence=0.75 if draft and evidence else 0.35 if draft else 0.0,
        next_step="FINALIZE" if draft else "FALLBACK",
        warnings=warnings,
        tool_calls=called,
        handoff_id=task.handoff_id,
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
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
        evidence = dict(state.get("image_evidence") or {})
        if evidence:
            args.update(
                {
                    "imagePath": evidence.get("path"),
                    "imageModerationId": evidence.get("moderationId"),
                    "imageDescription": evidence.get("vlmDescription"),
                    "vlmStatus": evidence.get("vlmStatus"),
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
    if artifact.draft_answer and not has_verified_result:
        artifact.status = "BLOCKED"
        artifact.facts = []
        artifact.draft_answer = ""
        artifact.confidence = 0.0
        artifact.next_step = "FALLBACK"
        warnings.append("UNVERIFIED_FACTS_DROPPED")
    has_knowledge_evidence = _has_policy_evidence(artifact.evidence)
    if artifact.agent_id == "after_sales_policy_specialist" and not has_knowledge_evidence:
        # Order/tool facts do not prove a policy. Preserve a safe, useful
        # handoff signal instead of allowing an unsupported eligibility claim
        # to reach Supervisor synthesis.
        artifact.status = "DEGRADED"
        artifact.facts = []
        artifact.draft_answer = "未找到可引用的售后政策证据，无法确认资格，建议人工核验。"
        artifact.confidence = 0.0
        artifact.next_step = "HUMAN_HANDOFF"
        warnings.append("POLICY_EVIDENCE_MISSING")
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
            and str(item.get("tool") or "") in _ORDER_CONTEXT_TOOLS
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
        )
        if not has_policy_source:
            return "POLICY_EVIDENCE_INSUFFICIENT"
    return None


def _action_failure_answer(answer: str) -> str:
    base = str(answer or "").strip().rstrip("。")
    suffix = "操作确认未创建，请稍后重试或转人工处理。"
    return f"{base}。{suffix}" if base else suffix


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

    result: dict[str, Any] = {
        "chunks": [answer],
        "tools_called": [],
        "rag_source_refs": [
            evidence
            for artifact in artifacts
            for evidence in artifact.evidence
            if evidence.get("type") != "tool_result"
        ][:30],
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
                result["tools_called"] = [plan.action_type]
                proposal_contract = ActionProposal(
                    tool=plan.action_type,
                    arguments=args,
                    success=proposal.success,
                    evidence_refs=evidence_refs,
                    reason=None if proposal.success else (proposal.error_code or "ACTION_REJECTED"),
                )
                result["action_proposal"] = proposal_contract.model_dump(mode="json")
                messages = list(state.get("llm_messages") or [])
                messages.append(
                    ToolMessage(
                        content=proposal.to_tool_message(),
                        tool_call_id="supervisor-action",
                    )
                )
                result["llm_messages"] = messages
                if proposal.success and proposal.assistant_cards:
                    result["assistant_cards"] = proposal.assistant_cards
                if not proposal.success:
                    result["chunks"] = [_action_failure_answer(answer)]
                episode_service.record_step(
                    "ACTION_POLICY_DECISION",
                    node_name="action_executor",
                    status="OK" if proposal.success else "BLOCKED",
                    tool_name=plan.action_type,
                    output_data={
                        "proposal": proposal_contract.model_dump(mode="json"),
                        "success": proposal.success,
                        "reason": proposal_contract.reason,
                        "requiresConfirmation": True,
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
    shopping_profile: dict[str, Any] = {}
    shopping_summary = ""
    if "shopping_advisor" in plan.specialists:
        try:
            effective_profile = await shopping_profile_service.get_effective_profile(
                str(state.get("user_id") or "")
            )
            shopping_profile = {
                field: effective_profile.get(field)
                for field in _SHOPPING_PROFILE_FIELDS
                if effective_profile.get(field) not in (None, [], "")
            }
            shopping_summary = shopping_profile_service.summary(shopping_profile)[:1000]
        except Exception as exc:
            episode_service.record_step(
                "SPECIALIST_CONTEXT",
                node_name="supervisor_plan",
                status="DEGRADED",
                error_code=type(exc).__name__,
                output_data={"context": "shoppingProfile", "available": False},
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
            shopping_profile=shopping_profile,
        )
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
            session_summary=(
                _sanitize_specialist_text(shopping_summary, max_length=1000)
                if agent_id == "shopping_advisor"
                else ""
            ),
            verified_context=verified_context,
            tool_scope=sorted(spec.tool_allowlist),
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
