from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config.settings import get_settings
from app.services.redis_service import redis_service


@dataclass(frozen=True)
class AdminAssertion:
    admin_id: str
    account: str
    roles: frozenset[str]
    permissions: frozenset[str]
    key_id: str

    def require_any(self, *permissions: str) -> "AdminAssertion":
        if not permissions or self.permissions.intersection(permissions):
            return self
        raise HTTPException(status_code=403, detail="admin permission denied")


def _csv_set(value: str | None) -> frozenset[str]:
    return frozenset(part.strip() for part in str(value or "").split(",") if part.strip())


def canonical_admin_assertion(
    method: str,
    path: str,
    body_hash: str,
    admin_id: str,
    account: str,
    roles: str,
    permissions: str,
    timestamp: str,
    nonce: str,
) -> str:
    return "\n".join(
        [
            method.upper(),
            path,
            body_hash,
            admin_id,
            account,
            roles,
            permissions,
            timestamp,
            nonce,
        ]
    )


def _secret_for_key(key_id: str) -> str | None:
    settings = get_settings()
    if key_id == settings.admin_assertion_current_key_id:
        return settings.admin_assertion_current_secret
    if (
        settings.admin_assertion_previous_secret
        and key_id == settings.admin_assertion_previous_key_id
    ):
        return settings.admin_assertion_previous_secret
    return None


async def require_admin_assertion(request: Request) -> AdminAssertion:
    settings = get_settings()
    admin_id = (request.headers.get("X-Admin-Id") or "").strip()
    account = (request.headers.get("X-Admin-Account") or "").strip()
    roles_raw = request.headers.get("X-Admin-Roles") or ""
    permissions_raw = request.headers.get("X-Admin-Permissions") or ""
    timestamp_raw = (request.headers.get("X-Admin-Timestamp") or "").strip()
    nonce = (request.headers.get("X-Admin-Nonce") or "").strip()
    body_hash = (request.headers.get("X-Admin-Body-SHA256") or "").strip().lower()
    signature = (request.headers.get("X-Admin-Signature") or "").strip().lower()
    key_id = (request.headers.get("X-Admin-Key-Id") or "").strip()

    if not all([admin_id, timestamp_raw, nonce, body_hash, signature, key_id]):
        raise HTTPException(status_code=401, detail="missing admin assertion")
    if len(nonce) > 128 or len(signature) != 64 or len(body_hash) != 64:
        raise HTTPException(status_code=401, detail="invalid admin assertion")

    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid admin assertion timestamp") from exc
    now = int(time.time())
    if abs(now - timestamp) > settings.admin_assertion_max_age_seconds:
        raise HTTPException(status_code=401, detail="expired admin assertion")

    body = await request.body()
    actual_body_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(actual_body_hash, body_hash):
        raise HTTPException(status_code=401, detail="admin assertion body mismatch")

    secret = _secret_for_key(key_id)
    if not secret:
        raise HTTPException(status_code=401, detail="unknown admin assertion key")
    canonical = canonical_admin_assertion(
        request.method,
        request.url.path,
        body_hash,
        admin_id,
        account,
        roles_raw,
        permissions_raw,
        timestamp_raw,
        nonce,
    )
    expected = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid admin assertion signature")

    nonce_key = hashlib.sha256(f"{admin_id}\0{key_id}\0{nonce}".encode()).hexdigest()
    if not await redis_service.claim_admin_assertion_nonce(
        nonce_key, settings.admin_assertion_nonce_ttl_seconds
    ):
        raise HTTPException(status_code=401, detail="replayed admin assertion")

    return AdminAssertion(
        admin_id=admin_id,
        account=account,
        roles=_csv_set(roles_raw),
        permissions=_csv_set(permissions_raw),
        key_id=key_id,
    )
