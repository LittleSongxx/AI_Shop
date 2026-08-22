"""Evidence-driven Workflow, single-agent, and multi-agent routing policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

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
    # The order resolver has already verified ownership, state eligibility,
    # and exact tool arguments. A conditional RAG prefetch must not promote a
    # single deterministic write proposal into a multi-agent policy workflow.
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
