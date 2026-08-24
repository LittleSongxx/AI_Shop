import hashlib
import json
import random
import time
from collections.abc import Mapping

import redis.asyncio as aioredis
import structlog

from app.config.settings import get_settings
from app.constants import (
    CANCEL_FLAG_TTL,
    CONSULT_ACTIVE_TTL,
    CONSULT_PRODUCT_TTL,
    IMPRESSION_ATTRIBUTION_TTL,
    IMPRESSION_LOG_MAX_ENTRIES,
    IMPRESSION_LOG_MAX_PRODUCTS,
    IMPRESSION_LOG_TTL,
    PENDING_ACTION_TTL,
    PENDING_MSG_TTL,
    REDIS_AGENT_CLICK_LOG,
    REDIS_AGENT_CONSULT_ACTIVE,
    REDIS_AGENT_CONSULT_PRODUCT,
    REDIS_AGENT_HISTORY_CONDENSED,
    REDIS_AGENT_IMPRESSION_LOG,
    REDIS_AGENT_IMPRESSION_REQUEST,
    REDIS_AGENT_PENDING_ACTION,
    REDIS_AGENT_PENDING_MSG,
    REDIS_AGENT_SESSION,
    REDIS_AGENT_SESSION_COMPRESS_LOCK,
    REDIS_AGENT_SHOPPING_PROFILE,
    REDIS_AGENT_USER_LOCK,
    REDIS_AGENT_WORKER_HEARTBEAT,
    REDIS_AGENT_WORKER_HEARTBEAT_METADATA,
    REDIS_CANCEL_AGENT,
    REDIS_HEARTBEAT_TTL,
    REDIS_PROMPT,
    REDIS_SENSITIVE_WORD_PAYLOAD,
    REDIS_WS_USER_HEARTBEAT,
    SHOPPING_PROFILE_TTL,
    WS_MESSAGE_TOPIC_AGENT,
)

logger = structlog.get_logger()

