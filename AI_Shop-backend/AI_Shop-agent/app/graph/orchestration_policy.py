"""Evidence-driven Workflow, single-agent, and multi-agent routing policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from app.domain.intent.rules import deterministic_social_reply
from app.domain.intent.types import IntentKind, RequestMode

OrchestrationMode = Literal["workflow", "single_agent", "multi_agent"]
ConfiguredMode = Literal["adaptive", "workflow", "single_agent", "multi_agent"]

_WORKFLOW_READ_INTENTS = frozenset(
    {
        # A plain product search is an authoritative read: the query parser
        # and Java-backed search tool can execute it without asking an LLM to
        # decide whether to call SEARCH_PRODUCTS. Composite and product
        # consultation requests are filtered separately below.
        IntentKind.PRODUCT_SEARCH.value,
        IntentKind.QUERY_ORDER.value,
        IntentKind.QUERY_LOGISTICS.value,
        IntentKind.QUERY_FULFILLMENT.value,
        IntentKind.QUERY_COUPON.value,
        IntentKind.QUERY_COMMENT.value,
        IntentKind.REFUND_STATUS.value,
    }
)
_COMPOSITE_CONNECTORS = (
    "顺便",
    "同时",
    "另外",
    "以及",
    "并且",
    "还想",
    "分别",
)
_DOMAIN_MARKERS: dict[str, tuple[str, ...]] = {
    "shopping": ("商品", "推荐", "价格", "库存", "优惠", "适合"),
    "order": ("订单", "物流", "发货", "收货", "签收", "配送"),
    "after_sales": (
        "退款",
        "退货",
        "换货",
        "售后",
        "政策",
        "投诉",
        "发票",
        "支付",
    ),
}


def fast_support_eligible(state: Mapping[str, Any]) -> bool:
    """Whether a latency experiment may use a bounded first LLM turn.

    This is intentionally stricter than "does not write": RAG grounding,
    security-flagged input, handoff, and non-read request modes keep the normal
    reasoning budget.  The caller still needs the explicit settings switch.
    """

    if int(state.get("react_round") or 0) != 0:
        return False
    if state.get("rag_evidence_required") or state.get("rag_agentic_allowed"):
        return False
    if state.get("input_security_flags"):
        return False
    request_mode = str(state.get("request_mode") or "")
    if request_mode not in {RequestMode.READ_QUERY.value, RequestMode.INFORMATIONAL.value}:
        return False
    decision = state.get("intent_decision")
    if not isinstance(decision, Mapping):
        return False
    if str(decision.get("risk_level") or decision.get("riskLevel") or "LOW").upper() != "LOW":
        return False
    if str(decision.get("next_action") or decision.get("nextAction") or "").upper() in {
        "HANDOFF",
        "HANDOFF_SUGGESTED",
    }:
        return False
    return True


@dataclass(frozen=True)
class OrchestrationDecision:
    mode: OrchestrationMode
    reason: str

    @property
    def route(self) -> str:
        return {
            "workflow": "deterministic_workflow",
            "single_agent": "agent_loop",
            "multi_agent": "multi_agent_plan",
        }[self.mode]


def select_orchestration(
    state: Mapping[str, Any],
    *,
    configured_mode: ConfiguredMode = "adaptive",
    multi_agent_enabled: bool = True,
) -> OrchestrationDecision:
    """Select the least complex serving path that satisfies the request."""
    if configured_mode == "single_agent":
        return OrchestrationDecision("single_agent", "configured_single_agent")
    if configured_mode == "multi_agent":
        if multi_agent_enabled:
            return OrchestrationDecision("multi_agent", "configured_multi_agent")
        return OrchestrationDecision("single_agent", "multi_agent_kill_switch")
    if configured_mode == "workflow":
        if _workflow_eligible(state):
            return OrchestrationDecision("workflow", "configured_workflow")
        return OrchestrationDecision("single_agent", "configured_workflow_inapplicable")

    if _workflow_eligible(state):
        return OrchestrationDecision("workflow", "deterministic_business_path")
    if multi_agent_enabled and _cross_domain_request(state):
        return OrchestrationDecision("multi_agent", "cross_domain_request")
    return OrchestrationDecision("single_agent", "open_or_incomplete_request")


def _workflow_eligible(state: Mapping[str, Any]) -> bool:
    if _looks_composite(str(state.get("user_text") or "")):
        return False
    decision = state.get("intent_decision")
    if (
        str(state.get("intent") or "") == IntentKind.CHAT.value
        and isinstance(decision, Mapping)
        and str(decision.get("source") or "") == "deterministic_social"
        and not state.get("input_security_flags")
        and deterministic_social_reply(str(state.get("user_text") or "")) is not None
    ):
        return True
    # The order resolver has verified ownership and exact target arguments;
    # write eligibility is supplied by the separate Java/policy capability
    # gate before ``resolved_order_tool`` is populated. A conditional RAG
    # prefetch must not promote that bounded workflow into multi-agent policy.
    if state.get("resolved_order_tool"):
        return True
    if state.get("rag_evidence_required"):
        return False
    # Product search has a deterministic tool contract. Keep product detail
    # consultation and visual search on the Agent path because they may need
    # explanation or image-specific reasoning.
    return (
        str(state.get("request_mode") or "") == RequestMode.READ_QUERY.value
        and str(state.get("intent") or "") in _WORKFLOW_READ_INTENTS
    )


def _cross_domain_request(state: Mapping[str, Any]) -> bool:
    if state.get("rag_evidence_required") and state.get("verified_order_context"):
        return True
    text = str(state.get("user_text") or "")
    domains = {
        domain
        for domain, markers in _DOMAIN_MARKERS.items()
        if any(marker in text for marker in markers)
    }
    return len(domains) >= 2 and _looks_composite(text)


def _looks_composite(text: str) -> bool:
    return any(marker in text for marker in _COMPOSITE_CONNECTORS) or (
        text.count("？") + text.count("?") >= 2
    )
