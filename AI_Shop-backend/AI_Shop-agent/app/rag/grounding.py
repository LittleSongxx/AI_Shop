"""Stable grounding contract shared by production retrieval and evaluations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class EvidenceState(StrEnum):
    SUPPORTED = "SUPPORTED"
    INSUFFICIENT = "INSUFFICIENT"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class EvidenceItem:
    citation: int
    text: str
    ref: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryPlan:
    original_query_hash: str
    safe_business_query: str
    variant_count: int
    normalization_rules: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "originalQueryHash": self.original_query_hash,
            "safeBusinessQuery": self.safe_business_query,
            "variantCount": self.variant_count,
            "normalizationRules": list(self.normalization_rules),
        }


@dataclass(frozen=True)
class GroundingEnvelope:
    evidence_state: EvidenceState
    evidence_items: tuple[EvidenceItem, ...] = ()
    query_plan: QueryPlan | None = None
    security_flags: tuple[str, ...] = field(default_factory=tuple)

    def result_fields(self) -> dict[str, Any]:
        return {
            "evidenceState": self.evidence_state.value,
            "evidenceItems": [item.public() for item in self.evidence_items],
            "queryPlan": self.query_plan.public() if self.query_plan else None,
            "securityFlags": list(dict.fromkeys(self.security_flags)),
        }
