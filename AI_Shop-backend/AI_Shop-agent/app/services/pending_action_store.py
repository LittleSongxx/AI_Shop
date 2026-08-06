from __future__ import annotations

import json
from datetime import datetime, timedelta

from aiomysql import IntegrityError

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
    INCONCLUSIVE = "INCONCLUSIVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
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
        INCONCLUSIVE: 6,
        MANUAL_REVIEW: 7,
    }

    async def create(self, pending: dict) -> tuple[dict, bool]:
        expires_at = datetime.now() + timedelta(seconds=PENDING_ACTION_TTL)
        try:
            async with acquire() as cur:
                # Generated columns cannot depend on NOW(). Release an expired
                # PENDING owner explicitly before trying the unique business key.
                await cur.execute(
                    """
                    UPDATE agent_pending_action
                    SET status=%s, updated_at=NOW()
                    WHERE active_business_key=%s AND status=%s AND expires_at <= NOW()
                    """,
                    (self.EXPIRED, pending["businessKey"], self.PENDING),
                )
                await cur.execute(
                    """
                    INSERT INTO agent_pending_action
                        (action_token, user_id, action_type, message_id, params_json,
                         business_key, args_fingerprint, summary, confirm_text, risk_tip,
                         status, result_message, error_message, reconcile_attempts,
                         reconcile_deadline, last_reconcile_at, review_reason,
                         expires_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, NULL, NULL, 0, NULL, NULL, NULL, %s, NOW(), NOW())
                    """,
                    (
                        pending["token"],
                        pending["userId"],
                        pending["actionType"],
                        pending.get("messageId"),
                        pending.get("paramsJson") or "{}",
                        pending["businessKey"],
                        pending["argsFingerprint"],
                        pending.get("summary"),
                        pending.get("confirmText"),
                        pending.get("riskTip"),
                        self.PENDING,
                        expires_at,
                    ),
                )
        except IntegrityError as exc:
            if not exc.args or exc.args[0] != 1062:
                raise
            existing = await self.get_active_by_business_key(pending["businessKey"])
            if existing is None:
                # A duplicate primary token is not a business-key replay. It is
                # vanishingly unlikely and must not be hidden as a successful reuse.
                raise
            return existing, False

        created = await self.get(pending["token"])
        if created is None:
            raise RuntimeError("pending action insert was not readable")
        return created, True

    async def get(self, token: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       business_key, args_fingerprint, active_business_key,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, reconcile_attempts, reconcile_deadline,
                       last_reconcile_at, review_reason, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def get_active_by_business_key(self, business_key: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT action_token, user_id, action_type, message_id, params_json,
                       business_key, args_fingerprint, active_business_key,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, reconcile_attempts, reconcile_deadline,
                       last_reconcile_at, review_reason, expires_at, created_at
                FROM agent_pending_action
                WHERE active_business_key=%s
                """,
                (business_key,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def list_for_review(
        self,
        *,
        status: str = MANUAL_REVIEW,
        token: str | None = None,
        user_id: str | None = None,
        business_key: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = ["status=%s"]
        params: list[object] = [status]
        if token:
            conditions.append("action_token=%s")
            params.append(token)
        if user_id:
            conditions.append("user_id=%s")
            params.append(user_id)
        if business_key:
            conditions.append("business_key=%s")
            params.append(business_key)
        params.append(max(1, min(int(limit), 200)))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT * FROM agent_pending_action
                WHERE {' AND '.join(conditions)}
                ORDER BY updated_at DESC, action_token
                LIMIT %s
                """,
                tuple(params),
            )
            rows = await cur.fetchall()
        return [self._to_public(row) for row in rows]

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
                       business_key, args_fingerprint, active_business_key,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, reconcile_attempts, reconcile_deadline,
                       last_reconcile_at, review_reason, expires_at, created_at
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
                       business_key, args_fingerprint, active_business_key,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, reconcile_attempts, reconcile_deadline,
                       last_reconcile_at, review_reason, expires_at, created_at
                FROM agent_pending_action
                WHERE action_token=%s
                """,
                (token,),
            )
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def mark_inconclusive(
        self, token: str, deadline_seconds: int, reason: str
    ) -> dict | None:
        deadline_seconds = max(int(deadline_seconds), 1)
        async with acquire() as cur:
            await cur.execute(
                f"""
                UPDATE agent_pending_action
                SET status=%s,
                    reconcile_deadline=COALESCE(
                        reconcile_deadline,
                        DATE_ADD(NOW(), INTERVAL {deadline_seconds} SECOND)),
                    review_reason=%s,
                    updated_at=NOW()
                WHERE action_token=%s AND status=%s
                """,
                (self.INCONCLUSIVE, (reason or "unknown outcome")[:512], token, self.EXECUTING),
            )
            await cur.execute("SELECT * FROM agent_pending_action WHERE action_token=%s", (token,))
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def list_reconcilable(self, stale_seconds: int = 600) -> list[dict]:
        """Return uncertain actions whose next read-only reconciliation is due.

        EXECUTING covers a process crash before the explicit INCONCLUSIVE write.
        MANUAL_REVIEW is intentionally excluded from automatic polling.
        """
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT * FROM agent_pending_action
                WHERE status IN (%s, %s)
                  AND updated_at < DATE_SUB(NOW(), INTERVAL %s SECOND)
                ORDER BY updated_at ASC LIMIT 100
                """,
                (self.EXECUTING, self.INCONCLUSIVE, max(int(stale_seconds), 30)),
            )
            return [self._to_public(row) for row in await cur.fetchall()]

    async def complete_reconciled(
        self,
        token: str,
        status: str,
        *,
        result_message: str | None = None,
        error_message: str | None = None,
    ) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pending_action
                SET status=%s, result_message=%s, error_message=%s,
                    reconcile_attempts=reconcile_attempts+1,
                    last_reconcile_at=NOW(), review_reason=NULL, updated_at=NOW()
                WHERE action_token=%s AND status IN (%s, %s)
                """,
                (
                    status,
                    result_message,
                    error_message,
                    token,
                    self.EXECUTING,
                    self.INCONCLUSIVE,
                ),
            )
            await cur.execute("SELECT * FROM agent_pending_action WHERE action_token=%s", (token,))
            row = await cur.fetchone()
        return self._to_public(row) if row else None

    async def record_inconclusive_attempt(
        self,
        token: str,
        *,
        max_attempts: int,
        deadline_seconds: int,
        reason: str,
    ) -> dict | None:
        max_attempts = max(int(max_attempts), 1)
        deadline_seconds = max(int(deadline_seconds), 1)
        async with acquire() as cur:
            await cur.execute(
                f"""
                UPDATE agent_pending_action
                SET reconcile_deadline=COALESCE(
                        reconcile_deadline,
                        DATE_ADD(NOW(), INTERVAL {deadline_seconds} SECOND)),
                    reconcile_attempts=reconcile_attempts+1,
                    last_reconcile_at=NOW(),
                    review_reason=%s,
                    status=CASE
                        WHEN reconcile_attempts >= %s
                          OR reconcile_deadline <= NOW()
                        THEN %s ELSE %s END,
                    updated_at=NOW()
                WHERE action_token=%s AND status IN (%s, %s)
                """,
                (
                    (reason or "reconciliation inconclusive")[:512],
                    max_attempts,
                    self.MANUAL_REVIEW,
                    self.INCONCLUSIVE,
                    token,
                    self.EXECUTING,
                    self.INCONCLUSIVE,
                ),
            )
            await cur.execute("SELECT * FROM agent_pending_action WHERE action_token=%s", (token,))
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
                       business_key, args_fingerprint, active_business_key,
                       summary, confirm_text, risk_tip, status, result_message,
                       error_message, reconcile_attempts, reconcile_deadline,
                       last_reconcile_at, review_reason, expires_at, created_at
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
            "businessKey": row.get("business_key"),
            "argsFingerprint": row.get("args_fingerprint"),
            "activeBusinessKey": row.get("active_business_key"),
            "summary": row.get("summary"),
            "confirmText": row.get("confirm_text"),
            "riskTip": row.get("risk_tip"),
            "status": self._PUBLIC_STATUS.get(row.get("status"), 5),
            "statusName": row.get("status"),
            "resultMessage": row.get("result_message"),
            "errorMessage": row.get("error_message"),
            "reconcileAttempts": int(row.get("reconcile_attempts") or 0),
            "reconcileDeadline": self._isoformat(row.get("reconcile_deadline")),
            "lastReconcileAt": self._isoformat(row.get("last_reconcile_at")),
            "reviewReason": row.get("review_reason"),
            "expiresAt": row.get("expires_at").isoformat()
            if row.get("expires_at") is not None
            else None,
            "createTime": int(created_at.timestamp() * 1000)
            if isinstance(created_at, datetime)
            else None,
        }

    @staticmethod
    def _isoformat(value) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)


pending_action_store = PendingActionStore()
