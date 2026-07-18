from datetime import datetime

import json

from app.config.settings import get_settings

from app.constants import MSG_STATUS_CANCEL, MSG_STATUS_COMPLETE, MSG_STATUS_INTERRUPTED, MSG_STATUS_NORMAL

from app.db.pool import acquire

from app.memory.assistant_condense import (
    schedule_assistant_condense,
    truncate_assistant_for_history,
)
from app.services.redis_service import redis_service
from app.utils.biz_payload import trim_assistant

class AgentMessageService:

    async def save_user_message(self, user_id: str, message: str) -> dict:

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        async with acquire() as cur:
            await cur.execute(
                "INSERT INTO agent_message (user_message, send_time, user_id, status) VALUES (%s, %s, %s, %s)",
                (message, now, user_id, MSG_STATUS_NORMAL),
            )
            message_id = cur.lastrowid

        return {
            "messageId": message_id,
            "userId": user_id,
            "userMessage": message,
            "status": MSG_STATUS_NORMAL,
            "sendTime": now,
        }

    async def complete_message(
        self,
        message_id: int,
        assistant_message: str,
        biz_type: str | None = None,
        biz_data: str | None = None,
    ) -> None:

        trimmed = trim_assistant(assistant_message)
        async with acquire() as cur:

            rows = await cur.execute(
                """UPDATE agent_message SET assistant_message=%s, biz_type=%s, biz_data=%s, status=%s
                   WHERE message_id=%s AND status=%s""",
                (trimmed, biz_type, biz_data, MSG_STATUS_COMPLETE, message_id, MSG_STATUS_NORMAL),
            )
            if rows == 0:

                await cur.execute(
                    """UPDATE agent_message SET assistant_message=%s, biz_type=%s, biz_data=%s, status=%s
                       WHERE message_id=%s""",
                    (trimmed, biz_type, biz_data, MSG_STATUS_COMPLETE, message_id),
                )

    async def cancel_message(self, user_id: str, message_id: int) -> None:

        async with acquire() as cur:
            await cur.execute(
                "UPDATE agent_message SET status=%s WHERE user_id=%s AND message_id=%s",
                (MSG_STATUS_CANCEL, user_id, message_id),
            )

    async def interrupt_message(
        self,
        user_id: str,
        message_id: int,
        partial_message: str,
        biz_type: str | None = None,
    ) -> None:

        trimmed = trim_assistant(partial_message)
        if not trimmed:
            await self.cancel_message(user_id, message_id)
            return
        async with acquire() as cur:
            await cur.execute(
                """UPDATE agent_message SET assistant_message=%s, biz_type=%s, status=%s
                   WHERE message_id=%s AND user_id=%s AND status=%s""",
                (trimmed, biz_type, MSG_STATUS_INTERRUPTED, message_id, user_id, MSG_STATUS_NORMAL),
            )

    async def load_history(
        self,
        user_id: str,
        page_no: int = 1,
        max_message_id: int | None = None,
        page_size: int = 15,
    ) -> dict:

        offset = (max(page_no, 1) - 1) * page_size
        where = "user_id=%s"
        params: list = [user_id]
        if max_message_id:
            where += " AND message_id < %s"
            params.append(max_message_id)
        async with acquire() as cur:
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM agent_message WHERE {where}", params)
            count_row = await cur.fetchone()
            total = count_row["cnt"] if count_row else 0
            await cur.execute(
                f"""SELECT message_id, assistant_message, user_message, send_time, user_id, status, biz_type, biz_data
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

    async def count_user_messages(self, user_id: str) -> int:

        async with acquire() as cur:
            await cur.execute("SELECT COUNT(*) AS cnt FROM agent_message WHERE user_id=%s", (user_id,))
            row = await cur.fetchone()
        return row["cnt"] if row else 0

    async def admin_load_messages(
        self,
        page_no: int = 1,
        page_size: int = 15,
        user_id: str | None = None,
    ) -> dict:

        page_no = max(int(page_no or 1), 1)
        page_size = max(1, min(int(page_size or 15), 100))
        offset = (page_no - 1) * page_size
        where = "1=1"
        params: list = []
        if user_id:
            where += " AND user_id=%s"
            params.append(user_id)
        async with acquire() as cur:
            await cur.execute(f"SELECT COUNT(*) AS cnt FROM agent_message WHERE {where}", params)
            count_row = await cur.fetchone()
            total = count_row["cnt"] if count_row else 0
            await cur.execute(
                f"""SELECT message_id, assistant_message, user_message, send_time, user_id, status, biz_type, biz_data
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
                """SELECT message_id, assistant_message, user_message, send_time, user_id, status, biz_type, biz_data
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
    }

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
            ):
                return False
        except json.JSONDecodeError:
            pass

        return False
    return True
