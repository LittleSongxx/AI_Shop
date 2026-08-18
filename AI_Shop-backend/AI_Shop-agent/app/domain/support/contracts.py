from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SupportTerminalState = Literal[
    "ANSWERED",
    "CLARIFICATION_REQUIRED",
    "PROPOSED",
    "CONFIRM_REQUIRED",
    "SUCCEEDED",
    "INCONCLUSIVE",
    "MANUAL_REVIEW",
]
SupportLifecycle = Literal["QUEUED", "IN_PROGRESS", "WAITING_USER", "FINAL"]
SupportActionType = Literal[
    "REFUND",
    "CANCEL_ORDER",
    "CONFIRM_RECEIPT",
    "PRODUCT_REVIEW",
    "RECOMMENT",
    "CREATE_SUPPORT_CASE",
]


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class SupportModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _non_blank(value: Any, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds {max_length} characters")
    return normalized


class OrderCandidate(SupportModel):
    order_id: str = Field(min_length=1, max_length=64)
    order_no: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)
    created_at: datetime | None = None
    match_reason: str | None = Field(default=None, max_length=200)
    ownership_verified: bool = False

    @field_validator("order_id", mode="before")
    @classmethod
    def validate_order_id(cls, value: Any) -> str:
        return _non_blank(value, "orderId", 64)


class OrderFact(SupportModel):
    order_id: str = Field(min_length=1, max_length=64)
    order_status: str | None = Field(default=None, max_length=64)
    order_total: float | None = Field(default=None, ge=0)
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    source: Literal["JAVA_GATEWAY", "FIXTURE"] = "JAVA_GATEWAY"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ownership_verified: bool = False

    @field_validator("order_id", mode="before")
    @classmethod
    def validate_order_id(cls, value: Any) -> str:
        return _non_blank(value, "orderId", 64)

    @field_validator("fetched_at")
    @classmethod
    def normalize_fetched_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class PolicyEvidence(SupportModel):
    release: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    chunk_id: str | None = Field(default=None, max_length=128)
    quote: str = Field(min_length=1, max_length=1_000)
    supports_claim: bool = True
    score: float | None = Field(default=None, ge=0, le=1)


class ActionProposal(SupportModel):
    proposal_token: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    action_type: SupportActionType
    summary: str = Field(min_length=1, max_length=1_000)
    order_id: str | None = Field(default=None, max_length=64)
    order_item_id: str | None = Field(default=None, max_length=64)
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list, max_length=20)
    requires_confirmation: bool = True
    expires_at: datetime | None = None
    status: Literal[
        "PROPOSED",
        "CONFIRM_REQUIRED",
        "EXECUTING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
        "INCONCLUSIVE",
        "MANUAL_REVIEW",
    ] = "CONFIRM_REQUIRED"

    @field_validator("proposal_token", "idempotency_key", mode="before")
    @classmethod
    def normalize_tokens(cls, value: Any) -> str:
        return _non_blank(value, "token", 160)

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_action_target(self) -> "ActionProposal":
        if self.action_type == "REFUND" and not self.order_item_id:
            raise ValueError("REFUND proposal requires orderItemId")
        if self.action_type != "REFUND" and self.action_type != "CREATE_SUPPORT_CASE" and not self.order_id:
            raise ValueError(f"{self.action_type} proposal requires orderId")
        if self.status in {
            "EXECUTING",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "EXPIRED",
            "INCONCLUSIVE",
            "MANUAL_REVIEW",
        } and self.requires_confirmation:
            raise ValueError("terminal action proposal cannot still require confirmation")
        return self


class ConfirmAction(SupportModel):
    proposal_token: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=160)
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}", max_length=128)
    run_id: str | None = Field(default=None, max_length=64)
    episode_id: str | None = Field(default=None, max_length=64)
    traceparent: str | None = Field(default=None, max_length=255)

    @field_validator(
        "proposal_token",
        "idempotency_key",
        "request_id",
        "run_id",
        "episode_id",
        "traceparent",
        mode="before",
    )
    @classmethod
    def normalize_identifiers(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _non_blank(value, "identifier", 160)


class SupportTaskRequest(SupportModel):
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex}", max_length=128)
    run_id: str | None = Field(default=None, max_length=64)
    episode_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2_000)
    order_id: str | None = Field(default=None, max_length=64)
    order_item_id: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=160)
    traceparent: str | None = Field(default=None, max_length=255)

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value: Any) -> str:
        return _non_blank(value, "message", 2_000)

    @field_validator(
        "request_id",
        "run_id",
        "episode_id",
        "order_id",
        "order_item_id",
        "idempotency_key",
        "traceparent",
        mode="before",
    )
    @classmethod
    def normalize_optional_identifiers(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _non_blank(value, "identifier", 160)

    @model_validator(mode="after")
    def fill_idempotency_key(self) -> "SupportTaskRequest":
        if not self.idempotency_key:
            self.idempotency_key = self.request_id
        return self


class SupportTask(SupportModel):
    contract_version: Literal["support/v1"] = "support/v1"
    task_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    episode_id: str | None = Field(default=None, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    state: SupportTerminalState
    lifecycle: SupportLifecycle = "FINAL"
    traceparent: str | None = Field(default=None, max_length=255)
    order_candidates: list[OrderCandidate] = Field(default_factory=list, max_length=20)
    selected_order: OrderFact | None = None
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list, max_length=20)
    action_proposal: ActionProposal | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=160)
    message: str | None = Field(default=None, max_length=2_000)
    manual_review_reason: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_state_contract(self) -> "SupportTask":
        if self.state == "CLARIFICATION_REQUIRED" and not self.order_candidates:
            raise ValueError("CLARIFICATION_REQUIRED requires order candidates or a reason")
        if self.state in {"PROPOSED", "CONFIRM_REQUIRED"} and self.action_proposal is None:
            raise ValueError(f"{self.state} requires actionProposal")
        if self.state in {"INCONCLUSIVE", "MANUAL_REVIEW"} and not self.manual_review_reason:
            raise ValueError(f"{self.state} requires manualReviewReason")
        if self.lifecycle == "FINAL" and self.state == "ANSWERED" and self.evidence.get("deliveryState") in {
            "QUEUED",
            "PENDING_RECOVERY",
        }:
            raise ValueError("queued support task cannot be reported as a final ANSWERED state")
        return self
