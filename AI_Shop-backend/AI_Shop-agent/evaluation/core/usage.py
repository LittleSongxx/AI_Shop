"""Provider usage and cost evidence with explicit unknown states.

The evaluation package must never turn a missing provider usage object into a
zero-cost claim.  This module is deliberately dependency-free so adapters and
offline contract tests use exactly the same normalization rules.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from evaluation.core.contracts import CostStatus


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return int(number) if number.is_integer() else number


def _first_number(source: Mapping[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        if key in source:
            value = _number(source.get(key))
            if value is not None:
                return value
    return None


def _pricing(pricing: Mapping[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(pricing, Mapping):
        return None
    input_price = _number(pricing.get("input"))
    output_price = _number(pricing.get("output"))
    if input_price is None or output_price is None:
        return None
    return float(input_price), float(output_price)


def normalize_usage(
    raw: Mapping[str, Any] | None,
    *,
    pricing: Mapping[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    default_calls: int = 0,
) -> dict[str, Any]:
    """Normalize LangChain/OpenAI/provider-specific usage into one shape.

    ``MISSING_USAGE`` means the provider did not return token usage at all.
    ``UNPRICED`` means tokens are known but a trusted price table is absent.
    Both states keep ``costCny`` as ``None``.
    """

    source: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    nested = source.get("usage")
    if isinstance(nested, Mapping):
        merged = {**source, **nested}
    else:
        merged = dict(source)
    input_tokens = _first_number(
        merged,
        "inputTokens",
        "input_tokens",
        "promptTokens",
        "prompt_tokens",
    )
    output_tokens = _first_number(
        merged,
        "outputTokens",
        "output_tokens",
        "completionTokens",
        "completion_tokens",
    )
    usage_reported = input_tokens is not None and output_tokens is not None
    calls = _first_number(
        merged,
        "providerCalls",
        "calls",
        "requests",
        "requestCount",
    )
    provider_calls = int(calls if calls is not None else default_calls)
    retries = int(_first_number(merged, "retries", "retryCount") or 0)
    fallback_calls = int(_first_number(merged, "fallbackCalls", "fallbackCount") or 0)
    price = _pricing(pricing)
    cost: float | None = None
    if usage_reported and price is not None:
        cost = round(float(input_tokens) * price[0] / 1_000_000 + float(output_tokens) * price[1] / 1_000_000, 8)
        status = CostStatus.PRICED.value
    elif usage_reported:
        status = CostStatus.UNPRICED.value
    else:
        status = CostStatus.MISSING_USAGE.value
    return {
        "inputTokens": int(input_tokens or 0),
        "outputTokens": int(output_tokens or 0),
        "providerCalls": provider_calls,
        "pricedCalls": provider_calls if status == CostStatus.PRICED.value else 0,
        "unpricedCalls": provider_calls if status == CostStatus.UNPRICED.value else 0,
        "costCny": cost,
        "costStatus": status,
        "usageReported": usage_reported,
        "missingUsageCalls": (
            provider_calls if status == CostStatus.MISSING_USAGE.value else 0
        ),
        "provider": str(provider or merged.get("provider") or "unknown"),
        "model": str(model or merged.get("model") or merged.get("modelName") or "unknown"),
        "retryCount": retries,
        "fallbackCalls": fallback_calls,
    }


def merge_usage(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate normalized usage without hiding unknown cost states."""

    values = list(rows)
    input_tokens = sum(int(_number(row.get("inputTokens")) or 0) for row in values)
    output_tokens = sum(int(_number(row.get("outputTokens")) or 0) for row in values)
    provider_calls = sum(int(_number(row.get("providerCalls")) or 0) for row in values)
    priced_calls = sum(int(_number(row.get("pricedCalls")) or 0) for row in values)
    unpriced_calls = sum(int(_number(row.get("unpricedCalls")) or 0) for row in values)
    missing_usage_calls = sum(
        int(
            _number(row.get("missingUsageCalls"))
            or (
                _number(row.get("providerCalls"))
                if str(row.get("costStatus")) == CostStatus.MISSING_USAGE.value
                else 0
            )
            or 0
        )
        for row in values
    )
    missing = missing_usage_calls > 0
    costs = [float(row["costCny"]) for row in values if row.get("costCny") is not None]
    if missing:
        status = CostStatus.MISSING_USAGE.value
        cost = None
    elif unpriced_calls:
        status = CostStatus.UNPRICED.value
        cost = None
    elif costs and priced_calls:
        status = CostStatus.PRICED.value
        cost = round(sum(costs), 8)
    else:
        status = CostStatus.MISSING_USAGE.value
        cost = None
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "providerCalls": provider_calls,
        "pricedCalls": priced_calls,
        "unpricedCalls": unpriced_calls,
        "costCny": cost,
        "costStatus": status,
        "usageReported": provider_calls > 0 and not missing,
        "missingUsageCalls": missing_usage_calls,
        "retryCount": sum(int(_number(row.get("retryCount")) or 0) for row in values),
        "fallbackCalls": sum(int(_number(row.get("fallbackCalls")) or 0) for row in values),
    }


def summarize_usage(
    rows: Iterable[Mapping[str, Any]],
    *,
    dimensions: tuple[str, ...] = ("domain", "slice", "provider", "model"),
) -> dict[str, Any]:
    """Return a reproducible grouped usage report for a run."""

    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(str(row.get(d) or "unknown") for d in dimensions)
        groups[key].append(row.get("usage") if isinstance(row.get("usage"), Mapping) else row)
    output: dict[str, Any] = {}
    for key, values in sorted(groups.items()):
        cursor = output
        for part in key[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[key[-1]] = merge_usage(values)
    return {"dimensions": list(dimensions), "groups": output}
