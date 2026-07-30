"""P2-3 A/B testing — stable bucket assignment + retrieval-settings overlay.

Bucket assignment is deterministic: the same user_id always maps to the same
bucket, regardless of restarts or replicas.  Bucket "A" is the unmodified
baseline; buckets "B", "C", … receive the param overrides declared in
settings.ab_test_config.

Configuration (settings.py / .env)
-----------------------------------
AB_TEST_BUCKETS=2                          # 0 or 1 → disabled; 2 → A/B; 3 → A/B/C…
AB_TEST_CONFIG='{"B":{"rag_top_k":20,"rerank_top_n":10}}'
"""
from __future__ import annotations

import hashlib
from typing import Any

import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()


def get_bucket(user_id: str) -> str:
    """Return a stable single-letter bucket label (A, B, C…) for *user_id*.

    When A/B testing is disabled (``ab_test_buckets`` ≤ 1), always returns
    ``"A"`` so callers need no special-case logic.
    """
    settings = get_settings()
    n = settings.ab_test_buckets
    if n <= 1:
        return "A"
    # MD5 is not used for security here — only for stable, fast hashing.
    digest = int(
        hashlib.md5(user_id.encode(), usedforsecurity=False).hexdigest(), 16
    )
    return chr(ord("A") + (digest % n))


def get_rag_overrides(bucket: str) -> dict[str, Any]:
    """Return a copy of the retrieval-param overrides for *bucket*.

    Returns an empty dict for the baseline bucket "A" or when no override is
    configured, so callers can safely do ``overrides.get("rag_top_k")``.
    """
    settings = get_settings()
    overrides: dict[str, Any] = dict(
        (settings.ab_test_config or {}).get(bucket) or {}
    )
    if overrides:
        logger.debug(
            "ab_test_rag_override_applied", bucket=bucket, overrides=overrides
        )
    return overrides
