"""Normalize conversational product queries using managed taxonomy data."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "config" / "search_taxonomy.yml"
_RUNTIME_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "search_runtime_taxonomy.yml"
)
_VAGUE_QUERY_SHELL = (
    "有什么类似的", "有没有类似的", "怎么样", "怎么选", "推荐一下",
    "有没有", "还有", "类似", "同款", "推荐", "哪些", "哪个", "什么",
    "这一款", "这款", "这个", "另一款", "另一个", "其他款", "别的",
    "帮我", "给我", "找找", "看看", "换个", "换一个", "再来", "再",
    "更好", "一下", "一个", "一款", "商品", "产品", "东西",
    "怎么", "有", "的", "啥", "下", "吗", "么", "嘛", "呢", "啊",
)


def is_vague_search_keyword(text: str | None) -> bool:
    """Whether a query contains only a request/reference shell, not a topic."""

    value = str(text or "").strip()
    if not value or len(value) < 2:
        return True
    remainder = re.sub(r"[\s，。！？、,.;:!?~～]+", "", value).casefold()
    for shell in _VAGUE_QUERY_SHELL:
        remainder = remainder.replace(shell, "")
    return not re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", remainder)


def is_literal_availability_query(text: str | None) -> bool:
    value = str(text or "").strip()
    return not is_vague_search_keyword(value) and bool(
        value.startswith("有没有")
        or re.fullmatch(r"有.+(?:吗|么|嘛|[?？])", value)
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


def category_surface_terms(category: str | None) -> tuple[str, ...]:
    """Return configured title/category terms for one managed runtime category."""

    target = str(category or "").strip().casefold()
    values: list[str] = []
    for topic in [*_runtime_topics(), *_topics()]:
        topic_category = str(
            topic.get("runtimeCategory") or topic.get("canonical") or ""
        ).strip()
        if topic_category.casefold() != target:
            continue
        for raw in topic.get("surfaceTerms") or _topic_aliases(topic):
            value = str(raw or "").strip()
            if value and value.casefold() not in {item.casefold() for item in values}:
                values.append(value)
    return tuple(values)


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
_MODEL_SURFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,79}$")
_MAX_MODEL_IDENTIFIERS = 8
_MAX_QUERY_SCOPE_TERMS = 12
_MAX_QUERY_SCOPE_TERM_CHARS = 120
_ARABIC_BUDGET_NUMBER = r"\d+(?:\.\d+)?"
_CHINESE_BUDGET_NUMBER = r"[零〇一二两三四五六七八九十百千万]+"
_BUDGET_NUMBER = rf"(?:{_ARABIC_BUDGET_NUMBER}|{_CHINESE_BUDGET_NUMBER})"
_BUDGET_RANGE_RE = re.compile(
    rf"(?P<min>{_BUDGET_NUMBER})\s*(?:元|块)?\s*(?:到|至|[-~—])\s*"
    rf"(?P<max>{_BUDGET_NUMBER})\s*(?:元|块)?"
)
_BUDGET_MAX_RE = re.compile(
    rf"(?P<value>{_BUDGET_NUMBER})\s*(?:元|块)\s*(?:以内|以下|不超过|至多|封顶|内)"
    rf"|(?:预算|价格)\s*(?P<budget>{_BUDGET_NUMBER})\s*(?:元|块)?"
)
_BUDGET_MIN_RE = re.compile(
    rf"(?P<value>{_BUDGET_NUMBER})\s*(?:元|块)\s*(?:以上|起步|至少)"
)
_NEGATIVE_TERM_RE = re.compile(
    r"(?:不要|不含|排除|剔除|不选|别要|无需|不要买)\s*"
    r"([^，。；;！？!?并且同时和与保留的]+)"
)
_QUERY_FILLER_RE = re.compile(
    r"(?:预算|价格|以内|以下|不超过|至多|封顶|以上|至少|"
    r"如何比较|怎么比较|如何选|怎么选|哪个好|哪个更好|"
    r"推荐|想买|我要|帮我|请|平价|便宜|比较|对比)"
)


def _exact_model_identifiers(query: str | None) -> tuple[tuple[str, str], ...]:
    """Return bounded ``(surface, normalized)`` model identifiers."""

    identifiers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _MODEL_TOKEN_RE.finditer(str(query or "")):
        raw = match.group(0).strip("-_")
        if (
            not raw
            or _SPEC_TOKEN_RE.fullmatch(raw)
            or not _MODEL_SURFACE_RE.fullmatch(raw)
        ):
            continue
        normalized = re.sub(r"[^a-z0-9]", "", raw.casefold())
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        identifiers.append((raw, normalized))
        if len(identifiers) >= _MAX_MODEL_IDENTIFIERS:
            break
    return tuple(identifiers)


def exact_model_surfaces(query: str | None) -> tuple[str, ...]:
    """Preserve safe display forms for explicit model identifiers."""

    return tuple(surface for surface, _token in _exact_model_identifiers(query))


def exact_model_tokens(query: str | None) -> tuple[str, ...]:
    """Extract model-like identifiers while excluding common units and network labels."""

    return tuple(token for _surface, token in _exact_model_identifiers(query))


def _clean_constraint_term(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[，。；;！？!?、：:（）()\[\]\\\"']+", " ", text)
    text = _QUERY_FILLER_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def parse_budget_number(value: str | None) -> float | None:
    """Parse an explicit budget number, including common Chinese shorthand.

    Conversational forms such as ``一千二`` and ``一万二`` mean 1,200 and
    12,000 in a budget slot. The parser is only called from budget-bound
    regular expressions, so arbitrary Chinese numerals elsewhere are not
    interpreted as prices.
    """

    raw = re.sub(r"[\s,，]", "", str(value or ""))
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    valid_characters = set(_CHINESE_DIGITS) | set(_CHINESE_UNITS)
    if any(character not in valid_characters for character in raw):
        return None

    shorthand_tail = 0
    if raw[-1] in _CHINESE_DIGITS and len(raw) >= 2 and raw[-2] not in {"零", "〇"}:
        unit_positions = [
            (index, _CHINESE_UNITS[character])
            for index, character in enumerate(raw[:-1])
            if character in _CHINESE_UNITS
        ]
        if unit_positions:
            last_index, last_unit = unit_positions[-1]
            if last_index == len(raw) - 2 and last_unit > 1:
                shorthand_tail = _CHINESE_DIGITS[raw[-1]] * (last_unit // 10)
                raw = raw[:-1]

    total = 0
    section = 0
    number = 0
    for character in raw:
        if character in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[character]
            continue
        unit = _CHINESE_UNITS[character]
        if unit == 10000:
            section += number
            total += (section or 1) * unit
            section = 0
        else:
            section += (number or 1) * unit
        number = 0
    return float(total + section + number + shorthand_tail)


def extract_budget_constraints(query: str | None) -> tuple[float | None, float | None]:
    value = str(query or "")
    minimum: float | None = None
    maximum: float | None = None
    range_match = _BUDGET_RANGE_RE.search(value)
    if range_match:
        minimum = parse_budget_number(range_match.group("min"))
        maximum = parse_budget_number(range_match.group("max"))
        if minimum is not None and maximum is not None and minimum > maximum:
            minimum, maximum = maximum, minimum
    max_match = _BUDGET_MAX_RE.search(value)
    if max_match:
        raw = max_match.group("value") or max_match.group("budget")
        if raw is not None:
            parsed = parse_budget_number(raw)
            if parsed is not None:
                maximum = parsed if maximum is None else min(maximum, parsed)
    min_match = _BUDGET_MIN_RE.search(value)
    if min_match:
        minimum = parse_budget_number(min_match.group("value"))
    return minimum, maximum


def extract_query_exclusions(query: str | None) -> tuple[str, ...]:
    values: list[str] = []
    for match in _NEGATIVE_TERM_RE.finditer(str(query or "")):
        term = _clean_constraint_term(match.group(1))
        if term and term.casefold() not in {item.casefold() for item in values}:
            values.append(term)
    return tuple(values)


def _comparison_segments(query: str | None) -> list[str]:
    value = primary_product_request(query)
    value = re.sub(
        r"(?:分别)?(?:"
        r"有(?:什么|何)(?:不同|区别|差异)|"
        r"(?:主要)?差(?:在)?哪(?:里)?|"
        r"(?:区别|差别|差异)(?:是)?(?:什么|在哪|有哪些)?|"
        r"(?:适合|用于)(?:什么|哪些|哪种)?(?:场景|人群|用途)|"
        r"(?:哪个|哪款)(?:更好|更适合)?|"
        r"(?:如何|怎么|怎样)(?:比较|对比|选(?:择)?)"
        r")\s*$",
        "",
        value,
    )
    value = _BUDGET_RANGE_RE.sub(" ", value)
    value = _BUDGET_MAX_RE.sub(" ", value)
    value = _BUDGET_MIN_RE.sub(" ", value)
    value = re.sub(r"(?:不要|不含|排除|剔除|不选|别要)\s*[^，。；;！？!?]+", " ", value)
    parts = re.split(
        r"\s*(?:和|与|及|以及|对比|比较|、|VS|vs|VS\.|怎么选|如何选|哪个好|哪个更好)\s*",
        value,
    )
    result: list[str] = []
    for part in parts:
        cleaned = _clean_constraint_term(part)
        cleaned = re.sub(
            r"^(?:请|麻烦)?(?:帮我)?(?:看下|看看)?(?:这款|这两个|这两款|这两种)\s*",
            "",
            cleaned,
        )
        compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", cleaned)
        if len(compact) < 2:
            continue
        if cleaned.casefold() not in {item.casefold() for item in result}:
            result.append(cleaned)
    return result


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
    """Extract bounded, user-named comparison target terms."""

    raw = str(query or "")
    explicit_target_spans = re.findall(
        r"(?:保留|对比|比较)\s*([^，。；;！？!?]+)", raw.casefold()
    )
    segments = (
        explicit_target_spans
        if explicit_target_spans
        else _comparison_segments(raw)
        if re.search(r"(?:和|与|及|以及|对比|比较|、|VS|vs|怎么选|如何选|哪个好)", raw)
        else []
    )
    terms: list[str] = []
    generic_suffixes = {
        "零食",
        "耳机",
        "降噪耳机",
        "无线降噪耳机",
        "手机",
        "电脑",
        "办公机",
    }
    for segment in segments:
        for token in re.findall(
            r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9_-]{1,}", segment.casefold()
        ):
            normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", token)
            for suffix in sorted(generic_suffixes, key=len, reverse=True):
                if normalized == suffix:
                    normalized = ""
                    break
                if normalized.endswith(suffix) and len(normalized) > len(suffix):
                    normalized = normalized[: -len(suffix)]
                    break
            if len(normalized) >= 2 and normalized not in terms:
                terms.append(normalized)
    return tuple(terms)


def extract_query_hard_constraints(query: str | None) -> dict[str, Any]:
    """Parse only explicit, conservative hard constraints from user text."""

    raw = str(query or "").strip()
    budget_min, budget_max = extract_budget_constraints(raw)
    must_not = extract_query_exclusions(raw)
    model_tokens = exact_model_tokens(raw)
    targets = comparison_target_terms(raw)
    comparison_required = bool(
        is_comparison_query(raw) and len(model_tokens) + len(targets) >= 2
    )
    excluded_compact = {
        re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", term.casefold())
        for term in must_not
    }
    must_terms: list[str] = [
        term
        for term in model_tokens
        if re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", term.casefold())
        not in excluded_compact
    ]
    if comparison_required:
        must_terms.extend(targets)
    unique_must: list[str] = []
    for term in must_terms:
        if term and term.casefold() not in {item.casefold() for item in unique_must}:
            unique_must.append(term)
    return {
        "budget_min": budget_min,
        "budget_max": budget_max,
        "must_terms": tuple(unique_must),
        "must_not_terms": must_not,
        "comparison_targets": targets,
        "comparison_required": comparison_required,
    }


def _bounded_query_scope_terms(values: Any) -> list[str]:
    """Project parser-owned terms without carrying arbitrary query prose."""

    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw or "").strip().split())
        if not value or len(value) > _MAX_QUERY_SCOPE_TERM_CHARS:
            continue
        if any(ord(character) < 32 for character in value):
            continue
        identity = value.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(value)
        if len(result) >= _MAX_QUERY_SCOPE_TERMS:
            break
    return result


def build_product_query_scope(query: str | None) -> dict[str, Any]:
    """Build a bounded, auditable projection of the accepted constraint query.

    The full user turn is represented only by its digest and character count.
    Displayable values are limited to parser-owned model surfaces and explicit
    hard constraints, so this object can be persisted in evidence safely.
    """

    raw = str(query or "").strip()
    constraints = extract_query_hard_constraints(raw)
    return {
        "schemaVersion": "product-query-scope/v1",
        "querySha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "queryChars": len(raw),
        "requestedModels": list(exact_model_surfaces(raw)),
        "modelTokens": list(exact_model_tokens(raw)),
        "budgetMin": constraints.get("budget_min"),
        "budgetMax": constraints.get("budget_max"),
        "mustTerms": _bounded_query_scope_terms(constraints.get("must_terms")),
        "mustNotTerms": _bounded_query_scope_terms(
            constraints.get("must_not_terms")
        ),
        "comparisonTargets": _bounded_query_scope_terms(
            constraints.get("comparison_targets")
        ),
        "comparisonRequired": bool(constraints.get("comparison_required")),
    }


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
    availability_question = bool(re.search(r"(?:吗|么|嘛|[?？])\s*$", value))
    fillers = [re.escape(str(item)) for item in _taxonomy().get("fillers") or [] if item]
    cleaned = re.sub("|".join(fillers), "", value) if fillers else value
    punctuation = str(_taxonomy().get("punctuation") or "")
    if punctuation:
        cleaned = re.sub(f"[{re.escape(punctuation)}\\s]+", "", cleaned)
    if availability_question:
        cleaned = re.sub(r"^有", "", cleaned)
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
    for token in re.findall(
        r"[a-z][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", normalized_query.casefold()
    ):
        add(token)
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
