from __future__ import annotations

import asyncio
import os

import redis.asyncio as aioredis
import structlog

from app.db.pool import acquire
from app.graph.checkpoint.redis_saver import RedisCheckpointSaver
from app.worker import AgentWorker

logger = structlog.get_logger()


class FaultDrillWorker(AgentWorker):
    """Run deterministic payloads through the production queue/lease machinery."""

    async def _start_attempt(self, message_id: int, mode: str) -> tuple[int, int]:
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO fault_drill_attempt
                    (message_id, worker_id, mode, outcome, started_at)
                VALUES (%s, %s, %s, 'STARTED', NOW(3))
                """,
                (message_id, self._worker_id, mode),
            )
            attempt_id = int(cur.lastrowid)
            await cur.execute(
                "SELECT COUNT(*) AS cnt FROM fault_drill_attempt WHERE message_id=%s",
                (message_id,),
            )
            row = await cur.fetchone()
        return attempt_id, int(row["cnt"])

    async def _finish_attempt(
        self,
        attempt_id: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE fault_drill_attempt
                SET outcome=%s, error_message=%s, completed_at=NOW(3)
                WHERE attempt_id=%s
                """,
                (outcome, (error or "")[:500] or None, attempt_id),
            )

    async def _record_effect(self, message_id: int, action_key: str) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO fault_drill_effect
                    (action_key, message_id, worker_id, created_at)
                VALUES (%s, %s, %s, NOW(3))
                ON DUPLICATE KEY UPDATE
                    action_key=fault_drill_effect.action_key
                """,
                (action_key, message_id, self._worker_id),
            )

    async def _write_checkpoint(self, message_id: int) -> None:
        client = aioredis.from_url(
            os.environ["FAULT_CHECKPOINT_REDIS_URL"],
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry_on_timeout=False,
        )
        saver = RedisCheckpointSaver(
            client,
            key_prefix="fault-drill:checkpoint",
            ttl_seconds=300,
        )
        config = {
            "configurable": {
                "thread_id": f"fault-drill:{message_id}",
                "checkpoint_ns": "",
            }
        }
        checkpoint = {
            "v": 4,
            "ts": "2026-08-06T00:00:00+08:00",
            "id": f"fault-{message_id}",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "updated_channels": [],
        }
        try:
            await saver.aput(
                config,
                checkpoint,
                {"source": "input", "step": 0, "parents": {}},
                {},
            )
        finally:
            await client.aclose()

    async def _execute_payload(self, payload: dict) -> str:
        message_id = int(payload["messageId"])
        mode = str(payload.get("faultMode") or "normal")
        action_key = str(payload.get("actionKey") or f"message:{message_id}")
        attempt_id, attempt_number = await self._start_attempt(message_id, mode)
        logger.info(
            "fault_drill_attempt_started",
            message_id=message_id,
            mode=mode,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            worker_id=self._worker_id,
        )

        try:
            if mode == "takeover" and attempt_number == 1:
                await asyncio.sleep(int(payload.get("firstAttemptSleepSeconds") or 90))
            if mode == "checkpoint_failure":
                await self._write_checkpoint(message_id)
            await self._record_effect(message_id, action_key)
            await self._finish_attempt(attempt_id, "COMPLETED")
            return "ok"
        except asyncio.CancelledError:
            # SIGKILL cannot run this branch; a graceful cancellation remains visible.
            await self._finish_attempt(attempt_id, "CANCELLED")
            raise
        except Exception as exc:
            await self._finish_attempt(
                attempt_id,
                "CHECKPOINT_ERROR" if mode == "checkpoint_failure" else "ERROR",
                f"{type(exc).__name__}: {exc}",
            )
            raise


async def run() -> None:
    await FaultDrillWorker().run()


if __name__ == "__main__":
    asyncio.run(run())
