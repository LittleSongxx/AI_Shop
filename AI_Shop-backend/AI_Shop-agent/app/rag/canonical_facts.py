from __future__ import annotations

import contextvars
import json
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Any, Iterable, Iterator, Mapping, Sequence

CATALOG_SCHEMA = "aishop-knowledge-catalog/v1"
CATALOG_OVERLAY_SCHEMA = "aishop-knowledge-catalog-overlay/v1"
LEGACY_V1_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "demo_knowledge" / "catalog.v1.json"
)
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "demo_knowledge_v3"
    / "catalog.v3.json"
)
_CATALOG_PATH_OVERRIDE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "canonical_fact_catalog_path", default=None
)

FAQ_FACT_IDS: dict[str, str] = {
    "9001": "shopping.recommendation.input_constraints",
    "9002": "coupon.single_per_order_and_revalidate",
    "9003": "member.growth.thresholds",
    "9004": "logistics.view_tracking",
    "9005": "ai.capability_and_confirmation",
    "9006": "ai.memory.local_storage",
}

EXPECTED_BEHAVIORS = frozenset({"ANSWER", "REFUSE", "ANSWER_SAFE_PREFIX"})
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_COUNTED_NUMBER = re.compile(r"([零〇一二两三四五六七八九十]+)(张|天|次|个|元|级)")


def _source_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return PurePath(text).name if text else ""


def _load_catalog_payload(
    path: Path,
    *,
    seen: frozenset[Path] = frozenset(),
) -> dict[str, Any]:
    """Resolve an immutable catalog overlay into one effective catalog."""

    resolved = path.resolve()
    if resolved in seen:
        raise ValueError("canonical fact catalog overlay cycle")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"canonical fact catalog is unreadable: {resolved}") from exc
    schema = payload.get("schemaVersion") if isinstance(payload, dict) else None
    if schema == CATALOG_SCHEMA:
        return payload
    if schema != CATALOG_OVERLAY_SCHEMA:
        raise ValueError("unsupported canonical fact catalog schema")

    extension = str(payload.get("extends") or "").strip()
    if not extension or Path(extension).is_absolute():
        raise ValueError("canonical fact catalog overlay has invalid extends")
    base = _load_catalog_payload(
        resolved.parent / extension,
        seen=seen | {resolved},
    )
    documents = json.loads(json.dumps(base.get("documents") or []))
    by_file = {
        str(document.get("file") or ""): document
        for document in documents
        if isinstance(document, dict)
    }
    for override in payload.get("sectionOverrides") or []:
        if not isinstance(override, dict):
            raise ValueError("canonical fact catalog section override must be an object")
        filename = str(override.get("file") or "")
        heading = str(override.get("heading") or "")
        document = by_file.get(filename)
        section = next(
            (
                row
                for row in (document or {}).get("sections") or []
                if str(row.get("heading") or "") == heading
            ),
            None,
        )
        if section is None:
            raise ValueError(
                f"canonical fact catalog override target missing: {filename}#{heading}"
            )
        unsupported = set(override) - {"file", "heading", "equivalentRefs"}
        if unsupported or not isinstance(override.get("equivalentRefs"), list):
            raise ValueError("canonical fact catalog section override is invalid")
        section["equivalentRefs"] = list(override["equivalentRefs"])

    for document in payload.get("documents") or []:
        if not isinstance(document, dict):
            raise ValueError("canonical fact catalog overlay document must be an object")
        filename = str(document.get("file") or "")
        if not filename or filename in by_file:
            raise ValueError(f"duplicate canonical fact catalog document: {filename}")
        copied = json.loads(json.dumps(document))
        documents.append(copied)
        by_file[filename] = copied
    return {
        "schemaVersion": CATALOG_SCHEMA,
        "catalogVersion": int(payload.get("catalogVersion") or 0),
        "expectedDocumentCount": int(payload.get("expectedDocumentCount") or 0),
        "expectedKnowledgeChunkCount": int(
            payload.get("expectedKnowledgeChunkCount") or 0
        ),
        "expectedFaqCount": int(payload.get("expectedFaqCount") or 0),
        "documents": documents,
    }


def reference_key(ref: Mapping[str, Any] | str) -> str | None:
    if isinstance(ref, str):
        value = ref.strip()
        if value.startswith("faq:"):
            return value.casefold()
        if "#" in value:
            source, heading = value.split("#", 1)
            source = _source_name(source).casefold()
            heading = heading.strip().casefold()
            return f"{source}#{heading}" if source and heading else None
        return None
    ref_type = str(ref.get("type") or ref.get("dataType") or "").casefold()
    question_id = ref.get("questionId") or ref.get("question_id")
    if ref_type == "faq" or question_id is not None:
        value = str(question_id or "").strip()
        return f"faq:{value}" if value else None
    source = _source_name(ref.get("source") or ref.get("sourceName")).casefold()
    heading = str(ref.get("heading") or "").strip().casefold()
    return f"{source}#{heading}" if source and heading else None


