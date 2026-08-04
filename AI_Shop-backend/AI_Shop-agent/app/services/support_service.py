from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime

import structlog
from aiomysql import IntegrityError

from app.config.settings import get_settings
from app.constants import (
    SUPPORT_STATUS_ACTIVE,
    SUPPORT_STATUS_ASSIGNED,
    SUPPORT_STATUS_QUEUED,
    WS_MESSAGE_TOPIC_ADMIN,
    WS_MESSAGE_TYPE_SUPPORT,
)
from app.db.pool import acquire
from app.services.java_internal_client import java_internal_client
from app.services.redis_service import redis_service

logger = structlog.get_logger()
_ACTIVE_STATUSES = (SUPPORT_STATUS_QUEUED, SUPPORT_STATUS_ASSIGNED, SUPPORT_STATUS_ACTIVE)


class SupportService:

    async def get_active(self, user_id: str) -> dict | None:
        placeholders = ", ".join(["%s"] * len(_ACTIVE_STATUSES))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT * FROM support_session
                WHERE user_id=%s AND status IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (user_id, *_ACTIVE_STATUSES),
            )
            return await cur.fetchone()

    async def create_or_get(
        self,
        user_id: str,
        source_message_id: int | None,
        decision: dict,
        reason: str,
        summary: str,
    ) -> dict:
        active = await self.get_active(user_id)
        if active:
            return active
        session_id = str(uuid.uuid4())
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO support_session
                        (session_id, user_id, status, trigger_reason, summary, intent, sentiment,
                         urgency, risk_level, source_message_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        session_id,
                        user_id,
                        SUPPORT_STATUS_QUEUED,
                        reason,
                        summary,
                        decision.get("intent"),
                        decision.get("sentiment"),
                        decision.get("urgency"),
                        decision.get("risk_level"),
                        source_message_id,
                    ),
                )
        except IntegrityError as exc:
            # P0-6：两个并发请求都过了上面的 get_active；数据库唯一约束
            # uk_support_active_user（active_user 生成列）只放行一个，
            # 抢输的一方读回赢家的会话，而不是自己再插一条重复的。
            # 必须同时核对 MySQL 错误码和索引名；IntegrityError 还包含非空、
            # 外键等约束错误，不能把那些错误误装成并发赢家。
            if not _is_active_session_duplicate(exc):
                raise
            winner = await self.get_active(user_id)
            if winner:
                return winner
            raise
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO support_message
                        (session_id, sender_type, sender_id, content, source_message_id, created_at)
                    VALUES (%s, 'SYSTEM', 'agent', %s, %s, NOW())
                    """,
                    (session_id, "已为您登记人工客服请求，客服接入后会在此对话回复。", source_message_id),
                )
        except Exception:
            # 欢迎消息写失败不致命：会话已建立、管理端仍会收到会话通知，
            # 不能把用户请求整体判失败。记日志，会话照常返回。
            logger.warning(
                "support_welcome_message_failed", session_id=session_id
            )
        session = await self.get_by_id(session_id)
        await self.publish_admin(
            {"event": "support.created", "session": self._public_session(session)}
        )
        return session or {"session_id": session_id, "status": SUPPORT_STATUS_QUEUED}

    async def route_user_message(self, session: dict, user_id: str, content: str, message_id: int) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO support_message
                    (session_id, sender_type, sender_id, content, source_message_id, created_at)
                VALUES (%s, 'USER', %s, %s, %s, NOW())
                """,
                (session["session_id"], user_id, content, message_id),
            )
        await self.publish_admin(
            {
                "event": "support.message",
                "sessionId": session["session_id"],
                "userId": user_id,
                "senderType": "USER",
                "messageId": message_id,
                "content": content,
            }
        )

    async def claim(self, session_id: str, admin_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_session
                SET status='ASSIGNED', assigned_admin=%s, assigned_at=NOW(), updated_at=NOW()
                WHERE session_id=%s
                  AND (
                    status='QUEUED'
                    OR (status='ASSIGNED' AND assigned_admin=%s)
                  )
                """,
                (admin_id, session_id, admin_id),
            )
            if cur.rowcount == 0:
                raise ValueError("会话已被其他客服认领或已经结束")
        session = await self.get_by_id(session_id)
        await self.publish_admin({"event": "support.updated", "session": self._public_session(session)})
        return session

    async def activate(self, session_id: str, admin_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_session
                SET status='ACTIVE', assigned_admin=%s, assigned_at=COALESCE(assigned_at, NOW()),
                    updated_at=NOW()
                WHERE session_id=%s
                  AND status IN ('QUEUED', 'ASSIGNED', 'ACTIVE')
                  AND (assigned_admin IS NULL OR assigned_admin=%s)
                """,
                (admin_id, session_id, admin_id),
            )
            if cur.rowcount == 0:
                raise ValueError("会话已被其他客服认领或已经结束")
        session = await self.get_by_id(session_id)
        await self.publish_admin({"event": "support.updated", "session": self._public_session(session)})
        return session

    async def reply(self, session_id: str, admin_id: str, content: str) -> dict | None:
        async with acquire() as cur:
            # The pool uses autocommit for ordinary one-statement operations. A
            # reply needs a real transaction: lock the session so resolve/cancel
            # cannot close it between the ownership check and the two message
            # inserts, then commit the state transition and messages together.
            await cur.execute("START TRANSACTION")
            try:
                await cur.execute(
                    "SELECT * FROM support_session WHERE session_id=%s FOR UPDATE",
                    (session_id,),
                )
                session = await cur.fetchone()
                if not session or session["status"] not in (
                    SUPPORT_STATUS_ASSIGNED,
                    SUPPORT_STATUS_ACTIVE,
                ):
                    raise ValueError("会话尚未认领或已结束")
                if session.get("assigned_admin") not in (None, admin_id):
                    raise ValueError("会话已被其他客服认领")
                await cur.execute(
                    """
                    UPDATE support_session
                    SET status='ACTIVE', assigned_admin=%s,
                        assigned_at=COALESCE(assigned_at, NOW()), updated_at=NOW()
                    WHERE session_id=%s
                      AND status IN ('ASSIGNED', 'ACTIVE')
                      AND (assigned_admin IS NULL OR assigned_admin=%s)
                    """,
                    (admin_id, session_id, admin_id),
                )
                if cur.rowcount != 1:
                    raise ValueError("会话已被其他客服认领或已经结束")
                await cur.execute(
                    """
                    INSERT INTO support_message
                        (session_id, sender_type, sender_id, content, created_at)
                    VALUES (%s, 'ADMIN', %s, %s, NOW())
                    """,
                    (session_id, admin_id, content),
                )
                await cur.execute(
                    """
                    INSERT INTO agent_message
                        (session_id, user_id, assistant_message, status, biz_type, send_time, queue_name)
                    VALUES (%s, %s, %s, 2, 'human_support', NOW(), 'human')
                    """,
                    (session_id, session["user_id"], content),
                )
                message_id = cur.lastrowid
                await cur.execute("COMMIT")
            except BaseException:
                await cur.execute("ROLLBACK")
                raise
        await redis_service.publish_ws(
            {
                "messageType": WS_MESSAGE_TYPE_SUPPORT,
                "event": "support.reply",
                "userId": session["user_id"],
                "sessionId": session_id,
                "messageId": str(message_id),
                "assistantMessage": content,
                "outPutType": 1,
            }
        )
        await self.publish_admin(
            {
                "event": "support.message",
                "sessionId": session_id,
                "senderType": "ADMIN",
                "senderId": admin_id,
                "content": content,
                "messageId": message_id,
            }
        )
        return await self.get_by_id(session_id)

    async def resolve(self, session_id: str, admin_id: str, remark: str | None = None) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_session
                SET status='RESOLVED', resolved_at=NOW(), updated_at=NOW(),
                    summary=CASE WHEN %s IS NULL OR %s='' THEN summary
                                ELSE CONCAT(COALESCE(summary, ''), '\n处理备注：', %s) END
                WHERE session_id=%s AND status IN ('QUEUED', 'ASSIGNED', 'ACTIVE')
                  AND (assigned_admin IS NULL OR assigned_admin=%s)
                """,
                (remark, remark, remark, session_id, admin_id),
            )
            if cur.rowcount != 1:
                raise ValueError("会话不存在、已结束或不属于当前客服")
        session = await self.get_by_id(session_id)
        await self.publish_both(session, {"event": "support.resolved"})
        return session

    async def return_to_ai(self, session_id: str, admin_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_session
                SET status='CANCELLED', resolved_at=NOW(), updated_at=NOW(),
                    summary=CONCAT(COALESCE(summary, ''), '\n已转回AI客服')
                WHERE session_id=%s AND status IN ('QUEUED', 'ASSIGNED', 'ACTIVE')
                  AND (assigned_admin IS NULL OR assigned_admin=%s)
                """,
                (session_id, admin_id),
            )
            if cur.rowcount != 1:
                raise ValueError("会话不存在、已结束或不属于当前客服")
        session = await self.get_by_id(session_id)
        await self.publish_both(session, {"event": "support.returned_to_ai"})
        return session

    async def cancel_by_user(self, session_id: str, user_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE support_session
                SET status='CANCELLED', resolved_at=NOW(), updated_at=NOW(),
                    summary=CONCAT(COALESCE(summary, ''), '\n用户取消人工请求')
                WHERE session_id=%s AND user_id=%s
                  AND status IN ('QUEUED', 'ASSIGNED', 'ACTIVE')
                """,
                (session_id, user_id),
            )
            if cur.rowcount != 1:
                return None
        session = await self.get_by_id(session_id)
        await self.publish_both(session, {"event": "support.cancelled"})
        return session

    async def get_by_id(self, session_id: str) -> dict | None:
        async with acquire() as cur:
            await cur.execute("SELECT * FROM support_session WHERE session_id=%s", (session_id,))
            return await cur.fetchone()

    async def list_queue(self, page_no: int = 1, page_size: int = 30) -> dict:
        page_no = max(1, page_no)
        page_size = max(1, min(page_size, 100))
        offset = (page_no - 1) * page_size
        placeholders = ", ".join(["%s"] * 3)
        async with acquire() as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS cnt FROM support_session WHERE status IN ({placeholders})",
                (SUPPORT_STATUS_QUEUED, SUPPORT_STATUS_ASSIGNED, SUPPORT_STATUS_ACTIVE),
            )
            count = int((await cur.fetchone())["cnt"])
            await cur.execute(
                f"""
                SELECT * FROM support_session
                WHERE status IN ({placeholders})
                ORDER BY FIELD(status, 'ACTIVE', 'ASSIGNED', 'QUEUED'),
                         FIELD(urgency, 'CRITICAL', 'HIGH', 'NORMAL', 'LOW'),
                         created_at ASC
                LIMIT %s OFFSET %s
                """,
                (
                    SUPPORT_STATUS_QUEUED,
                    SUPPORT_STATUS_ASSIGNED,
                    SUPPORT_STATUS_ACTIVE,
                    page_size,
                    offset,
                ),
            )
            rows = list(await cur.fetchall())
        return {
            "totalCount": count,
            "pageNo": page_no,
            "pageSize": page_size,
            "pageTotal": (count + page_size - 1) // page_size if count else 0,
            "list": [self._public_session(row) for row in rows],
        }

    async def list_sessions(
        self,
        page_no: int = 1,
        page_size: int = 30,
        status: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        page_no = max(1, page_no)
        page_size = max(1, min(page_size, 100))
        offset = (page_no - 1) * page_size
        where = "1=1"
        params: list = []
        if status:
            where += " AND status=%s"
            params.append(status)
        if user_id:
            where += " AND user_id=%s"
            params.append(user_id)
        async with acquire() as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS cnt FROM support_session WHERE {where}",
                params,
            )
            row = await cur.fetchone()
            total = int(row["cnt"]) if row else 0
            await cur.execute(
                f"""
                SELECT * FROM support_session
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = list(await cur.fetchall())
        return {
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "pageTotal": (total + page_size - 1) // page_size if total else 0,
            "list": [self._public_session(item) for item in rows],
        }

    async def sla_stats(
        self,
        window_hours: int = 24,
        first_response_sla_seconds: int | None = None,
        queue_alert_seconds: int | None = None,
    ) -> dict:
        settings = get_settings()
        window_hours = max(1, min(int(window_hours or 24), 720))
        first_response_target = max(
            1,
            int(first_response_sla_seconds or settings.support_first_response_sla_seconds),
        )
        queue_alert_target = max(
            1,
            int(queue_alert_seconds or settings.support_queue_alert_seconds),
        )
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT s.*,
                       (
                         SELECT MIN(m.created_at)
                         FROM support_message m
                         WHERE m.session_id=s.session_id
                           AND m.sender_type='ADMIN'
                       ) AS first_response_at
                FROM support_session s
                WHERE s.created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                """,
                (window_hours,),
            )
            rows = list(await cur.fetchall())
        return build_sla_stats(
            rows,
            window_hours=window_hours,
            first_response_target=first_response_target,
            queue_alert_target=queue_alert_target,
        )

    async def history(self, session_id: str, limit: int = 100) -> list[dict]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT support_message_id, session_id, sender_type, sender_id, content,
                       source_message_id, created_at
                FROM support_message
                WHERE session_id=%s ORDER BY support_message_id ASC LIMIT %s
                """,
                (session_id, max(1, min(limit, 500))),
            )
            rows = list(await cur.fetchall())
        return [self._public_message(row) for row in rows]

    async def save_feedback(
        self, user_id: str, message_id: int, rating: int, reason: str | None, detail: str | None
    ) -> None:
        rating = 1 if rating > 0 else -1
        row = None
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT message_id, user_message, assistant_message, biz_type, intent
                FROM agent_message
                WHERE message_id=%s AND user_id=%s
                """,
                (message_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("消息不存在")
            await cur.execute(
                """
                INSERT INTO agent_message_feedback
                    (message_id, user_id, rating, reason, detail, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON DUPLICATE KEY UPDATE rating=VALUES(rating), reason=VALUES(reason),
                    detail=VALUES(detail), updated_at=NOW()
                """,
                (message_id, user_id, rating, reason, detail),
            )
            if rating < 0:
                await cur.execute(
                    """
                    INSERT IGNORE INTO ai_badcase_candidate
                        (message_id, candidate_type, reason, status, snapshot_json, created_at, updated_at)
                    SELECT message_id, 'NEGATIVE_FEEDBACK', COALESCE(%s, '用户点踩'),
                           'PENDING', JSON_OBJECT('detail', COALESCE(%s, '')), NOW(), NOW()
                    FROM agent_message WHERE message_id=%s
                    """,
                    (reason, detail, message_id),
                )
        if rating > 0 and _eligible_faq_candidate(row):
            try:
                await java_internal_client.submit_faq_candidate(
                    row.get("user_message") or "",
                    row.get("assistant_message") or "",
                    message_id,
                    category="online_feedback",
                )
            except Exception as exc:
                logger.warning(
                    "faq_candidate_submit_failed",
                    message_id=message_id,
                    error=str(exc),
                )

    async def add_badcase(
        self,
        message_id: int | None,
        candidate_type: str,
        reason: str,
        snapshot: dict | None = None,
    ) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO ai_badcase_candidate
                    (message_id, candidate_type, reason, status, snapshot_json,
                     created_at, updated_at)
                VALUES (%s, %s, %s, 'PENDING', %s, NOW(), NOW())
                ON DUPLICATE KEY UPDATE reason=VALUES(reason),
                    snapshot_json=VALUES(snapshot_json), updated_at=NOW()
                """,
                (
                    message_id,
                    candidate_type,
                    (reason or "")[:255],
                    json.dumps(snapshot or {}, ensure_ascii=False),
                ),
            )

    async def list_badcases(
        self,
        page_no: int = 1,
        page_size: int = 30,
        status: str | None = "PENDING",
    ) -> dict:
        page_no = max(1, page_no)
        page_size = max(1, min(page_size, 100))
        offset = (page_no - 1) * page_size
        where = "1=1"
        params: list = []
        if status:
            where += " AND b.status=%s"
            params.append(status)
        async with acquire() as cur:
            await cur.execute(
                f"SELECT COUNT(*) AS cnt FROM ai_badcase_candidate b WHERE {where}",
                params,
            )
            row = await cur.fetchone()
            total = int(row["cnt"]) if row else 0
            await cur.execute(
                f"""
                SELECT b.*, m.user_message, m.assistant_message, m.intent,
                       m.intent_confidence, m.sentiment
                FROM ai_badcase_candidate b
                LEFT JOIN agent_message m ON m.message_id=b.message_id
                WHERE {where}
                ORDER BY b.created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = list(await cur.fetchall())
        return {
            "totalCount": total,
            "pageNo": page_no,
            "pageSize": page_size,
            "pageTotal": (total + page_size - 1) // page_size if total else 0,
            "list": [_public_badcase(item) for item in rows],
        }

    async def review_badcase(
        self,
        candidate_id: int,
        status: str,
        reviewer: str,
        remark: str | None = None,
        faq_answer: str | None = None,
    ) -> dict:
        next_status = (status or "").strip().upper()
        if next_status not in {"RESOLVED", "IGNORED"}:
            raise ValueError("坏例状态只支持 RESOLVED 或 IGNORED")

        async with acquire() as cur:
            await cur.execute(
                """
                SELECT b.*, m.user_message, m.assistant_message, m.biz_type, m.intent
                FROM ai_badcase_candidate b
                LEFT JOIN agent_message m ON m.message_id=b.message_id
                WHERE b.candidate_id=%s
                """,
                (candidate_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("坏例不存在")
            if row.get("status") != "PENDING":
                raise ValueError("坏例已经处理")

        answer = (faq_answer or "").strip()
        if next_status == "RESOLVED" and answer:
            question = str(row.get("user_message") or "").strip()
            if not question:
                raise ValueError("该坏例没有可用的用户问题，不能生成 FAQ")
            await java_internal_client.submit_faq_candidate(
                question,
                answer[:1200],
                int(row["message_id"]) if row.get("message_id") else None,
                category="badcase_fixed",
            )

        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE ai_badcase_candidate
                SET status=%s, reviewer=%s, review_remark=%s, updated_at=NOW()
                WHERE candidate_id=%s AND status='PENDING'
                """,
                (next_status, reviewer, (remark or "")[:500], candidate_id),
            )
            row["status"] = next_status
            row["reviewer"] = reviewer
            row["review_remark"] = (remark or "")[:500]

        return _public_badcase(row)

    async def publish_admin(self, payload: dict) -> None:
        await redis_service.client.publish(WS_MESSAGE_TOPIC_ADMIN, json.dumps(payload, ensure_ascii=False))

    async def publish_both(self, session: dict | None, event: dict) -> None:
        if not session:
            return
        payload = {
            **event,
            "session": self._public_session(session),
            "userId": session.get("user_id"),
            "sessionId": session.get("session_id"),
        }
        await self.publish_admin(payload)
        await redis_service.publish_ws(
            {
                "messageType": WS_MESSAGE_TYPE_SUPPORT,
                "event": event.get("event"),
                "userId": session.get("user_id"),
                "sessionId": session.get("session_id"),
                "status": session.get("status"),
            }
        )

    @staticmethod
    def build_summary(user_text: str, decision: dict, history: list[dict] | None = None) -> str:
        text = SupportService.desensitize(user_text or "")
        lines = [
            f"用户诉求：{text[:300]}",
            f"意图：{decision.get('intent', 'UNKNOWN')}，置信度：{decision.get('confidence', 0)}",
            f"情绪：{decision.get('sentiment', 'NEUTRAL')}，紧急度：{decision.get('urgency', 'NORMAL')}",
        ]
        entities = decision.get("entities") or {}
        if entities:
            lines.append("订单/商品实体：" + json.dumps(entities, ensure_ascii=False))
        if history:
            lines.append("近期对话：" + " | ".join(
                SupportService.desensitize(str(item.get("content") or ""))[:100]
                for item in history[-get_settings().agent_support_summary_limit :]
            ))
        return "\n".join(lines)[:1800]

    @staticmethod
    def desensitize(value: str) -> str:
        value = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "***@***", value)
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "***********", value)
        value = re.sub(r"\b\d{15,19}\b", "****************", value)
        return value

    @staticmethod
    def _public_session(row: dict | None) -> dict:
        if not row:
            return {}
        return {
            "sessionId": row.get("session_id"),
            "userId": row.get("user_id"),
            "status": row.get("status"),
            "triggerReason": row.get("trigger_reason"),
            "summary": row.get("summary"),
            "intent": row.get("intent"),
            "sentiment": row.get("sentiment"),
            "urgency": row.get("urgency"),
            "riskLevel": row.get("risk_level"),
            "assignedAdmin": row.get("assigned_admin"),
            "sourceMessageId": row.get("source_message_id"),
            "createdAt": _format_time(row.get("created_at")),
            "assignedAt": _format_time(row.get("assigned_at")),
            "resolvedAt": _format_time(row.get("resolved_at")),
        }

    @staticmethod
    def _public_message(row: dict) -> dict:
        return {
            "supportMessageId": row.get("support_message_id"),
            "sessionId": row.get("session_id"),
            "senderType": row.get("sender_type"),
            "senderId": row.get("sender_id"),
            "content": row.get("content"),
            "sourceMessageId": row.get("source_message_id"),
            "createdAt": _format_time(row.get("created_at")),
        }

    @staticmethod
    def public_session(row: dict | None) -> dict:
        return SupportService._public_session(row)


