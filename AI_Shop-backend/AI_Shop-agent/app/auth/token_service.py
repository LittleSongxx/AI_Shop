import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Cookie, Header, Request

from app.auth.security import Principal, TokenSource
from app.constants import (
    REDIS_TOKEN_ADMIN,
    REDIS_TOKEN_WEB,
    TOKEN_COOKIE_NAME,
    TOKEN_HEADER_NAME,
)
from app.exceptions import BusinessException
from app.services.redis_service import redis_service


@dataclass
class TokenUserInfo:

    user_id: str
    email: str | None = None
    nick_name: str | None = None
    avatar: str | None = None
    token: str | None = None
    # ``header``/``cookie`` is filled by the boundary that resolved the token;
    # old callers constructing this DTO keep the neutral ``session`` default.
    auth_source: TokenSource = "session"

    @property
    def principal(self) -> Principal:
        return Principal(self.user_id, "USER", self.auth_source)


def _payload_not_expired(data: dict[str, Any]) -> bool:
    """Honor an embedded expiry when a deployment includes one.

    The normal Java session contract is Redis TTL; this check only adds a
    defense for serialized sessions that carry ``exp``/``expiresAt`` metadata.
    """

    now = time.time()
    for key in ("exp", "expiresAt", "expireAt", "expiration"):
        value = data.get(key)
        if value in (None, ""):
            continue
        try:
            if isinstance(value, str):
                text = value.strip()
                if text.isdigit():
                    value = int(text)
                else:
                    value = datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            numeric = float(value)
            # Java timestamps are commonly milliseconds; JWT ``exp`` is seconds.
            if numeric > 10_000_000_000:
                numeric /= 1000
            return numeric > now
        except (TypeError, ValueError, OverflowError):
            return False
    return True


async def _redis_session_is_live(key: str) -> bool:
    """Check the authoritative Redis TTL when the client exposes it.

    Tiny test doubles often implement only ``get``; in that case the value
    lookup remains the compatibility source of truth. A real redis client
    always has ``ttl`` and therefore rejects expired/non-expiring sessions.
    """

    ttl_reader = getattr(redis_service.client, "ttl", None)
    if not callable(ttl_reader):
        return True
    try:
        ttl = await ttl_reader(key)
    except Exception:
        # Redis GET already succeeded. Keep the existing session contract when
        # a best-effort TTL probe is unavailable, while logging no token data.
        return True
    if isinstance(ttl, (int, float)):
        return ttl > 0
    return True

def _parse_spring_json(raw: str | bytes) -> dict:

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    text = raw.strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start >= 0:
            text = text[start:]
    return json.loads(text)

async def get_user_by_token(token: str) -> TokenUserInfo | None:

    if not token:
        return None

    key = f"{REDIS_TOKEN_WEB}{token}"
    raw = await redis_service.client.get(key)
    if not raw:
        return None
    if not await _redis_session_is_live(key):
        return None
    try:
        data = _parse_spring_json(raw)
    except (json.JSONDecodeError, TypeError):

        m = re.search(r'"userId"\s*:\s*"([^"]+)"', str(raw))
        if not m:
            return None
        return TokenUserInfo(user_id=m.group(1), token=token)

    if not isinstance(data, dict):
        return None
    if not _payload_not_expired(data):
        return None

    user_id = str(data.get("userId") or data.get("user_id") or "").strip()
    if not user_id:
        return None

    return TokenUserInfo(
        user_id=user_id,
        email=data.get("email"),
        nick_name=data.get("nickName") or data.get("nick_name"),
        avatar=data.get("avatar"),
        token=data.get("token") or token,
    )


async def get_admin_by_token(token: str) -> str | None:
    if not token:
        return None
    key = f"{REDIS_TOKEN_ADMIN}{token}"
    raw = await redis_service.client.get(key)
    if raw is None:
        return None
    if not await _redis_session_is_live(key):
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str):
            return parsed.strip() or None
        if isinstance(parsed, dict):
            if not _payload_not_expired(parsed):
                return None
            account = parsed.get("account") or parsed.get("adminId")
            return str(account).strip() if account else None
    except json.JSONDecodeError:
        pass
    return value.strip('"') or None

def resolve_token(
    request: Request,
    token_header: str | None = None,
    token_cookie: str | None = None,
) -> str | None:

    if token_header:
        return token_header.strip()
    if token_cookie:
        return token_cookie.strip()

    return request.headers.get(TOKEN_HEADER_NAME) or request.cookies.get(TOKEN_COOKIE_NAME)


def resolve_token_with_source(
    request: Request,
    token_header: str | None = None,
    token_cookie: str | None = None,
) -> tuple[str | None, TokenSource]:
    """Resolve the token and retain how it crossed the trust boundary."""

    if token_header and token_header.strip():
        return token_header.strip(), "header"
    if token_cookie and token_cookie.strip():
        return token_cookie.strip(), "cookie"
    header_value = request.headers.get(TOKEN_HEADER_NAME)
    if header_value and header_value.strip():
        return header_value.strip(), "header"
    cookie_value = request.cookies.get(TOKEN_COOKIE_NAME)
    if cookie_value and cookie_value.strip():
        return cookie_value.strip(), "cookie"
    return None, "session"

async def require_login(
    request: Request,
    token: str | None = Header(None, alias="token"),
    cookie_token: str | None = Cookie(None, alias="token"),
) -> TokenUserInfo:

    resolved, source = resolve_token_with_source(request, token, cookie_token)
    if not resolved:
        raise BusinessException(901, "登录超时，请重新登录")
    if len(resolved) > 512:
        raise BusinessException(901, "登录凭证无效")
    user = await get_user_by_token(resolved)
    if not user or not user.user_id:
        raise BusinessException(901, "登录超时，请重新登录")
    user.auth_source = source
    return user
