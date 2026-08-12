from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.routes.attribution import require_internal_token
from app.models.response import ResponseVO, success
from app.services.privacy_job_service import (
    PrivacyExportUnavailable,
    PrivacyJobConflict,
    PrivacyJobNotFound,
    privacy_job_service,
)

router = APIRouter(prefix="/internal/privacy", tags=["internal-privacy"])


def _required(body: dict[str, Any], key: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{key} is required")
    return value


@router.post("/jobs/create")
async def create_job(
    body: dict[str, Any],
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    try:
        result = await privacy_job_service.create(
            user_id=_required(body, "userId"),
            job_type=_required(body, "jobType"),
            idempotency_key=_required(body, "idempotencyKey"),
            request_fingerprint=_required(body, "requestFingerprint"),
        )
    except PrivacyJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return success(result)


@router.post("/jobs/list")
async def list_jobs(
    body: dict[str, Any],
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    result = await privacy_job_service.list(
        _required(body, "userId"), limit=int(body.get("limit") or 20)
    )
    return success(result)


@router.post("/jobs/detail")
async def job_detail(
    body: dict[str, Any],
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    try:
        result = await privacy_job_service.get(
            _required(body, "userId"), _required(body, "jobId")
        )
    except PrivacyJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return success(result)


@router.post("/jobs/retry")
async def retry_job(
    body: dict[str, Any],
    _internal_token: str = Depends(require_internal_token),
) -> ResponseVO:
    try:
        result = await privacy_job_service.retry(
            _required(body, "userId"), _required(body, "jobId")
        )
    except PrivacyJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return success(result)


@router.post("/jobs/download")
async def download_export(
    body: dict[str, Any],
    _internal_token: str = Depends(require_internal_token),
) -> FileResponse:
    try:
        path, filename = await privacy_job_service.download(
            _required(body, "userId"), _required(body, "jobId")
        )
    except PrivacyExportUnavailable as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="application/json",
        filename=filename,
        headers={"Cache-Control": "no-store, private"},
    )
