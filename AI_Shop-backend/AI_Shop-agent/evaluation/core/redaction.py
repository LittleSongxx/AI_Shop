from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

_SECRET_FIELD_NAMES = frozenset(
    {
        "authorization",
        "api_key",
        "access_token",
        "refresh_token",
        "token",
        "action_token",
        "password",
        "passwd",
        "cookie",
        "set_cookie",
        "secret",
        "credential",
        "credentials",
        "private_key",
    }
)
_SECRET_FIELD_SUFFIXES = tuple(f"_{name}" for name in _SECRET_FIELD_NAMES)
_IDENTITY_KEY_RE = re.compile(
    r"(?:^|[_-])(?:user|account|member|phone|email)(?:id)?(?:$|[_-])",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_TOKEN_RE = re.compile(r"(?i)\b(?:sk|ak)-[A-Za-z0-9_-]{12,}")
_ACTION_TOKEN_RE = re.compile(r"(?i)\bact_[a-f0-9]{32}\b")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _identity_digest(value: Any) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"[REDACTED_ID:{digest}]"


def redact_text(value: str) -> str:
    text = _BEARER_RE.sub("[REDACTED_BEARER]", value)
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _ACTION_TOKEN_RE.sub("[REDACTED_ACTION_TOKEN]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return _EMAIL_RE.sub("[REDACTED_EMAIL]", text)


def _normalized_field_name(key: str) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return re.sub(r"[^a-zA-Z0-9]+", "_", snake).strip("_").casefold()


def _is_secret_field(key: str) -> bool:
    normalized = _normalized_field_name(key)
    return normalized in _SECRET_FIELD_NAMES or normalized.endswith(
        _SECRET_FIELD_SUFFIXES
    )


def redact(value: Any, *, key: str | None = None) -> Any:
    if key and _is_secret_field(key):
        return "[REDACTED_SECRET]"
    if key and _IDENTITY_KEY_RE.search(key) and value not in (None, ""):
        return _identity_digest(value)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact(item) for item in value]
    return value