_RECORD_ATTRIBUTED_CLICK_LUA = """
local raw = redis.call('GET', KEYS[1])
if not raw then return {} end

local decoded, snapshot = pcall(cjson.decode, raw)
if not decoded or type(snapshot) ~= 'table' then return {} end
if tostring(snapshot.userId or '') ~= ARGV[1] then return {} end

local position = tonumber(ARGV[4])
local product_ids = snapshot.productIds
if not position or position < 1 or position ~= math.floor(position) then return {} end
if type(product_ids) ~= 'table' or product_ids[position] == nil then return {} end
if tostring(product_ids[position]) ~= ARGV[2] then return {} end

local source = string.sub(tostring(snapshot.source or ''), 1, 40)
if redis.call('EXISTS', KEYS[3]) == 1 then
    return {'DUPLICATE', source, tostring(position)}
end

local event = cjson.encode({
    ts = tonumber(ARGV[5]),
    productId = ARGV[2],
    source = source,
    position = position,
    requestId = ARGV[3]
})
redis.call('LPUSH', KEYS[2], event)
redis.call('LTRIM', KEYS[2], 0, tonumber(ARGV[6]) - 1)
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[7]))
redis.call('SET', KEYS[3], '1', 'EX', tonumber(ARGV[8]))
return {'RECORDED', source, tostring(position)}
"""


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

    async def claim_admin_assertion_nonce(self, nonce_hash: str, ttl_seconds: int) -> bool:
        key = f"mall:agent:admin-assertion:nonce:{nonce_hash}"
        claimed = await self.client.set(key, "1", nx=True, ex=max(1, int(ttl_seconds)))
        return bool(claimed)

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

    async def log_impression(
        self,
        user_id: str,
        product_ids: list[str],
        *,
        query: str = "",
        source: str = "",
        request_id: str = "",
    ) -> None:
        """Record which products were shown, so clicks later have a denominator.

        This is the missing half of every CTR metric: without the shown-set there
        is no rate to compute and no negative samples to train on. Capped list
        with a TTL rather than an append-only table, because an MVP needs the
        signal, not a warehouse. Never raises - losing a log line must not fail
        a search.
        """
        shown = [str(pid) for pid in product_ids if pid][:IMPRESSION_LOG_MAX_PRODUCTS]
        if not user_id or not shown:
            return
        entry = {
            "ts": int(time.time() * 1000),
            "query": (query or "")[:120],
            "source": source or "",
            "requestId": request_id or "",
            "productIds": shown,
        }
        if request_id:
            await self._push_attributed_impression(
                user_id,
                request_id,
                shown,
                entry,
                query=query,
                source=source,
            )
            return
        await self._push_capped(
            f"{REDIS_AGENT_IMPRESSION_LOG}{user_id}",
            entry,
            event="impression",
            user_id=user_id,
        )

    async def _push_attributed_impression(
        self,
        user_id: str,
        request_id: str,
        product_ids: list[str],
        entry: dict,
        *,
        query: str,
        source: str,
    ) -> None:
        snapshot = {
            "ts": int(time.time() * 1000),
            "userId": user_id,
            "requestId": request_id,
            "query": (query or "")[:120],
            "source": (source or "")[:40],
            "productIds": product_ids,
        }
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.setex(
                self._impression_request_key(request_id),
                IMPRESSION_ATTRIBUTION_TTL,
                json.dumps(snapshot, ensure_ascii=False),
            )
            log_key = f"{REDIS_AGENT_IMPRESSION_LOG}{user_id}"
            pipe.lpush(log_key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(log_key, 0, IMPRESSION_LOG_MAX_ENTRIES - 1)
            pipe.expire(log_key, IMPRESSION_LOG_TTL)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "attributed_impression_log_failed",
                user_id=user_id,
                error=str(exc),
            )

    async def validate_click_attribution(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        position: int,
    ) -> dict | None:
        """Resolve a click against the immutable serving snapshot.

        Position is 1-based, matching analytics conventions and the UI payload.
        The returned source is server-owned; callers must not trust a client
        supplied source label.
        """
        if not user_id or not request_id or not product_id or position < 1:
            return None
        try:
            raw = await self.client.get(self._impression_request_key(request_id))
        except Exception as exc:
            logger.warning(
                "click_attribution_lookup_failed",
                user_id=user_id,
                error=str(exc),
            )
            return None
        if not raw:
            return None
        try:
            snapshot = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(snapshot, dict) or str(snapshot.get("userId") or "") != user_id:
            return None
        product_ids = snapshot.get("productIds")
        if not isinstance(product_ids, list) or position > len(product_ids):
            return None
        expected_product_id = str(product_ids[position - 1])
        if expected_product_id != product_id:
            return None
        return {
            "source": str(snapshot.get("source") or "")[:40],
            "position": position,
            "requestId": request_id,
            "productId": product_id,
        }

    async def log_attributed_click(
        self,
        user_id: str,
        request_id: str,
        product_id: str,
        position: int,
    ) -> dict | None:
        """Atomically validate, deduplicate and record a served-product click."""
        if not user_id or not request_id or not product_id or position < 1:
            return None
        snapshot_key = self._impression_request_key(request_id)
        click_key = f"{REDIS_AGENT_CLICK_LOG}{user_id}"
        dedup_material = f"{user_id}\0{product_id}\0{position}".encode("utf-8")
        dedup_key = (
            f"{snapshot_key}:click:{hashlib.sha256(dedup_material).hexdigest()}"
        )
        try:
            result = await self.client.eval(
                _RECORD_ATTRIBUTED_CLICK_LUA,
                3,
                snapshot_key,
                click_key,
                dedup_key,
                user_id,
                product_id,
                request_id,
                int(position),
                int(time.time() * 1000),
                IMPRESSION_LOG_MAX_ENTRIES,
                IMPRESSION_LOG_TTL,
                IMPRESSION_ATTRIBUTION_TTL,
            )
        except Exception as exc:
            logger.warning(
                "attributed_click_log_failed",
                user_id=user_id,
                error=str(exc),
            )
            return None
        if not isinstance(result, (list, tuple)) or len(result) < 3:
            return None
        status = self._redis_text(result[0])
        if status not in {"RECORDED", "DUPLICATE"}:
            return None
        return {
            "source": self._redis_text(result[1])[:40],
            "position": int(self._redis_text(result[2])),
            "requestId": request_id,
            "productId": product_id,
            "duplicate": status == "DUPLICATE",
        }

    @staticmethod
    def _impression_request_key(request_id: str) -> str:
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        return f"{REDIS_AGENT_IMPRESSION_REQUEST}{digest}"

    @staticmethod
    def _redis_text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    async def log_click(
        self,
        user_id: str,
        product_id: str,
        *,
        source: str = "",
        position: int | None = None,
        request_id: str = "",
    ) -> None:
        """Record a product click as the positive counterpart to an impression."""
        if not user_id or not product_id:
            return
        entry = {
            "ts": int(time.time() * 1000),
            "productId": str(product_id),
            "source": source or "",
            "position": position,
            "requestId": request_id or "",
        }
        await self._push_capped(
            f"{REDIS_AGENT_CLICK_LOG}{user_id}",
            entry,
            event="click",
            user_id=user_id,
        )

    async def _push_capped(
        self,
        key: str,
        entry: dict,
        *,
        event: str,
        user_id: str,
    ) -> None:
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.lpush(key, json.dumps(entry, ensure_ascii=False))
            pipe.ltrim(key, 0, IMPRESSION_LOG_MAX_ENTRIES - 1)
            pipe.expire(key, IMPRESSION_LOG_TTL)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "rec_event_log_failed",
                event=event,
                user_id=user_id,
                error=str(exc),
            )

    async def read_impressions(self, user_id: str, limit: int = 50) -> list[dict]:
        """Read back a user's impression log, newest first. Offline analysis only."""
        return await self._read_events(f"{REDIS_AGENT_IMPRESSION_LOG}{user_id}", limit)

    async def read_clicks(self, user_id: str, limit: int = 50) -> list[dict]:
        """Read back a user's click log, newest first. Offline analysis only."""
        return await self._read_events(f"{REDIS_AGENT_CLICK_LOG}{user_id}", limit)

    async def _read_events(self, key: str, limit: int) -> list[dict]:
        try:
            raw = await self.client.lrange(key, 0, max(1, int(limit)) - 1)
        except Exception:
            return []
        events: list[dict] = []
        for item in raw or []:
            try:
                value = json.loads(item)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                events.append(value)
        return events

    async def pause_consult(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}")

    async def bind_message_id(self, user_id: str, message_id: int) -> None:

        await self.client.setex(f"{REDIS_AGENT_PENDING_MSG}{user_id}", PENDING_MSG_TTL, str(message_id))

    async def get_bound_message_id(self, user_id: str) -> int | None:

        val = await self.client.get(f"{REDIS_AGENT_PENDING_MSG}{user_id}")
        return int(val) if val else None

    async def clear_bound_message_id(self, user_id: str) -> None:

        await self.client.delete(f"{REDIS_AGENT_PENDING_MSG}{user_id}")

    async def clear_user_ai_state(self, user_id: str) -> int:
        """Remove user-scoped Agent caches after a privacy deletion.

        Request-scoped impression snapshots expire on their own and do not expose a
        user lookup key. Durable attribution rows are handled by the deletion job.
        """
        exact_keys = [
            f"{REDIS_AGENT_CONSULT_PRODUCT}{user_id}",
            f"{REDIS_AGENT_CONSULT_ACTIVE}{user_id}",
            f"{REDIS_AGENT_SHOPPING_PROFILE}{user_id}",
            f"{REDIS_AGENT_IMPRESSION_LOG}{user_id}",
            f"{REDIS_AGENT_CLICK_LOG}{user_id}",
            f"{REDIS_AGENT_PENDING_MSG}{user_id}",
            f"{REDIS_AGENT_SESSION}{user_id}",
            f"{REDIS_AGENT_SESSION_COMPRESS_LOCK}{user_id}",
            f"{REDIS_AGENT_USER_LOCK}{user_id}",
            f"{REDIS_WS_USER_HEARTBEAT}{user_id}",
        ]
        deleted = int(await self.client.delete(*exact_keys))
        for pattern in (
            f"{REDIS_CANCEL_AGENT}{user_id}:msg:*",
            f"{REDIS_AGENT_HISTORY_CONDENSED}{user_id}:msg:*",
        ):
            keys = [key async for key in self.client.scan_iter(match=pattern, count=100)]
            if keys:
                deleted += int(await self.client.delete(*keys))
        return deleted

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

    async def renew_agent_user_lock(
        self, user_id: str, owner: str, ttl_seconds: int
    ) -> bool:
        """仅当锁仍归 owner 时续期；锁已被别人拿走则返回 False。

        Worker 长任务处理期间调用（worker._renew_lease_loop），否则锁
        用户锁和任务租约都由 Worker 周期续期；任何一份续期失败都停止执行，
        避免同用户并发或租约被接管后旧 Worker 继续产生副作用。
        """
        result = await self.client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('expire', KEYS[1], ARGV[2])
            end
            return 0
            """,
            1,
            f"{REDIS_AGENT_USER_LOCK}{user_id}",
            owner,
            max(1, int(ttl_seconds)),
        )
        return bool(result)

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

    async def allow_fixed_window(
        self,
        key: str,
        window_seconds: int,
        max_count: int,
    ) -> bool:
        """固定窗口限流，计数与设置过期在一次 EVAL 里完成。

        与 Java 侧 ``lua/rate_limit_v1.lua`` 同语义：只在计数从 0 变 1 时设置过期，
        避免每次请求都续期导致窗口永不结束。

        必须是原子的：先 INCR 再 EXPIRE 的写法一旦在两次往返之间断开（进程被杀、
        连接抖动），这个 key 就永久没有 TTL，该用户对该动作会被永久锁死。
        第二个分支是给这种历史脏 key 兜底的——老版本可能已经留下无 TTL 的 key，
        下次请求时顺手补上过期时间，让它自己恢复。
        """
        result = await self.client.eval(
            """
            local current = redis.call('INCR', KEYS[1]);
            if current == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1]);
            elseif redis.call('TTL', KEYS[1]) < 0 then
                redis.call('EXPIRE', KEYS[1], ARGV[1]);
            end
            if current > tonumber(ARGV[2]) then return 0 else return 1 end;
            """,
            1,
            key,
            max(1, int(window_seconds)),
            max(1, int(max_count)),
        )
        return result == 1

    async def set_worker_heartbeat(
        self,
        worker_id: str,
        ttl_seconds: int,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Refresh the legacy heartbeat and its safe runtime identity together.

        The primary key remains a worker ID for the ownership-aware Lua cleanup
        below.  Metadata is optional only for rollout compatibility; readiness
        treats a live heartbeat without valid metadata as not ready.
        """

        ttl = max(5, int(ttl_seconds))
        pipeline = self.client.pipeline(transaction=True)
        pipeline.setex(REDIS_AGENT_WORKER_HEARTBEAT, ttl, worker_id)
        if metadata is None:
            pipeline.delete(REDIS_AGENT_WORKER_HEARTBEAT_METADATA)
        else:
            source = dict(metadata.get("source") or {})
            safe_metadata = {
                "schemaVersion": str(metadata.get("schemaVersion") or ""),
                "processRole": str(metadata.get("processRole") or ""),
                "startedAt": str(metadata.get("startedAt") or ""),
                "pid": int(metadata.get("pid") or 0),
                "workerId": worker_id,
                "source": {
                    "scope": str(source.get("scope") or ""),
                    "sha256": str(source.get("sha256") or ""),
                    "fileCount": int(source.get("fileCount") or 0),
                },
            }
            pipeline.setex(
                REDIS_AGENT_WORKER_HEARTBEAT_METADATA,
                ttl,
                json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True),
            )
        await pipeline.execute()

    async def worker_is_alive(self) -> bool:
        return await self.client.exists(REDIS_AGENT_WORKER_HEARTBEAT) > 0

    async def worker_heartbeat_metadata(self) -> dict[str, object] | None:
        """Read only the public, Worker-owned heartbeat metadata."""

        raw = await self.client.get(REDIS_AGENT_WORKER_HEARTBEAT_METADATA)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        source = value.get("source")
        if (
            not str(value.get("workerId") or "").strip()
            or str(value.get("processRole") or "") != "worker"
            or not isinstance(source, dict)
            or len(str(source.get("sha256") or "")) != 64
        ):
            return None
        return value

    async def clear_worker_heartbeat(self, worker_id: str) -> None:
        await self.client.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                local removed = redis.call('del', KEYS[1])
                local raw = redis.call('get', KEYS[2])
                if raw then
                    local parsed, metadata = pcall(cjson.decode, raw)
                    if parsed and type(metadata) == 'table'
                        and tostring(metadata.workerId or '') == ARGV[1] then
                        redis.call('del', KEYS[2])
                    end
                end
                return removed
            end
            return 0
            """,
            2,
            REDIS_AGENT_WORKER_HEARTBEAT,
            REDIS_AGENT_WORKER_HEARTBEAT_METADATA,
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
