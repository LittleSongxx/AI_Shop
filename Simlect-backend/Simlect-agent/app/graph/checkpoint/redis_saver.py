from __future__ import annotations

import pickle
from collections import defaultdict
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
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.memory import InMemorySaver

from app.config.settings import get_settings

logger = structlog.get_logger()

class RedisCheckpointSaver(BaseCheckpointSaver[str]):

    def __init__(self, redis_client, *, key_prefix: str, ttl_seconds: int = 3600):
        super().__init__()
        self._redis = redis_client
        self._prefix = key_prefix.rstrip(":")
        self._ttl = ttl_seconds
        self._memory = InMemorySaver()

    def _redis_key(self, thread_id: str) -> str:
        return f"{self._prefix}:{thread_id}"

    async def hydrate_thread(self, thread_id: str) -> bool:

        if thread_id in self._memory.storage:
            return True
        raw = await self._redis.get(self._redis_key(thread_id))
        if not raw:
            return False
        try:
            payload = pickle.loads(raw)
            self._memory.storage[thread_id] = payload["storage"]
            for k, v in payload.get("writes", {}).items():
                self._memory.writes[k] = v
            for k, v in payload.get("blobs", {}).items():
                self._memory.blobs[k] = v
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
        payload = pickle.dumps(
            {
                "storage": self._memory.storage[thread_id],
                "writes": writes,
                "blobs": blobs,
            },
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        await self._redis.setex(self._redis_key(thread_id), self._ttl, payload)

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
            logger.warning("graph_checkpoint_persist_failed", thread_id=thread_id, error=str(e))
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
            logger.warning("graph_checkpoint_writes_persist_failed", thread_id=thread_id, error=str(e))

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)
        try:
            await self._redis.delete(self._redis_key(thread_id))
        except Exception as e:
            logger.warning("graph_checkpoint_delete_failed", thread_id=thread_id, error=str(e))

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
