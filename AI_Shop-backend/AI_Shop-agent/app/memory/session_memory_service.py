from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from redis.exceptions import WatchError

from app.config.settings import get_settings
from app.constants import REDIS_AGENT_SESSION, REDIS_AGENT_SESSION_COMPRESS_LOCK
from app.db.pool import acquire
from app.memory.models import SessionMemory, empty_state, empty_summary

logger = structlog.get_logger()

_TABLE_ENSURED = False
_MEMORY_REVISION_FIELD = "memoryRevision"


def _coerce_revision(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _revision_from_state(state: Any) -> int:
    return _coerce_revision(state.get(_MEMORY_REVISION_FIELD) if isinstance(state, dict) else 0)


def _decode_cached_memory(raw: Any) -> tuple[dict, dict, int] | None:
    """Decode both the current envelope and pre-CAS Redis values."""

    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    state = data.get("state")
    if not isinstance(summary, dict):
        summary = empty_summary()
    if not isinstance(state, dict):
        state = empty_state()
    revision = _coerce_revision(data.get("revision"))
    if revision == 0:
        revision = _revision_from_state(state)
    return summary, state, revision

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
            decoded = _decode_cached_memory(cached)
            if decoded is not None:
                summary, state, revision = decoded
                return SessionMemory.from_storage(user_id, summary, state, revision)
            else:
                logger.warning("session_memory_redis_corrupt", user_id=user_id)

        row = await self._load_from_db(user_id)
        if row:
            mem = SessionMemory.from_storage(
                user_id,
                row.get("summary_json"),
                row.get("state_json"),
                row.get("revision"),
            )
            # A cache miss is not a write conflict: seed the cache with the
            # durable DB value while retaining its revision token.
            try:
                await self._save_redis(
                    user_id,
                    mem,
                    redis_client,
                    expected_revision=mem.revision,
                    next_revision=mem.revision,
                )
            except Exception as exc:
                logger.warning(
                    "session_memory_redis_seed_failed",
                    user_id=user_id,
                    error=type(exc).__name__,
                )
            return mem

        return SessionMemory(user_id=user_id)

    async def save(self, memory: SessionMemory, redis_client) -> bool:
        """Persist a memory snapshot with an optimistic revision guard.

        ``agent_session_memory`` predates a dedicated revision column.  The
        revision therefore lives in the JSON state and is checked atomically
        by the upsert.  A stale writer is rejected (and logged) instead of
        overwriting a newer turn; existing callers intentionally do not need
        to catch an exception, so the method returns ``False`` on conflict.
        """

        await self.ensure_table()
        expected_revision = _coerce_revision(memory.revision)
        next_revision = expected_revision + 1
        state = dict(memory.state)
        state[_MEMORY_REVISION_FIELD] = next_revision
        saved = await self._save_db(
            memory,
            state=state,
            expected_revision=expected_revision,
            next_revision=next_revision,
        )
        # Treat ``None`` as success for compatibility with older test doubles
        # and out-of-tree adapters; only an explicit ``False`` is a CAS miss.
        if saved is False:
            logger.warning(
                "session_memory_revision_conflict",
                user_id=memory.user_id,
                expected_revision=expected_revision,
            )
            try:
                await self._discard_stale_cache(
                    memory.user_id, expected_revision, redis_client
                )
            except Exception as exc:
                logger.debug(
                    "session_memory_stale_cache_discard_failed",
                    user_id=memory.user_id,
                    error=type(exc).__name__,
                )
            return False

        memory.state[_MEMORY_REVISION_FIELD] = next_revision
        memory.revision = next_revision
        # DB is authoritative.  CAS the cache so a delayed writer cannot put
        # an older snapshot back over a newer one.  Cache failure is non-fatal;
        # the next load will fall back to the durable row.
        try:
            cache_saved = await self._save_redis(
                memory.user_id,
                memory,
                redis_client,
                expected_revision=expected_revision,
                next_revision=next_revision,
                state=state,
            )
        except Exception as exc:
            cache_saved = False
            logger.warning(
                "session_memory_redis_write_failed",
                user_id=memory.user_id,
                error=type(exc).__name__,
            )
        if not cache_saved:
            logger.info(
                "session_memory_redis_cas_skipped",
                user_id=memory.user_id,
                revision=next_revision,
            )
        return True

    async def try_acquire_compress_lock(self, user_id: str, redis_client) -> bool:

        key = f"{REDIS_AGENT_SESSION_COMPRESS_LOCK}{user_id}"
        settings = get_settings()
        return bool(await redis_client.set(key, "1", nx=True, ex=settings.session_compress_lock_ttl))

    async def release_compress_lock(self, user_id: str, redis_client) -> None:

        await redis_client.delete(f"{REDIS_AGENT_SESSION_COMPRESS_LOCK}{user_id}")

    async def _write_redis(
        self,
        user_id: str,
        memory: SessionMemory,
        redis_client,
        *,
        state: dict[str, Any] | None = None,
    ) -> None:
        """Write a cache value without a compare-and-set (used on cache miss)."""

        settings = get_settings()
        payload = json.dumps(
            {
                "revision": _coerce_revision(memory.revision),
                "summary": memory.summary,
                "state": state if state is not None else memory.state,
            },
            ensure_ascii=False,
        )
        await redis_client.setex(
            f"{REDIS_AGENT_SESSION}{user_id}",
            settings.session_redis_ttl,
            payload,
        )

    async def _save_redis(
        self,
        user_id: str,
        memory: SessionMemory,
        redis_client,
        *,
        expected_revision: int | None = None,
        next_revision: int | None = None,
        state: dict[str, Any] | None = None,
    ) -> bool:
        """CAS the Redis snapshot, falling back to a plain seed write if needed."""

        if expected_revision is None:
            await self._write_redis(user_id, memory, redis_client, state=state)
            return True

        settings = get_settings()
        expected = _coerce_revision(expected_revision)
        next_value = _coerce_revision(
            next_revision if next_revision is not None else memory.revision
        )
        key = f"{REDIS_AGENT_SESSION}{user_id}"
        payload = json.dumps(
            {
                "revision": next_value,
                "summary": memory.summary,
                "state": state if state is not None else memory.state,
            },
            ensure_ascii=False,
        )
        pipe = redis_client.pipeline(transaction=True)
        try:
            await pipe.watch(key)
            raw = await pipe.get(key)
            current = _decode_cached_memory(raw)
            # An expired or older cache is safe to repopulate after the DB
            # CAS.  A cache newer than the revision we loaded belongs to a
            # later writer and must not be clobbered.
            if current is not None and current[2] > expected:
                return False
            pipe.multi()
            pipe.setex(key, settings.session_redis_ttl, payload)
            result = await pipe.execute()
            return bool(result)
        except WatchError:
            return False
        finally:
            try:
                await pipe.reset()
            except Exception:
                pass

    async def _discard_stale_cache(
        self, user_id: str, expected_revision: int, redis_client
    ) -> None:
        """Drop only the cache snapshot that proved stale for this writer."""

        key = f"{REDIS_AGENT_SESSION}{user_id}"
        pipe = redis_client.pipeline(transaction=True)
        try:
            await pipe.watch(key)
            current = _decode_cached_memory(await pipe.get(key))
            if current is None or current[2] > _coerce_revision(expected_revision):
                return
            pipe.multi()
            pipe.delete(key)
            await pipe.execute()
        except WatchError:
            return
        except Exception as exc:
            logger.debug(
                "session_memory_stale_cache_discard_failed",
                user_id=user_id,
                error=type(exc).__name__,
            )
        finally:
            try:
                await pipe.reset()
            except Exception:
                pass

    async def _save_db(
        self,
        memory: SessionMemory,
        *,
        state: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        next_revision: int | None = None,
    ) -> bool:

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        expected = _coerce_revision(
            memory.revision if expected_revision is None else expected_revision
        )
        next_value = _coerce_revision(
            expected + 1 if next_revision is None else next_revision
        )
        persisted_state = dict(state if state is not None else memory.state)
        persisted_state[_MEMORY_REVISION_FIELD] = next_value
        turn_count = int(persisted_state.get("turnCount") or 0)
        # Keep the revision in JSON so existing deployments need no DDL.  The
        # conditional expressions make duplicate-key updates atomic: a writer
        # whose expected revision is no longer current updates zero columns.
        revision_expr = (
            "COALESCE(CAST(JSON_UNQUOTE(JSON_EXTRACT("
            "agent_session_memory.state_json, '$.memoryRevision')) AS UNSIGNED), 0)"
        )
        async with acquire() as cur:
            await cur.execute(
                f"""INSERT INTO agent_session_memory (user_id, summary_json, state_json, turn_count, updated_at)
                   VALUES (%s, %s, %s, %s, %s) AS incoming
                   ON DUPLICATE KEY UPDATE
                     summary_json=IF({revision_expr}=%s, incoming.summary_json, agent_session_memory.summary_json),
                     turn_count=IF({revision_expr}=%s, incoming.turn_count, agent_session_memory.turn_count),
                     updated_at=IF({revision_expr}=%s, incoming.updated_at, agent_session_memory.updated_at),
                     state_json=IF({revision_expr}=%s, incoming.state_json, agent_session_memory.state_json)""",
                (
                    memory.user_id,
                    json.dumps(memory.summary, ensure_ascii=False),
                    json.dumps(persisted_state, ensure_ascii=False),
                    turn_count,
                    now,
                    expected,
                    expected,
                    expected,
                    expected,
                ),
            )
            return bool(cur.rowcount)

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
        state = state or empty_state()
        return {
            "summary_json": summary or empty_summary(),
            "state_json": state,
            "revision": _revision_from_state(state),
        }

session_memory_service = SessionMemoryService()
