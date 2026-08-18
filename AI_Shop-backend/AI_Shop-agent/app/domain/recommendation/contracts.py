from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RecommendationMode = Literal["TEXT", "IMAGE", "MIXED"]
RecommendationStatus = Literal[
    "ACCEPTED",
    "COMPLETED",
    "NO_RESULT",
    "CLARIFICATION_REQUIRED",
    "DEGRADED",
    "BLOCKED",
]
RecommendationEventType = Literal[
    "IMPRESSION",
    "CLICK",
    "ADD_TO_CART",
    "PAYMENT",
    "REPEAT_PURCHASE",
]
EvidenceKind = Literal[
    "TEXT_RECALL",
    "VISUAL_RECALL",
    "RERANK",
    "CONSTRAINT",
    "OFFER",
    "ATTRIBUTION",
]
MAX_RECOMMENDATION_ITEMS = 20


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class RecommendationModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


def _identifier(value: Any, *, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} exceeds {max_length} characters")
    return normalized


class RecommendationConstraints(RecommendationModel):
    category: str | None = Field(default=None, max_length=80)
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    required_brands: list[str] = Field(default_factory=list, max_length=20)
    excluded_brands: list[str] = Field(default_factory=list, max_length=20)
    excluded_terms: list[str] = Field(default_factory=list, max_length=30)
    use_cases: list[str] = Field(default_factory=list, max_length=12)
    preferred_features: list[str] = Field(default_factory=list, max_length=30)

    @field_validator(
        "required_brands",
        "excluded_brands",
        "excluded_terms",
        "use_cases",
        "preferred_features",
        mode="before",
    )
    @classmethod
    def normalize_terms(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("constraint terms must be an array")
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                result.append(text[:100])
        return result

    @model_validator(mode="after")
    def validate_budget_range(self) -> "RecommendationConstraints":
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_min > self.budget_max
        ):
            raise ValueError("budgetMin cannot exceed budgetMax")
        return self


class RecommendationRequest(RecommendationModel):
    request_id: str = Field(
        default_factory=lambda: f"req_{uuid.uuid4().hex}",
        min_length=1,
        max_length=128,
    )
    run_id: str | None = Field(default=None, max_length=64)
    episode_id: str | None = Field(default=None, max_length=64)
    traceparent: str | None = Field(default=None, max_length=255)
    mode: RecommendationMode = "TEXT"
    query: str | None = Field(default=None, max_length=2_000)
    image_asset_id: str | None = Field(default=None, max_length=128)
    selection_id: str | None = Field(default=None, max_length=128)
    selected_subject_id: str | None = Field(default=None, max_length=128)
    constraints: RecommendationConstraints = Field(default_factory=RecommendationConstraints)
    candidate_product_ids: list[str] = Field(default_factory=list, max_length=100)
    model_version: str | None = Field(default=None, max_length=128)
    catalog_version: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=160)

    @field_validator(
        "request_id",
        "run_id",
        "episode_id",
        "traceparent",
        "image_asset_id",
        "selection_id",
        "selected_subject_id",
        "model_version",
        "catalog_version",
        "idempotency_key",
        mode="before",
    )
    @classmethod
    def normalize_optional_identifiers(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized[:2_000] or None

    @field_validator("candidate_product_ids", mode="before")
    @classmethod
    def normalize_candidate_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("candidateProductIds must be an array")
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = str(item or "").strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized[:64])
        return result

    @model_validator(mode="after")
    def validate_request_shape(self) -> "RecommendationRequest":
        has_query = bool(self.query)
        has_image = bool(self.image_asset_id)
        if self.mode == "TEXT" and not has_query:
            raise ValueError("TEXT recommendation requires query")
        if self.mode == "IMAGE" and not has_image:
            raise ValueError("IMAGE recommendation requires imageAssetId")
        if self.mode == "MIXED" and not (has_query or has_image):
            raise ValueError("MIXED recommendation requires query or imageAssetId")
        if self.selected_subject_id and not has_image:
            raise ValueError("selectedSubjectId requires imageAssetId")
        if not self.idempotency_key:
            self.idempotency_key = self.request_id
        return self


