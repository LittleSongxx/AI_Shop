from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.config.settings import get_settings
from app.db.pool import acquire
from app.harness.metrics.runtime_sensors import AGENT_TASK_BACKLOG


class AgentTaskService:

    async def create(
        self,
        message_id: int,
        user_id: str,
        queue_name: str,
        priority: int,
        payload: dict,
    ) -> bool:
        settings = get_settings()
        deadline = datetime.now() + timedelta(
            seconds=settings.agent_task_deadline_seconds
        )
        payload["deadlineAt"] = deadline.isoformat()
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT IGNORE INTO agent_task
                    (message_id, user_id, queue_name, priority, status, retry_count,
                     deadline_at, payload_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'PENDING', 0, %s, %s, NOW(), NOW())
                """,
                (
                    message_id,
                    user_id,
                    queue_name,
                    priority,
                    deadline,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cur.rowcount == 1

    async def count_pending(self) -> int:
        async with acquire() as cur:
            await cur.execute(
                "SELECT COUNT(*) AS cnt FROM agent_task WHERE status IN ('PENDING', 'QUEUED', 'PROCESSING')"
            )
            row = await cur.fetchone()
        count = int(row["cnt"]) if row else 0
        AGENT_TASK_BACKLOG.set(count)
        return count

    async def mark_queued(self, message_id: int) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='QUEUED', updated_at=NOW()
                WHERE message_id=%s AND status IN ('PENDING', 'FAILED')
                """,
                (message_id,),
            )

    async def claim(self, message_id: int, redelivered: bool = False) -> bool:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='PROCESSING', started_at=NOW(), updated_at=NOW()
                WHERE message_id=%s
                  AND (
                    status IN ('PENDING', 'QUEUED')
                    OR (
                        status='PROCESSING'
                        AND (
                            %s=1
                            OR updated_at < DATE_SUB(NOW(), INTERVAL 5 MINUTE)
                        )
                    )
                  )
                """,
                (message_id, 1 if redelivered else 0),
            )
            return cur.rowcount == 1

    async def mark_completed(self, message_id: int) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='COMPLETED', completed_at=NOW(), updated_at=NOW()
                WHERE message_id=%s
                """,
                (message_id,),
            )

    async def release(self, message_id: int) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='QUEUED', started_at=NULL, updated_at=NOW()
                WHERE message_id=%s AND status='PROCESSING'
                """,
                (message_id,),
            )

    async def mark_failed(self, message_id: int, error: str) -> int:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='FAILED', retry_count=retry_count+1,
                    error_message=%s, updated_at=NOW()
                WHERE message_id=%s
                """,
                ((error or "")[:500], message_id),
            )
            await cur.execute(
                "SELECT retry_count FROM agent_task WHERE message_id=%s",
                (message_id,),
            )
            row = await cur.fetchone()
        return int(row["retry_count"]) if row else get_settings().agent_task_max_retries

    async def mark_terminal(self, message_id: int, error: str) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='DEAD', error_message=%s, completed_at=NOW(), updated_at=NOW()
                WHERE message_id=%s
                """,
                ((error or "")[:500], message_id),
            )

    async def load_pending(self, limit: int = 100) -> list[dict]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT task_id, message_id, user_id, queue_name, priority, retry_count,
                       deadline_at, payload_json
                FROM agent_task
                WHERE status IN ('PENDING', 'FAILED')
                  AND retry_count < %s
                ORDER BY priority DESC, created_at ASC
                LIMIT %s
                """,
                (get_settings().agent_task_max_retries, max(1, min(limit, 500))),
            )
            rows = list(await cur.fetchall())
        for row in rows:
            raw = row.get("payload_json")
            if isinstance(raw, str):
                try:
                    row["payload"] = json.loads(raw)
                except json.JSONDecodeError:
                    row["payload"] = None
            else:
                row["payload"] = raw
        return rows


agent_task_service = AgentTaskService()
