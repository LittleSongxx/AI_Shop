import operator
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

RouteKind = Literal[
    "agent_loop",
    "tools",
    "multi_agent_plan",
    "multi_agent_fanout",
    "multi_agent_synthesis",
    "orchestration_router",
    "deterministic_workflow",
    "finalize",
    "post_turn",
    "end",
]

class AgentGraphState(TypedDict, total=False):

    agent_msg: dict
    user_id: str
    message_id: int
    request_id: str
    run_id: str
    episode_id: str
    traceparent: str | None
    user_message: str
    user_text: str
    verified_image_context: dict | None
    image_understanding: str | None
    from_product: bool
    card: dict | None
    message_card: dict | None
    comparison_product_ids: list[str]

    cancelled: bool
    finished: bool
    route: RouteKind

    # 用户最终看到什么。Worker 以此决定任务终态：只有 ok 才进 COMPLETED，
    # llm_error / graph_error 说明用户收到的是错误文案，绝不应当成成功任务。
    outcome: str | None

    llm_messages: list[BaseMessage]
    working_turns: list[dict]
    working_oldest_id: int | None

    chunks: Annotated[list[str], operator.add]
    react_round: int
    tools_called: Annotated[list[str], operator.add]
    pending_tool_calls: list[dict]

    tool_biz: dict | None
    biz_type: str | None
    biz_data: str | None
    assistant_cards: str | None
    search_tool_hint: str | None
    search_fallback_done: bool
    category_switch_search: bool
    intent: str | None
    intent_data: str | None
    intent_decision: dict | None
    request_mode: str | None
    # Authoritative refs returned by business tools (orders, offers, coupons,
    # logistics, etc.). Keep this separate from RAG refs so business facts do
    # not accidentally satisfy a policy-citation requirement.
    tool_source_refs: list[dict] | None
    rag_source_refs: list[dict] | None
    rag_trace: dict | None
    rag_evidence_state: str
    rag_evidence_items: list[dict]
    rag_safe_business_query: str
    rag_repair_attempted: bool
    rag_repair_reason: str | None
    rag_mode: str
    rag_queries: list[str]
    rag_retrieval_count: int
    rag_agentic_allowed: bool
    rag_evidence_required: bool
    input_security_flags: list[str]
    order_resolution: str | None
    # Redacted evidence for the order-reference stage. This stays separate
    # from policy RAG refs and from the user-visible answer.
    order_reference_evidence: dict | None
    pending_order_reference: dict | None
    selected_order_reference: dict | None

    # Root orchestration state. Specialist scratch messages never enter this
    # schema; parallel branches return validated artifacts only.
    verified_order_context: dict | None
    supervisor_plan: dict | None
    specialist_tasks: list[dict]
    specialist_artifacts: Annotated[list[dict], operator.add]
    action_proposal: dict | None
    verifier_fallback: str | None
    orchestration_mode: str | None
    orchestration_reason: str | None
    resolved_order_tool: dict | None
    llm_skipped: bool
    llm_skip_reason: str | None
    # Set only by fixed graph branches that provide a non-policy next step.
    # It never comes from model output or user-controlled state.
    deterministic_clarification: bool
    structured_result_finalized: bool

def initial_state(agent_msg: dict, card: dict | None, user_text: str) -> AgentGraphState:

    image_context = _verified_image_context(agent_msg)

    return {
        "agent_msg": agent_msg,
        "user_id": agent_msg["userId"],
        "message_id": agent_msg["messageId"],
        "request_id": str(
            agent_msg.get("requestId")
            or f"req_{agent_msg.get('runId') or agent_msg['messageId']}"
        ),
        "run_id": str(agent_msg.get("runId") or ""),
        "episode_id": str(agent_msg.get("episodeId") or agent_msg.get("runId") or ""),
        "traceparent": agent_msg.get("traceparent"),
        "user_message": agent_msg.get("userMessage") or "",
        "user_text": user_text,
        "verified_image_context": image_context,
        "image_understanding": None,
        "from_product": bool(agent_msg.get("fromProduct")),
        "card": card,
        "message_card": card,
        "comparison_product_ids": list(agent_msg.get("comparisonProductIds") or []),
        "cancelled": False,
        "finished": False,
        "route": "agent_loop",
        "outcome": None,
        "llm_messages": [],
        "working_turns": [],
        "working_oldest_id": None,
        "chunks": [],
        "react_round": 0,
        "tools_called": [],
        "pending_tool_calls": [],
        "tool_biz": None,
        "biz_type": None,
        "biz_data": None,
        "assistant_cards": None,
        "search_tool_hint": None,
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": None,
        "intent_data": None,
        "intent_decision": None,
        "request_mode": None,
        "tool_source_refs": [],
        "rag_source_refs": [],
        "rag_trace": None,
        "rag_evidence_state": "INSUFFICIENT",
        "rag_evidence_items": [],
        "rag_safe_business_query": user_text,
        "rag_repair_attempted": False,
        "rag_repair_reason": None,
        "rag_mode": "conditional",
        "rag_queries": [],
        "rag_retrieval_count": 0,
        "rag_agentic_allowed": False,
        "rag_evidence_required": False,
        "input_security_flags": list(agent_msg.get("inputSecurityFlags") or []),
        "order_resolution": None,
        "order_reference_evidence": None,
        "pending_order_reference": None,
        "selected_order_reference": agent_msg.get("selectedOrderReference"),
        "verified_order_context": None,
        "supervisor_plan": None,
        "specialist_tasks": [],
        "specialist_artifacts": [],
        "action_proposal": None,
        "verifier_fallback": None,
        "orchestration_mode": None,
        "orchestration_reason": None,
        "resolved_order_tool": None,
        "llm_skipped": False,
        "llm_skip_reason": None,
        "deterministic_clarification": False,
        "structured_result_finalized": False,
    }

def thread_id_for(user_id: str, message_id: int) -> str:

    return f"{user_id}:{message_id}"


def _verified_image_context(agent_msg: dict) -> dict | None:
    explicit = agent_msg.get("verifiedImageContext")
    if isinstance(explicit, dict):
        return explicit
    snapshot = agent_msg.get("imageSnapshot")
    if not isinstance(snapshot, dict) or not snapshot.get("assetId"):
        return None
    subject = agent_msg.get("selectedVisualSubject")
    return {
        "asset_id": snapshot.get("assetId"),
        "moderation_status": snapshot.get("moderationStatus") or "APPROVED",
        "content_sha256": snapshot.get("contentSha256"),
        "mime_type": snapshot.get("mimeType"),
        "width": snapshot.get("width"),
        "height": snapshot.get("height"),
        "scene": snapshot.get("scene") or "agent",
        "expires_at": snapshot.get("expiresAt"),
        "selected_subject": subject,
    }
