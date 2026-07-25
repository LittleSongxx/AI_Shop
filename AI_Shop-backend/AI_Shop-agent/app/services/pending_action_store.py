from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.constants import PENDING_ACTION_TTL
from app.db.pool import acquire


class PendingActionStore:
    """MySQL source of truth for confirmation proposals.

    Every state transition is guarded by the current state and token owner.
    Redis may cache the returned dictionaries, but it never decides whether an
    action can be executed.
    """

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    _PUBLIC_STATUS = {
        PENDING: 0,
        CONFIRMED: 1,
        CANCELLED: 2,
        EXECUTING: 3,
        FAILED: 4,
        EXPIRED: 5,
    }

    async def create(self, pending: dict) -> None:
        expires_at = datetime.now() + timedelta(seconds=PENDING_ACTION_TTL)
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_pending_action
                    (action_token, user_id, action_type, message_id, params_json,
                     summary, confirm_text, risk_tip, status, result_message,
                     error_message, expires_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, NOW(), NOW())
                """,
                (
                    pending["token"],
                    pending["userId"],
                    pending["actionType"],
                    pending.get("messageId"),
                    pending.get("paramsJson") or "{}",
                    pending.get("summary"),
                    pending.get("confirmText"),
                    pending.get("riskTip"),
                    self.PENDING,
                    expires_at,
                ),
            )

    async def get(self, token: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def claim(self, user_id: str, token: str) -> tuple[bool, dict | None]:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, updated_at=NOW()
                WHERE action_token=%s AND user_id=%s
                  AND status=%s AND expires_at > NOW()
                """,
                (self.EXECUTING, token, user_id, self.PENDING),
            )
            claimed = cur.rowcount == 1
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, updated_at=NOW()
                WHERE action_token=%s AND user_id=%s
                  AND status=%s AND expires_at <= NOW()
                """,
                (self.EXPIRED, token, user_id, self.PENDING),
            )
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return claimed, (self._to_public(row) if row else None)

    async def complete(
        self,
        token: str,
        status: str,
        result_message: str | None = None,
        error_message: str | None = None,
    ) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, result_message=%s, error_message=%s, updated_at=NOW()
                WHERE action_token=%s AND status=%s
                """,
                (status, result_message, error_message, token, self.EXECUTING),
            )
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def cancel(self, user_id: str, token: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, updated_at=NOW()
                WHERE action_token=%s AND user_id=%s
                  AND status=%s AND expires_at > NOW()
                """,
                (self.CANCELLED, token, user_id, self.PENDING),
            )
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, updated_at=NOW()
                WHERE action_token=%s AND user_id=%s
                  AND status=%s AND expires_at <= NOW()
                """,
                (self.EXPIRED, token, user_id, self.PENDING),
            )
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    def _to_public(self, row: dict) -> dict:
        params = row.get("params_json") or "{}"
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        created_at = row.get("created_at")
        return {
            "token": row.get("action_token"),
            "userId": row.get("user_id"),
            "actionType": row.get("action_type"),
            "messageId": row.get("message_id"),
            "paramsJson": json.dumps(params, ensure_ascii=False),
            "summary": row.get("summary"),
            "confirmText": row.get("confirm_text"),
            "riskTip": row.get("risk_tip"),
            "status": self._PUBLIC_STATUS.get(row.get("status"), 5),
            "statusName": row.get("status"),
            "resultMessage": row.get("result_message"),
            "errorMessage": row.get("error_message"),
            "expiresAt": row.get("expires_at").isoformat()
            if row.get("expires_at") is not None
            else None,
            "createTime": int(created_at.timestamp() * 1000)
            if isinstance(created_at, datetime)
            else None,
        }


pending_action_store = PendingActionStore()
