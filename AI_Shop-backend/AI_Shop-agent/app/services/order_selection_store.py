from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.config.settings import get_settings
from app.constants import MSG_STATUS_NORMAL
from app.db.pool import acquire, transaction
from app.domain.intent.types import IntentDecision


class OrderSelectionExpired(ValueError):
    pass


class OrderSelectionConflict(ValueError):
    pass


class OrderSelectionStore:
    TTL_MINUTES = 30
    LEGACY_PROCESSING_TIMEOUT_SECONDS = 120

    async def create(
        self,
        *,
        user_id: str,
        source_message_id: int,
        intent: str,
        original_text: str,
        candidates: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selection_id = f"sel_{uuid.uuid4().hex}"
        expires_at = datetime.now() + timedelta(minutes=self.TTL_MINUTES)
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_order_selection
                    (selection_id, user_id, source_message_id, intent,
                     original_text, candidates_json, context_json, status,
                     expires_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, NOW(), NOW())
                """,
                (
                    selection_id,
                    user_id,
                    source_message_id,
                    intent,
                    original_text,
                    json.dumps(candidates, ensure_ascii=False),
                    json.dumps(context or {}, ensure_ascii=False),
                    expires_at,
                ),
            )
        return {
            "selectionId": selection_id,
            "expiresAt": expires_at.isoformat(timespec="seconds"),
        }

    async def preview(
        self,
        *,
        selection_id: str,
        user_id: str,
        target_type: str,
        target_id: str,
    ) -> dict[str, Any]:
        row = await self._load(selection_id, user_id)
        if not row:
            raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")

        status = str(row.get("status") or "")
        if status == "CONSUMED":
            if not self._same_target(row, target_type, target_id):
                raise OrderSelectionConflict("该候选卡已经选择过其他订单，请重新发起")
            return {**self._public_row(row), "alreadyConsumed": True}

        expires_at = row.get("expires_at")
        if status == "EXPIRED" or not expires_at or expires_at <= datetime.now():
            await self._expire(selection_id, user_id)
            raise OrderSelectionExpired("订单候选已过期，请重新描述要办理的订单")

        if status == "PROCESSING" and not self._is_stale_processing(row):
            if not self._same_target(row, target_type, target_id):
                raise OrderSelectionConflict("该订单候选正在处理，请勿重复点击")
            raise OrderSelectionConflict("该订单候选正在处理，请稍后重试")
        if status not in {"ACTIVE", "PROCESSING"}:
            raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")

        candidate = self._candidate_for(row, target_type, target_id)
        if not candidate:
            raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")

        return {
            **self._public_row(row),
            "candidate": candidate,
            "alreadyConsumed": False,
        }

    async def consume_with_message_and_task(
        self,
        *,
        selection_id: str,
        user_id: str,
        target_type: str,
        target_id: str,
        message: str,
        decision: IntentDecision,
        previous_unresolved_count: int,
        queue_name: str,
        priority: int,
        trace_id: str,
        selected_reference: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Atomically consume a candidate and create its durable worker task.

        MQ publication deliberately happens after this transaction. A publish
        failure therefore leaves a PENDING task for the existing recovery loop,
        while no crash can leave a consumed selection without a message/task.
        """

        from app.services.message_service import (
            _decision_to_public_fields,
            _row_to_dict,
            next_unresolved_count,
        )

        settings = get_settings()
        now = datetime.now()
        deadline = now + timedelta(seconds=settings.agent_task_deadline_seconds)
        unresolved_count = next_unresolved_count(decision, previous_unresolved_count)

        async with transaction() as cur:
            await cur.execute(
                """
                SELECT * FROM agent_order_selection
                WHERE selection_id=%s AND user_id=%s
                FOR UPDATE
                """,
                (selection_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")

            status = str(row.get("status") or "")
            if status == "CONSUMED":
                if not self._same_target(row, target_type, target_id):
                    raise OrderSelectionConflict("该候选卡已经选择过其他订单，请重新发起")
                await cur.execute(
                    """
                    SELECT * FROM agent_message
                    WHERE message_id=%s AND user_id=%s
                    """,
                    (row.get("selected_message_id"), user_id),
                )
                existing = await cur.fetchone()
                if not existing:
                    raise OrderSelectionConflict("该候选消息正在恢复，请稍后重试")
                return _row_to_dict(existing), False

            expires_at = row.get("expires_at")
            if status == "EXPIRED" or not expires_at or expires_at <= now:
                raise OrderSelectionExpired("订单候选已过期，请重新描述要办理的订单")
            if status == "PROCESSING":
                if not self._same_target(row, target_type, target_id):
                    raise OrderSelectionConflict("该订单候选正在处理，请勿重复点击")
                if not self._is_stale_processing(row, now=now):
                    raise OrderSelectionConflict("该订单候选正在处理，请稍后重试")
            elif status != "ACTIVE":
                raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")

            candidate = self._candidate_for(row, target_type, target_id)
            if not candidate:
                raise OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")
            durable_reference = {
                **candidate,
                "intent": selected_reference.get("intent"),
                "selectionId": selection_id,
                # The card TTL authorizes the click. Once consumed, the durable task
                # must retain its selected target until its own execution deadline.
                "expiresAt": deadline.isoformat(timespec="seconds"),
            }

            await cur.execute(
                """
                INSERT INTO agent_message
                    (user_message, send_time, user_id, status, session_id, intent,
                     intent_confidence, sentiment, urgency, risk_level, trace_id,
                     unresolved_count, queue_name)
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message,
                    now,
                    user_id,
                    MSG_STATUS_NORMAL,
                    decision.intent.value,
                    decision.confidence,
                    decision.sentiment.value,
                    decision.urgency.value,
                    decision.risk_level.value,
                    trace_id,
                    unresolved_count,
                    queue_name,
                ),
            )
            message_id = int(cur.lastrowid)
            agent_msg: dict[str, Any] = {
                "messageId": message_id,
                "userId": user_id,
                "userMessage": message,
                "status": MSG_STATUS_NORMAL,
                "sendTime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "sessionId": None,
                "traceId": trace_id,
                "queueName": queue_name,
                "unresolvedCount": unresolved_count,
                "fromProduct": False,
                "intentDecision": decision.model_dump(mode="json"),
                "selectedOrderReference": durable_reference,
                "selectionId": selection_id,
                "deadlineAt": deadline.isoformat(),
                "enqueuedAtEpochMs": int(now.timestamp() * 1000),
            }
            agent_msg.update(_decision_to_public_fields(decision))
            task_payload = dict(agent_msg)
            await cur.execute(
                """
                INSERT INTO agent_task
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
                    json.dumps(task_payload, ensure_ascii=False),
                ),
            )
            await cur.execute(
                """
                UPDATE agent_order_selection
                SET status='CONSUMED', selected_target_type=%s,
                    selected_target_id=%s, selected_message_id=%s, updated_at=NOW()
                WHERE selection_id=%s AND user_id=%s
                  AND status IN ('ACTIVE','PROCESSING') AND expires_at > NOW()
                """,
                (target_type, target_id, message_id, selection_id, user_id),
            )
            if cur.rowcount != 1:
                raise OrderSelectionConflict("该订单候选状态已变化，请重新发起")

        return agent_msg, True

    async def selected_message(
        self, selection_id: str, user_id: str
    ) -> dict[str, Any] | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT m.* FROM agent_order_selection s
                JOIN agent_message m ON m.message_id=s.selected_message_id
                WHERE s.selection_id=%s AND s.user_id=%s
                  AND m.user_id=%s AND s.status='CONSUMED'
                """,
                (selection_id, user_id, user_id),
            )
            row = await cur.fetchone()
        if not row:
            return None
        from app.services.message_service import _row_to_dict

        return _row_to_dict(row)

    @staticmethod
    def _same_target(row: dict[str, Any], target_type: str, target_id: str) -> bool:
        return (
            str(row.get("selected_target_type") or "") == target_type
            and str(row.get("selected_target_id") or "") == target_id
        )

    def _candidate_for(
        self, row: dict[str, Any], target_type: str, target_id: str
    ) -> dict[str, Any] | None:
        candidates = self._json_value(row.get("candidates_json"), [])
        return next(
            (
                item
                for item in candidates
                if isinstance(item, dict)
                and str(item.get("targetType") or "") == target_type
                and str(item.get("targetId") or "") == target_id
            ),
            None,
        )

    def _is_stale_processing(
        self, row: dict[str, Any], *, now: datetime | None = None
    ) -> bool:
        updated_at = row.get("updated_at")
        if not isinstance(updated_at, datetime):
            return False
        return updated_at <= (now or datetime.now()) - timedelta(
            seconds=self.LEGACY_PROCESSING_TIMEOUT_SECONDS
        )

    async def _load(self, selection_id: str, user_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT * FROM agent_order_selection
                WHERE selection_id=%s AND user_id=%s
                """,
                (selection_id, user_id),
            )
            return await cur.fetchone()

    async def _expire(self, selection_id: str, user_id: str) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_order_selection SET status='EXPIRED', updated_at=NOW()
                WHERE selection_id=%s AND user_id=%s AND status <> 'CONSUMED'
                """,
                (selection_id, user_id),
            )

    @staticmethod
    def _json_value(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "selectionId": row.get("selection_id"),
            "sourceMessageId": row.get("source_message_id"),
            "intent": row.get("intent"),
            "originalText": row.get("original_text"),
            "candidates": self._json_value(row.get("candidates_json"), []),
            "context": self._json_value(row.get("context_json"), {}),
            "selectedMessageId": row.get("selected_message_id"),
            "expiresAt": (
                row["expires_at"].isoformat(timespec="seconds")
                if isinstance(row.get("expires_at"), datetime)
                else row.get("expires_at")
            ),
        }


order_selection_store = OrderSelectionStore()
