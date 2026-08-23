"""Explicitly sourced list-price estimates, separate from actual billing.

Provider usage can be converted to a reproducible *estimate* when a public
catalogue price is available.  The result deliberately never uses the
``PRICED`` status used for trusted contract/billing prices.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from evaluation.core.io import canonical_json_bytes

LIST_PRICE_ESTIMATE_SCHEMA = "aishop-list-price-estimate/v1"
LIST_PRICE_ESTIMATE_STATUS = "ESTIMATED_LIST_PRICE"


class PricingEvidenceError(ValueError):
    """Raised when a public price quote lacks reproducible provenance."""


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise PricingEvidenceError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PricingEvidenceError(f"{field} must be numeric") from exc
    if result < 0:
        raise PricingEvidenceError(f"{field} must be non-negative")
    return result


def validate_price_quote(quote: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize a sourced public catalogue quote."""

    required = {
        "sourceUrl",
        "retrievedAt",
        "sourceContentSha256",
        "provider",
        "region",
        "modelId",
        "modelFingerprint",
        "inputPriceCnyPerMillion",
        "outputPriceCnyPerMillion",
        "inputTokenUpperBound",
    }
    missing = sorted(field for field in required if not str(quote.get(field) or ""))
    if missing:
        raise PricingEvidenceError(f"price quote is missing: {', '.join(missing)}")
    digest = str(quote["sourceContentSha256"])
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise PricingEvidenceError("sourceContentSha256 must be a lowercase SHA-256")
    upper_bound = _positive_number(quote["inputTokenUpperBound"], "inputTokenUpperBound")
    input_price = _positive_number(
        quote["inputPriceCnyPerMillion"], "inputPriceCnyPerMillion"
    )
    output_price = _positive_number(
        quote["outputPriceCnyPerMillion"], "outputPriceCnyPerMillion"
    )
    normalized = {
        "schemaVersion": LIST_PRICE_ESTIMATE_SCHEMA,
        "status": LIST_PRICE_ESTIMATE_STATUS,
        "sourceUrl": str(quote["sourceUrl"]),
        "retrievedAt": str(quote["retrievedAt"]),
        "sourceContentSha256": digest,
        "provider": str(quote["provider"]),
        "region": str(quote["region"]),
        "modelId": str(quote["modelId"]),
        "modelFingerprint": str(quote["modelFingerprint"]),
        "inputPriceCnyPerMillion": input_price,
        "outputPriceCnyPerMillion": output_price,
        "inputTokenUpperBound": int(upper_bound),
        "priceBasis": str(quote.get("priceBasis") or "ORIGINAL_PUBLIC_CATALOGUE_PRICE"),
        "usableForBudgetGate": False,
        "billingContractVerified": False,
        "notes": list(quote.get("notes") or []),
    }
    normalized["quoteSha256"] = hashlib.sha256(
        canonical_json_bytes(normalized)
    ).hexdigest()
    return normalized


def estimate_usage_cost(
    usage: Mapping[str, Any], quote: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a catalogue estimate without changing the usage cost status."""

    normalized_quote = validate_price_quote(quote)
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    calls = usage.get("providerCalls")
    if input_tokens is None or output_tokens is None or int(calls or 0) <= 0:
        return {
            "status": "UNAVAILABLE",
            "costCny": None,
            "quoteSha256": normalized_quote["quoteSha256"],
            "reason": "MISSING_USAGE_OR_PROVIDER_CALL",
        }
    input_count = _positive_number(input_tokens, "inputTokens")
    output_count = _positive_number(output_tokens, "outputTokens")
    if input_count > normalized_quote["inputTokenUpperBound"]:
        return {
            "status": "UNAVAILABLE",
            "costCny": None,
            "quoteSha256": normalized_quote["quoteSha256"],
            "reason": "INPUT_TOKEN_TIER_NOT_COVERED",
        }
    estimate = (
        input_count * normalized_quote["inputPriceCnyPerMillion"]
        + output_count * normalized_quote["outputPriceCnyPerMillion"]
    ) / 1_000_000
    return {
        "status": LIST_PRICE_ESTIMATE_STATUS,
        "costCny": round(estimate, 8),
        "quoteSha256": normalized_quote["quoteSha256"],
        "actualBillingStatus": str(usage.get("costStatus") or "UNKNOWN"),
        "usableForBudgetGate": False,
    }

