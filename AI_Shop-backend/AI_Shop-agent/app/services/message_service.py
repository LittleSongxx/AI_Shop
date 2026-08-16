import json
from datetime import datetime

from app.config.settings import get_settings
from app.constants import (
    MSG_STATUS_CANCEL,
    MSG_STATUS_COMPLETE,
    MSG_STATUS_INTERRUPTED,
    MSG_STATUS_NORMAL,
)
from app.db.pool import acquire
from app.domain.intent.types import IntentDecision, NextAction
from app.memory.assistant_condense import (
    schedule_assistant_condense,
    truncate_assistant_for_history,
)
from app.services.redis_service import redis_service
from app.utils.biz_payload import trim_assistant


class AgentMessageService:

    async def save_user_message(
        self,
        user_id: str,
        message: str,
        *,
        decision: IntentDecision | None = None,
        previous_unresolved_count: int = 0,
        queue_name: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        image_asset_id: str | None = None,
        image_snapshot: dict | None = None,
        selected_visual_subject: dict | None = None,
    ) -> dict:

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        unresolved_count = next_unresolved_count(
            decision, previous_unresolved_count
        )

        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_message
                    (user_message, send_time, user_id, status, session_id, intent,
                     intent_confidence, sentiment, urgency, risk_level, run_id, trace_id,
                     unresolved_count, queue_name, image_asset_id, image_snapshot_json,
                     selected_visual_subject_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s)
                """,
                (
                    message,
                    now,
                    user_id,
                    MSG_STATUS_NORMAL,
                    session_id,
                    decision.intent.value if decision else None,
                    decision.confidence if decision else None,
                    decision.sentiment.value if decision else None,
                    decision.urgency.value if decision else None,
                    decision.risk_level.value if decision else None,
                    run_id,
                    trace_id,
                    unresolved_count,
                    queue_name,
                    image_asset_id,
                    _json_dump(image_snapshot),
                    _json_dump(selected_visual_subject),
                ),
            )
            message_id = cur.lastrowid

        result = {
            "messageId": message_id,
            "userId": user_id,
            "userMessage": message,
            "status": MSG_STATUS_NORMAL,
            "sendTime": now,
            "sessionId": session_id,
            "runId": run_id,
            "traceId": trace_id,
            "queueName": queue_name,
            "unresolvedCount": unresolved_count,
            "imageAssetId": image_asset_id,
            "imageSnapshot": image_snapshot,
            "selectedVisualSubject": selected_visual_subject,
        }
        if decision:
            result["intentDecision"] = decision.model_dump(mode="json")
            result.update(_decision_to_public_fields(decision))
        return result

    async def update_decision(
        self,
        message_id: int,
        decision: IntentDecision,
        unresolved_count: int | None = None,
    ) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_message
                SET intent=%s, intent_confidence=%s, sentiment=%s, urgency=%s,
                    risk_level=%s,
                    unresolved_count=COALESCE(%s, unresolved_count)
                WHERE message_id=%s
                """,
                (
                    decision.intent.value,
                    decision.confidence,
                    decision.sentiment.value,
                    decision.urgency.value,
                    decision.risk_level.value,
                    unresolved_count,
                    message_id,
                ),
            )

    async def reset_unresolved_count(self, message_id: int) -> None:
        """Prevent a completed/degraded infrastructure turn poisoning later routing."""
        async with acquire() as cur:
            await cur.execute(
                "UPDATE agent_message SET unresolved_count=0 WHERE message_id=%s",
                (message_id,),
            )

    async def bind_session(self, message_id: int, session_id: str) -> None:
        async with acquire() as cur:
            await cur.execute(
                "UPDATE agent_message SET session_id=%s WHERE message_id=%s",
                (session_id, message_id),
            )

    async def complete_message(
        self,
        message_id: int,
        assistant_message: str,
        biz_type: str | None = None,
        biz_data: str | None = None,
        source_refs: list[dict] | dict | None = None,
        latency_ms: int | None = None,
    ) -> None:

        trimmed = trim_assistant(assistant_message)
        source_refs_json = (
            json.dumps(source_refs, ensure_ascii=False)
            if source_refs is not None
            else None
        )
        async with acquire() as cur:

            rows = await cur.execute(
                """UPDATE agent_message
                   SET assistant_message=%s, biz_type=%s, biz_data=%s,
                       source_refs=%s,
                       latency_ms=COALESCE(%s, TIMESTAMPDIFF(MICROSECOND, send_time, NOW()) DIV 1000),
                       status=%s
                   WHERE message_id=%s AND status=%s""",
                (
                    trimmed,
                    biz_type,
                    biz_data,
                    source_refs_json,
                    latency_ms,
                    MSG_STATUS_COMPLETE,
                    message_id,
                    MSG_STATUS_NORMAL,
                ),
            )
            if rows == 0:

                await cur.execute(
                    """UPDATE agent_message
                       SET assistant_message=%s, biz_type=%s, biz_data=%s,
                           source_refs=%s,
                           latency_ms=COALESCE(%s, TIMESTAMPDIFF(MICROSECOND, send_time, NOW()) DIV 1000),
                           status=%s
                       WHERE message_id=%s AND status=%s""",
                    (
                        trimmed,
                        biz_type,
                        biz_data,
                        source_refs_json,
                        latency_ms,
                        MSG_STATUS_COMPLETE,
                        message_id,
                        MSG_STATUS_COMPLETE,
                    ),
                )

    async def cancel_message(self, user_id: str, message_id: int) -> bool:

        async with acquire() as cur:
            await cur.execute(
                """UPDATE agent_message SET status=%s
                   WHERE user_id=%s AND message_id=%s AND status=%s""",
                (MSG_STATUS_CANCEL, user_id, message_id, MSG_STATUS_NORMAL),
            )
            return cur.rowcount == 1

    async def interrupt_message(
        self,
        user_id: str,
        message_id: int,
        partial_message: str,
        biz_type: str | None = None,
    ) -> bool:

        trimmed = trim_assistant(partial_message)
        if not trimmed:
            return await self.cancel_message(user_id, message_id)
        async with acquire() as cur:
            await cur.execute(
                """UPDATE agent_message SET assistant_message=%s, biz_type=%s, status=%s
                   WHERE message_id=%s AND user_id=%s AND status=%s""",
                (trimmed, biz_type, MSG_STATUS_INTERRUPTED, message_id, user_id, MSG_STATUS_NORMAL),
            )
            return cur.rowcount == 1

    async def is_execution_cancelled(self, user_id: str, message_id: int) -> bool:
        """Check durable state before a Worker starts or times out a task."""
        async with acquire() as cur:
            await cur.execute(
                "SELECT status FROM agent_message WHERE user_id=%s AND message_id=%s",
                (user_id, message_id),
            )
            row = await cur.fetchone()
        return bool(
            row
            and row.get("status") in (MSG_STATUS_CANCEL, MSG_STATUS_INTERRUPTED)
        )

    async def load_history(
        self,
        user_id: str,
        page_no: int = 1,
        max_message_id: int | None = None,
        page_size: int = 15,
    ) -> dict:
        offset = (max(page_no, 1) - 1) * page_size
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT history_cleared_through_message_id AS cleared_through
                FROM agent_session_memory
                WHERE user_id=%s
                """,
                (user_id,),
            )
            visibility = await cur.fetchone() or {}
            cleared_through = int(visibility.get("cleared_through") or 0)
            where = "user_id=%s AND message_id>%s"
            params: list = [user_id, cleared_through]
            if max_message_id:
                where += " AND message_id < %s"
                params.append(max_message_id)
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM agent_message WHERE {where}", params)
            count_row = await cur.fetchone()
            total = count_row["cnt"] if count_row else 0
            await cur.execute(
                f"""SELECT {_MESSAGE_SELECT_COLUMNS}
                    FROM agent_message WHERE {where} ORDER BY message_id DESC LIMIT %s OFFSET %s""",
                params + [page_size, offset],
            )
            rows = await cur.fetchall()
        page_total = (total + page_size - 1) // page_size if page_size else 0
        return {
            "totalCount": total,
            "pageSize": page_size,
            "pageNo": page_no,
            "pageTotal": page_total,
            "list": [_row_to_dict(r) for r in rows],
        }

    async def clear_visible_history(self, user_id: str) -> dict:
        """Hide completed chat turns without deleting memory or audit data."""

        async with acquire() as cur:
            await cur.execute(
                """
                SELECT
                    COALESCE(MAX(message_id), 0) AS max_message_id,
                    SUM(CASE WHEN status=%s THEN 1 ELSE 0 END) AS active_count
                FROM agent_message
                WHERE user_id=%s
                """,
                (MSG_STATUS_NORMAL, user_id),
            )
            row = await cur.fetchone() or {}
            if int(row.get("active_count") or 0) > 0:
                raise ValueError("当前回复尚未结束，请等待完成或先停止回答")

            requested_cursor = int(row.get("max_message_id") or 0)
            await cur.execute(
                """
                INSERT INTO agent_session_memory
                    (user_id, history_cleared_through_message_id)
                VALUES (%s, %s) AS incoming
                ON DUPLICATE KEY UPDATE
                    history_cleared_through_message_id=GREATEST(
                        agent_session_memory.history_cleared_through_message_id,
                        incoming.history_cleared_through_message_id
                    )
                """,
                (user_id, requested_cursor),
            )
            await cur.execute(
                """
                SELECT history_cleared_through_message_id AS cleared_through
                FROM agent_session_memory
                WHERE user_id=%s
                """,
                (user_id,),
            )
            visibility = await cur.fetchone() or {}
            cleared_through = int(visibility.get("cleared_through") or 0)
        return {
            "clearedThroughMessageId": cleared_through,
            "memoryPreserved": True,
        }

    async def load_recent_history(self, user_id: str, limit: int = 15) -> list[dict]:

        async with acquire() as cur:
            await cur.execute(
                """SELECT user_message, assistant_message, biz_type FROM agent_message
                   WHERE user_id=%s AND status IN (%s, %s) ORDER BY message_id DESC LIMIT %s""",
                (user_id, MSG_STATUS_COMPLETE, MSG_STATUS_INTERRUPTED, limit),
            )
            rows = list(await cur.fetchall())
        rows.reverse()
        history = []
        for r in rows:
            if r.get("user_message"):
                history.append({"role": "user", "content": r["user_message"]})
            assistant = r.get("assistant_message")
            if _should_include_assistant_in_history(assistant):

                history.append({"role": "assistant", "content": assistant[:500]})
        return history

    async def load_turns_for_memory(self, user_id: str) -> list[dict]:

        settings = get_settings()
        limit = max(settings.history_message_limit * 4, 60)
        async with acquire() as cur:
            await cur.execute(
                """SELECT message_id, user_message, assistant_message, biz_type, biz_data
                   FROM agent_message
                   WHERE user_id=%s AND status IN (%s, %s)
                   ORDER BY message_id DESC LIMIT %s""",
                (user_id, MSG_STATUS_COMPLETE, MSG_STATUS_INTERRUPTED, limit),
            )
            rows = list(await cur.fetchall())
        rows.reverse()
        turns: list[dict] = []
        for row in rows:
            assistant = row.get("assistant_message")

            condensed = await redis_service.get_history_condensed(
                user_id, int(row["message_id"])
            )
            if not condensed and assistant:

                from app.utils.biz_payload import parse_product_search_message

                intro, _ = parse_product_search_message(assistant)
                if intro:
                    condensed = intro
            if not condensed and assistant and self.should_include_in_working_memory(assistant):

                condensed = truncate_assistant_for_history(assistant)
                schedule_assistant_condense(user_id, int(row["message_id"]), assistant)
            turns.append(
                {
                    "message_id": int(row["message_id"]),
                    "user_message": row.get("user_message") or "",
                    "assistant_message": assistant or "",
                    "assistant_for_history": condensed,
                    "biz_type": row.get("biz_type"),
                    "biz_data": row.get("biz_data"),
                }
            )
        return turns

    @staticmethod
    def should_include_in_working_memory(assistant_message: str | None) -> bool:

        return _should_include_assistant_in_history(assistant_message)

    async def get_recent_intents(self, user_id: str, limit: int = 4) -> list[str]:
        """最近 N 轮已完成的意图（新→旧），限 24 小时内的轮次。

        A2/A3 的输入：会话级意图延续（取最近一轮）与死循环检测
        （连续同意图计数）都从这里拿事实，而不是让每个调用方自己查一遍表。
        时间窗把"会话级"延续钉死为"最近一天"，昨天的旧话题不会隔夜续上
        （跨会话误延续会让用户今天的话沿用昨天的意图）。
        """
        safe = max(1, min(int(limit), 10))
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT intent FROM agent_message
                WHERE user_id=%s
                  AND status IN (%s, %s)
                  AND intent IS NOT NULL
                  AND send_time > DATE_SUB(NOW(), INTERVAL 1 DAY)
                ORDER BY message_id DESC LIMIT %s
                """,
                (user_id, MSG_STATUS_COMPLETE, MSG_STATUS_INTERRUPTED, safe),
            )
            rows = list(await cur.fetchall())
        return [str(r["intent"]) for r in rows if r.get("intent")]

    async def count_user_messages(self, user_id: str) -> int:

        async with acquire() as cur:
            await cur.execute("SELECT COUNT(*) AS cnt FROM agent_message WHERE user_id=%s", (user_id,))
            row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def get_unresolved_count(self, user_id: str) -> int:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT unresolved_count
                FROM agent_message
                WHERE user_id=%s
                ORDER BY message_id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cur.fetchone()
        return int(row.get("unresolved_count") or 0) if row else 0

    async def get_message_for_task(self, message_id: int) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT {_MESSAGE_SELECT_COLUMNS}
                FROM agent_message
                WHERE message_id=%s
                """,
                (message_id,),
            )
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def admin_load_messages(
        self,
        page_no: int = 1,
        page_size: int = 15,
        user_id: str | None = None,
        biz_type: str | None = None,
    ) -> dict:

        page_no = max(int(page_no or 1), 1)
        page_size = max(1, min(int(page_size or 15), 100))
        offset = (page_no - 1) * page_size
        where = "1=1"
        params: list = []
        if user_id:
            where += " AND user_id=%s"
            params.append(user_id)
        if biz_type:
            where += " AND biz_type=%s"
            params.append(biz_type)
        async with acquire() as cur:
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM agent_message WHERE {where}", params)
            count_row = await cur.fetchone()
            total = count_row["cnt"] if count_row else 0
            await cur.execute(
                f"""SELECT {_MESSAGE_SELECT_COLUMNS}
                    FROM agent_message WHERE {where}
                    ORDER BY message_id DESC LIMIT %s OFFSET %s""",
                params + [page_size, offset],
            )
            rows = await cur.fetchall()
        page_total = (total + page_size - 1) // page_size if page_size else 0
        return {
            "totalCount": total,
            "pageSize": page_size,
            "pageNo": page_no,
            "pageTotal": page_total,
            "list": [_row_to_dict(r) for r in rows],
        }

    async def admin_get_message(self, message_id: int) -> dict | None:

        async with acquire() as cur:
            await cur.execute(
                f"""SELECT {_MESSAGE_SELECT_COLUMNS}
                   FROM agent_message WHERE message_id=%s""",
                (message_id,),
            )
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def admin_delete_message(self, message_id: int) -> bool:

        async with acquire() as cur:
            rows = await cur.execute(
                "DELETE FROM agent_message WHERE message_id=%s",
                (message_id,),
            )
        return bool(rows)

def _row_to_dict(row: dict) -> dict:

    return {
        "messageId": row["message_id"],
        "assistantMessage": row.get("assistant_message") or "",
        "userMessage": row.get("user_message") or "",
        "sendTime": row["send_time"].strftime("%Y-%m-%d %H:%M:%S") if row.get("send_time") else None,
        "userId": row["user_id"],
        "status": row["status"],
        "bizType": row.get("biz_type"),
        "bizData": row.get("biz_data"),
        "sessionId": row.get("session_id"),
        "intent": row.get("intent"),
        "intentConfidence": (
            float(row["intent_confidence"])
            if row.get("intent_confidence") is not None
            else None
        ),
        "sentiment": row.get("sentiment"),
        "urgency": row.get("urgency"),
        "riskLevel": row.get("risk_level"),
        "runId": row.get("run_id"),
        "traceId": row.get("trace_id"),
        "sourceRefs": _json_value(row.get("source_refs")),
        "imageAssetId": row.get("image_asset_id"),
        "imageSnapshot": _json_value(row.get("image_snapshot_json")),
        "selectedVisualSubject": _json_value(
            row.get("selected_visual_subject_json")
        ),
        "latencyMs": row.get("latency_ms"),
        "unresolvedCount": int(row.get("unresolved_count") or 0),
        "queueName": row.get("queue_name"),
    }

_MESSAGE_SELECT_COLUMNS = """
    message_id, assistant_message, user_message, send_time, user_id, status,
    biz_type, biz_data, session_id, intent, intent_confidence, sentiment,
    urgency, risk_level, run_id, trace_id, source_refs, image_asset_id,
    image_snapshot_json, selected_visual_subject_json, latency_ms, unresolved_count,
    queue_name
