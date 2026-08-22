"""Normalize conversational product queries using managed taxonomy data."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "config" / "search_taxonomy.yml"
_RUNTIME_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "search_runtime_taxonomy.yml"
)


@lru_cache(maxsize=1)
def _taxonomy() -> dict[str, Any]:
    payload = yaml.safe_load(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("topics"), list):
        raise ValueError(f"invalid product search taxonomy: {_TAXONOMY_PATH}")
    return payload


def taxonomy_contract() -> str:
    return str(_taxonomy().get("contract") or "")


def _topics() -> list[dict[str, Any]]:
    return [item for item in _taxonomy()["topics"] if isinstance(item, dict)]


@lru_cache(maxsize=1)
def _runtime_topics() -> list[dict[str, Any]]:
    payload = yaml.safe_load(_RUNTIME_TAXONOMY_PATH.read_text(encoding="utf-8"))
    topics = payload.get("topics") if isinstance(payload, dict) else None
    if not isinstance(topics, list):
        raise ValueError(f"invalid runtime product taxonomy: {_RUNTIME_TAXONOMY_PATH}")
    return [item for item in topics if isinstance(item, dict)]


@lru_cache(maxsize=1)
def _verified_qualifiers() -> list[dict[str, Any]]:
    payload = yaml.safe_load(_RUNTIME_TAXONOMY_PATH.read_text(encoding="utf-8"))
    qualifiers = payload.get("verifiedQualifiers") if isinstance(payload, dict) else None
    if qualifiers is None:
        return []
    if not isinstance(qualifiers, list):
        raise ValueError(f"invalid runtime product qualifiers: {_RUNTIME_TAXONOMY_PATH}")
    return [item for item in qualifiers if isinstance(item, dict)]


@lru_cache(maxsize=1)
def _variant_exclusion_evidence() -> list[dict[str, Any]]:
    payload = yaml.safe_load(_RUNTIME_TAXONOMY_PATH.read_text(encoding="utf-8"))
    contracts = payload.get("variantExclusionEvidence") if isinstance(payload, dict) else None
    if contracts is None:
        return []
    if not isinstance(contracts, list):
        raise ValueError(
            f"invalid runtime variant exclusion evidence: {_RUNTIME_TAXONOMY_PATH}"
        )
    return [item for item in contracts if isinstance(item, dict)]


def _topic_aliases(topic: dict[str, Any]) -> list[str]:
    values = [topic.get("canonical"), *(topic.get("aliases") or [])]
    return [str(value).strip() for value in values if str(value or "").strip()]


def _topic_exact_aliases(topic: dict[str, Any]) -> list[str]:
    return [
        str(value).strip()
        for value in topic.get("exactAliases") or []
        if str(value or "").strip()
    ]


_NEGATED_SURFACE_MARKERS = (
    "不要",
    "不含",
    "排除",
    "剔除",
    "不选",
    "别要",
    "无需",
    "不要买",
)


def _alias_is_negated(value: str, alias: str, start: int) -> bool:
    """Return whether an alias occurrence belongs to a nearby exclusion span.

    Product queries often combine a broad positive shelf with a narrow negative
    example (``平价零食不要旺旺雪饼``).  Treating the latter alias as a positive
    category contract would make the runtime discard every valid alternative.
    The check is deliberately local to the current clause so a later positive
    target (``不要 XM6，保留十周年版``) remains usable.
    """

    clause_start = max(
        value.rfind(marker, 0, start)
        for marker in ("，", ",", "。", "；", ";", "并", "但", "却")
    )
    prefix = value[clause_start + 1 : start]
    return any(marker in prefix for marker in _NEGATED_SURFACE_MARKERS)


def _alias_in_positive_context(value: str, alias: str) -> bool:
    alias_value = alias.casefold()
    offset = 0
    while True:
        start = value.find(alias_value, offset)
        if start < 0:
            return False
        if not _alias_is_negated(value, alias_value, start):
            return True
        offset = start + len(alias_value)


def runtime_surface_contracts(query: str | None) -> list[dict[str, list[str] | str]]:
    """Return snapshot-verifiable product-type contracts explicitly named in a query."""

    value = str(query or "").strip().casefold()
    if not value:
        return []
    contracts: list[dict[str, list[str] | str]] = []
    for topic in _runtime_topics():
        aliases = _topic_aliases(topic)
        exact_aliases = _topic_exact_aliases(topic)
        if not any(_alias_in_positive_context(value, alias.casefold()) for alias in aliases) and not any(
            value == alias.casefold() for alias in exact_aliases
        ):
            continue
        surface_terms = topic.get("surfaceTerms") or aliases
        contracts.append(
            {
                "category": str(
                    topic.get("runtimeCategory") or topic.get("canonical") or ""
                ).strip(),
                "surfaceTerms": [
                    str(term).strip()
                    for term in surface_terms
                    if str(term or "").strip()
                ],
                "blockedTerms": [
                    str(term).strip()
                    for term in topic.get("blockedTerms") or []
                    if str(term or "").strip()
                ],
            }
        )
    return contracts


def verified_qualifier_contracts(query: str | None) -> list[dict[str, list[str] | str]]:
    value = str(query or "").strip().casefold()
    contracts: list[dict[str, list[str] | str]] = []
    for qualifier in _verified_qualifiers():
        triggers = [str(item).strip() for item in qualifier.get("triggers") or []]
        if not any(trigger and trigger.casefold() in value for trigger in triggers):
            continue
        contracts.append(
            {
                "id": str(qualifier.get("id") or "verified-qualifier"),
                "evidenceTerms": [
                    str(item).strip()
                    for item in qualifier.get("evidenceTerms") or []
                    if str(item or "").strip()
                ],
            }
        )
    return contracts


def variant_exclusion_contracts(term: str | None) -> list[dict[str, list[str] | str]]:
    """Return explicit SKU evidence that can disambiguate a noisy listing title."""

    value = str(term or "").strip().casefold()
    if not value:
        return []
    contracts: list[dict[str, list[str] | str]] = []
    for contract in _variant_exclusion_evidence():
        excluded_terms = [
            str(item).strip()
            for item in contract.get("excludedTerms") or []
            if str(item or "").strip()
        ]
        if value not in {item.casefold() for item in excluded_terms}:
            continue
        contracts.append(
            {
                "id": str(contract.get("id") or "variant-exclusion"),
                "excludedTerms": excluded_terms,
                "requiredAlternativeTerms": [
                    str(item).strip()
                    for item in contract.get("requiredAlternativeTerms") or []
                    if str(item or "").strip()
                ],
            }
        )
    return contracts


_MODEL_TOKEN_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+|"
    r"[a-z]+[a-z0-9]*\d+[a-z0-9-]*|"
    r"\d+[a-z][a-z0-9-]*"
    r")(?![a-z0-9])"
)
_SPEC_TOKEN_RE = re.compile(
    r"(?i)^(?:[345]g|\d+(?:\.\d+)?(?:gb|tb|g|w|mah|ml|cm|mm))$"
)


def exact_model_tokens(query: str | None) -> tuple[str, ...]:
    """Extract model-like identifiers while excluding common units and network labels."""

    tokens: list[str] = []
    for match in _MODEL_TOKEN_RE.finditer(str(query or "")):
        raw = match.group(0).strip("-_")
        if not raw or _SPEC_TOKEN_RE.fullmatch(raw):
            continue
        normalized = re.sub(r"[^a-z0-9]", "", raw.casefold())
        if len(normalized) >= 2 and normalized not in tokens:
            tokens.append(normalized)
    return tuple(tokens)


def is_comparison_query(query: str | None) -> bool:
    """Whether model identifiers describe alternative targets, not one SKU."""

    value = str(query or "")
    return bool(
        re.search(
            r"(?:与|和|对比|比较|怎么选|如何选|哪个好|排除|保留|不要|不选)",
            value,
        )
    )


def comparison_target_terms(query: str | None) -> tuple[str, ...]:
    """Extract non-model words naming an explicitly retained comparison target."""

    value = str(query or "").casefold()
    spans = re.findall(r"(?:保留|对比|比较)\s*([^，。；;！？!?]+)", value)
    terms: list[str] = []
    for span in spans:
        for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9_-]{1,}", span):
            if token not in terms:
                terms.append(token)
    return tuple(terms)


def infer_product_category(text: str | None) -> str | None:
    """Return the most specific managed catalog category named by the user.

    Only canonical names and aliases participate.  Broad retrieval terms are
    deliberately excluded because words such as "children" or "electric"
    can describe an audience or feature without naming a product category.
    """

    value = (text or "").strip().lower()
    if not value:
        return None
    # A comparison target is not necessarily the requested product.  For
    # example, "buy Martian soil and use it as a phone" must not become a
    # phone mission merely because the suffix names a managed category.
    value = re.split(r"(?:并能|并可|能|可)?(?:当作|当成|作为)", value, maxsplit=1)[0]
    cleaned_value = _clean_query(value).casefold()
    matches: list[tuple[int, int, str]] = []
    for position, topic in enumerate([*_runtime_topics(), *_topics()]):
        category = str(
            topic.get("runtimeCategory") or topic.get("canonical") or ""
        ).strip()
        if not category:
            continue
        for alias in _topic_aliases(topic):
            if _alias_in_positive_context(value, alias.casefold()):
                matches.append((len(alias), -position, category))
        for alias in _topic_exact_aliases(topic):
            if cleaned_value == alias.casefold():
                matches.append((len(alias), -position, category))
    if not matches:
        return None
    return max(matches)[2]


def primary_product_request(text: str | None) -> str:
    """Return the request span before an explicit comparison/use-as suffix."""

    value = (text or "").strip()
    return re.split(
        r"(?:并能|并可|能|可)?(?:当作|当成|作为)", value, maxsplit=1
    )[0].strip()


def _clean_query(value: str) -> str:
    fillers = [re.escape(str(item)) for item in _taxonomy().get("fillers") or [] if item]
    cleaned = re.sub("|".join(fillers), "", value) if fillers else value
    punctuation = str(_taxonomy().get("punctuation") or "")
    if punctuation:
        cleaned = re.sub(f"[{re.escape(punctuation)}\\s]+", "", cleaned)
    return cleaned.strip()


def normalize_product_search_query(text: str | None) -> str:
    """Extract a known topic when present, while preserving unknown categories."""
    value = (text or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    cleaned = _clean_query(value)
    for topic in _topics():
        canonical = str(topic.get("canonical") or "").strip()
        if any(cleaned.lower() == alias.lower() for alias in _topic_exact_aliases(topic)):
            return canonical
    candidates: list[tuple[str, str]] = []
    for topic in _topics():
        canonical = str(topic.get("canonical") or "").strip()
        candidates.extend((alias, canonical) for alias in _topic_aliases(topic))
    for alias, canonical in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if alias.lower() in lowered:
            return canonical

    return cleaned or value


def is_managed_search_keyword(value: str | None) -> bool:
    """Return whether a normalized keyword is a historical taxonomy canonical."""

    normalized = str(value or "").strip().casefold()
    return bool(normalized) and any(
        normalized == str(topic.get("canonical") or "").strip().casefold()
        for topic in _topics()
    )


def match_terms_for_query(query: str | None) -> list[str]:
    value = (query or "").strip()
    if not value:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str | None) -> None:
        normalized = str(term or "").strip().lower()
        if len(normalized) < 2 or normalized in seen:
            return
        seen.add(normalized)
        terms.append(normalized)

    normalized_query = normalize_product_search_query(value)
    add(normalized_query)
    add(value)
    lowered = value.lower()
    for topic in _topics():
        aliases = _topic_aliases(topic)
        exact_aliases = _topic_exact_aliases(topic)
        exact_match = any(
            normalized_query.lower() == alias.lower() for alias in exact_aliases
        )
        if exact_match or any(
            alias.lower() in lowered or alias.lower() in normalized_query.lower()
            for alias in aliases
        ):
            for term in topic.get("terms") or aliases:
                add(str(term))
    return terms


def topic_terms_for_text(text: str | None) -> list[str]:
    """Return managed taxonomy terms only when the text names a known topic."""
    value = (text or "").strip().lower()
    if not value:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for topic in _topics():
        aliases = _topic_aliases(topic)
        exact_aliases = _topic_exact_aliases(topic)
        topic_terms = topic.get("terms") or aliases
        exact_match = any(value == alias.lower() for alias in exact_aliases)
        if not exact_match and not any(
            alias.lower() in value for alias in aliases + [str(v) for v in topic_terms]
        ):
            continue
        for term in topic_terms:
            normalized = str(term or "").strip().lower()
            if len(normalized) >= 2 and normalized not in seen:
                seen.add(normalized)
                terms.append(normalized)
    return terms


def product_matches_query_terms(product: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    name = str(product.get("product_name") or product.get("productName") or "").lower()
    description = str(
        product.get("product_desc")
        or product.get("productDesc")
        or product.get("description")
        or ""
    ).lower()
    haystack = f"{name} {description}"
    return any(term in haystack for term in terms)


def filter_products_by_query_relevance(products: list[dict], query: str | None) -> list[dict]:
    if not products:
        return []
    terms = match_terms_for_query(query)
    if not terms:
        return list(products)
    return [product for product in products if product_matches_query_terms(product, terms)]
