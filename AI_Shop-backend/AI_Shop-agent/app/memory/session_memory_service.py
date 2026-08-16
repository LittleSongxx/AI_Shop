from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from app.config.settings import get_settings
from app.constants import REDIS_AGENT_SESSION, REDIS_AGENT_SESSION_COMPRESS_LOCK
from app.db.pool import acquire
from app.memory.models import SessionMemory, empty_state, empty_summary

logger = structlog.get_logger()

_TABLE_ENSURED = False

class SessionMemoryService:

    async def ensure_table(self) -> None:

        global _TABLE_ENSURED
        if _TABLE_ENSURED:
            return

        async with acquire() as cur:
            await cur.execute("SELECT 1 FROM agent_session_memory LIMIT 1")
        _TABLE_ENSURED = True

    async def load(self, user_id: str, redis_client) -> SessionMemory:

        await self.ensure_table()
        key = f"{REDIS_AGENT_SESSION}{user_id}"
        cached = await redis_client.get(key)
        if cached:
            try:
                data = json.loads(cached)

                return SessionMemory.from_storage(user_id, data.get("summary"), data.get("state"))
            except json.JSONDecodeError:
                logger.warning("session_memory_redis_corrupt", user_id=user_id)

        row = await self._load_from_db(user_id)
        if row:
            mem = SessionMemory.from_storage(user_id, row.get("summary_json"), row.get("state_json"))
            await self._save_redis(user_id, mem, redis_client)
            return mem

        return SessionMemory(user_id=user_id)

    async def save(self, memory: SessionMemory, redis_client) -> None:

        await self.ensure_table()
        await self._save_redis(memory.user_id, memory, redis_client)
        await self._save_db(memory)

    async def try_acquire_compress_lock(self, user_id: str, redis_client) -> bool:

        key = f"{REDIS_AGENT_SESSION_COMPRESS_LOCK}{user_id}"
        settings = get_settings()
        return bool(await redis_client.set(key, "1", nx=True, ex=settings.session_compress_lock_ttl))

    async def release_compress_lock(self, user_id: str, redis_client) -> None:

        await redis_client.delete(f"{REDIS_AGENT_SESSION_COMPRESS_LOCK}{user_id}")

    async def _save_redis(self, user_id: str, memory: SessionMemory, redis_client) -> None:

        settings = get_settings()
        payload = json.dumps(
            {"summary": memory.summary, "state": memory.state},
            ensure_ascii=False,
        )
        await redis_client.setex(
            f"{REDIS_AGENT_SESSION}{user_id}",
            settings.session_redis_ttl,
            payload,
        )

    async def _save_db(self, memory: SessionMemory) -> None:

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        turn_count = int(memory.state.get("turnCount") or 0)
        async with acquire() as cur:
            await cur.execute(
                """INSERT INTO agent_session_memory (user_id, summary_json, state_json, turn_count, updated_at)
                   VALUES (%s, %s, %s, %s, %s) AS incoming
                   ON DUPLICATE KEY UPDATE
                     summary_json=incoming.summary_json,
                     state_json=incoming.state_json,
                     turn_count=incoming.turn_count,
                     updated_at=incoming.updated_at""",
                (
                    memory.user_id,
                    json.dumps(memory.summary, ensure_ascii=False),
                    json.dumps(memory.state, ensure_ascii=False),
                    turn_count,
                    now,
                ),
            )

    async def _load_from_db(self, user_id: str) -> dict[str, Any] | None:

        async with acquire() as cur:
            await cur.execute(
                "SELECT summary_json, state_json FROM agent_session_memory WHERE user_id=%s",
                (user_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        summary = row.get("summary_json")
        state = row.get("state_json")

        if isinstance(summary, str):
            summary = json.loads(summary)
        if isinstance(state, str):
            state = json.loads(state)
        return {"summary_json": summary or empty_summary(), "state_json": state or empty_state()}

session_memory_service = SessionMemoryService()
