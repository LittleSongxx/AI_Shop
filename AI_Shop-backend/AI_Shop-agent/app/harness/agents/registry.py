from __future__ import annotations

from dataclasses import dataclass

from app.domain.intent.types import IntentKind


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    version: str
    prompt_name: str
    tool_allowlist: frozenset[str]
    max_rounds: int
    token_budget: int
    instructions: str = ""
    allows_proposals: bool = False
    model_config: dict[str, object] = None  # type: ignore[assignment]
    prompt_version: str = "v1"
    input_schema: str = "HandoffEnvelope"
    output_schema: str = "AgentArtifact"
    timeout_seconds: int = 30
    admin_only: bool = False

    def __post_init__(self) -> None:
        if self.model_config is None:
            object.__setattr__(self, "model_config", {"provider": "existing_llm"})


SHOPPING_TOOLS = frozenset(
    {
        "SEARCH_PRODUCTS",
        "GET_PRODUCT_DETAIL",
        "COMPARE_PRODUCTS",
        "SEARCH_PRODUCTS_BY_IMAGE",
    }
)
ORDER_TOOLS = frozenset(
    {
        "QUERY_ORDERS",
        "QUERY_LOGISTICS",
        "QUERY_COMMENT",
        "QUERY_REFUND_STATUS",
        "QUERY_USER_COUPONS",
        "QUERY_SUPPORT_CASES",
    }
)
AFTER_SALES_TOOLS = frozenset(
    {
        "QUERY_SUPPORT_CASES",
        "CHECK_AFTER_SALES_ELIGIBILITY",
        "SEARCH_KNOWLEDGE",
    }
)

AGENT_SPECS: dict[str, AgentSpec] = {
    "supervisor": AgentSpec("supervisor", "v1", "agent", frozenset(), 1, 1600),
    "shopping_advisor": AgentSpec(
        "shopping_advisor",
        "v1",
        "product_consult",
        SHOPPING_TOOLS,
        2,
        2400,
        instructions=(
            "只处理商品检索、商品详情、对比和偏好约束推荐。价格、库存和属性必须来自工具；"
            "找不到商品时明确说明，不得转而处理订单或售后交易。"
        ),
    ),
    "order_fulfillment_specialist": AgentSpec(
        "order_fulfillment_specialist",
        "v1",
        "query_order",
        ORDER_TOOLS,
        2,
        2200,
        instructions=(
            "只核对用户所属订单、物流、评价、优惠券、退款状态和工单状态。"
            "订单事实必须来自只读工具，不解释未检索到的政策，也不得建议已完成交易。"
        ),
    ),
    "after_sales_policy_specialist": AgentSpec(
        "after_sales_policy_specialist",
        "v1",
        "after_sales_policy",
        AFTER_SALES_TOOLS,
        2,
        2400,
        instructions=(
            "先调用规则引擎核验具体订单资格，再检索售后政策证据解释条件；"
            "资格结论必须带规则版本和事实引用，政策证据不足时保守答复；"
            "证据不足时保守答复或建议人工，永远不创建退款、取消、评价或工单提案。"
        ),
    ),
}

DATA_ANALYST_SPEC = AgentSpec(
    "data_analyst", "v1", "data_analyst", frozenset(), 1, 3200, admin_only=True
)
INVENTORY_OPS_SPEC = AgentSpec(
    "inventory_ops", "v1", "inventory_ops", frozenset(), 1, 1600, admin_only=True
)
CUSTOMER_AGENT_IDS = frozenset(AGENT_SPECS)

_SHOPPING_INTENTS = {
    IntentKind.PRODUCT_CONSULT,
    IntentKind.PRODUCT_SEARCH,
    IntentKind.VISUAL_PRODUCT_SEARCH,
}
_ORDER_INTENTS = {
    IntentKind.QUERY_ORDER,
    IntentKind.QUERY_LOGISTICS,
    IntentKind.QUERY_FULFILLMENT,
    IntentKind.QUERY_COUPON,
    IntentKind.QUERY_COMMENT,
    IntentKind.REFUND_STATUS,
}
_AFTER_SALES_INTENTS = {
    IntentKind.REFUND,
    IntentKind.CANCEL_ORDER,
    IntentKind.CONFIRM_RECEIPT,
    IntentKind.PRODUCT_REVIEW,
    IntentKind.RECOMMENT,
    IntentKind.DAMAGED_OR_WRONG_ITEM,
    IntentKind.AFTERSALES_UNKNOWN,
    IntentKind.ADDRESS_CHANGE,
    IntentKind.INVOICE,
    IntentKind.COMPLAINT,
    IntentKind.PAYMENT_ISSUE,
}


def agent_for_intent(intent: str | IntentKind | None) -> AgentSpec:
    try:
        resolved = intent if isinstance(intent, IntentKind) else IntentKind(str(intent))
    except ValueError:
        return AGENT_SPECS["supervisor"]
    if resolved in _SHOPPING_INTENTS:
        return AGENT_SPECS["shopping_advisor"]
    if resolved in _ORDER_INTENTS:
        return AGENT_SPECS["order_fulfillment_specialist"]
    if resolved in _AFTER_SALES_INTENTS:
        return AGENT_SPECS["after_sales_policy_specialist"]
    return AGENT_SPECS["supervisor"]
