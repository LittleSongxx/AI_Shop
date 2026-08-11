from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from app.config.settings import get_settings
from app.constants import MSG_STATUS_NORMAL
from app.db.pool import acquire, transaction
from app.domain.intent.types import IntentDecision
from app.harness.agents.contracts import VisualSubject


class VisualSelectionExpired(ValueError):
    pass


class VisualSelectionConflict(ValueError):
    pass


class VisualSelectionStore:
    TTL_MINUTES = 30

    async def create(
        self,
        *,
        user_id: str,
        source_message_id: int,
        image_asset_id: str,
        original_text: str,
        subjects: list[VisualSubject],
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if len(subjects) < 2:
            raise ValueError("VISUAL_SELECTION_REQUIRES_MULTIPLE_SUBJECTS")
        selection_id = f"vsel_{uuid.uuid4().hex}"
        expires_at = datetime.now() + timedelta(minutes=self.TTL_MINUTES)
        serialized = [subject.model_dump(mode="json") for subject in subjects[:5]]
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_visual_selection
                    (selection_id, user_id, source_message_id, image_asset_id,
                     original_text, subjects_json, constraints_json, status,
                     expires_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, NOW(), NOW())
                """,
                (
                    selection_id,
                    user_id,
                    source_message_id,
                    image_asset_id,
                    original_text[:4000],
                    json.dumps(serialized, ensure_ascii=False),
                    json.dumps(constraints or {}, ensure_ascii=False),
                    expires_at,
                ),
            )
        return {
            "type": "VISUAL_SUBJECT_SELECTION",
            "selectionId": selection_id,
            "imageAssetId": image_asset_id,
            "subjects": [
                {
                    "subjectId": subject.subject_id,
                    "label": subject.label,
                    "bbox": list(subject.bbox),
                }
                for subject in subjects[:5]
            ],
            "expiresAt": expires_at.isoformat(timespec="seconds"),
        }

    async def preview(
        self, *, selection_id: str, subject_id: str, user_id: str
    ) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT * FROM agent_visual_selection
                WHERE selection_id=%s AND user_id=%s
                LIMIT 1
                """,
                (selection_id, user_id),
            )
            row = await cur.fetchone()
        if not row:
            raise VisualSelectionExpired("图片主体选择已失效，请重新上传图片")
        status = str(row.get("status") or "")
        if status == "CONSUMED":
            if str(row.get("selected_subject_id") or "") != subject_id:
                raise VisualSelectionConflict("该图片已选择过其他商品主体")
            return {**self._public(row), "alreadyConsumed": True}
        if status != "ACTIVE" or not row.get("expires_at") or row["expires_at"] <= datetime.now():
            await self.expire(selection_id, user_id)
            raise VisualSelectionExpired("图片主体选择已过期，请重新上传图片")
        subject = next(
            (item for item in self._subjects(row) if item.subject_id == subject_id),
            None,
        )
        if subject is None:
            raise VisualSelectionExpired("图片主体不存在或已失效")
        return {
            **self._public(row),
            "subject": subject.model_dump(mode="json"),
            "alreadyConsumed": False,
        }

    async def mark_consumed(
        self,
        *,
        selection_id: str,
        user_id: str,
        subject_id: str,
        message_id: int,
    ) -> bool:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_visual_selection
                SET status='CONSUMED', selected_subject_id=%s,
                    selected_message_id=%s, updated_at=NOW()
                WHERE selection_id=%s AND user_id=%s AND status='ACTIVE'
                  AND expires_at > NOW()
                """,
                (subject_id, message_id, selection_id, user_id),
            )
            if cur.rowcount == 1:
                return True
            await cur.execute(
                """
                SELECT selected_subject_id, selected_message_id, status
                FROM agent_visual_selection
                WHERE selection_id=%s AND user_id=%s
                """,
                (selection_id, user_id),
            )
            row = await cur.fetchone()
        return bool(
            row
            and row.get("status") == "CONSUMED"
            and str(row.get("selected_subject_id") or "") == subject_id
            and int(row.get("selected_message_id") or 0) == int(message_id)
        )

    async def consume_with_message_and_task(
        self,
        *,
        selection_id: str,
        user_id: str,
        subject_id: str,
        message: str,
        decision: IntentDecision,
        previous_unresolved_count: int,
        queue_name: str,
        priority: int,
        trace_id: str,
        run_id: str,
        image_snapshot: dict[str, Any],
        verified_image_context: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Atomically consume one visual subject and create its worker task.

        Queue publication intentionally occurs after the transaction. If a
        publisher crashes, the normal pending-task recovery loop can resume it;
        the selection can never be consumed without a durable message/task.
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
                SELECT * FROM agent_visual_selection
                WHERE selection_id=%s AND user_id=%s
                FOR UPDATE
                """,
                (selection_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                raise VisualSelectionExpired("图片主体选择已失效，请重新上传图片")

            status = str(row.get("status") or "")
            if status == "CONSUMED":
                if str(row.get("selected_subject_id") or "") != subject_id:
                    raise VisualSelectionConflict("该图片已选择过其他商品主体")
                await cur.execute(
                    """
                    SELECT * FROM agent_message
                    WHERE message_id=%s AND user_id=%s
                    """,
                    (row.get("selected_message_id"), user_id),
                )
                existing = await cur.fetchone()
                if not existing:
                    raise VisualSelectionConflict("该图片主体选择正在恢复，请稍后重试")
                return _row_to_dict(existing), False

            expires_at = row.get("expires_at")
            if status != "ACTIVE" or not expires_at or expires_at <= now:
                if status == "ACTIVE":
                    await cur.execute(
                        """
                        UPDATE agent_visual_selection
                        SET status='EXPIRED', updated_at=NOW()
                        WHERE selection_id=%s AND user_id=%s AND status='ACTIVE'
                        """,
                        (selection_id, user_id),
                    )
                raise VisualSelectionExpired("图片主体选择已过期，请重新上传图片")

            selected_subject = next(
                (item for item in self._subjects(row) if item.subject_id == subject_id),
                None,
            )
            if selected_subject is None:
                raise VisualSelectionExpired("图片主体不存在或已失效")

            await cur.execute(
                """
                INSERT INTO agent_message
                    (user_message, send_time, user_id, status, session_id, intent,
                     intent_confidence, sentiment, urgency, risk_level, run_id, trace_id,
                     unresolved_count, queue_name, image_asset_id, image_snapshot_json,
                     selected_visual_subject_json)
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s)
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
                    run_id,
                    trace_id,
                    unresolved_count,
                    queue_name,
                    str(row.get("image_asset_id") or ""),
                    json.dumps(image_snapshot, ensure_ascii=False),
                    json.dumps(selected_subject.model_dump(mode="json"), ensure_ascii=False),
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
                "runId": run_id,
                "traceId": trace_id,
                "episodeKeep": True,
                "queueName": queue_name,
                "unresolvedCount": unresolved_count,
                "fromProduct": False,
                "intentDecision": decision.model_dump(mode="json"),
                "imageAssetId": str(row.get("image_asset_id") or ""),
                "imageSnapshot": image_snapshot,
                "selectedVisualSubject": selected_subject.model_dump(mode="json"),
                "verifiedImageContext": verified_image_context,
                "selectionId": selection_id,
                "deadlineAt": deadline.isoformat(),
                "enqueuedAtEpochMs": int(now.timestamp() * 1000),
            }
            agent_msg.update(_decision_to_public_fields(decision))
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
                    json.dumps(agent_msg, ensure_ascii=False),
                ),
            )
            await cur.execute(
                """
                UPDATE agent_visual_selection
                SET status='CONSUMED', selected_subject_id=%s,
                    selected_message_id=%s, updated_at=NOW()
                WHERE selection_id=%s AND user_id=%s AND status='ACTIVE'
                  AND expires_at > NOW()
                """,
                (subject_id, message_id, selection_id, user_id),
            )
            if cur.rowcount != 1:
                raise VisualSelectionConflict("该图片主体选择状态已变化，请重新发起")

        return agent_msg, True

    async def selected_message(
        self, selection_id: str, user_id: str
    ) -> dict[str, Any] | None:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT m.* FROM agent_visual_selection s
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

    async def expire(self, selection_id: str, user_id: str) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_visual_selection SET status='EXPIRED', updated_at=NOW()
                WHERE selection_id=%s AND user_id=%s AND status='ACTIVE'
                """,
                (selection_id, user_id),
            )

    @staticmethod
    def _subjects(row: dict) -> list[VisualSubject]:
        raw = row.get("subjects_json")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        subjects: list[VisualSubject] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                subjects.append(VisualSubject.model_validate(item))
            except ValueError:
                continue
        return subjects

    @staticmethod
    def _public(row: dict) -> dict[str, Any]:
        constraints = row.get("constraints_json")
        if isinstance(constraints, str):
            try:
                constraints = json.loads(constraints)
            except json.JSONDecodeError:
                constraints = {}
        return {
            "selectionId": row.get("selection_id"),
            "sourceMessageId": row.get("source_message_id"),
            "imageAssetId": row.get("image_asset_id"),
            "originalText": row.get("original_text"),
            "constraints": constraints if isinstance(constraints, dict) else {},
            "selectedMessageId": row.get("selected_message_id"),
            "expiresAt": (
                row["expires_at"].isoformat(timespec="seconds")
                if isinstance(row.get("expires_at"), datetime)
                else str(row.get("expires_at") or "")
            ),
        }


visual_selection_store = VisualSelectionStore()
