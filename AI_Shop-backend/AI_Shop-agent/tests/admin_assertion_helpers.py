from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from app.auth.admin_assertion import canonical_admin_assertion
from app.config.settings import get_settings


def signed_admin_request(
    path: str,
    body: dict | None = None,
    *,
    permissions: tuple[str, ...] = (
        "ai:config",
        "ai:evaluate",
        "ai:pilot",
        "support:read",
        "support:write",
        "analytics:read",
        "analytics:export",
        "audit:read",
    ),
    admin_id: str = "admin-session",
    account: str = "admin",
    secret: str | None = None,
    key_id: str | None = None,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False).encode()
    settings = get_settings()
    actual_secret = secret or settings.admin_assertion_current_secret
    actual_key_id = key_id or settings.admin_assertion_current_key_id
    timestamp_raw = str(timestamp if timestamp is not None else int(time.time()))
    actual_nonce = nonce or uuid.uuid4().hex
    roles = "SUPER_ADMIN"
    permission_csv = ",".join(sorted(permissions))
    body_hash = hashlib.sha256(payload).hexdigest()
    canonical = canonical_admin_assertion(
        "POST",
        path,
        body_hash,
        admin_id,
        account,
        roles,
        permission_csv,
        timestamp_raw,
        actual_nonce,
    )
    signature = hmac.new(
        actual_secret.encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()
    return payload, {
        "Content-Type": "application/json",
        "X-Internal-Token": settings.internal_token,
        "X-Admin-Id": admin_id,
        "X-Admin-Account": account,
        "X-Admin-Roles": roles,
        "X-Admin-Permissions": permission_csv,
        "X-Admin-Timestamp": timestamp_raw,
        "X-Admin-Nonce": actual_nonce,
        "X-Admin-Body-SHA256": body_hash,
        "X-Admin-Key-Id": actual_key_id,
        "X-Admin-Signature": signature,
    }
