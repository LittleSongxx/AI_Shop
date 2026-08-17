"""Resumable export and deletion of user-owned Agent data.

The Java user service is the public policy boundary: it authenticates the current
user and verifies the account password. This module receives only that derived
identity over the internal service channel and owns the Agent-domain lifecycle.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiomysql
import structlog

from app.config.settings import get_settings
from app.db.pool import acquire, transaction
from app.services.pilot_batch_service import participant_user_hash
from app.services.redis_service import redis_service

logger = structlog.get_logger()

_JOB_TYPES = frozenset({"EXPORT", "DELETE"})
_RUNNABLE_STATUSES = frozenset({"PENDING", "FAILED", "RUNNING"})
_EXPORT_STEPS = ("PREPARE_EXPORT", "WRITE_EXPORT", "FINALIZE_EXPORT")
_DELETE_STEPS = (
    "REVOKE_EXPORTS",
    "DETACH_RETAINED_FACTS",
    "DELETE_SUPPORT_DATA",
    "DELETE_PERSONALIZATION",
    "DELETE_RUNTIME_DATA",
    "DELETE_TRACES_MESSAGES",
    "CLEAR_CACHES",
)


class PrivacyJobConflict(ValueError):
    """An idempotency key was reused for a materially different request."""


class PrivacyJobNotFound(ValueError):
    """The requested job is absent or belongs to another user."""


class PrivacyExportUnavailable(ValueError):
    """The export is incomplete, expired, or no longer present."""


def _steps_for(job_type: str) -> list[dict[str, Any]]:
    names = _EXPORT_STEPS if job_type == "EXPORT" else _DELETE_STEPS
    return [
        {"name": name, "status": "PENDING", "attempts": 0, "result": None}
        for name in names
    ]


def _decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<BINARY:{len(value)} bytes>"
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    steps = _decode_json(row.get("steps_json"), [])
    completed_steps = sum(1 for step in steps if step.get("status") == "COMPLETED")
    return {
        "jobId": row.get("job_id"),
        "jobType": row.get("job_type"),
        "status": row.get("status"),
        "currentStep": row.get("current_step"),
        "steps": steps,
        "progress": {
            "completed": completed_steps,
            "total": len(steps),
            "percent": round(completed_steps * 100 / len(steps)) if steps else 0,
        },
        "retryCount": int(row.get("retry_count") or 0),
        "errorCode": row.get("error_code"),
        "errorMessage": row.get("error_message"),
        "downloadable": bool(
            row.get("status") == "COMPLETED"
            and row.get("job_type") == "EXPORT"
            and row.get("export_path")
            and row.get("export_available", True)
        ),
        "exportExpiresAt": row.get("export_expires_at"),
        "requestedAt": row.get("requested_at"),
        "startedAt": row.get("started_at"),
        "completedAt": row.get("completed_at"),
        "updatedAt": row.get("updated_at"),
    }


class PrivacyJobService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def create(
        self,
        *,
        user_id: str,
        job_type: str,
        idempotency_key: str,
        request_fingerprint: str,
        schedule: bool = True,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        normalized_type = str(job_type or "").strip().upper()
        normalized_key = str(idempotency_key or "").strip()
        fingerprint = str(request_fingerprint or "").strip().lower()
        if not normalized_user:
            raise ValueError("userId 不能为空")
        if normalized_type not in _JOB_TYPES:
            raise ValueError("jobType 必须是 EXPORT 或 DELETE")
        if not normalized_key or len(normalized_key) > 128:
            raise ValueError("Idempotency-Key 长度必须为 1 到 128")
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise ValueError("requestFingerprint 必须是 SHA-256 十六进制")

        job_id = f"privacy_{uuid.uuid4().hex}"
        steps_json = json.dumps(_steps_for(normalized_type), ensure_ascii=False)
        try:
            async with transaction() as cur:
                await cur.execute(
                    """
                    SELECT * FROM user_privacy_job
                    WHERE user_id=%s AND job_type=%s AND idempotency_key=%s
                    FOR UPDATE
                    """,
                    (normalized_user, normalized_type, normalized_key),
                )
                existing = await cur.fetchone()
                if existing:
                    if str(existing.get("request_fingerprint")) != fingerprint:
                        raise PrivacyJobConflict("Idempotency-Key 已用于不同请求")
                    result = dict(existing)
                else:
                    await cur.execute(
                        """
                        INSERT INTO user_privacy_job
                            (job_id,user_id,job_type,idempotency_key,request_fingerprint,
                             status,steps_json)
                        VALUES (%s,%s,%s,%s,%s,'PENDING',%s)
                        """,
                        (
                            job_id,
                            normalized_user,
                            normalized_type,
                            normalized_key,
                            fingerprint,
                            steps_json,
                        ),
                    )
                    await cur.execute(
                        "SELECT * FROM user_privacy_job WHERE job_id=%s", (job_id,)
                    )
                    result = dict(await cur.fetchone())
        except aiomysql.IntegrityError as exc:
            if not exc.args or int(exc.args[0]) != 1062:
                raise
            result = await self._get_owned_row(normalized_user, job_id=None, key=(normalized_type, normalized_key))
            if str(result.get("request_fingerprint")) != fingerprint:
                raise PrivacyJobConflict("Idempotency-Key 已用于不同请求") from exc

        if schedule and result.get("status") in _RUNNABLE_STATUSES:
            self.schedule(str(result["job_id"]))
        return _public_job(result)

    async def get(self, user_id: str, job_id: str) -> dict[str, Any]:
        return _public_job(await self._get_owned_row(user_id, job_id=job_id))

    async def list(self, user_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT *,
                       CASE WHEN export_expires_at IS NOT NULL
                                  AND export_expires_at > NOW(3)
                            THEN 1 ELSE 0 END AS export_available
                FROM user_privacy_job
                WHERE user_id=%s
                ORDER BY requested_at DESC
                LIMIT %s
                """,
                (str(user_id), max(1, min(int(limit), 100))),
            )
            rows = list(await cur.fetchall())
        return [_public_job(row) for row in rows]

    async def retry(self, user_id: str, job_id: str) -> dict[str, Any]:
        row = await self._get_owned_row(user_id, job_id=job_id)
        if row.get("status") not in {"FAILED", "PENDING"}:
            raise ValueError("只有失败或等待中的任务可以重试")
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET status='PENDING',retry_count=retry_count+1,
                    error_code=NULL,error_message=NULL
                WHERE job_id=%s AND user_id=%s AND status IN ('FAILED','PENDING')
                """,
                (str(job_id), str(user_id)),
            )
        self.schedule(str(job_id))
        return await self.get(user_id, job_id)

    async def download(self, user_id: str, job_id: str) -> tuple[Path, str]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT export_path
                FROM user_privacy_job
                WHERE job_id=%s AND user_id=%s AND job_type='EXPORT'
                  AND status='COMPLETED' AND export_path IS NOT NULL
                  AND export_expires_at > NOW(3)
                """,
                (str(job_id), str(user_id)),
            )
            row = await cur.fetchone()
        if not row:
            raise PrivacyExportUnavailable("导出文件不存在、尚未完成或已过期")
        path = Path(str(row["export_path"])).resolve()
        base = self._export_dir().resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise PrivacyExportUnavailable("导出文件不存在、尚未完成或已过期")
        return path, f"ai-data-export-{job_id}.json"

    async def resume_incomplete(self, *, limit: int = 20) -> int:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT job_id FROM user_privacy_job
                WHERE status IN ('PENDING','RUNNING')
                ORDER BY requested_at
                LIMIT %s
                """,
                (max(1, min(int(limit), 100)),),
            )
            rows = list(await cur.fetchall())
        for row in rows:
            self.schedule(str(row["job_id"]))
        return len(rows)

    def schedule(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._execute_guarded(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task, key=job_id: self._tasks.pop(key, None))

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _execute_guarded(self, job_id: str) -> None:
        try:
            await self.execute(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "privacy_job_failed",
                job_id=job_id,
                error=type(exc).__name__,
            )

    async def execute(self, job_id: str) -> None:
        row = await self._claim(job_id)
        if not row:
            return
        steps = _decode_json(row.get("steps_json"), [])
        job_type = str(row["job_type"])
        user_id = str(row["user_id"])
        handlers = self._export_handlers() if job_type == "EXPORT" else self._delete_handlers()

        for step in steps:
            if step.get("status") == "COMPLETED":
                continue
            name = str(step.get("name") or "")
            handler = handlers.get(name)
            if handler is None:
                await self._fail(job_id, steps, name, ValueError(f"unknown step: {name}"))
                return
            step["status"] = "RUNNING"
            step["attempts"] = int(step.get("attempts") or 0) + 1
            step["error"] = None
            await self._save_steps(job_id, steps, current_step=name)
            try:
                result = await handler(job_id, user_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                step["status"] = "FAILED"
                step["error"] = str(exc)[:512]
                await self._fail(job_id, steps, name, exc)
                return
            step["status"] = "COMPLETED"
            step["result"] = result
            await self._save_steps(job_id, steps, current_step=name)

        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET status='COMPLETED',current_step=NULL,error_code=NULL,
                    error_message=NULL,completed_at=NOW(3)
                WHERE job_id=%s
                """,
                (job_id,),
            )

    async def _claim(self, job_id: str) -> dict[str, Any] | None:
        async with transaction() as cur:
            await cur.execute(
                "SELECT * FROM user_privacy_job WHERE job_id=%s FOR UPDATE", (job_id,)
            )
            row = await cur.fetchone()
            if not row or row.get("status") not in _RUNNABLE_STATUSES:
                return None
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET status='RUNNING',started_at=COALESCE(started_at,NOW(3)),
                    error_code=NULL,error_message=NULL
                WHERE job_id=%s
                """,
                (job_id,),
            )
        return dict(row)

    async def _save_steps(
        self, job_id: str, steps: list[dict[str, Any]], *, current_step: str
    ) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET steps_json=%s,current_step=%s
                WHERE job_id=%s
                """,
                (json.dumps(steps, ensure_ascii=False, default=_json_default), current_step, job_id),
            )

    async def _fail(
        self,
        job_id: str,
        steps: list[dict[str, Any]],
        current_step: str,
        exc: Exception,
    ) -> None:
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET status='FAILED',steps_json=%s,current_step=%s,
                    error_code=%s,error_message=%s
                WHERE job_id=%s
                """,
                (
                    json.dumps(steps, ensure_ascii=False, default=_json_default),
                    current_step,
                    type(exc).__name__[:64],
                    str(exc)[:512],
                    job_id,
                ),
            )

    async def _get_owned_row(
        self,
        user_id: str,
        *,
        job_id: str | None,
        key: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        async with acquire() as cur:
            if job_id is not None:
                await cur.execute(
                    """
                    SELECT *,
                           CASE WHEN export_expires_at IS NOT NULL
                                      AND export_expires_at > NOW(3)
                                THEN 1 ELSE 0 END AS export_available
                    FROM user_privacy_job WHERE job_id=%s AND user_id=%s
                    """,
                    (str(job_id), str(user_id)),
                )
            else:
                assert key is not None
                await cur.execute(
                    """
                    SELECT * FROM user_privacy_job
                    WHERE user_id=%s AND job_type=%s AND idempotency_key=%s
                    """,
                    (str(user_id), key[0], key[1]),
                )
            row = await cur.fetchone()
        if not row:
            raise PrivacyJobNotFound("隐私任务不存在")
        return dict(row)

    def _export_handlers(
        self,
    ) -> dict[str, Callable[[str, str], Awaitable[dict[str, Any]]]]:
        return {
            "PREPARE_EXPORT": self._prepare_export,
            "WRITE_EXPORT": self._write_export,
            "FINALIZE_EXPORT": self._finalize_export,
        }

    def _delete_handlers(
        self,
    ) -> dict[str, Callable[[str, str], Awaitable[dict[str, Any]]]]:
        return {
            "REVOKE_EXPORTS": self._revoke_exports,
            "DETACH_RETAINED_FACTS": self._detach_retained_facts,
            "DELETE_SUPPORT_DATA": self._delete_support_data,
            "DELETE_PERSONALIZATION": self._delete_personalization,
            "DELETE_RUNTIME_DATA": self._delete_runtime_data,
            "DELETE_TRACES_MESSAGES": self._delete_traces_messages,
            "CLEAR_CACHES": self._clear_caches,
        }

    async def _prepare_export(self, _job_id: str, _user_id: str) -> dict[str, Any]:
        path = self._export_dir()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return {"directoryReady": True}

    async def _write_export(self, job_id: str, user_id: str) -> dict[str, Any]:
        export = await self._collect_export(user_id)
        destination = self._export_dir() / f"{job_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        payload = json.dumps(
            export,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ).encode("utf-8")
        temporary.write_bytes(payload)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, destination)
        async with acquire() as cur:
            await cur.execute(
                "UPDATE user_privacy_job SET export_path=%s WHERE job_id=%s",
                (str(destination.resolve()), job_id),
            )
        return {"bytes": len(payload), "sections": len(export["data"])}

    async def _finalize_export(self, job_id: str, _user_id: str) -> dict[str, Any]:
        ttl = int(get_settings().privacy_export_ttl_seconds)
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET export_expires_at=DATE_ADD(NOW(3), INTERVAL %s SECOND)
                WHERE job_id=%s AND export_path IS NOT NULL
                """,
                (ttl, job_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("export file was not prepared")
        return {"expiresInSeconds": ttl}

    async def _collect_export(self, user_id: str) -> dict[str, Any]:
        participant_hash = participant_user_hash(user_id)
        queries: dict[str, tuple[str, tuple[Any, ...]]] = {
            "messages": ("SELECT * FROM agent_message WHERE user_id=%s ORDER BY message_id", (user_id,)),
            "feedback": ("SELECT * FROM agent_message_feedback WHERE user_id=%s ORDER BY feedback_id", (user_id,)),
            "sessionMemory": ("SELECT * FROM agent_session_memory WHERE user_id=%s", (user_id,)),
            "shoppingProfile": ("SELECT * FROM agent_shopping_profile WHERE user_id=%s", (user_id,)),
            "shoppingMission": ("SELECT * FROM agent_shopping_mission WHERE user_id=%s", (user_id,)),
            "orderSelections": ("SELECT * FROM agent_order_selection WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "visualSelections": ("SELECT * FROM agent_visual_selection WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "pendingActions": ("SELECT * FROM agent_pending_action WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "recommendationEvents": ("SELECT * FROM agent_recommendation_event WHERE user_id=%s ORDER BY occurred_at", (user_id,)),
            "offerSnapshots": ("SELECT * FROM agent_final_offer_snapshot WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "rankingDecisions": ("SELECT * FROM agent_ranking_policy_decision WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "recommendationExplanations": (
                """SELECT e.* FROM agent_recommendation_explanation e
                   INNER JOIN agent_ranking_policy_decision d ON d.decision_id=e.decision_id
                   WHERE d.user_id=%s ORDER BY e.created_at""",
                (user_id,),
            ),
            "afterSalesEligibility": ("SELECT * FROM agent_after_sales_eligibility WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "runs": ("SELECT * FROM agent_run WHERE user_id=%s ORDER BY started_at", (user_id,)),
            "steps": (
                """SELECT s.* FROM agent_step s
                   INNER JOIN agent_run r ON r.run_id=s.run_id
                   WHERE r.user_id=%s ORDER BY s.occurred_at,s.step_id""",
                (user_id,),
            ),
            "handoffs": (
                """SELECT DISTINCT h.* FROM agent_handoff h
                   LEFT JOIN agent_run p ON p.run_id=h.parent_run_id
                   LEFT JOIN agent_run c ON c.run_id=h.child_run_id
                   WHERE p.user_id=%s OR c.user_id=%s ORDER BY h.created_at""",
                (user_id, user_id),
            ),
            "tasks": ("SELECT * FROM agent_task WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "supportSessions": ("SELECT * FROM support_session WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "supportMessages": (
                """SELECT m.* FROM support_message m
                   INNER JOIN support_session s ON s.session_id=m.session_id
                   WHERE s.user_id=%s ORDER BY m.created_at,m.support_message_id""",
                (user_id,),
            ),
            "supportCases": ("SELECT * FROM support_case WHERE user_id=%s ORDER BY created_at", (user_id,)),
            "commerceOutcomes": ("SELECT * FROM commerce_outcome_ledger WHERE user_id=%s ORDER BY occurred_at", (user_id,)),
            "pilotParticipation": (
                """SELECT participant_id,batch_id,pseudonym,status,consented_at,
                          withdrawn_at,created_at,updated_at
                   FROM agent_pilot_participant WHERE user_id_hash=%s""",
                (participant_hash,),
            ),
        }
        data: dict[str, list[dict[str, Any]]] = {}
        async with transaction() as cur:
            for section, (sql, params) in queries.items():
                await cur.execute(sql, params)
                data[section] = [dict(row) for row in await cur.fetchall()]
        return {
            "schema": "aishop-user-ai-export/v2",
            "generatedAt": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "scope": "Agent-domain data owned by the authenticated user",
            "retainedDataNotice": (
                "Orders, payments, invoices and other legally or operationally required "
                "business records are owned by Java domain services and are not part of this export."
            ),
            "data": data,
        }

    async def _revoke_exports(self, job_id: str, user_id: str) -> dict[str, Any]:
        async with acquire() as cur:
            await cur.execute(
                """
                SELECT job_id,export_path FROM user_privacy_job
                WHERE user_id=%s AND job_type='EXPORT' AND export_path IS NOT NULL
                """,
                (user_id,),
            )
            rows = list(await cur.fetchall())
        removed = 0
        for row in rows:
            path_value = row.get("export_path")
            if not path_value:
                continue
            path = Path(str(path_value)).resolve()
            base = self._export_dir().resolve()
            if path.is_relative_to(base) and path.is_file():
                path.unlink()
                removed += 1
        async with acquire() as cur:
            await cur.execute(
                """
                UPDATE user_privacy_job
                SET export_path=NULL,export_token_hash=NULL,export_expires_at=NOW(3)
                WHERE user_id=%s AND job_type='EXPORT' AND job_id<>%s
                """,
                (user_id, job_id),
            )
        return {"filesRevoked": removed}

    async def _detach_retained_facts(self, _job_id: str, user_id: str) -> dict[str, Any]:
        anonymous = self._anonymous_user_id(user_id)
        async with transaction() as cur:
            await cur.execute(
                """
                UPDATE commerce_outcome_ledger
                SET event_id=CONCAT('privacy_',ledger_id),
                    idempotency_key=CONCAT('privacy_',ledger_id),
                    user_id=%s,request_id=NULL,run_id=NULL,pilot_batch_id=NULL,
                    order_id=NULL,payload_json=JSON_OBJECT('privacyAnonymized',TRUE)
                WHERE user_id=%s
                """,
                (anonymous, user_id),
            )
            changed = int(cur.rowcount)
        return {"anonymizedOutcomes": changed}

    async def _delete_support_data(self, _job_id: str, user_id: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        async with transaction() as cur:
            await cur.execute(
                """DELETE m FROM support_message m
                   INNER JOIN support_session s ON s.session_id=m.session_id
                   WHERE s.user_id=%s""",
                (user_id,),
            )
            counts["supportMessages"] = int(cur.rowcount)
            await cur.execute("DELETE FROM support_case WHERE user_id=%s", (user_id,))
            counts["supportCases"] = int(cur.rowcount)
            await cur.execute("DELETE FROM support_session WHERE user_id=%s", (user_id,))
            counts["supportSessions"] = int(cur.rowcount)
        return counts

    async def _delete_personalization(self, _job_id: str, user_id: str) -> dict[str, Any]:
        statements = (
            (
                "recommendationExplanations",
                """DELETE e FROM agent_recommendation_explanation e
                   INNER JOIN agent_ranking_policy_decision d ON d.decision_id=e.decision_id
                   WHERE d.user_id=%s""",
            ),
            ("rankingDecisions", "DELETE FROM agent_ranking_policy_decision WHERE user_id=%s"),
            ("offerSnapshots", "DELETE FROM agent_final_offer_snapshot WHERE user_id=%s"),
            ("afterSalesEligibility", "DELETE FROM agent_after_sales_eligibility WHERE user_id=%s"),
            ("recommendationEvents", "DELETE FROM agent_recommendation_event WHERE user_id=%s"),
            ("shoppingMissions", "DELETE FROM agent_shopping_mission WHERE user_id=%s"),
            ("shoppingProfiles", "DELETE FROM agent_shopping_profile WHERE user_id=%s"),
            ("sessionMemories", "DELETE FROM agent_session_memory WHERE user_id=%s"),
        )
        counts: dict[str, int] = {}
        async with transaction() as cur:
            for name, sql in statements:
                await cur.execute(sql, (user_id,))
                counts[name] = int(cur.rowcount)
            await cur.execute(
                """
                UPDATE agent_pilot_participant
                SET status='WITHDRAWN',withdrawn_at=COALESCE(withdrawn_at,NOW(3)),
                    user_id_hash=SHA2(CONCAT('privacy:',participant_id),256),
                    user_id_encrypted=NULL
                WHERE user_id_hash=%s
                """,
                (participant_user_hash(user_id),),
            )
            counts["pilotParticipationsWithdrawn"] = int(cur.rowcount)
        return counts

    async def _delete_runtime_data(self, _job_id: str, user_id: str) -> dict[str, Any]:
        statements = (
            ("feedback", "DELETE FROM agent_message_feedback WHERE user_id=%s"),
            ("tasks", "DELETE FROM agent_task WHERE user_id=%s"),
            ("pendingActions", "DELETE FROM agent_pending_action WHERE user_id=%s"),
            ("orderSelections", "DELETE FROM agent_order_selection WHERE user_id=%s"),
            ("visualSelections", "DELETE FROM agent_visual_selection WHERE user_id=%s"),
        )
        counts: dict[str, int] = {}
        async with transaction() as cur:
            for name, sql in statements:
                await cur.execute(sql, (user_id,))
                counts[name] = int(cur.rowcount)
        return counts

    async def _delete_traces_messages(self, _job_id: str, user_id: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        async with transaction() as cur:
            await cur.execute(
                """DELETE b FROM ai_badcase_candidate b
                   LEFT JOIN agent_run r ON r.run_id=b.run_id
                   LEFT JOIN agent_message m ON m.message_id=b.message_id
                   WHERE r.user_id=%s OR m.user_id=%s""",
                (user_id, user_id),
            )
            counts["badcases"] = int(cur.rowcount)
            await cur.execute(
                """DELETE h FROM agent_handoff h
                   LEFT JOIN agent_run p ON p.run_id=h.parent_run_id
                   LEFT JOIN agent_run c ON c.run_id=h.child_run_id
                   WHERE p.user_id=%s OR c.user_id=%s""",
                (user_id, user_id),
            )
            counts["handoffs"] = int(cur.rowcount)
            await cur.execute(
                """DELETE s FROM agent_step s
                   INNER JOIN agent_run r ON r.run_id=s.run_id
                   WHERE r.user_id=%s""",
                (user_id,),
            )
            counts["steps"] = int(cur.rowcount)
            await cur.execute("DELETE FROM agent_run WHERE user_id=%s", (user_id,))
            counts["runs"] = int(cur.rowcount)
            await cur.execute("DELETE FROM agent_message WHERE user_id=%s", (user_id,))
            counts["messages"] = int(cur.rowcount)
        return counts

    async def _clear_caches(self, _job_id: str, user_id: str) -> dict[str, Any]:
        return {"redisKeysDeleted": await redis_service.clear_user_ai_state(user_id)}

    def _export_dir(self) -> Path:
        configured = Path(get_settings().privacy_export_dir).expanduser()
        if configured.is_absolute():
            return configured
        return Path(__file__).resolve().parents[2] / configured

    @staticmethod
    def _anonymous_user_id(user_id: str) -> str:
        secret = get_settings().privacy_export_signing_secret.encode("utf-8")
        digest = hmac.new(secret, user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"anon_{digest[:24]}"


privacy_job_service = PrivacyJobService()
