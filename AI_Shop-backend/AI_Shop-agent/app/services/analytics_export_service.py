from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import structlog

from app.config.settings import get_settings
from app.services.analytics_result_service import (
    AnalyticsResultError,
    analytics_result_service,
    owner_scope_hash,
    result_hash,
)
from app.services.redis_service import redis_service

_JOB_KEY_PREFIX = "aishop:analytics:export:job:v2:"
_ARTIFACT_KEY_PREFIX = "aishop:analytics:export:artifact:v2:"
_RUNNABLE_STATUSES = frozenset({"PENDING", "RUNNING"})
logger = structlog.get_logger()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at() -> str:
    ttl = int(get_settings().analytics_export_ttl_seconds)
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


class AnalyticsExportService:
    """Build asynchronous JSON artifacts from an existing frozen result only."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"{_JOB_KEY_PREFIX}{job_id}"

    @staticmethod
    def _artifact_key(job_id: str) -> str:
        return f"{_ARTIFACT_KEY_PREFIX}{job_id}"

    async def _read_job(self, job_id: str) -> dict[str, Any] | None:
        try:
            value = await redis_service.get_json(self._job_key(job_id))
        except Exception as exc:
            raise AnalyticsResultError(
                "EXPORT_STATE_UNAVAILABLE", 503, "导出任务状态服务暂不可用"
            ) from exc
        return value if isinstance(value, dict) else None

    async def _write_job(self, job: dict[str, Any]) -> None:
        try:
            await redis_service.set_json(
                self._job_key(str(job["jobId"])),
                job,
                int(get_settings().analytics_export_ttl_seconds),
            )
        except Exception as exc:
            raise AnalyticsResultError(
                "EXPORT_STATE_UNAVAILABLE", 503, "导出任务状态服务暂不可用"
            ) from exc

    async def _write_artifact(self, job_id: str, content: bytes) -> None:
        artifact = {
            "schemaVersion": "aishop-analytics-export-artifact/v2",
            "jobId": job_id,
            "contentType": "application/json",
            "contentBase64": base64.b64encode(content).decode("ascii"),
            "contentSha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "createdAt": _now(),
            "expiresAt": _expires_at(),
        }
        try:
            await redis_service.set_json(
                self._artifact_key(job_id),
                artifact,
                int(get_settings().analytics_export_ttl_seconds),
            )
        except Exception as exc:
            raise AnalyticsResultError(
                "EXPORT_ARTIFACT_UNAVAILABLE", 503, "导出工件服务暂不可用"
            ) from exc

    async def _read_artifact(self, job_id: str) -> dict[str, Any] | None:
        try:
            value = await redis_service.get_json(self._artifact_key(job_id))
        except Exception as exc:
            raise AnalyticsResultError(
                "EXPORT_ARTIFACT_UNAVAILABLE", 503, "导出工件服务暂不可用"
            ) from exc
        return value if isinstance(value, dict) else None

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in job.items() if key not in {"ownerScopeHash", "snapshot"}
        }

    @staticmethod
    def _assert_owner(
        job: dict[str, Any],
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
    ) -> None:
        expected = owner_scope_hash(admin_id, permissions, tenant_id)
        if job.get("ownerScopeHash") != expected:
            raise AnalyticsResultError("EXPORT_OWNER_MISMATCH", 403, "导出任务不属于当前管理员范围")

    async def request(
        self,
        result_set_id: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        permission_set = {str(item).strip() for item in permissions if str(item).strip()}
        if "analytics:export" not in permission_set:
            raise AnalyticsResultError(
                "ANALYTICS_EXPORT_PERMISSION_DENIED", 403, "缺少分析导出权限"
            )
        normalized_result_set_id = str(result_set_id or "").strip()
        if not normalized_result_set_id:
            raise AnalyticsResultError("RESULT_SET_ID_REQUIRED", 400, "导出必须提供 resultSetId")
        snapshot = await analytics_result_service.get(
            normalized_result_set_id,
            admin_id=admin_id,
            permissions=permission_set,
            tenant_id=tenant_id,
        )
        rows = list(snapshot.get("rows") or [])
        if len(rows) > int(get_settings().analytics_max_rows):
            raise AnalyticsResultError("RESULT_SET_TOO_LARGE", 409, "冻结结果超过 V0 最大行数")
        actual_hash = result_hash(
            columns=list(snapshot.get("columns") or []),
            column_types=dict(snapshot.get("columnTypes") or {}),
            rows=rows,
        )
        if actual_hash != snapshot.get("resultHash"):
            raise AnalyticsResultError("RESULT_HASH_MISMATCH", 409, "冻结结果完整性校验失败")

        job_id = f"analytics-export-{uuid.uuid4().hex}"
        job = {
            "schemaVersion": "aishop-analytics-export-job/v2",
            "jobId": job_id,
            "status": "PENDING",
            "ownerScopeHash": owner_scope_hash(admin_id, permission_set, tenant_id),
            "resultSetId": normalized_result_set_id,
            "resultHash": actual_hash,
            "catalogVersion": snapshot.get("catalogVersion"),
            "dataAsOf": snapshot.get("dataAsOf"),
            "createdAt": _now(),
            "updatedAt": _now(),
            "expiresAt": _expires_at(),
            "rowCount": len(rows),
            "bytes": 0,
            "downloadable": False,
            # Self-contained restart context after the 15-minute result TTL.
            # It is the exact typed result and contains no question to rerun.
            "snapshot": snapshot,
        }
        await self._write_job(job)
        self.schedule(job_id)
        return self._public(job)

    def schedule(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._run_guarded(job_id), name=f"analytics-export:{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task, key=job_id: self._tasks.pop(key, None))

    async def resume_incomplete(self, *, limit: int = 100) -> int:
        try:
            keys = [
                str(key)
                async for key in redis_service.client.scan_iter(
                    match=f"{_JOB_KEY_PREFIX}*", count=100
                )
            ]
        except Exception as exc:
            logger.warning("analytics_export_recovery_scan_failed", error=type(exc).__name__)
            return 0

        recovered = 0
        for key in keys[: max(1, min(int(limit), 1000))]:
            job_id = key.removeprefix(_JOB_KEY_PREFIX)
            try:
                job = await self._read_job(job_id)
            except AnalyticsResultError:
                continue
            if not job or job.get("status") not in _RUNNABLE_STATUSES:
                continue
            if not self._has_recovery_context(job):
                job.update(
                    {
                        "status": "FAILED",
                        "updatedAt": _now(),
                        "errorCode": "RECOVERY_CONTEXT_MISSING",
                        "errorMessage": "任务缺少冻结结果，不能重新执行 SQL",
                        "downloadable": False,
                    }
                )
                try:
                    await self._write_job(job)
                except AnalyticsResultError:
                    pass
                continue
            job.update(
                {
                    "status": "PENDING",
                    "updatedAt": _now(),
                    "recoveredAt": _now(),
                    "recoveryCount": int(job.get("recoveryCount") or 0) + 1,
                }
            )
            try:
                await self._write_job(job)
            except AnalyticsResultError:
                continue
            self.schedule(job_id)
            recovered += 1
        return recovered

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @staticmethod
    def _has_recovery_context(job: dict[str, Any]) -> bool:
        snapshot = job.get("snapshot")
        return bool(
            isinstance(snapshot, dict)
            and snapshot.get("resultSetId") == job.get("resultSetId")
            and snapshot.get("resultHash") == job.get("resultHash")
            and isinstance(snapshot.get("rows"), list)
        )

    async def _run_guarded(self, job_id: str) -> None:
        try:
            await self._run(job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "analytics_export_task_failed",
                job_id=job_id,
                error=type(exc).__name__,
            )

    async def _run(self, job_id: str) -> None:
        job = await self._read_job(job_id)
        if not job:
            return
        if not self._has_recovery_context(job):
            job.update(
                {
                    "status": "FAILED",
                    "updatedAt": _now(),
                    "errorCode": "RECOVERY_CONTEXT_MISSING",
                    "errorMessage": "任务缺少冻结结果，不能重新执行 SQL",
                    "downloadable": False,
                }
            )
            await self._write_job(job)
            return

        job["status"] = "RUNNING"
        job["updatedAt"] = _now()
        await self._write_job(job)
        try:
            snapshot = dict(job["snapshot"])
            rows = list(snapshot.get("rows") or [])
            actual_hash = result_hash(
                columns=list(snapshot.get("columns") or []),
                column_types=dict(snapshot.get("columnTypes") or {}),
                rows=rows,
            )
            if actual_hash != job.get("resultHash"):
                raise RuntimeError("RESULT_HASH_MISMATCH")
            payload = {
                "schemaVersion": "aishop-analytics-export/v2",
                "jobId": job_id,
                "resultSetId": snapshot.get("resultSetId"),
                "resultHash": actual_hash,
                "catalogVersion": snapshot.get("catalogVersion"),
                "catalogContentSha256": snapshot.get("catalogContentSha256"),
                "dataAsOf": snapshot.get("dataAsOf"),
                "columns": snapshot.get("columns") or [],
                "columnTypes": snapshot.get("columnTypes") or {},
                "rows": rows,
                "branches": snapshot.get("branches") or [],
                "queries": snapshot.get("queries") or [],
                "lineage": snapshot.get("lineage") or [],
            }
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                separators=(",", ": "),
            ).encode("utf-8")
            await self._write_artifact(job_id, encoded)
            job.update(
                {
                    "status": "COMPLETED",
                    "updatedAt": _now(),
                    "completedAt": _now(),
                    "rowCount": len(rows),
                    "bytes": len(encoded),
                    "contentSha256": hashlib.sha256(encoded).hexdigest(),
                    "downloadable": True,
                }
            )
        except Exception as exc:
            job.update(
                {
                    "status": "FAILED",
                    "updatedAt": _now(),
                    "errorCode": str(exc) if str(exc) else type(exc).__name__,
                    "errorMessage": str(exc)[:300],
                    "downloadable": False,
                }
            )
        await self._write_job(job)

    async def get(
        self,
        job_id: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
    ) -> dict[str, Any]:
        job = await self._read_job(str(job_id or "").strip())
        if not job:
            raise AnalyticsResultError("EXPORT_NOT_FOUND", 410, "导出任务不存在或已过期")
        self._assert_owner(
            job,
            admin_id=admin_id,
            permissions=permissions,
            tenant_id=tenant_id,
        )
        return self._public(job)

    async def download(
        self,
        job_id: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
    ) -> bytes:
        job = await self._read_job(str(job_id or "").strip())
        if not job:
            raise AnalyticsResultError("EXPORT_NOT_FOUND", 410, "导出任务不存在或已过期")
        self._assert_owner(
            job,
            admin_id=admin_id,
            permissions=permissions,
            tenant_id=tenant_id,
        )
        if job.get("status") != "COMPLETED" or not job.get("downloadable"):
            raise AnalyticsResultError("EXPORT_NOT_READY", 409, "导出工件尚未生成")
        artifact = await self._read_artifact(str(job["jobId"]))
        if not artifact:
            raise AnalyticsResultError("EXPORT_ARTIFACT_EXPIRED", 410, "导出工件不存在或已过期")
        try:
            content = base64.b64decode(str(artifact["contentBase64"]), validate=True)
        except Exception as exc:
            raise AnalyticsResultError("EXPORT_ARTIFACT_CORRUPT", 503, "导出工件损坏") from exc
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.get("contentSha256") or digest != job.get("contentSha256"):
            raise AnalyticsResultError("EXPORT_ARTIFACT_CORRUPT", 503, "导出工件完整性校验失败")
        return content


analytics_export_service = AnalyticsExportService()
