"""按用户 + 动作维度的固定窗口限流。

这是 Agent 侧唯一生效的接口限流层：进程内的计数器在多 worker 下会各算一份，
所以配额必须放在 Redis 里，由 ``allow_fixed_window`` 一次 EVAL 完成计数与过期。
"""

from app.services.redis_service import redis_service

_PREFIX = "mall:agent:ratelimit:"

class RateLimitService:

    async def allow(self, user_id: str, action: str, window_seconds: int, max_count: int) -> bool:

        if not user_id:
            return True
        key = f"{_PREFIX}{action}:{user_id}"
        return await redis_service.allow_fixed_window(key, window_seconds, max_count)

rate_limit_service = RateLimitService()