"""


def next_unresolved_count(
    decision: IntentDecision | None, previous_unresolved_count: int
) -> int:
    if not decision:
        return 0
    unresolved = decision.next_action in {
        NextAction.ASK_CLARIFICATION,
        NextAction.HANDOFF_SUGGESTED,
    } or decision.handoff_reason == "REPEATED_UNRESOLVED"
    return max(0, previous_unresolved_count) + 1 if unresolved else 0


def _decision_to_public_fields(decision: IntentDecision) -> dict:
    return {
        "intent": decision.intent.value,
        "intentConfidence": decision.confidence,
        "sentiment": decision.sentiment.value,
        "urgency": decision.urgency.value,
        "riskLevel": decision.risk_level.value,
        "nextAction": decision.next_action.value,
        "requestMode": decision.request_mode.value,
        "handoffReason": decision.handoff_reason,
        "entities": decision.entities,
    }


def _json_value(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _json_dump(value: dict | list | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


agent_message_service = AgentMessageService()

def _should_include_assistant_in_history(assistant_message: str | None) -> bool:

    text = (assistant_message or "").strip()
    if not text:
        return False

    if text.startswith("["):
        return False
    if text.startswith("{"):
        try:
            obj = json.loads(text)

            if isinstance(obj, dict) and obj.get("type") in (
                "ACTION_CONFIRM",
                "PRODUCT_SEARCH_RESULT",
                "ORDER_SELECTION",
            ):
                return False
        except json.JSONDecodeError:
            pass

        return False
    return True
