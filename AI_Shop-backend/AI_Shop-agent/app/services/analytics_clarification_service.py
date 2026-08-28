from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Iterable

from app.config.settings import get_settings
from app.services.analytics_result_service import AnalyticsResultError, owner_scope_hash
from app.services.redis_service import redis_service

_KEY_PREFIX = "aishop:analytics:clarification:v1:"
_CONSUME_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {'MISSING'} end
local decoded, payload = pcall(cjson.decode, raw)
if not decoded or type(payload) ~= 'table' then return {'CORRUPT'} end
if tostring(payload.ownerScopeHash or '') ~= ARGV[1] then return {'OWNER'} end
if tonumber(payload.expiresAt or 0) < tonumber(ARGV[3]) then
    redis.call('DEL', KEYS[1])
    return {'EXPIRED'}
end
local options = payload.options or {}
local found = false
for _, option in ipairs(options) do
    if tostring(option.choiceId or '') == ARGV[2] then
        found = true
        break
    end
end
if not found then return {'CHOICE'} end
redis.call('DEL', KEYS[1])
return {'OK', raw}
"""


class AnalyticsClarificationService:
    async def issue(
        self,
        *,
        question: str,
        clarification_question: str,
        options: list[dict[str, str]],
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
        run_id: str,
    ) -> dict[str, Any]:
        if len(options) < 2:
            raise ValueError("clarification requires at least two structured options")
        ttl = int(get_settings().analytics_cursor_ttl_seconds)
        expires_at = int(time.time()) + ttl
        token = f"acl_{secrets.token_urlsafe(32)}"
        payload = {
            "schemaVersion": "aishop-analytics-clarification/v1",
            "token": token,
            "ownerScopeHash": owner_scope_hash(admin_id, permissions, tenant_id),
            "question": question,
            "clarificationQuestion": clarification_question,
            "options": options,
            "runId": run_id,
            "expiresAt": expires_at,
        }
        try:
            await redis_service.set_json(f"{_KEY_PREFIX}{token}", payload, ttl)
        except Exception as exc:
            raise AnalyticsResultError(
                "CLARIFICATION_STATE_UNAVAILABLE", 503, "澄清状态服务暂不可用"
            ) from exc
        return {
            "clarificationToken": token,
            "clarificationTokenTtlSeconds": ttl,
            "clarificationTokenExpiresAt": datetime.fromtimestamp(
                expires_at, timezone.utc
            ).isoformat(),
            "clarificationOptions": options,
        }

    async def consume(
        self,
        token: str,
        choice_id: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
    ) -> dict[str, Any]:
        normalized_token = str(token or "").strip()
        if not normalized_token.startswith("acl_"):
            raise AnalyticsResultError("CLARIFICATION_TOKEN_INVALID", 400, "澄清 token 无效")
        expected_scope = owner_scope_hash(admin_id, permissions, tenant_id)
        normalized_choice = str(choice_id or "").strip()
        try:
            response = await redis_service.client.eval(
                _CONSUME_LUA,
                1,
                f"{_KEY_PREFIX}{normalized_token}",
                expected_scope,
                normalized_choice,
                int(time.time()),
            )
        except Exception as exc:
            raise AnalyticsResultError(
                "CLARIFICATION_STATE_UNAVAILABLE", 503, "澄清状态服务暂不可用"
            ) from exc
        values = [
            item.decode("utf-8") if isinstance(item, bytes) else str(item)
            for item in (response or [])
        ]
        status = values[0] if values else "CORRUPT"
        if status in {"MISSING", "EXPIRED"}:
            raise AnalyticsResultError("CLARIFICATION_TOKEN_EXPIRED", 410, "澄清 token 已过期")
        if status == "OWNER":
            raise AnalyticsResultError(
                "CLARIFICATION_OWNER_MISMATCH", 403, "澄清 token 不属于当前管理员范围"
            )
        if status == "CHOICE":
            raise AnalyticsResultError("CLARIFICATION_CHOICE_INVALID", 400, "澄清选项无效")
        if status != "OK" or len(values) != 2:
            raise AnalyticsResultError("CLARIFICATION_STATE_UNAVAILABLE", 503, "澄清状态数据损坏")
        try:
            payload = json.loads(values[1])
        except (json.JSONDecodeError, TypeError) as exc:
            raise AnalyticsResultError(
                "CLARIFICATION_STATE_UNAVAILABLE", 503, "澄清状态数据损坏"
            ) from exc
        selected = next(
            (
                dict(option)
                for option in payload.get("options") or []
                if str(option.get("choiceId") or "") == normalized_choice
            ),
            None,
        )
        if selected is None:
            raise AnalyticsResultError("CLARIFICATION_CHOICE_INVALID", 400, "澄清选项无效")
        suffix = str(selected.get("answerSuffix") or "").strip()
        question = str(payload.get("question") or "").strip()
        return {
            "resolvedQuestion": f"{question}（已确认：{suffix}）",
            "choice": selected,
            "parentRunId": payload.get("runId"),
        }


analytics_clarification_service = AnalyticsClarificationService()
