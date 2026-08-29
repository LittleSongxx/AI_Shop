"""Small, fail-closed lifecycle and access gate for RAG candidates.

The search service owns publication state.  This module is the Agent-side
backstop for cached/legacy/vector candidates: an absent policy means the
legacy single-store public policy, while an explicit malformed policy is
rejected.  It deliberately has no tenant or row-level-security concept.
"""

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.harness.metrics.runtime_sensors import RAG_LIFECYCLE_FILTER_TOTAL

_PUBLIC = "PUBLIC"
_AUTHENTICATED = "AUTHENTICATED"
_ADMIN = "ADMIN"
_ROLE_USER = "ROLE:USER"
_ROLE_ADMIN = "ROLE:ADMIN"
_POLICY_KEYS = (
    "accessPolicy",
    "access_policy",
    "acl",
    "allowedPrincipals",
    "allowed_principals",
)
_START_KEYS = ("effectiveStart", "effective_start", "freshnessStart", "validFrom")
_END_KEYS = (
    "effectiveEnd",
    "effective_end",
    "freshnessUntil",
    "freshness_until",
    "validUntil",
    "expiresAt",
)


@dataclass(frozen=True)
class RagPrincipal:
    """The minimum server-bound identity needed by the RAG access gate."""

    subject: str = ""
    kind: str = "ANONYMOUS"

    @property
    def authenticated(self) -> bool:
        return bool(self.subject)


@dataclass(frozen=True)
class LifecycleFilterResult:
    documents: list[dict[str, Any]]
    rejected: dict[str, int]


def normalize_principal(value: Any) -> RagPrincipal:
    if isinstance(value, RagPrincipal):
        return value
    if value is None:
        return RagPrincipal()
    if isinstance(value, Mapping):
        subject = str(value.get("subject") or value.get("userId") or "").strip()
        kind = str(value.get("kind") or "USER").strip().upper() if subject else "ANONYMOUS"
        return RagPrincipal(subject=subject, kind=kind if kind in {"USER", "ADMIN"} else "USER")
    subject = str(getattr(value, "subject", "") or getattr(value, "user_id", "") or value).strip()
    kind = str(getattr(value, "kind", "USER") or "USER").upper() if subject else "ANONYMOUS"
    return RagPrincipal(subject=subject, kind=kind if kind in {"USER", "ADMIN"} else "USER")


def _policy_value(metadata: Mapping[str, Any]) -> tuple[Any, bool]:
    for key in _POLICY_KEYS:
        if key in metadata:
            return metadata[key], True
    return _PUBLIC, False


def parse_access_policy(value: Any) -> tuple[frozenset[str], bool]:
    """Return canonical policy tokens and whether the value is valid."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return frozenset({_PUBLIC}), True
    raw_values: list[Any]
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                return frozenset(), False
            raw_values = parsed if isinstance(parsed, list) else [parsed]
        else:
            raw_values = [item for item in raw.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    elif isinstance(value, Mapping):
        raw_values = [value.get("mode") or value.get("policy")]
    else:
        return frozenset(), False

    tokens: set[str] = set()
    for item in raw_values:
        token = str(item or "").strip()
        if not token:
            return frozenset(), False
        upper = token.upper()
        if upper in {_PUBLIC, _AUTHENTICATED, _ADMIN, _ROLE_USER, _ROLE_ADMIN}:
            tokens.add(upper)
            continue
        if upper.startswith("USER:") and token[5:].strip() and "*" not in token:
            tokens.add("USER:" + token[5:].strip())
            continue
        return frozenset(), False
    return frozenset(tokens), bool(tokens)


def is_authorized(metadata: Mapping[str, Any], principal: Any = None) -> bool:
    value, _present = _policy_value(metadata)
    policies, valid = parse_access_policy(value)
    if not valid:
        return False
    identity = normalize_principal(principal)
    if _PUBLIC in policies:
        return True
    if not identity.authenticated:
        return False
    if _AUTHENTICATED in policies:
        return True
    if identity.kind == "ADMIN" and (_ADMIN in policies or _ROLE_ADMIN in policies):
        return True
    if identity.kind == "USER" and _ROLE_USER in policies:
        return True
    return any(
        token.startswith("USER:")
        and hmac.compare_digest(token[5:], identity.subject)
        for token in policies
    )


def _epoch_ms(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if abs(number) < 100_000_000_000:
            number *= 1000
        return int(number)
    raw = str(value).strip()
    try:
        return _epoch_ms(float(raw))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def freshness_reason(metadata: Mapping[str, Any], now_ms: int | None = None) -> str | None:
    """Return a stable rejection reason, or ``None`` when currently fresh."""

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    for key in _START_KEYS:
        if key not in metadata or metadata[key] in (None, ""):
            continue
        value = _epoch_ms(metadata[key])
        if value is None:
            return "freshness_invalid"
        if value > now:
            return "freshness_not_started"
    for key in _END_KEYS:
        if key not in metadata or metadata[key] in (None, ""):
            continue
        value = _epoch_ms(metadata[key])
        if value is None:
            return "freshness_invalid"
        if value <= now:
            return "freshness_expired"
    return None


def status_reason(metadata: Mapping[str, Any]) -> str | None:
    data_type = str(metadata.get("dataType") or metadata.get("data_type") or "").upper()
    status = metadata.get("status")
    if status is None and data_type == "FAQ":
        status = metadata.get("publishStatus") or metadata.get("publish_status")
    if status is not None and str(status).strip().upper() != "PUBLISHED":
        return "status_not_published"
    if metadata.get("deleted") is True or metadata.get("deletedAt"):
        return "status_deleted"
    return None


def filter_documents(
    documents: list[dict[str, Any]],
    principal: Any = None,
    *,
    now_ms: int | None = None,
) -> LifecycleFilterResult:
    """Filter candidates before scoring/evidence selection and count decisions."""

    kept: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for document in documents:
        metadata = document.get("metadata") or {}
        reason = status_reason(metadata)
        if reason is None and not is_authorized(metadata, principal):
            reason = "acl_denied"
        if reason is None:
            reason = freshness_reason(metadata, now_ms)
        if reason is not None:
            rejected[reason] = rejected.get(reason, 0) + 1
            RAG_LIFECYCLE_FILTER_TOTAL.labels(reason=reason).inc()
            continue
        kept.append(document)
    return LifecycleFilterResult(documents=kept, rejected=rejected)


def elastic_filter(principal: Any = None) -> dict[str, Any]:
    """Build the coarse ES ACL filter; ``filter_documents`` remains authoritative."""

    identity = normalize_principal(principal)
    allowed = [_PUBLIC]
    if identity.authenticated:
        allowed.extend([_AUTHENTICATED, f"USER:{identity.subject}"])
        allowed.append(_ROLE_ADMIN if identity.kind == "ADMIN" else _ROLE_USER)
        if identity.kind == "ADMIN":
            allowed.append(_ADMIN)
    return {
        "bool": {
            "should": [
                {"bool": {"must_not": [{"exists": {"field": "metadata.accessPolicy"}}]}},
                {"terms": {"metadata.accessPolicy.keyword": allowed}},
            ],
            "minimum_should_match": 1,
        }
    }
