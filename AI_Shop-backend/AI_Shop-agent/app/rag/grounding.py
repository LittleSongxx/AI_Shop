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
    fact_ids: tuple[str, ...] = ()
    domain: str = "GENERAL"
    support_type: str = "SEMANTIC"

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value["factIds"] = value.pop("fact_ids")
        value["supportType"] = value.pop("support_type")
        return value


@dataclass(frozen=True)
class QueryPlan:
    original_query_hash: str
    safe_business_query: str
    variant_count: int
    normalization_rules: tuple[str, ...] = ()
    subquestions: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    route: str = "GENERAL"
    expansion_reasons: tuple[str, ...] = ()
    fact_hints: tuple[str, ...] = ()
    llm_expansion_calls: int = 0
    policy_fingerprint: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "originalQueryHash": self.original_query_hash,
            "safeBusinessQuery": self.safe_business_query,
            "variantCount": self.variant_count,
            "normalizationRules": list(self.normalization_rules),
            "subquestions": list(self.subquestions),
            "domains": list(self.domains),
            "route": self.route,
            "expansionReasons": list(self.expansion_reasons),
            "factHints": list(self.fact_hints),
            "llmExpansionCalls": self.llm_expansion_calls,
            "policyFingerprint": self.policy_fingerprint,
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
