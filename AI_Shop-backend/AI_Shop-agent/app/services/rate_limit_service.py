"""按用户 + 动作维度的固定窗口限流，以及 Token 消耗预算管理。

这是 Agent 侧唯一生效的接口限流层：进程内的计数器在多 worker 下会各算一份，
所以配额必须放在 Redis 里，由 ``allow_fixed_window`` 一次 EVAL 完成计数与过期。

Token 预算：
- ``check_session_token_budget``：单会话 token 累计上限（防 Denial-of-Wallet）。
- ``check_daily_token_quota``：每用户每自然日 token 配额（防持续轰炸）。
- ``record_token_usage``：Worker 端每次 LLM 调用后原子累加，兼容多进程。

两个配额均为软限制：超限后调用方降级到 FAQ 快速路径，不强制中断当前流式输出。
"""

from app.services.redis_service import redis_service

_PREFIX = "mall:agent:ratelimit:"
_SESSION_TOKEN_PREFIX = "mall:agent:token:session:"
_DAILY_TOKEN_PREFIX = "mall:agent:token:daily:"


class RateLimitService:

    async def allow(self, user_id: str, action: str, window_seconds: int, max_count: int) -> bool:

        if not user_id:
            return True
        key = f"{_PREFIX}{action}:{user_id}"
        return await redis_service.allow_fixed_window(key, window_seconds, max_count)

    # ── Token 预算 ────────────────────────────────────────────────────────────

    async def record_token_usage(
        self,
        user_id: str,
        tokens: int,
        *,
        session_ttl_seconds: int = 86400 * 7,
    ) -> None:
        """原子地将本次 LLM 调用消耗的 token 累加到会话计数器和每日计数器。

        ``session_ttl_seconds``：会话 key 的最大存活时间，默认 7 天（超出后自然归零）。
        每日 key 的 TTL 固定为当日剩余秒数，保证 00:00 UTC 自动重置。
        """
        if not user_id or tokens <= 0:
            return
        session_key = f"{_SESSION_TOKEN_PREFIX}{user_id}"
        daily_key = _daily_token_key(user_id)
        daily_ttl = _seconds_until_utc_midnight()
        await redis_service.client.eval(
            """
            redis.call('INCRBY', KEYS[1], ARGV[1])
            if redis.call('TTL', KEYS[1]) < 0 then
                redis.call('EXPIRE', KEYS[1], ARGV[2])
            end
            redis.call('INCRBY', KEYS[2], ARGV[1])
            if redis.call('TTL', KEYS[2]) < 0 then
                redis.call('EXPIRE', KEYS[2], ARGV[3])
            end
            return 1
            """,
            2,
            session_key,
            daily_key,
            tokens,
            session_ttl_seconds,
            daily_ttl,
        )

    async def get_session_token_usage(self, user_id: str) -> int:
        """返回当前会话累计消耗的 token 数，key 不存在返回 0。"""
        if not user_id:
            return 0
        raw = await redis_service.client.get(f"{_SESSION_TOKEN_PREFIX}{user_id}")
        return int(raw or 0)

    async def get_daily_token_usage(self, user_id: str) -> int:
        """返回今日（UTC）累计消耗的 token 数，key 不存在返回 0。"""
        if not user_id:
            return 0
        raw = await redis_service.client.get(_daily_token_key(user_id))
        return int(raw or 0)

    async def check_session_token_budget(self, user_id: str, budget: int) -> bool:
        """检查会话 token 预算是否还有余量。budget<=0 表示不限制，直接返回 True。"""
        if budget <= 0:
            return True
        usage = await self.get_session_token_usage(user_id)
        return usage < budget

    async def check_daily_token_quota(self, user_id: str, quota: int) -> bool:
        """检查每日 token 配额是否还有余量。quota<=0 表示不限制，直接返回 True。"""
        if quota <= 0:
            return True
        usage = await self.get_daily_token_usage(user_id)
        return usage < quota

    # ── 重复意图计数 ──────────────────────────────────────────────────────────

    async def get_intent_repeat_count(
        self,
        user_id: str,
        intent: str,
        window_seconds: int = 600,
    ) -> int:
        """返回用户在最近 ``window_seconds`` 秒内触发同一意图的次数。

        此计数在 ``record_intent`` 被调用时更新，用于检测反复提问同一问题。
        """
        if not user_id or not intent:
            return 0
        raw = await redis_service.client.get(_intent_repeat_key(user_id, intent))
        return int(raw or 0)

    async def record_intent(
        self,
        user_id: str,
        intent: str,
        window_seconds: int = 600,
    ) -> int:
        """记录一次意图触发，返回窗口内的累计次数（含本次）。"""
        if not user_id or not intent:
            return 0
        key = _intent_repeat_key(user_id, intent)
        await redis_service.allow_fixed_window.__func__(  # type: ignore[attr-defined]
            redis_service, key, window_seconds, 99_999
        )
        # allow_fixed_window 返回 bool；我们直接读计数器更直接：
        raw = await redis_service.client.get(key)
        return int(raw or 1)


def _daily_token_key(user_id: str) -> str:
    """今日 UTC 日期作为 key 的一部分，保证自然日边界自动区分。"""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{_DAILY_TOKEN_PREFIX}{today}:{user_id}"


def _intent_repeat_key(user_id: str, intent: str) -> str:
    return f"mall:agent:intent:repeat:{intent}:{user_id}"


def _seconds_until_utc_midnight() -> int:
    """距离今日 23:59:59 UTC 的剩余秒数，至少 60 秒。"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=23, minute=59, second=59, microsecond=0)
    remaining = int((midnight - now).total_seconds())
    return max(remaining, 60)


rate_limit_service = RateLimitService()