class AuthoritativeOffer(RecommendationModel):
    product_id: str = Field(min_length=1, max_length=64)
    sku_key: str | None = Field(default=None, max_length=128)
    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="CNY", min_length=3, max_length=8)
    stock: float | None = Field(default=None, ge=0)
    in_stock: bool = False
    purchasable: bool = False
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: Literal["JAVA_GATEWAY", "FIXTURE", "UNKNOWN"] = "JAVA_GATEWAY"
    snapshot_id: str | None = Field(default=None, max_length=128)
    quote_expires_at: datetime | None = None

    @field_validator("product_id", mode="before")
    @classmethod
    def validate_product_id(cls, value: Any) -> str:
        return _identifier(value, field_name="productId", max_length=64)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str:
        return str(value or "CNY").strip().upper()[:8]

    @field_validator("checked_at", "quote_expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def enforce_purchase_truth(self) -> "AuthoritativeOffer":
        if self.price is None or not self.in_stock:
            self.purchasable = False
        return self


class RecommendationEvidence(RecommendationModel):
    kind: EvidenceKind
    source: str = Field(min_length=1, max_length=128)
    reference: str | None = Field(default=None, max_length=256)
    score: float | None = Field(default=None, ge=0, le=1)
    detail: str | None = Field(default=None, max_length=500)
    fresh: bool = True
    supports_claim: bool = True


class RecommendationCard(RecommendationModel):
    product_id: str = Field(min_length=1, max_length=64)
    product_name: str = Field(default="", max_length=300)
    position: int = Field(ge=1, le=MAX_RECOMMENDATION_ITEMS)
    offer: AuthoritativeOffer
    evidence: list[RecommendationEvidence] = Field(default_factory=list, max_length=30)
    model_version: str = Field(default="unknown", min_length=1, max_length=128)
    explanation: dict[str, Any] = Field(default_factory=dict)
    attribution: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keep_card_identity_consistent(self) -> "RecommendationCard":
        if self.product_id != self.offer.product_id:
            raise ValueError("card productId must match authoritative offer productId")
        return self


class RecommendationResponse(RecommendationModel):
    contract_version: Literal["recommendation/v1"] = "recommendation/v1"
    request_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    episode_id: str | None = Field(default=None, max_length=64)
    mode: RecommendationMode
    status: RecommendationStatus
    items: list[RecommendationCard] = Field(
        default_factory=list,
        max_length=MAX_RECOMMENDATION_ITEMS,
    )
    evidence: list[RecommendationEvidence] = Field(default_factory=list, max_length=100)
    model_version: str = Field(default="unknown", min_length=1, max_length=128)
    catalog_version: str | None = Field(default=None, max_length=128)
    degradation: str | None = Field(default=None, max_length=500)
    fallback_used: bool = False
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
    traceparent: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_terminal_semantics(self) -> "RecommendationResponse":
        if self.status == "COMPLETED" and not self.items:
            raise ValueError("COMPLETED recommendation requires items")
        if self.status == "NO_RESULT" and self.items:
            raise ValueError("NO_RESULT recommendation cannot contain items")
        if self.status == "BLOCKED" and not self.degradation:
            raise ValueError("BLOCKED recommendation requires degradation reason")
        return self


class RecommendationEvent(RecommendationModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}", max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=160)
    event_type: RecommendationEventType
    request_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=64)
    product_id: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=1, le=MAX_RECOMMENDATION_ITEMS)
    model_version: str = Field(min_length=1, max_length=128)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "idempotency_key", "request_id", "run_id", "product_id", "model_version", mode="before")
    @classmethod
    def normalize_event_identifiers(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("event identifiers cannot be blank")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def normalize_event_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
