from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.config.settings import get_settings
from app.services.data_analyst_service import data_analyst_service
from app.services.redis_service import redis_service

_KEY_PREFIX = "aishop:analytics:export:v1:"
_LOCAL_JOBS: dict[str, dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalyticsExportService:
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
            if key not in {"adminIdHash", "filePath", "question"}
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
            "adminIdHash": hashlib.sha256(str(admin_id).encode("utf-8")).hexdigest(),
            "question": normalized_question,
            "questionHash": hashlib.sha256(normalized_question.encode("utf-8")).hexdigest(),
            "createdAt": _now(),
            "updatedAt": _now(),
            "rowCount": 0,
            "bytes": 0,
            "downloadable": False,
        }
        await self._write(job)
        asyncio.create_task(
            self._run(
                job_id,
                admin_id=admin_id,
                permissions=permission_set,
                tenant_id=tenant_id,
            )
        )
        return self._public(job)

    async def _run(
        self,
        job_id: str,
        *,
        admin_id: str,
        permissions: set[str],
        tenant_id: str | None,
    ) -> None:
        job = await self._read(job_id)
        if not job:
            return
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
