"""Small, shared trust-boundary helpers for the Agent HTTP/WS surfaces.

The Java gateway remains the first authentication hop.  These helpers are the
same-origin and object-ownership backstop used when the Python service is
reached directly (which is also how the local evidence harness runs it).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Literal, Mapping
from urllib.parse import urlsplit

TokenSource = Literal["header", "cookie", "query", "session"]
PrincipalKind = Literal["USER", "ADMIN"]


@dataclass(frozen=True)
class Principal:
    """An authenticated subject; never populated from a request body field."""

    subject: str
    kind: PrincipalKind = "USER"
    auth_source: TokenSource = "session"

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", str(self.subject or "").strip())

    def owns(self, subject: object) -> bool:
        """Constant-time ownership check for user-scoped resources."""

        candidate = str(subject or "").strip()
        return bool(self.subject) and hmac.compare_digest(self.subject, candidate)

    def as_dict(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "kind": self.kind,
            "authSource": self.auth_source,
        }


@dataclass(frozen=True)
class WsCredentials:
    token: str
    source: TokenSource


def canonical_origin(value: str | None) -> str | None:
    """Return a strict scheme/host/port origin, or ``None`` when malformed."""

    raw = str(value or "").strip()
    if not raw or raw.lower() == "null":
        return None
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        host = parsed.hostname.rstrip(".").lower()
        port = parsed.port
    except ValueError:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = parsed.scheme.lower()
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        suffix = ""
    else:
        suffix = f":{port}"
    return f"{scheme}://{host}{suffix}"


def _same_origin_from_headers(host: str | None, forwarded_proto: str | None) -> str | None:
    raw_host = str(host or "").split(",", 1)[0].strip()
    if not raw_host:
        return None
    scheme = str(forwarded_proto or "http").split(",", 1)[0].strip().lower()
    if scheme not in {"http", "https"}:
        return None
    return canonical_origin(f"{scheme}://{raw_host}")


def is_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: list[str] | tuple[str, ...] | None = None,
    request_host: str | None = None,
    forwarded_proto: str | None = None,
    allow_missing: bool = False,
) -> bool:
    """Validate an Origin exactly; wildcard credentials are intentionally unsupported."""

    raw = str(origin or "").strip()
    if not raw:
        return allow_missing
    candidate = canonical_origin(raw)
    if candidate is None:
        return False

    configured = {
        normalized
        for value in allowed_origins or ()
        if (normalized := canonical_origin(value)) is not None
    }
    if configured:
        return candidate in configured

    expected = _same_origin_from_headers(request_host, forwarded_proto)
    return expected is not None and hmac.compare_digest(candidate, expected)


def websocket_origin_allowed(
    origin: str | None,
    *,
    token_source: TokenSource,
    allowed_origins: list[str] | tuple[str, ...] | None = None,
    request_host: str | None = None,
    forwarded_proto: str | None = None,
) -> bool:
    """Cookie-authenticated WebSockets must carry a same-site Origin.

    Header/query tokens are intended for non-browser clients.  They may omit
    Origin, but an Origin that is present is still checked.
    """

    if not str(origin or "").strip():
        return token_source != "cookie"
    return is_origin_allowed(
        origin,
        allowed_origins=allowed_origins,
        request_host=request_host,
        forwarded_proto=forwarded_proto,
    )


def csrf_origin_allowed(
    origin: str | None,
    *,
    allowed_origins: list[str] | tuple[str, ...] | None = None,
    request_host: str | None = None,
    forwarded_proto: str | None = None,
) -> bool:
    """Origin-based CSRF check for unsafe requests authenticated by a cookie."""

    return is_origin_allowed(
        origin,
        allowed_origins=allowed_origins,
        request_host=request_host,
        forwarded_proto=forwarded_proto,
        allow_missing=False,
    )


def content_length_exceeds(headers: Mapping[str, str], limit_bytes: int) -> bool:
    """Fail closed for malformed/oversized declared request bodies."""

    raw = headers.get("content-length")
    if raw is None or not str(raw).strip():
        return False
    try:
        length = int(str(raw).strip())
    except (TypeError, ValueError):
        return True
    return length < 0 or length > max(1, int(limit_bytes))
