from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import structlog

from app.config.settings import get_settings
from app.services.data_analyst_service import data_analyst_service
from app.services.redis_service import redis_service

_KEY_PREFIX = "aishop:analytics:export:v1:"
_LOCAL_JOBS: dict[str, dict] = {}
_RUNNABLE_STATUSES = frozenset({"PENDING", "RUNNING"})
logger = structlog.get_logger()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalyticsExportService:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def _read(self, job_id: str) -> dict | None:
        try:
            value = await redis_service.get_json(f"{_KEY_PREFIX}{job_id}")
            if isinstance(value, dict):
                return value
        except Exception:
            pass
        value = _LOCAL_JOBS.get(job_id)
        return dict(value) if isinstance(value, dict) else None

    async def _write(self, job: dict) -> None:
        _LOCAL_JOBS[str(job["jobId"])] = dict(job)
        try:
            ttl = int(getattr(get_settings(), "analytics_cursor_ttl_seconds", 900))
            await redis_service.set_json(f"{_KEY_PREFIX}{job['jobId']}", job, max(ttl, 900))
        except Exception:
            return

    @staticmethod
    def _public(job: dict) -> dict:
        return {
            key: value
            for key, value in job.items()
            if key
            not in {
                "adminId",
                "adminIdHash",
                "filePath",
                "permissions",
                "question",
                "tenantId",
            }
        }

    @staticmethod
    def _assert_owner(job: dict, admin_id: str) -> None:
        owner = hashlib.sha256(str(admin_id).encode("utf-8")).hexdigest()
        if job.get("adminIdHash") != owner:
            raise PermissionError("analytics export owner mismatch")

    async def request(
        self,
        question: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None = None,
    ) -> dict:
        permission_set = {str(item).strip() for item in permissions}
        if "analytics:export" not in permission_set:
            raise PermissionError("analytics export permission denied")
        normalized_question = str(question or "").strip()
        if not normalized_question or len(normalized_question) > 500:
            raise ValueError("问题不能为空且不超过500字")
        job_id = f"analytics-export-{uuid.uuid4().hex}"
        job = {
            "jobId": job_id,
            "status": "PENDING",
            "adminId": str(admin_id),
            "adminIdHash": hashlib.sha256(str(admin_id).encode("utf-8")).hexdigest(),
            "permissions": sorted(permission_set),
            "tenantId": str(tenant_id) if tenant_id is not None else None,
            "question": normalized_question,
            "questionHash": hashlib.sha256(normalized_question.encode("utf-8")).hexdigest(),
            "createdAt": _now(),
            "updatedAt": _now(),
            "rowCount": 0,
            "bytes": 0,
            "downloadable": False,
        }
        await self._write(job)
        self.schedule(job_id)
        return self._public(job)

    def schedule(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._run_guarded(job_id), name=f"analytics-export:{job_id}"
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _task, key=job_id: self._tasks.pop(key, None))

    async def resume_incomplete(self, *, limit: int = 100) -> int:
        jobs: dict[str, dict] = {
            job_id: dict(job)
            for job_id, job in _LOCAL_JOBS.items()
            if isinstance(job, dict)
        }
        try:
            keys = [
                key
                async for key in redis_service.client.scan_iter(
                    match=f"{_KEY_PREFIX}*", count=100
                )
            ]
            for key in keys[: max(1, min(int(limit), 1000))]:
                value = await redis_service.get_json(str(key))
                if isinstance(value, dict) and value.get("jobId"):
                    jobs[str(value["jobId"])] = value
        except Exception as exc:
            logger.warning(
                "analytics_export_recovery_scan_failed", error=type(exc).__name__
            )

        recovered = 0
        for job in list(jobs.values())[: max(1, min(int(limit), 1000))]:
            if job.get("status") not in _RUNNABLE_STATUSES:
                continue
            job_id = str(job.get("jobId") or "")
            if not job_id:
                continue
            if not self._has_recovery_context(job):
                job.update(
                    {
                        "status": "FAILED",
                        "updatedAt": _now(),
                        "errorCode": "RECOVERY_CONTEXT_MISSING",
                        "errorMessage": "任务缺少可恢复的执行上下文，请重新提交",
                        "downloadable": False,
                    }
                )
                await self._write(job)
                continue
            job.update(
                {
                    "status": "PENDING",
                    "updatedAt": _now(),
                    "recoveredAt": _now(),
                    "recoveryCount": int(job.get("recoveryCount") or 0) + 1,
                }
            )
            await self._write(job)
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
    def _has_recovery_context(job: dict) -> bool:
        return bool(
            str(job.get("adminId") or "").strip()
            and str(job.get("question") or "").strip()
            and isinstance(job.get("permissions"), list)
            and "analytics:export" in job.get("permissions", [])
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
        job = await self._read(job_id)
        if not job:
            return
        if not self._has_recovery_context(job):
            job.update(
                {
                    "status": "FAILED",
                    "updatedAt": _now(),
                    "errorCode": "RECOVERY_CONTEXT_MISSING",
                    "errorMessage": "任务缺少可恢复的执行上下文，请重新提交",
                    "downloadable": False,
                }
            )
            await self._write(job)
            return
        admin_id = str(job["adminId"])
        permissions = {str(item) for item in job.get("permissions", [])}
        tenant_id = job.get("tenantId")
        job["status"] = "RUNNING"
        job["updatedAt"] = _now()
        await self._write(job)
        try:
            result = await data_analyst_service.ask(
                job["question"],
                admin_id=admin_id,
                permissions=permissions,
                tenant_id=tenant_id,
                page_size=int(getattr(get_settings(), "analytics_export_max_rows", 10_000)),
            )
            if result.get("status") not in {"SUCCEEDED", "EMPTY_RESULT"}:
                raise RuntimeError(str(result.get("status") or "ANALYTICS_EXPORT_FAILED"))
            rows = list(result.get("rows") or [])
            max_rows = int(getattr(get_settings(), "analytics_export_max_rows", 10_000))
            rows = rows[:max_rows]
            payload = {
                "schemaVersion": "aishop-analytics-export/v1",
                "jobId": job_id,
                "status": result.get("status"),
                "sql": result.get("sql"),
                "sqlHash": hashlib.sha256(str(result.get("sql") or "").encode()).hexdigest(),
                "columns": result.get("columns") or [],
                "rows": rows,
                "lineage": result.get("lineage") or [],
                "explain": result.get("explain") or [],
                "warnings": result.get("warnings") or [],
                "answer": result.get("answer"),
                "runId": result.get("runId"),
            }
            base = Path(getattr(get_settings(), "privacy_export_dir", ".privacy-exports")) / "analytics"
            base.mkdir(parents=True, exist_ok=True)
            path = (base / f"{job_id}.json").resolve()
            if base.resolve() not in path.parents:
                raise RuntimeError("invalid analytics export path")
            encoded = json.dumps(payload, ensure_ascii=False, default=str, indent=2).encode("utf-8")
            path.write_bytes(encoded)
            job.update(
                {
                    "status": "COMPLETED",
                    "updatedAt": _now(),
                    "completedAt": _now(),
                    "rowCount": len(rows),
                    "bytes": len(encoded),
                    "filePath": str(path),
                    "downloadable": True,
                    "sqlHash": payload["sqlHash"],
                    "lineage": payload["lineage"],
                    "warnings": payload["warnings"],
                }
            )
        except Exception as exc:
            job.update(
                {
                    "status": "FAILED",
                    "updatedAt": _now(),
                    "errorCode": type(exc).__name__,
                    "errorMessage": str(exc)[:300],
                    "downloadable": False,
                }
            )
        await self._write(job)

    async def get(self, job_id: str, *, admin_id: str) -> dict:
        job = await self._read(job_id)
        if not job:
            raise LookupError("analytics export not found")
        self._assert_owner(job, admin_id)
        return self._public(job)

    async def download(self, job_id: str, *, admin_id: str) -> bytes:
        job = await self._read(job_id)
        if not job:
            raise LookupError("analytics export not found")
        self._assert_owner(job, admin_id)
        if job.get("status") != "COMPLETED" or not job.get("filePath"):
            raise RuntimeError("analytics export is not ready")
        path = Path(str(job["filePath"])).resolve()
        base = Path(getattr(get_settings(), "privacy_export_dir", ".privacy-exports"), "analytics").resolve()
        if base not in path.parents or not path.is_file():
            raise RuntimeError("analytics export is unavailable")
        return path.read_bytes()


analytics_export_service = AnalyticsExportService()