def _parse_chinese_number(value: str) -> int | None:
    if not value:
        return None
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(value) == 1:
        return _CHINESE_DIGITS.get(value)
    return None


def normalize_concept_text(value: Any) -> str:
    """Normalize only stable presentation differences, not broad semantics."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("优惠卷", "优惠券")
    text = re.sub(r"(?<=[零〇一二两三四五六七八九十])\s+(?=[张天次个元级])", "", text)
    text = re.sub(r"(?<=\d)\s+(?=[张天次个元级])", "", text)

    def replace_number(match: re.Match[str]) -> str:
        parsed = _parse_chinese_number(match.group(1))
        return f"{parsed}{match.group(2)}" if parsed is not None else match.group(0)

    text = _COUNTED_NUMBER.sub(replace_number, text)
    return "".join(char for char in text if char.isalnum())


def _concept_aliases(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        raw = value.get("aliases") or []
    else:
        raw = value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Sequence):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def concept_coverage(case: Mapping[str, Any], answer: str) -> dict[str, Any]:
    concepts = list(case.get("requiredConcepts") or [])
    if not concepts:
        return {"coverage": 1.0, "matched": [], "missing": []}
    normalized_answer = normalize_concept_text(answer)
    matched: list[int] = []
    missing: list[int] = []
    for index, concept in enumerate(concepts):
        aliases = _concept_aliases(concept)
        if not aliases:
            missing.append(index)
            continue
        if any(
            normalized and normalized in normalized_answer
            for normalized in (normalize_concept_text(alias) for alias in aliases)
        ):
            matched.append(index)
        else:
            missing.append(index)
    return {
        "coverage": len(matched) / len(concepts),
        "matched": matched,
        "missing": missing,
    }


@dataclass(frozen=True)
class CanonicalFactCatalog:
    path: Path
    catalog_version: int
    fact_to_refs: Mapping[str, frozenset[str]]
    ref_to_facts: Mapping[str, frozenset[str]]

    @classmethod
    def load(cls, path: Path = DEFAULT_CATALOG_PATH) -> "CanonicalFactCatalog":
        payload = _load_catalog_payload(path)
        fact_to_refs: dict[str, set[str]] = {}
        ref_to_facts: dict[str, set[str]] = {}

        def bind(fact_id: str, ref_key: str) -> None:
            fact_to_refs.setdefault(fact_id, set()).add(ref_key)
            ref_to_facts.setdefault(ref_key, set()).add(fact_id)

        for document in payload.get("documents") or []:
            source = _source_name(document.get("file"))
            for section in document.get("sections") or []:
                fact_id = str(section.get("factId") or "").strip()
                heading = str(section.get("heading") or "").strip()
                own_ref = reference_key(f"{source}#{heading}")
                if not fact_id or not own_ref:
                    raise ValueError(f"invalid canonical fact section: {source}#{heading}")
                bind(fact_id, own_ref)
                for equivalent in section.get("equivalentRefs") or []:
                    equivalent_key = reference_key(str(equivalent))
                    if not equivalent_key:
                        raise ValueError(
                            f"invalid equivalent canonical reference: {equivalent}"
                        )
                    bind(fact_id, equivalent_key)

        for question_id, fact_id in FAQ_FACT_IDS.items():
            if fact_id not in fact_to_refs:
                raise ValueError(f"FAQ {question_id} maps to unknown fact {fact_id}")
            bind(fact_id, f"faq:{question_id}")
        return cls(
            path=path.resolve(),
            catalog_version=int(payload.get("catalogVersion") or 0),
            fact_to_refs={
                key: frozenset(sorted(value)) for key, value in fact_to_refs.items()
            },
            ref_to_facts={
                key: frozenset(sorted(value)) for key, value in ref_to_facts.items()
            },
        )

    def facts_for_ref(self, ref: Mapping[str, Any] | str) -> frozenset[str]:
        key = reference_key(ref)
        return self.ref_to_facts.get(key or "", frozenset())

    def validate_case(self, case: Mapping[str, Any]) -> list[str]:
        errors: list[str] = []
        case_id = str(case.get("id") or "<missing>")
        behavior = str(case.get("expectedBehavior") or "").upper()
        if behavior not in EXPECTED_BEHAVIORS:
            errors.append(f"{case_id}: invalid expectedBehavior={behavior!r}")
        relevant = case.get("relevantFactIds")
        if not isinstance(relevant, list):
            errors.append(f"{case_id}: relevantFactIds must be a list")
            relevant = []
        missing = sorted({str(value) for value in relevant} - set(self.fact_to_refs))
        if missing:
            errors.append(f"{case_id}: unknown relevantFactIds={missing}")
        concepts = case.get("requiredConcepts")
        if not isinstance(concepts, list):
            errors.append(f"{case_id}: requiredConcepts must be a list")
        else:
            for index, concept in enumerate(concepts):
                if not _concept_aliases(concept):
                    errors.append(f"{case_id}: requiredConcepts[{index}] has no aliases")
        claims = case.get("requiredClaims")
        if claims is not None:
            if not isinstance(claims, list):
                errors.append(f"{case_id}: requiredClaims must be a list")
            else:
                claim_ids: set[str] = set()
                allowed_facts = set(str(value) for value in relevant)
                for index, claim in enumerate(claims):
                    if not isinstance(claim, Mapping):
                        errors.append(f"{case_id}: requiredClaims[{index}] must be an object")
                        continue
                    claim_id = str(claim.get("claimId") or "").strip()
                    if not claim_id or claim_id in claim_ids:
                        errors.append(f"{case_id}: requiredClaims has duplicate/missing claimId")
                    claim_ids.add(claim_id)
                    aliases = _concept_aliases(claim)
                    if not aliases:
                        errors.append(f"{case_id}: requiredClaims[{index}] has no aliases")
                    fact_ids = {
                        str(value) for value in claim.get("factIds") or [] if str(value)
                    }
                    if fact_ids - allowed_facts:
                        errors.append(
                            f"{case_id}: requiredClaims[{index}] references facts outside relevantFactIds"
                        )
        no_answer = bool(case.get("noAnswer"))
        if no_answer != (behavior == "REFUSE"):
            errors.append(f"{case_id}: noAnswer must match expectedBehavior=REFUSE")
        if behavior != "REFUSE" and not relevant:
            errors.append(f"{case_id}: answerable behavior requires relevantFactIds")
        return errors


def active_canonical_catalog_path() -> Path:
    return _CATALOG_PATH_OVERRIDE.get() or DEFAULT_CATALOG_PATH


@contextmanager
def canonical_fact_catalog_scope(path: Path) -> Iterator[CanonicalFactCatalog]:
    resolved = Path(path).resolve()
    catalog = _load_canonical_fact_catalog(str(resolved))
    token = _CATALOG_PATH_OVERRIDE.set(resolved)
    try:
        yield catalog
    finally:
        _CATALOG_PATH_OVERRIDE.reset(token)


@lru_cache(maxsize=None)
def _load_canonical_fact_catalog(path: str) -> CanonicalFactCatalog:
    return CanonicalFactCatalog.load(Path(path))


def get_canonical_fact_catalog(
    path: Path | None = None,
) -> CanonicalFactCatalog:
    resolved = Path(path or active_canonical_catalog_path()).resolve()
    return _load_canonical_fact_catalog(str(resolved))


def relevant_fact_ids(case: Mapping[str, Any]) -> list[str]:
    return [str(value) for value in case.get("relevantFactIds") or [] if str(value)]


def canonical_citation_metrics(
    case: Mapping[str, Any],
    cited_refs: Iterable[Mapping[str, Any]],
    *,
    catalog: CanonicalFactCatalog | None = None,
) -> dict[str, Any]:
    expected = set(relevant_fact_ids(case))
    refs = list(cited_refs)
    facts_by_ref = [set((catalog or get_canonical_fact_catalog()).facts_for_ref(ref)) for ref in refs]
    correct = sum(bool(facts.intersection(expected)) for facts in facts_by_ref)
    covered = set().union(*(facts.intersection(expected) for facts in facts_by_ref)) if facts_by_ref else set()
    return {
        "correctness": correct / len(refs) if refs else (1.0 if not expected else 0.0),
        "coverage": len(covered) / len(expected) if expected else (1.0 if not refs else 0.0),
        "coveredFactIds": sorted(covered),
        "missingFactIds": sorted(expected - covered),
        "unmappedCitationCount": sum(not facts for facts in facts_by_ref),
    }


def canonical_match(
    case: Mapping[str, Any],
    actual_ref: Mapping[str, Any],
    *,
    catalog: CanonicalFactCatalog | None = None,
) -> set[str]:
    expected = set(relevant_fact_ids(case))
    return set((catalog or get_canonical_fact_catalog()).facts_for_ref(actual_ref)).intersection(
        expected
    )
