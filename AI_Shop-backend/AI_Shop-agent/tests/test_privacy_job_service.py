from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.privacy_job_service import (
    PrivacyJobConflict,
    PrivacyJobNotFound,
    PrivacyJobService,
)


class _Cursor:
    def __init__(self, rows: list[dict | None]):
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple | None]] = []
        self.rowcount = 1

    async def execute(self, sql: str, params: tuple | None = None):
        self.calls.append((sql, params))

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    async def fetchall(self):
        row = self.rows.pop(0) if self.rows else []
        return row or []


def _context(cursor: _Cursor):
    @asynccontextmanager
    async def context():
        yield cursor

    return context


def _job(**overrides) -> dict:
    row = {
        "job_id": "privacy-1",
        "user_id": "u1",
        "job_type": "EXPORT",
        "idempotency_key": "key-1",
        "request_fingerprint": "a" * 64,
        "status": "PENDING",
        "steps_json": [{"name": "PREPARE_EXPORT", "status": "PENDING"}],
        "retry_count": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_returns_the_original_job():
    cursor = _Cursor([_job()])
    service = PrivacyJobService()
    with patch("app.services.privacy_job_service.transaction", _context(cursor)):
        result = await service.create(
            user_id="u1",
            job_type="EXPORT",
            idempotency_key="key-1",
            request_fingerprint="a" * 64,
            schedule=False,
        )

    assert result["jobId"] == "privacy-1"
    assert not any("INSERT INTO user_privacy_job" in sql for sql, _ in cursor.calls)


@pytest.mark.asyncio
async def test_reused_idempotency_key_with_different_fingerprint_conflicts():
    cursor = _Cursor([_job()])
    service = PrivacyJobService()
    with patch("app.services.privacy_job_service.transaction", _context(cursor)):
        with pytest.raises(PrivacyJobConflict):
            await service.create(
                user_id="u1",
                job_type="EXPORT",
                idempotency_key="key-1",
                request_fingerprint="b" * 64,
                schedule=False,
            )


@pytest.mark.asyncio
async def test_job_detail_is_scoped_to_the_authenticated_user():
    cursor = _Cursor([None])
    service = PrivacyJobService()
    with patch("app.services.privacy_job_service.acquire", _context(cursor)):
        with pytest.raises(PrivacyJobNotFound):
            await service.get("attacker", "privacy-1")

    sql, params = cursor.calls[0]
    assert "job_id=%s AND user_id=%s" in sql
    assert params == ("privacy-1", "attacker")


@pytest.mark.asyncio
async def test_retry_resumes_after_completed_steps():
    steps = [
        {"name": "PREPARE_EXPORT", "status": "COMPLETED", "attempts": 1},
        {"name": "WRITE_EXPORT", "status": "FAILED", "attempts": 1},
        {"name": "FINALIZE_EXPORT", "status": "PENDING", "attempts": 0},
    ]
    service = PrivacyJobService()
    service._claim = AsyncMock(return_value=_job(status="FAILED", steps_json=steps))
    prepare = AsyncMock()
    write = AsyncMock(return_value={"bytes": 10})
    finalize = AsyncMock(return_value={"expiresInSeconds": 60})
    service._export_handlers = lambda: {
        "PREPARE_EXPORT": prepare,
        "WRITE_EXPORT": write,
        "FINALIZE_EXPORT": finalize,
    }
    service._save_steps = AsyncMock()
    cursor = _Cursor([])

    with patch("app.services.privacy_job_service.acquire", _context(cursor)):
        await service.execute("privacy-1")

    prepare.assert_not_awaited()
    write.assert_awaited_once_with("privacy-1", "u1")
    finalize.assert_awaited_once_with("privacy-1", "u1")
    assert "status='COMPLETED'" in cursor.calls[-1][0]


@pytest.mark.asyncio
async def test_retained_commerce_facts_are_anonymized_and_detached():
    cursor = _Cursor([])
    service = PrivacyJobService()
    with (
        patch("app.services.privacy_job_service.transaction", _context(cursor)),
        patch.object(service, "_anonymous_user_id", return_value="anon-1"),
    ):
        result = await service._detach_retained_facts("privacy-1", "u1")

    sql, params = cursor.calls[0]
    assert "run_id=NULL" in sql
    assert "pilot_batch_id=NULL" in sql
    assert "order_id=NULL" in sql
    assert "payload_json=JSON_OBJECT('privacyAnonymized',TRUE)" in sql
    assert params == ("anon-1", "u1")
    assert result == {"anonymizedOutcomes": 1}
