from enum import Enum

from pydantic import BaseModel, Field


class IntentKind(str, Enum):
    PRODUCT_CONSULT = "PRODUCT_CONSULT"
    PRODUCT_SEARCH = "PRODUCT_SEARCH"
    QUERY_ORDER = "QUERY_ORDER"
    REFUND = "REFUND"
    CANCEL_ORDER = "CANCEL_ORDER"
    CONFIRM_RECEIPT = "CONFIRM_RECEIPT"
    QUERY_LOGISTICS = "QUERY_LOGISTICS"
    QUERY_FULFILLMENT = "QUERY_FULFILLMENT"
    QUERY_COUPON = "QUERY_COUPON"
    PRODUCT_REVIEW = "PRODUCT_REVIEW"
    RECOMMENT = "RECOMMENT"
    QUERY_COMMENT = "QUERY_COMMENT"
    COMPLAINT = "COMPLAINT"
    HUMAN_REQUEST = "HUMAN_REQUEST"
    PAYMENT_ISSUE = "PAYMENT_ISSUE"
    DAMAGED_OR_WRONG_ITEM = "DAMAGED_OR_WRONG_ITEM"
    INVOICE = "INVOICE"
    ADDRESS_CHANGE = "ADDRESS_CHANGE"
    REFUND_STATUS = "REFUND_STATUS"
    AFTERSALES_UNKNOWN = "AFTERSALES_UNKNOWN"
    CHAT = "CHAT"


class SentimentKind(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    VERY_NEGATIVE = "VERY_NEGATIVE"


class UrgencyKind(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class NextAction(str, Enum):
    ANSWER = "ANSWER"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    TOOL = "TOOL"
    HANDOFF = "HANDOFF"
    HANDOFF_SUGGESTED = "HANDOFF_SUGGESTED"


class RequestMode(str, Enum):
    """How the user expects the domain request to be handled.

    Intent answers "which business domain?" while request mode answers
    "read, explain, or mutate?". Keeping the two axes separate prevents a
    policy question such as "退款政策是什么" from becoming a refund action.
    """

    INFORMATIONAL = "INFORMATIONAL"
    READ_QUERY = "READ_QUERY"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"
    HUMAN_SUPPORT = "HUMAN_SUPPORT"


class IntentDecision(BaseModel):
    intent: IntentKind
    confidence: float = Field(ge=0, le=1)
    entities: dict[str, str] = Field(default_factory=dict)
    sentiment: SentimentKind = SentimentKind.NEUTRAL
    urgency: UrgencyKind = UrgencyKind.NORMAL
    risk_level: RiskLevel = RiskLevel.LOW
    next_action: NextAction = NextAction.ANSWER
    handoff_reason: str | None = None
    request_mode: RequestMode = RequestMode.READ_QUERY
    source: str = "default"
    data: str = ""

    @property
    def should_handoff(self) -> bool:
        return self.next_action == NextAction.HANDOFF

    @property
    def should_suggest_handoff(self) -> bool:
        return self.next_action == NextAction.HANDOFF_SUGGESTED


INTENT_PROMPT_KEY: dict[IntentKind, str] = {
    IntentKind.PRODUCT_CONSULT: "product_consult",
    IntentKind.PRODUCT_SEARCH: "product_search",
    IntentKind.QUERY_ORDER: "query_order",
    IntentKind.REFUND: "refund",
    IntentKind.CANCEL_ORDER: "cancel_order",
    IntentKind.CONFIRM_RECEIPT: "confirm_receipt",
    IntentKind.QUERY_LOGISTICS: "query_logistics",
    IntentKind.QUERY_FULFILLMENT: "query_order",
    IntentKind.QUERY_COUPON: "query_coupon",
    IntentKind.PRODUCT_REVIEW: "product_review",
    IntentKind.RECOMMENT: "recomment",
    IntentKind.QUERY_COMMENT: "query_comment",
    IntentKind.COMPLAINT: "chat",
    IntentKind.HUMAN_REQUEST: "chat",
    IntentKind.PAYMENT_ISSUE: "chat",
    IntentKind.DAMAGED_OR_WRONG_ITEM: "chat",
    IntentKind.INVOICE: "chat",
    IntentKind.ADDRESS_CHANGE: "chat",
    IntentKind.REFUND_STATUS: "chat",
    IntentKind.AFTERSALES_UNKNOWN: "chat",
    IntentKind.CHAT: "chat",
}
