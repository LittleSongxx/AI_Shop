from app.services.redis_service import redis_service

_PREFIX = "mall:agent:ratelimit:"

class RateLimitService:

    async def allow(self, user_id: str, action: str, window_seconds: int, max_count: int) -> bool:

        if not user_id:
            return True
        key = f"{_PREFIX}{action}:{user_id}"

        count = await redis_service.client.incr(key)
        if count == 1:

            await redis_service.client.expire(key, window_seconds)
        return count <= max_count

rate_limit_service = RateLimitService()
