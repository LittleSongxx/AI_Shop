"""Normalize conversational product queries using managed taxonomy data."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "config" / "search_taxonomy.yml"


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


def _topic_aliases(topic: dict[str, Any]) -> list[str]:
    values = [topic.get("canonical"), *(topic.get("aliases") or [])]
    return [str(value).strip() for value in values if str(value or "").strip()]


def normalize_product_search_query(text: str | None) -> str:
    """Extract a known topic when present, while preserving unknown categories."""
    value = (text or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    candidates: list[tuple[str, str]] = []
    for topic in _topics():
        canonical = str(topic.get("canonical") or "").strip()
        candidates.extend((alias, canonical) for alias in _topic_aliases(topic))
    for alias, canonical in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if alias.lower() in lowered:
            return canonical

    fillers = [re.escape(str(item)) for item in _taxonomy().get("fillers") or [] if item]
    cleaned = re.sub("|".join(fillers), "", value) if fillers else value
    punctuation = str(_taxonomy().get("punctuation") or "")
    if punctuation:
        cleaned = re.sub(f"[{re.escape(punctuation)}\\s]+", "", cleaned)
    cleaned = cleaned.strip()
    return cleaned or value


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
        if any(alias.lower() in lowered or alias.lower() in normalized_query.lower() for alias in aliases):
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
        topic_terms = topic.get("terms") or aliases
        if not any(alias.lower() in value for alias in aliases + [str(v) for v in topic_terms]):
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
