from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CommerceOutcomeType = Literal[
    "IMPRESSION",
    "CLICK",
    "ADD_TO_CART",
    "PAYMENT",
    "CANCEL",
    "REFUND",
    "RETURN",
    "REVIEW",
    "SUPPORT_CONTACT",
    "REPEAT_PURCHASE",
]

CommerceOutcomeSource = Literal[
    "AGENT",
    "CART",
    "ORDER",
    "PAYMENT",
    "AFTER_SALES",
    "REVIEW",
    "SUPPORT",
]

_SOURCE_EVENT_TYPES: dict[str, frozenset[str]] = {
    "AGENT": frozenset({"IMPRESSION", "CLICK"}),
    "CART": frozenset({"ADD_TO_CART"}),
    "ORDER": frozenset({"CANCEL", "REPEAT_PURCHASE"}),
    "PAYMENT": frozenset({"PAYMENT"}),
    "AFTER_SALES": frozenset({"REFUND", "RETURN"}),
    "REVIEW": frozenset({"REVIEW"}),
    "SUPPORT": frozenset({"SUPPORT_CONTACT"}),
}


def source_event_matches(source: str, event_type: str) -> bool:
    return event_type in _SOURCE_EVENT_TYPES.get(source, frozenset())


class CommerceOutcomeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(min_length=1, max_length=128)
    source: CommerceOutcomeSource
    idempotencyKey: str = Field(min_length=1, max_length=160)
    eventType: CommerceOutcomeType
    userId: str = Field(min_length=1, max_length=32)
    requestId: str | None = Field(default=None, max_length=128)
    runId: str | None = Field(default=None, max_length=64)
    productId: str | None = Field(default=None, max_length=64)
    skuKey: str | None = Field(default=None, max_length=64)
    orderId: str | None = Field(default=None, max_length=64)
    position: int | None = Field(default=None, ge=1, le=20)
    payload: dict[str, Any] = Field(default_factory=dict)
    occurredAt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("eventId", "idempotencyKey", "userId", mode="before")
    @classmethod
    def strip_required_identifiers(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator(
        "requestId",
        "runId",
        "productId",
        "skuKey",
        "orderId",
        mode="before",
    )
    @classmethod
    def strip_optional_identifiers(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("occurredAt")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def attributed_event_requires_complete_touchpoint(self) -> "CommerceOutcomeEvent":
        if self.requestId and (not self.productId or self.position is None):
            raise ValueError(
                "requestId attribution requires productId and position"
            )
        if self.eventType in {"IMPRESSION", "CLICK", "ADD_TO_CART", "REVIEW"}:
            if not self.productId:
                raise ValueError(f"{self.eventType} requires productId")
        if self.eventType in {
            "PAYMENT",
            "CANCEL",
            "REFUND",
            "RETURN",
            "REVIEW",
            "REPEAT_PURCHASE",
        } and not self.orderId:
            raise ValueError(f"{self.eventType} requires orderId")
        return self


class CommerceOutcomeBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[CommerceOutcomeEvent] = Field(min_length=1, max_length=100)
