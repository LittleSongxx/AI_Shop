from __future__ import annotations

import pytest

from evaluation.core.pricing import (
    LIST_PRICE_ESTIMATE_STATUS,
    PricingEvidenceError,
    estimate_usage_cost,
    validate_price_quote,
)


def _quote() -> dict:
    return {
        "sourceUrl": "https://example.test/pricing",
        "retrievedAt": "2026-08-24T00:00:00+08:00",
        "sourceContentSha256": "a" * 64,
        "provider": "example",
        "region": "cn-beijing",
        "modelId": "qwen3.7-plus",
        "modelFingerprint": "qwen3.7-plus-2026-05-26",
        "inputPriceCnyPerMillion": 2,
        "outputPriceCnyPerMillion": 8,
        "inputTokenUpperBound": 256000,
    }


def test_list_price_estimate_has_provenance_and_never_becomes_priced():
    quote = validate_price_quote(_quote())
    result = estimate_usage_cost(
        {
            "inputTokens": 1000,
            "outputTokens": 500,
            "providerCalls": 1,
            "costStatus": "UNPRICED",
        },
        quote,
    )
    assert result["status"] == LIST_PRICE_ESTIMATE_STATUS
    assert result["costCny"] == pytest.approx(0.006)
    assert result["actualBillingStatus"] == "UNPRICED"
    assert result["usableForBudgetGate"] is False
    assert len(result["quoteSha256"]) == 64


def test_list_price_estimate_is_unavailable_for_missing_usage_or_tier():
    quote = validate_price_quote(_quote())
    missing = estimate_usage_cost({"providerCalls": 1}, quote)
    assert missing["status"] == "UNAVAILABLE"
    oversized = estimate_usage_cost(
        {"inputTokens": 256001, "outputTokens": 1, "providerCalls": 1}, quote
    )
    assert oversized["reason"] == "INPUT_TOKEN_TIER_NOT_COVERED"


def test_quote_requires_source_hash():
    quote = _quote()
    quote["sourceContentSha256"] = "not-a-hash"
    with pytest.raises(PricingEvidenceError):
        validate_price_quote(quote)

