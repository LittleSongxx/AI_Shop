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
        payload["enqueuedAtEpochMs"] = int(datetime.now().timestamp() * 1000)
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
                "SELECT COUNT(*) AS cnt FROM agent_task "
                "WHERE status IN ('PENDING', 'DISPATCHING', 'QUEUED', 'PROCESSING')"
            )
            row = await cur.fetchone()
        count = int(row["cnt"]) if row else 0
        AGENT_TASK_BACKLOG.set(count)
        return count

    async def mark_dispatching(self, message_id: int) -> bool:
        """原子预占一次 MQ 发布，防止多个恢复 Worker 重复投递同一任务。"""
        settings = get_settings()
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='DISPATCHING', started_at=NULL,
                    lease_owner=NULL, lease_until=NULL, updated_at=NOW()
                WHERE message_id=%s
                  AND retry_count < %s
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                  AND (
                    status IN ('PENDING', 'FAILED')
                    OR (status='DISPATCHING'
                        AND updated_at < DATE_SUB(
                            NOW(), INTERVAL %s SECOND
                        ))
                    OR (status='PROCESSING'
                        AND (lease_until IS NULL OR lease_until < NOW()))
                  )
                """,
                (
                    message_id,
                    settings.agent_task_max_retries,
                    settings.agent_task_dispatch_timeout_seconds,
                ),
            )
            return cur.rowcount == 1

    async def mark_queued(self, message_id: int) -> bool:
        """发布确认后把预占状态切成 QUEUED；若已被消费则保持 PROCESSING。"""
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='QUEUED', next_retry_at=NULL, updated_at=NOW()
                WHERE message_id=%s AND status='DISPATCHING'
                """,
                (message_id,),
            )
            return cur.rowcount == 1

    async def claim(
        self,
        message_id: int,
        lease_owner: str | None = None,
        lease_seconds: int = 180,
    ) -> bool:
        """以租约方式抢占任务（P0-2b：lease/fencing）。

        只有两种情况能拿走任务：
          - 新任务（PENDING/DISPATCHING/QUEUED）——正常认领；
          - 租约已过期（lease_until < NOW）——原持有者疑似崩溃，接管。

        持有有效租约的其他 Worker 一律拒绝——MQ 重投再也不会导致双执行。
        旧的"5 分钟僵超时"启发式被短租约 + 周期续租取代：租约必须短于
        任务截止时间，正常执行持续续租，进程崩溃后才能在 deadline 内接管。

        注意：接管只发生在「有消息可认领」时。消费端在租约仍有效时收到
        重投会直接丢弃本份消息（见 worker），崩溃任务的真正恢复靠
        recover_pending 扫到「租约过期的 PROCESSING」后重新入队——
        两套机制配合，既防双执行，也不留永久悬挂。
        """
        owner = lease_owner or ""
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='PROCESSING',
                    lease_owner=%s,
                    lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND),
                    started_at=NOW(), updated_at=NOW()
                WHERE message_id=%s
                  AND (
                    status IN ('PENDING', 'DISPATCHING', 'QUEUED')
                    OR (
                        status='PROCESSING'
                        AND (lease_until IS NULL OR lease_until < NOW())
                    )
                  )
                """,
                (owner, max(lease_seconds, 30), message_id),
            )
            return cur.rowcount == 1

    async def renew_lease(self, message_id: int, lease_owner: str, lease_seconds: int) -> bool:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND), updated_at=NOW()
                WHERE message_id=%s
                  AND status='PROCESSING'
                  AND lease_owner=%s
                  AND lease_until >= NOW()
                """,
                (max(lease_seconds, 30), message_id, lease_owner),
            )
            return cur.rowcount == 1

    async def mark_completed(
        self, message_id: int, lease_owner: str | None = None
    ) -> bool:
        """终态写入带租约守卫：只有仍持有该任务租约的 Worker 能写终态。

        防止「A 停顿过久租约被 B 接管、A 恢复后又写了一遍终态」的双执行。
        传 None 时不加守卫（恢复扫描等无属主路径）。
        返回是否真的写入了终态（False = 守卫未命中，任务已被接管）。
        """
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='COMPLETED', completed_at=NOW(),
                    lease_owner=NULL, lease_until=NULL, updated_at=NOW()
                WHERE message_id=%s AND status='PROCESSING'
                  AND (
                    %s IS NULL
                    OR (lease_owner=%s AND lease_until >= NOW())
                  )
                """,
                (message_id, lease_owner, lease_owner or ""),
            )
            return cur.rowcount == 1

    async def release(self, message_id: int, lease_owner: str | None = None) -> bool:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='QUEUED', started_at=NULL, lease_owner=NULL,
                    lease_until=NULL, updated_at=NOW()
                WHERE message_id=%s AND status='PROCESSING'
                  AND (
                    %s IS NULL
                    OR (lease_owner=%s AND lease_until >= NOW())
                  )
                """,
                (message_id, lease_owner, lease_owner or ""),
            )
            return cur.rowcount == 1

    async def mark_failed(
        self,
        message_id: int,
        error: str,
        lease_owner: str | None = None,
        *,
        force_terminal: bool = False,
    ) -> tuple[int, bool] | None:
        """原子记录失败，并在重试耗尽或强制终止时直接写入 DEAD。

        返回 None 表示租约守卫未命中（任务已被其他 Worker 接管）——调用方
        不应继续重试调度或推送用户可见错误。返回值为
        ``(retry_count, terminal)``，终态与清租约在同一条 SQL 中完成，不留下
        FAILED(max_retries) 的不可恢复中间状态。
        """
        max_retries = get_settings().agent_task_max_retries
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status=CASE
                        WHEN retry_count + 1 >= %s OR %s THEN 'DEAD'
                        ELSE 'FAILED'
                    END,
                    completed_at=CASE
                        WHEN retry_count + 1 >= %s OR %s THEN NOW()
                        ELSE NULL
                    END,
                    retry_count=retry_count+1,
                    error_message=%s, lease_owner=NULL, lease_until=NULL,
                    updated_at=NOW()
                WHERE message_id=%s AND status='PROCESSING'
                  AND (
                    %s IS NULL
                    OR (lease_owner=%s AND lease_until >= NOW())
                  )
                """,
                (
                    max_retries,
                    bool(force_terminal),
                    max_retries,
                    bool(force_terminal),
                    (error or "")[:500],
                    message_id,
                    lease_owner,
                    lease_owner or "",
                ),
            )
            if cur.rowcount != 1:
                return None
            await cur.execute(
                "SELECT retry_count, status FROM agent_task WHERE message_id=%s",
                (message_id,),
            )
            row = await cur.fetchone()
        if not row:
            return max_retries, True
        return int(row["retry_count"]), row.get("status") == "DEAD"

    async def schedule_retry(self, message_id: int, delay_seconds: int) -> None:
        """退避调度：不立即重发，由 recover_pending 在 next_retry_at 后拉起。"""
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET next_retry_at=DATE_ADD(NOW(), INTERVAL %s SECOND),
                    updated_at=NOW()
                WHERE message_id=%s AND status='FAILED'
                """,
                (max(delay_seconds, 1), message_id),
            )

    async def mark_terminal(
        self,
        message_id: int,
        error: str,
        lease_owner: str | None = None,
        status: str = "DEAD",
    ) -> bool:
        """终态写入（默认 DEAD），带租约守卫。

        status 可指定其他终态（如用户主动取消用 CANCELLED），避免取消被
        计入死信率告警。返回是否真的写入终态（False = 守卫未命中）。
        """
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status=%s, error_message=%s, completed_at=NOW(),
                    lease_owner=NULL, lease_until=NULL, updated_at=NOW()
                WHERE message_id=%s
                  AND status IN (
                    'PENDING', 'DISPATCHING', 'QUEUED', 'PROCESSING', 'FAILED'
                  )
                  AND (
                    %s IS NULL
                    OR (lease_owner=%s AND lease_until >= NOW())
                  )
                """,
                (
                    status,
                    (error or "")[:500],
                    message_id,
                    lease_owner,
                    lease_owner or "",
                ),
            )
            return cur.rowcount == 1

    async def cancel(self, message_id: int, user_id: str) -> bool:
        """Durably stop a user-owned task in any executable state.

        The Redis cancellation flag is deliberately short-lived and cannot be
        the recovery source of truth. Moving the ledger to CANCELLED prevents a
        queued task from being claimed after a long Worker outage; clearing the
        lease also makes an in-flight Worker's next renewal fail closed.
        """
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_task
                SET status='CANCELLED', error_message='用户取消', completed_at=NOW(),
                    lease_owner=NULL, lease_until=NULL, updated_at=NOW()
                WHERE message_id=%s AND user_id=%s
                  AND status IN (
                    'PENDING', 'DISPATCHING', 'QUEUED', 'PROCESSING', 'FAILED'
                  )
                """,
                (message_id, user_id),
            )
            return cur.rowcount == 1

    async def load_pending(self, limit: int = 100) -> list[dict]:
        # QUEUED 表示 RabbitMQ 已确认接收，不能周期重发；只有发布前状态
        # DISPATCHING 超时才重试。恢复方随后用 mark_dispatching 做 CAS 预占，
        # 多 Worker 同时扫描也只有一个能真正 publish。
        settings = get_settings()
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT task_id, message_id, user_id, queue_name, priority, retry_count,
                       deadline_at, payload_json
                FROM agent_task
                WHERE (
                        status IN ('PENDING', 'FAILED')
                        OR (status='DISPATCHING'
                            AND updated_at < DATE_SUB(
                                NOW(), INTERVAL %s SECOND
                            ))
                        OR (status='PROCESSING'
                            AND (lease_until IS NULL OR lease_until < NOW()))
                      )
                  AND retry_count < %s
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY priority DESC, created_at ASC
                LIMIT %s
                """,
                (
                    settings.agent_task_dispatch_timeout_seconds,
                    settings.agent_task_max_retries,
                    max(1, min(limit, 500)),
                ),
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
