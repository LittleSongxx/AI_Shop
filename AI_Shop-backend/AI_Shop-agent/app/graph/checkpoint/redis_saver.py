from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import CHECKPOINT_PERSIST_FAILURES

logger = structlog.get_logger()


class CheckpointPersistenceError(RuntimeError):
    """A checkpoint mutation could not be persisted to the shared Redis store."""


class RedisCheckpointSaver(BaseCheckpointSaver[str]):

    def __init__(self, redis_client, *, key_prefix: str, ttl_seconds: int = 3600):
        self._serde = JsonPlusSerializer(pickle_fallback=False)
        super().__init__(serde=self._serde)
        self._redis = redis_client
        self._prefix = key_prefix.rstrip(":")
        self._ttl = ttl_seconds
        self._memory = InMemorySaver(serde=self._serde)
        # P0-2c：进程内累计的持久化失败次数。写不进 Redis 的 checkpoint
        # 意味着这次运行无法恢复——进程内 saver 只在当前进程生效，重启即失。
        # runner 用"运行前后差值"判断本轮是否可恢复，并打 ERROR 日志。
        self._persist_failures = 0

    @property
    def persist_failures(self) -> int:
        return self._persist_failures

    def _record_persist_failure(self, thread_id: str, operation: str, error: str) -> None:
        self._persist_failures += 1
        CHECKPOINT_PERSIST_FAILURES.inc()
        logger.error(
            "graph_checkpoint_persist_failed",
            thread_id=thread_id,
            operation=operation,
            error=error,
        )

    def _redis_key(self, thread_id: str) -> str:
        return f"{self._prefix}:{thread_id}"

    @staticmethod
    def _typed_value(value: Sequence[Any]) -> tuple[str, bytes]:
        return str(value[0]), value[1]

    def _restore_storage(self, raw: dict) -> dict:
        return {
            namespace: {
                checkpoint_id: (
                    self._typed_value(entry[0]),
                    self._typed_value(entry[1]),
                    entry[2],
                )
                for checkpoint_id, entry in checkpoints.items()
            }
            for namespace, checkpoints in raw.items()
        }

    def _restore_writes(self, raw: dict) -> dict:
        return {
            tuple(inner_key): (
                entry[0],
                entry[1],
                self._typed_value(entry[2]),
                entry[3],
            )
            for inner_key, entry in raw.items()
        }

    async def hydrate_thread(self, thread_id: str) -> bool:
        if thread_id in self._memory.storage:
            return True
        raw = await self._redis.get(self._redis_key(thread_id))
        if not raw:
            return False
        try:
            envelope = json.loads(raw)
            payload = self._serde.loads_typed(
                (
                    envelope["encoding"],
                    base64.b64decode(envelope["payload"].encode("ascii")),
                )
            )
            self._memory.storage[thread_id] = self._restore_storage(payload["storage"])
            for k, v in payload.get("writes", []):
                self._memory.writes[tuple(k)] = self._restore_writes(v)
            for k, v in payload.get("blobs", []):
                self._memory.blobs[tuple(k)] = self._typed_value(v)
            logger.info("graph_checkpoint_hydrated", thread_id=thread_id)
            return True
        except Exception as e:
            logger.warning("graph_checkpoint_hydrate_failed", thread_id=thread_id, error=str(e))
            return False

    async def _persist_thread(self, thread_id: str) -> None:
        if thread_id not in self._memory.storage:
            return
        writes = {k: v for k, v in self._memory.writes.items() if k[0] == thread_id}
        blobs = {k: v for k, v in self._memory.blobs.items() if k[0] == thread_id}
        payload = {
            "storage": self._memory.storage[thread_id],
            "writes": [(list(k), v) for k, v in writes.items()],
            "blobs": [(list(k), v) for k, v in blobs.items()],
        }
        encoding, encoded = self._serde.dumps_typed(payload)
        envelope = json.dumps(
            {
                "encoding": encoding,
                "payload": base64.b64encode(encoded).decode("ascii"),
            },
            separators=(",", ":"),
        )
        await self._redis.setex(self._redis_key(thread_id), self._ttl, envelope)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self._memory.get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        return self._memory.list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self._memory.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._memory.put_writes(config, writes, task_id, task_path)

    def delete_thread(self, thread_id: str) -> None:
        self._memory.delete_thread(thread_id)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        await self.hydrate_thread(thread_id)
        return self._memory.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        result = self._memory.put(config, checkpoint, metadata, new_versions)
        try:
            await self._persist_thread(thread_id)
        except Exception as e:
            self._record_persist_failure(thread_id, "put", str(e))
            raise CheckpointPersistenceError(
                f"checkpoint put failed for {thread_id}"
            ) from e
        return result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self._memory.put_writes(config, writes, task_id, task_path)
        thread_id = config["configurable"]["thread_id"]
        try:
            await self._persist_thread(thread_id)
        except Exception as e:
            self._record_persist_failure(thread_id, "put_writes", str(e))
            raise CheckpointPersistenceError(
                f"checkpoint writes failed for {thread_id}"
            ) from e

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)
        try:
            await self._redis.delete(self._redis_key(thread_id))
        except Exception as e:
            self._record_persist_failure(thread_id, "delete", str(e))
            raise CheckpointPersistenceError(
                f"checkpoint delete failed for {thread_id}"
            ) from e

_checkpointer: RedisCheckpointSaver | None = None


def get_checkpointer(redis_client) -> RedisCheckpointSaver:
    global _checkpointer
    if _checkpointer is None:
        settings = get_settings()
        _checkpointer = RedisCheckpointSaver(
            redis_client,
            key_prefix=settings.graph_checkpoint_prefix,
            ttl_seconds=settings.graph_checkpoint_ttl,
        )
    return _checkpointer


async def close_checkpointer() -> None:
    global _checkpointer
    _checkpointer = None
    from app.graph.builder import reset_compiled_graph_cache

    reset_compiled_graph_cache()