def _format_time(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else value


def _public_badcase(row: dict) -> dict:
    snapshot = row.get("snapshot_json")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    return {
        "candidateId": row.get("candidate_id"),
        "messageId": row.get("message_id"),
        "candidateType": row.get("candidate_type"),
        "reason": row.get("reason"),
        "status": row.get("status"),
        "snapshot": snapshot or {},
        "reviewer": row.get("reviewer"),
        "reviewRemark": row.get("review_remark"),
        "userMessage": row.get("user_message"),
        "assistantMessage": row.get("assistant_message"),
        "intent": row.get("intent"),
        "intentConfidence": (
            float(row["intent_confidence"])
            if row.get("intent_confidence") is not None
            else None
        ),
        "sentiment": row.get("sentiment"),
        "createdAt": _format_time(row.get("created_at")),
        "updatedAt": _format_time(row.get("updated_at")),
    }


def _eligible_faq_candidate(row: dict | None) -> bool:
    if not row:
        return False
    question = str(row.get("user_message") or "").strip()
    answer = str(row.get("assistant_message") or "").strip()
    if len(question) < 4 or len(answer) < 8 or len(answer) > 1200:
        return False
    if answer.startswith(("{", "[")):
        return False
    if any(token in answer for token in ("订单号", "手机号", "退款单", "支付单")):
        return False
    biz_type = str(row.get("biz_type") or "").strip()
    intent = str(row.get("intent") or "").strip()
    public_biz = {"", "chat", "product_consult"}
    public_intent = {"", "CHAT", "PRODUCT_CONSULT", "INVOICE", "ADDRESS_CHANGE"}
    return biz_type in public_biz and intent in public_intent


support_service = SupportService()


def _is_active_session_duplicate(exc: IntegrityError) -> bool:
    code = exc.args[0] if exc.args else None
    return code == 1062 and "uk_support_active_user" in str(exc)


def build_sla_stats(
    rows: list[dict],
    *,
    window_hours: int,
    first_response_target: int,
    queue_alert_target: int,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now()
    status_counts = Counter(str(row.get("status") or "UNKNOWN") for row in rows)
    active_statuses = set(_ACTIVE_STATUSES)
    queue_waits: list[float] = []
    first_response_times: list[float] = []
    resolution_times: list[float] = []
    sla_hits = 0
    overdue_first_response = 0
    overdue_queue = 0
    active_by_admin: Counter[str] = Counter()

    for row in rows:
        created = row.get("created_at")
        if not isinstance(created, datetime):
            continue
        assigned = row.get("assigned_at")
        first_response = row.get("first_response_at")
        resolved = row.get("resolved_at")
        if isinstance(assigned, datetime):
            queue_waits.append(max(0.0, (assigned - created).total_seconds()))
        if isinstance(first_response, datetime):
            seconds = max(0.0, (first_response - created).total_seconds())
            first_response_times.append(seconds)
            if seconds <= first_response_target:
                sla_hits += 1
        elif str(row.get("status") or "") in {
            SUPPORT_STATUS_ASSIGNED,
            SUPPORT_STATUS_ACTIVE,
        }:
            if (current - created).total_seconds() > first_response_target:
                overdue_first_response += 1
        if isinstance(resolved, datetime):
            resolution_times.append(max(0.0, (resolved - created).total_seconds()))
        if (
            str(row.get("status") or "") == SUPPORT_STATUS_QUEUED
            and (current - created).total_seconds() > queue_alert_target
        ):
            overdue_queue += 1
        admin = str(row.get("assigned_admin") or "").strip()
        if admin and str(row.get("status") or "") in active_statuses:
            active_by_admin[admin] += 1

    response_count = len(first_response_times)
    active_count = sum(status_counts[status] for status in active_statuses)

    def average(values: list[float]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    return {
        "windowHours": window_hours,
        "firstResponseSlaSeconds": first_response_target,
        "queueAlertSeconds": queue_alert_target,
        "totalSessions": len(rows),
        "statusCounts": dict(status_counts),
        "activeSessions": active_count,
        "averageQueueWaitSeconds": average(queue_waits),
        "averageFirstResponseSeconds": average(first_response_times),
        "averageResolutionSeconds": average(resolution_times),
        "firstResponseSlaRate": round(sla_hits / response_count, 4) if response_count else 0.0,
        "overdueFirstResponse": overdue_first_response,
        "overdueQueued": overdue_queue,
        "activeByAdmin": dict(active_by_admin),
    }
