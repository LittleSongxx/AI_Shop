import json

import time

from typing import Any

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
    REDIS_AGENT_PENDING_ACTION,
    REDIS_AGENT_PENDING_MSG,
    REDIS_AGENT_HISTORY_CONDENSED,
    REDIS_CANCEL_AGENT,
    REDIS_HEARTBEAT_TTL,
    REDIS_PROMPT,
    REDIS_SENSITIVE_WORD_PAYLOAD,
    REDIS_WS_USER_HEARTBEAT,
    WS_MESSAGE_TOPIC_AGENT,
)

class RedisService:

    def __init__(self):

        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:

        settings = get_settings()

        self._client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            protocol=2,
            max_connections=20,
        )

    async def close(self) -> None:

        if self._client:
            await self._client.aclose()

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

    async def pause_consult(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}")

    async def bind_message_id(self, user_id: str, message_id: int) -> None:

        await self.client.setex(f"{REDIS_AGENT_PENDING_MSG}{user_id}", PENDING_MSG_TTL, str(message_id))

    async def get_bound_message_id(self, user_id: str) -> int | None:

        val = await self.client.get(f"{REDIS_AGENT_PENDING_MSG}{user_id}")
        return int(val) if val else None

    async def clear_bound_message_id(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_PENDING_MSG}{user_id}")

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

    async def get_pending_action(self, token: str) -> dict | None:

        data = await self.client.get(f"{REDIS_AGENT_PENDING_ACTION}{token}")
        return json.loads(data) if data else None

    async def delete_pending_action(self, token: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_PENDING_ACTION}{token}")

    async def get_sensitive_words(self) -> list[dict]:

        raw = await self.client.get(REDIS_SENSITIVE_WORD_PAYLOAD)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    async def publish_ws(self, payload: dict) -> None:

        await self.client.publish(
            WS_MESSAGE_TOPIC_AGENT, json.dumps(payload, ensure_ascii=False)
        )

    async def save_user_heartbeat(self, user_id: str) -> None:

        key = f"{REDIS_WS_USER_HEARTBEAT}{user_id}"
        await self.client.setex(key, REDIS_HEARTBEAT_TTL, str(int(time.time() * 1000)))

redis_service = RedisService()
