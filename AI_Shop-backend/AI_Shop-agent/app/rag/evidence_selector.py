"""Select a small, fact-diverse evidence set for grounded generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.rag.canonical_facts import get_canonical_fact_catalog
from app.rag.fact_metadata import get_fact_metadata_catalog
from app.rag.grounding import EvidenceItem


@dataclass(frozen=True)
class EvidenceSelection:
    documents: tuple[dict[str, Any], ...]
    refs: tuple[dict[str, Any], ...]
    items: tuple[EvidenceItem, ...]
    duplicate_body_count: int
    duplicate_fact_count: int
    total_chars: int

    def trace(self) -> dict[str, Any]:
        return {
            "selectedCount": len(self.items),
            "totalChars": self.total_chars,
            "duplicateBodyCount": self.duplicate_body_count,
            "duplicateFactCount": self.duplicate_fact_count,
            "coveredFactIds": sorted(
                {fact_id for item in self.items for fact_id in item.fact_ids}
            ),
            "coveredDomains": sorted({item.domain for item in self.items}),
        }


def _body(doc: Mapping[str, Any]) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), Mapping) else {}
    original = metadata.get("originalContent")
    return str(original if original is not None else doc.get("content") or doc.get("text") or "").strip()


def evidence_item_limit(
    subquestions: Sequence[str],
    *,
    configured_max: int = 4,
    preferred_fact_ids: Sequence[str] = (),
    candidates: Sequence[Mapping[str, Any]] = (),
    ambiguity_margin: float = 0.05,
) -> int:
    """Bound evidence while retaining a close second candidate when uncertain."""

    question_count = len({str(value).strip() for value in subquestions if str(value).strip()})
    configured = max(1, int(configured_max))
    preferred = {str(value) for value in preferred_fact_ids if str(value)}
    candidate_facts = {
        str(fact_id)
        for candidate in candidates
        for fact_id in candidate.get("factIds") or ()
        if str(fact_id)
    }
    matched_preferred = preferred.intersection(candidate_facts)
    if matched_preferred:
        return min(
            configured,
            max(1, question_count, len(matched_preferred)),
        )
    if question_count > 1:
        return min(configured, question_count)
    scores = [
        float(candidate.get("score") or 0)
        for candidate in candidates[:2]
        if isinstance(candidate, Mapping)
    ]
    if len(scores) == 2 and scores[0] - scores[1] <= ambiguity_margin:
        return min(configured, 2)
    return 1


def select_minimal_evidence(
    docs: Sequence[Mapping[str, Any]],
    refs: Sequence[Mapping[str, Any]],
    *,
    query_domains: Sequence[str] = (),
    preferred_fact_ids: Sequence[str] = (),
    max_items: int = 4,
    max_chars: int = 6_000,
) -> EvidenceSelection:
    """Prefer uncovered domains/facts and never duplicate the same body."""

    canonical = get_canonical_fact_catalog()
    metadata_catalog = get_fact_metadata_catalog()
    candidates: list[dict[str, Any]] = []
    seen_bodies: set[str] = set()
    duplicate_body_count = 0
    for doc, ref in zip(docs, refs):
        text = _body(doc)
        if not text:
            continue
        body_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if body_hash in seen_bodies:
            duplicate_body_count += 1
            continue
        seen_bodies.add(body_hash)
        fact_ids = tuple(sorted(canonical.facts_for_ref(ref)))
        domains = {
            metadata_catalog.facts[fact_id].domain
            for fact_id in fact_ids
            if fact_id in metadata_catalog.facts
        }
        ref_domain = str(ref.get("domain") or "").upper()
        domain = sorted(domains)[0] if domains else ref_domain or "GENERAL"
        candidates.append(
            {
                "doc": dict(doc),
                "ref": dict(ref),
                "text": text,
                "factIds": fact_ids,
                "domain": domain,
                "supportType": (
                    "EXACT_FAQ"
                    if str(ref.get("retrieval") or "") == "exact_faq"
                    else "CANONICAL"
                    if fact_ids
                    else "SEMANTIC"
                ),
            }
        )

    preferred_facts = {str(value) for value in preferred_fact_ids if str(value)}
    available_preferred_facts = preferred_facts.intersection(
        {
            fact_id
            for candidate in candidates
            for fact_id in candidate["factIds"]
        }
    )
    requested_domains = [str(value).upper() for value in query_domains if str(value)]
    ordered: list[dict[str, Any]] = []
    ordered.extend(
        row
        for row in candidates
        if preferred_facts.intersection(row["factIds"])
    )
    for domain in requested_domains:
        candidate = next(
            (row for row in candidates if row["domain"] == domain and row not in ordered),
            None,
        )
        if candidate is not None:
            ordered.append(candidate)
    ordered.extend(row for row in candidates if row not in ordered)

    selected: list[dict[str, Any]] = []
    covered_facts: set[str] = set()
    duplicate_fact_count = 0
    total_chars = 0
    for row in ordered:
        facts = set(row["factIds"])
        if facts and facts.issubset(covered_facts):
            duplicate_fact_count += 1
            continue
        text_length = len(row["text"])
        if total_chars + text_length > max_chars:
            continue
        selected.append(row)
        total_chars += text_length
        covered_facts.update(facts)
        if (
            available_preferred_facts
            and max_items <= len(available_preferred_facts)
            and available_preferred_facts.issubset(covered_facts)
        ):
            break
        if len(selected) >= max_items:
            break

    items = tuple(
        EvidenceItem(
            citation=index,
            text=row["text"],
            ref=row["ref"],
            fact_ids=row["factIds"],
            domain=row["domain"],
            support_type=row["supportType"],
        )
        for index, row in enumerate(selected, 1)
    )
    return EvidenceSelection(
        documents=tuple(row["doc"] for row in selected),
        refs=tuple(row["ref"] for row in selected),
        items=items,
        duplicate_body_count=duplicate_body_count,
        duplicate_fact_count=duplicate_fact_count,
        total_chars=total_chars,
    )
