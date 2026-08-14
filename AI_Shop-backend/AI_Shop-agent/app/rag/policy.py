"""Versioned runtime policy shared by production RAG and evaluations."""

from __future__ import annotations

import contextvars
import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterator

from app.config.settings import get_settings

POLICY_SCHEMA = "aishop-rag-retrieval-policy/v4"


@dataclass(frozen=True)
class RagRetrievalPolicy:
    rerank_top_n: int = 6
    evidence_threshold: float = 0.70
    canonical_hint_floor: float = 0.55
    top_score_margin: float | None = 0.10
    max_query_variants: int = 3
    max_subquestions: int = 3
    max_evidence_items: int = 4
    max_evidence_chars: int = 6_000
    adaptive_expansion: bool = True
    contextual_retrieval: bool = True

    def validate(self) -> "RagRetrievalPolicy":
        if self.rerank_top_n < 1:
            raise ValueError("rerank_top_n must be positive")
        if not 0 <= self.evidence_threshold <= 1:
            raise ValueError("evidence_threshold must be between 0 and 1")
        if not 0 <= self.canonical_hint_floor <= 1:
            raise ValueError("canonical_hint_floor must be between 0 and 1")
        if self.top_score_margin is not None and not 0 <= self.top_score_margin <= 1:
            raise ValueError("top_score_margin must be between 0 and 1")
        if not 1 <= self.max_query_variants <= 3:
            raise ValueError("max_query_variants must be between 1 and 3")
        if not 1 <= self.max_subquestions <= 3:
            raise ValueError("max_subquestions must be between 1 and 3")
        if not 1 <= self.max_evidence_items <= 4:
            raise ValueError("max_evidence_items must be between 1 and 4")
        if not 1 <= self.max_evidence_chars <= 6_000:
            raise ValueError("max_evidence_chars must be between 1 and 6000")
        return self

    def public(self) -> dict[str, Any]:
        return {
            "schemaVersion": POLICY_SCHEMA,
            "rerankTopN": self.rerank_top_n,
            "evidenceThreshold": self.evidence_threshold,
            "canonicalHintFloor": self.canonical_hint_floor,
            "topScoreMargin": self.top_score_margin,
            "maxQueryVariants": self.max_query_variants,
            "maxSubquestions": self.max_subquestions,
            "maxEvidenceItems": self.max_evidence_items,
            "maxEvidenceChars": self.max_evidence_chars,
            "adaptiveExpansion": self.adaptive_expansion,
            "contextualRetrieval": self.contextual_retrieval,
            "fingerprint": self.fingerprint(),
        }

    def fingerprint(self) -> str:
        payload = json.dumps(
            {"schemaVersion": POLICY_SCHEMA, **asdict(self)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_POLICY_OVERRIDE: contextvars.ContextVar[RagRetrievalPolicy | None] = (
    contextvars.ContextVar("rag_retrieval_policy_override", default=None)
)


def runtime_rag_policy() -> RagRetrievalPolicy:
    override = _POLICY_OVERRIDE.get()
    if override is not None:
        return override.validate()
    settings = get_settings()
    margin = settings.rag_evidence_top_score_margin
    return RagRetrievalPolicy(
        rerank_top_n=int(settings.rerank_top_n),
        evidence_threshold=float(settings.rag_evidence_min_relevance),
        canonical_hint_floor=float(settings.rag_evidence_canonical_hint_floor),
        top_score_margin=None if margin is None else float(margin),
    ).validate()


@contextmanager
def rag_policy_scope(
    *,
    base: RagRetrievalPolicy | None = None,
    **overrides: Any,
) -> Iterator[RagRetrievalPolicy]:
    """Apply an explicit experiment-only policy override in the current task."""

    policy = replace(base or runtime_rag_policy(), **overrides).validate()
    token = _POLICY_OVERRIDE.set(policy)
    try:
        yield policy
    finally:
        _POLICY_OVERRIDE.reset(token)
