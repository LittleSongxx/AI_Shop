from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.harness.agents.contracts import VisualSubject


class VisualProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class VisualProviderMetadata:
    capability: Literal["grounding", "embedding", "rerank"]
    model: str
    request_id: str | None = None
    usage: dict = field(default_factory=dict)
    circuit_state: str = "closed"
    attempts: int = 1


@dataclass(frozen=True)
class GroundingResult:
    subjects: list[VisualSubject]
    metadata: VisualProviderMetadata


@dataclass(frozen=True)
class VisualEmbeddingResult:
    vector: list[float]
    metadata: VisualProviderMetadata


@dataclass(frozen=True)
class VisualRerankItem:
    index: int
    relevance_score: float


@dataclass(frozen=True)
class VisualRerankResult:
    items: list[VisualRerankItem]
    metadata: VisualProviderMetadata


@dataclass(frozen=True)
class VisualIndexHit:
    product_id: str
    document_id: str
    document_type: Literal["IMAGE", "PRODUCT_FUSED"]
    cover_index: int | None
    image_sha256: str | None
    normalized_sha256: str | None
    product_name: str
    category_id: str | None
    brand: str | None
    score: float
    cosine: float | None
    recall_source: str
