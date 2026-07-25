import json
import random
import time

import redis.asyncio as aioredis

from app.config.settings import get_settings
from app.constants import (
    CANCEL_FLAG_TTL,
    CONSULT_ACTIVE_TTL,
    CONSULT_PRODUCT_TTL,
    PENDING_ACTION_TTL,
    PENDING_MSG_TTL,
    REDIS_AGENT_CONSULT_ACTIVE,
    REDIS_AGENT_CONSULT_PRODUCT,
    REDIS_AGENT_HISTORY_CONDENSED,
    REDIS_AGENT_PENDING_ACTION,
    REDIS_AGENT_PENDING_MSG,
    REDIS_AGENT_SHOPPING_PROFILE,
    REDIS_AGENT_USER_LOCK,
    REDIS_AGENT_WORKER_HEARTBEAT,
    REDIS_CANCEL_AGENT,
    REDIS_HEARTBEAT_TTL,
    REDIS_PROMPT,
    REDIS_SENSITIVE_WORD_PAYLOAD,
    REDIS_WS_USER_HEARTBEAT,
    SHOPPING_PROFILE_TTL,
    WS_MESSAGE_TOPIC_AGENT,
)


class RedisService:

    def __init__(self):

        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:

        if self._client is not None:
            return

        settings = get_settings()

        self._client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            protocol=2,
            max_connections=20,
        )

    async def ensure_connected(self) -> None:
        """Idempotent connect for Agent lifespan and MCP server process."""
        await self.connect()

    async def close(self) -> None:

        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:

        if not self._client:
            raise RuntimeError("Redis not connected")
        return self._client

    async def set_cancel_flag(self, user_id: str, message_id: int) -> None:

        key = f"{REDIS_CANCEL_AGENT}{user_id}:msg:{message_id}"

        await self.client.setex(key, CANCEL_FLAG_TTL, "1")

    async def is_cancelled(self, user_id: str, message_id: int) -> bool:

        key = f"{REDIS_CANCEL_AGENT}{user_id}:msg:{message_id}"
        return await self.client.exists(key) > 0

    async def save_consult_product(self, user_id: str, product: dict) -> None:

        key = f"{REDIS_AGENT_CONSULT_PRODUCT}{user_id}"

        await self.client.setex(key, CONSULT_PRODUCT_TTL, json.dumps(product, ensure_ascii=False))

    async def get_consult_product(self, user_id: str) -> dict | None:

        key = f"{REDIS_AGENT_CONSULT_PRODUCT}{user_id}"
        data = await self.client.get(key)
        return json.loads(data) if data else None

    async def set_consult_active(self, user_id: str) -> None:

        await self.client.setex(f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}", CONSULT_ACTIVE_TTL, "1")

    async def is_consult_active(self, user_id: str) -> bool:

        return await self.client.exists(f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}") > 0

    async def clear_consult(self, user_id: str) -> None:

        await self.client.delete(
            f"{REDIS_AGENT_CONSULT_PRODUCT}{user_id}",
            f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}",
        )

    async def save_shopping_profile(self, user_id: str, profile: dict) -> None:
        await self.set_json(
            f"{REDIS_AGENT_SHOPPING_PROFILE}{user_id}",
            profile,
            SHOPPING_PROFILE_TTL,
            jitter_seconds=60 * 60,
        )

    async def get_shopping_profile(self, user_id: str) -> dict | None:
        value = await self.get_json(f"{REDIS_AGENT_SHOPPING_PROFILE}{user_id}")
        return value if isinstance(value, dict) else None

    async def pause_consult(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}")

    async def bind_message_id(self, user_id: str, message_id: int) -> None:

        await self.client.setex(f"{REDIS_AGENT_PENDING_MSG}{user_id}", PENDING_MSG_TTL, str(message_id))

    async def get_bound_message_id(self, user_id: str) -> int | None:

        val = await self.client.get(f"{REDIS_AGENT_PENDING_MSG}{user_id}")
        return int(val) if val else None

    async def clear_bound_message_id(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_PENDING_MSG}{user_id}")

    async def acquire_agent_user_lock(
        self,
        user_id: str,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        ok = await self.client.set(
            f"{REDIS_AGENT_USER_LOCK}{user_id}",
            owner,
            nx=True,
            ex=max(1, int(ttl_seconds)),
        )
        return bool(ok)

    async def release_agent_user_lock(self, user_id: str, owner: str) -> None:
        await self.client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            f"{REDIS_AGENT_USER_LOCK}{user_id}",
            owner,
        )

    async def set_worker_heartbeat(self, worker_id: str, ttl_seconds: int) -> None:
        await self.client.setex(
            REDIS_AGENT_WORKER_HEARTBEAT,
            max(5, int(ttl_seconds)),
            worker_id,
        )

    async def worker_is_alive(self) -> bool:
        return await self.client.exists(REDIS_AGENT_WORKER_HEARTBEAT) > 0

    async def clear_worker_heartbeat(self, worker_id: str) -> None:
        await self.client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            REDIS_AGENT_WORKER_HEARTBEAT,
            worker_id,
        )

    async def save_history_condensed(self, user_id: str, message_id: int, text: str) -> None:

        key = f"{REDIS_AGENT_HISTORY_CONDENSED}{user_id}:msg:{message_id}"
        await self.client.setex(key, CONSULT_PRODUCT_TTL, text)

    async def get_history_condensed(self, user_id: str, message_id: int) -> str | None:

        return await self.client.get(f"{REDIS_AGENT_HISTORY_CONDENSED}{user_id}:msg:{message_id}")

    async def get_prompt(self, prompt_key: str) -> str | None:

        return await self.client.get(f"{REDIS_PROMPT}{prompt_key}")

    async def save_pending_action(self, token: str, action: dict) -> None:

        await self.client.setex(
            f"{REDIS_AGENT_PENDING_ACTION}{token}",
            PENDING_ACTION_TTL,
            json.dumps(action, ensure_ascii=False),
        )

    async def try_lock_pending_action(
        self, token: str, owner: str, ttl_seconds: int = 120
    ) -> bool:
        """Best-effort contention lock; ownership is checked on unlock."""
        key = f"{REDIS_AGENT_PENDING_ACTION}lock:{token}"
        ok = await self.client.set(key, owner, nx=True, ex=max(1, int(ttl_seconds)))
        return bool(ok)

    async def unlock_pending_action(self, token: str, owner: str) -> None:
        await self.client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            f"{REDIS_AGENT_PENDING_ACTION}lock:{token}",
            owner,
        )

    async def get_sensitive_words(self) -> list[dict]:
        raw = await self.client.get(REDIS_SENSITIVE_WORD_PAYLOAD)
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    async def get_json(self, key: str):
        raw = await self.client.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set_json(
        self,
        key: str,
        value,
        ttl_seconds: int,
        jitter_seconds: int = 0,
    ) -> None:
        ttl = max(1, int(ttl_seconds))
        if jitter_seconds > 0:
            ttl += random.randint(0, int(jitter_seconds))
        await self.client.setex(key, ttl, json.dumps(value, ensure_ascii=False))

    async def publish_ws(self, payload: dict) -> None:

        await self.client.publish(
            WS_MESSAGE_TOPIC_AGENT, json.dumps(payload, ensure_ascii=False)
        )

    async def save_user_heartbeat(self, user_id: str) -> None:

        key = f"{REDIS_WS_USER_HEARTBEAT}{user_id}"
        await self.client.setex(key, REDIS_HEARTBEAT_TTL, str(int(time.time() * 1000)))

redis_service = RedisService()
