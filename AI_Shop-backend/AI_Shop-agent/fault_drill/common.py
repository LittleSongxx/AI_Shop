from __future__ import annotations

from app.db.pool import acquire


async def ensure_drill_schema() -> None:
    """Create audit-only tables inside the isolated drill database."""

    async with acquire() as cur:
        await cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fault_drill_attempt
            (
                attempt_id bigint AUTO_INCREMENT PRIMARY KEY,
                message_id int NOT NULL,
                worker_id varchar(64) NOT NULL,
                mode varchar(32) NOT NULL,
                outcome varchar(32) NOT NULL,
                error_message varchar(512) NULL,
                started_at datetime(3) DEFAULT CURRENT_TIMESTAMP(3) NOT NULL,
                completed_at datetime(3) NULL,
                KEY idx_fault_attempt_message (message_id, attempt_id)
            ) CHARSET = utf8mb4
            """
        )
        await cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fault_drill_effect
            (
                action_key varchar(128) NOT NULL PRIMARY KEY,
                message_id int NOT NULL,
                worker_id varchar(64) NOT NULL,
                created_at datetime(3) DEFAULT CURRENT_TIMESTAMP(3) NOT NULL,
                KEY idx_fault_effect_message (message_id)
            ) CHARSET = utf8mb4
            """
        )


async def load_drill_state(message_id: int) -> dict:
    async with acquire() as cur:
        await cur.execute(
            """
            SELECT message_id, user_id, queue_name, status, retry_count,
                   error_message, lease_owner, lease_until, deadline_at,
                   created_at, updated_at, started_at, completed_at
            FROM agent_task
            WHERE message_id=%s
            """,
            (message_id,),
        )
        task = await cur.fetchone()
        await cur.execute(
            """
            SELECT attempt_id, message_id, worker_id, mode, outcome,
                   error_message, started_at, completed_at
            FROM fault_drill_attempt
            WHERE message_id=%s
            ORDER BY attempt_id
            """,
            (message_id,),
        )
        attempts = list(await cur.fetchall())
        await cur.execute(
            """
            SELECT action_key, message_id, worker_id, created_at
            FROM fault_drill_effect
            WHERE message_id=%s
            ORDER BY action_key
            """,
            (message_id,),
        )
        effects = list(await cur.fetchall())
    return {
        "task": task,
        "attempts": attempts,
        "attemptCount": len(attempts),
        "effects": effects,
        "effectCount": len(effects),
    }
