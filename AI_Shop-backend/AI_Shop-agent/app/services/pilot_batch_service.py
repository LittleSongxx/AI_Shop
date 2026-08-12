"""Consent-bounded pilot batches used by interview evidence reports.

This intentionally stays small: batches govern whether a normal user run may
be labelled as evidence. Raw user IDs are never exposed by this service or the
reporting API; a stable HMAC is sufficient for matching at run time.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

import aiomysql

from app.config.settings import get_settings
from app.db.pool import acquire

_EVIDENCE_SOURCES = frozenset({"SYNTHETIC", "LOCAL_PILOT", "REAL_USER"})
_BATCH_STATUSES = frozenset({"DRAFT", "RUNNING", "CLOSED"})


def participant_user_hash(user_id: str) -> str:
    normalized = str(user_id or "").strip()
    if not normalized:
        raise ValueError("userId 不能为空")
    secret = get_settings().pilot_identity_hmac_secret.encode("utf-8")
    return hmac.new(secret, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def _public_batch(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "batchId": row.get("batch_id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "evidenceSource": row.get("evidence_source"),
        "status": row.get("status"),
        "consentTextVersion": row.get("consent_text_version"),
        "createdBy": row.get("created_by"),
        "startedAt": row.get("started_at"),
        "closedAt": row.get("closed_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "participantCount": int(row.get("participant_count") or 0),
        "activeParticipantCount": int(row.get("active_participant_count") or 0),
    }


def _public_participant(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "participantId": row.get("participant_id"),
        "batchId": row.get("batch_id"),
        "pseudonym": row.get("pseudonym"),
        "status": row.get("status"),
        "consentedAt": row.get("consented_at"),
        "withdrawnAt": row.get("withdrawn_at"),
        "createdAt": row.get("created_at"),
    }


class PilotBatchService:
    async def create(
        self,
        *,
        name: str,
        description: str | None,
        evidence_source: str,
        consent_text_version: str,
        created_by: str,
    ) -> dict[str, Any]:
        normalized_name = str(name or "").strip()
        source = str(evidence_source or "").strip().upper()
        consent_version = str(consent_text_version or "").strip()
        if not normalized_name:
            raise ValueError("name 不能为空")
        if source not in _EVIDENCE_SOURCES:
            raise ValueError("evidenceSource 必须是 SYNTHETIC、LOCAL_PILOT 或 REAL_USER")
        if not consent_version:
            raise ValueError("consentTextVersion 不能为空")
        batch_id = f"pilot_{uuid.uuid4().hex}"
        async with acquire() as cur:
            await cur.execute(
                """
                INSERT INTO agent_pilot_batch
                    (batch_id,name,description,evidence_source,status,
                     consent_text_version,created_by)
                VALUES (%s,%s,%s,%s,'DRAFT',%s,%s)
                """,
                (
                    batch_id,
                    normalized_name[:120],
                    str(description or "").strip()[:1000] or None,
                    source,
                    consent_version[:64],
                    str(created_by or "")[:100],
                ),
            )
        return await self.get(batch_id)

    async def get(self, batch_id: str) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT b.*,
                       COUNT(p.participant_id) AS participant_count,
                       SUM(CASE WHEN p.status='ACTIVE' THEN 1 ELSE 0 END)
                           AS active_participant_count
                FROM agent_pilot_batch b
                LEFT JOIN agent_pilot_participant p ON p.batch_id=b.batch_id
                WHERE b.batch_id=%s
                GROUP BY b.batch_id
                """,
                (str(batch_id),),
            )
            row = await cur.fetchone()
        if not row:
            raise ValueError("试用批次不存在")
        return _public_batch(row)

    async def list(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        normalized_status = str(status or "").strip().upper() or None
        if normalized_status and normalized_status not in _BATCH_STATUSES:
            raise ValueError("status 无效")
        clauses = ["1=1"]
        params: list[Any] = []
        if normalized_status:
            clauses.append("b.status=%s")
            params.append(normalized_status)
        params.append(max(1, min(int(limit), 100)))
        async with acquire() as cur:
            await cur.execute(
                f"""
                SELECT b.*,
                       COUNT(p.participant_id) AS participant_count,
                       SUM(CASE WHEN p.status='ACTIVE' THEN 1 ELSE 0 END)
                           AS active_participant_count
                FROM agent_pilot_batch b
                LEFT JOIN agent_pilot_participant p ON p.batch_id=b.batch_id
                WHERE {' AND '.join(clauses)}
                GROUP BY b.batch_id
                ORDER BY b.created_at DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = list(await cur.fetchall())
        return [_public_batch(row) for row in rows]

    async def start(self, batch_id: str) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pilot_batch
                SET status='RUNNING',started_at=COALESCE(started_at,NOW(3))
                WHERE batch_id=%s AND status='DRAFT'
                """,
                (str(batch_id),),
            )
            changed = cur.rowcount
        if not changed:
            current = await self.get(batch_id)
            if current["status"] != "RUNNING":
                raise ValueError("只有 DRAFT 批次可以启动")
        return await self.get(batch_id)

    async def close(self, batch_id: str) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pilot_batch
                SET status='CLOSED',closed_at=COALESCE(closed_at,NOW(3))
                WHERE batch_id=%s AND status IN ('DRAFT','RUNNING')
                """,
                (str(batch_id),),
            )
            changed = cur.rowcount
        if not changed:
            current = await self.get(batch_id)
            if current["status"] != "CLOSED":
                raise ValueError("批次无法关闭")
        return await self.get(batch_id)

    async def register_participant(
        self,
        *,
        batch_id: str,
        user_id: str,
        created_by: str,
        pseudonym: str | None = None,
    ) -> dict[str, Any]:
        batch = await self.get(batch_id)
        if batch["status"] == "CLOSED":
            raise ValueError("已关闭批次不能登记参与者")
        user_hash = participant_user_hash(user_id)
        alias = str(pseudonym or "").strip()
        if not alias:
            alias_digest = hmac.new(
                get_settings().pilot_identity_hmac_secret.encode("utf-8"),
                f"{batch_id}\0{user_hash}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:10]
            alias = f"P-{alias_digest.upper()}"
        participant_id = f"participant_{uuid.uuid4().hex}"
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    SELECT participant_id,pseudonym
                    FROM agent_pilot_participant
                    WHERE batch_id=%s AND user_id_hash=%s
                    """,
                    (str(batch_id), user_hash),
                )
                existing = await cur.fetchone()
                if existing:
                    participant_id = str(existing["participant_id"])
                    if pseudonym and str(existing.get("pseudonym") or "") != alias:
                        await cur.execute(
                            """
                            UPDATE agent_pilot_participant
                            SET pseudonym=%s,status='ACTIVE',withdrawn_at=NULL,
                                consented_at=NOW(3),created_by=%s
                            WHERE participant_id=%s
                            """,
                            (alias[:64], str(created_by or "")[:100], participant_id),
                        )
                    else:
                        await cur.execute(
                            """
                            UPDATE agent_pilot_participant
                            SET status='ACTIVE',withdrawn_at=NULL,consented_at=NOW(3),
                                created_by=%s
                            WHERE participant_id=%s
                            """,
                            (str(created_by or "")[:100], participant_id),
                        )
                else:
                    await cur.execute(
                        """
                        INSERT INTO agent_pilot_participant
                            (participant_id,batch_id,pseudonym,user_id_hash,
                             consented_at,status,created_by)
                        VALUES (%s,%s,%s,%s,NOW(3),'ACTIVE',%s)
                        """,
                        (
                            participant_id,
                            str(batch_id),
                            alias[:64],
                            user_hash,
                            str(created_by or "")[:100],
                        ),
                    )
                await cur.execute(
                    """
                    SELECT participant_id,batch_id,pseudonym,status,consented_at,
                           withdrawn_at,created_at
                    FROM agent_pilot_participant
                    WHERE batch_id=%s AND user_id_hash=%s
                    """,
                    (str(batch_id), user_hash),
                )
                row = await cur.fetchone()
        except aiomysql.IntegrityError as exc:
            if exc.args and int(exc.args[0]) == 1062:
                raise ValueError("批次内伪名已被占用") from exc
            raise
        if not row:
            raise RuntimeError("参与者登记失败")
        return _public_participant(row)

    async def withdraw_participant(
        self, *, batch_id: str, participant_id: str
    ) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE agent_pilot_participant
                SET status='WITHDRAWN',withdrawn_at=COALESCE(withdrawn_at,NOW(3))
                WHERE batch_id=%s AND participant_id=%s
                """,
                (str(batch_id), str(participant_id)),
            )
            await cur.execute(
                """
                SELECT participant_id,batch_id,pseudonym,status,consented_at,
                       withdrawn_at,created_at
                FROM agent_pilot_participant
                WHERE batch_id=%s AND participant_id=%s
                """,
                (str(batch_id), str(participant_id)),
            )
            row = await cur.fetchone()
        if not row:
            raise ValueError("参与者不存在")
        return _public_participant(row)

    async def list_participants(self, batch_id: str) -> list[dict[str, Any]]:
        await self.get(batch_id)
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT participant_id,batch_id,pseudonym,status,consented_at,
                       withdrawn_at,created_at
                FROM agent_pilot_participant
                WHERE batch_id=%s ORDER BY created_at
                """,
                (str(batch_id),),
            )
            rows = list(await cur.fetchall())
        return [_public_participant(row) for row in rows]

    async def resolve_active_participation(self, user_id: str) -> dict[str, str] | None:
        user_hash = participant_user_hash(user_id)
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT b.batch_id,b.evidence_source,p.pseudonym
                FROM agent_pilot_participant p
                INNER JOIN agent_pilot_batch b ON b.batch_id=p.batch_id
                WHERE p.user_id_hash=%s AND p.status='ACTIVE' AND b.status='RUNNING'
                ORDER BY b.started_at DESC,b.created_at DESC
                LIMIT 1
                """,
                (user_hash,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return {
            "pilotBatchId": str(row["batch_id"]),
            "evidenceSource": str(row["evidence_source"]),
            "pseudonym": str(row["pseudonym"]),
        }


pilot_batch_service = PilotBatchService()
