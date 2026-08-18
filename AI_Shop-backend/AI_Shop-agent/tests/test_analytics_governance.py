from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.data_analyst_service import (
    _decode_result_cursor,
    _encode_result_cursor,
    _json_size,
    _page_result_rows,
)
from app.services.sql_guard import AnalyticsAccessPolicy, validate_sql


def _sql() -> str:
    return (
        "SELECT date, gross_paid_amount FROM analytics_sales_daily "
        "WHERE date BETWEEN '2026-08-01' AND '2026-08-07' LIMIT 20"
    )


def test_analytics_access_policy_enforces_read_view_and_column_permissions():
    denied = validate_sql(_sql(), access_policy=AnalyticsAccessPolicy())
    assert denied.reason == "SQL_PERMISSION_DENIED"

    policy = AnalyticsAccessPolicy.from_permissions(
        {
            "analytics:read",
            "analytics:view:analytics_sales_daily",
            "analytics:column:analytics_sales_daily:date",
            "analytics:column:analytics_sales_daily:gross_paid_amount",
        }
    )
    assert validate_sql(_sql(), access_policy=policy).allowed
    other_view = validate_sql(
        _sql().replace("analytics_sales_daily", "analytics_inventory_risk"),
        access_policy=policy,
    )
    assert other_view.reason in {"SQL_VIEW_PERMISSION_DENIED", "SQL_VIEW_NOT_ALLOWLISTED"}


def test_tenant_scoped_analytics_fails_closed():
    policy = AnalyticsAccessPolicy.from_permissions(
        {"analytics:read"}, tenant_id="tenant-a"
    )
    result = validate_sql(_sql(), access_policy=policy)
    assert result.reason == "TENANT_SCOPE_REQUIRED"


@pytest.mark.asyncio
async def test_analytics_export_is_async_audited_and_owner_bound(monkeypatch, tmp_path):
    from app.services import analytics_export_service as export_module

    settings = SimpleNamespace(
        privacy_export_dir=str(tmp_path),
        analytics_cursor_ttl_seconds=900,
        analytics_export_max_rows=10_000,
    )
    monkeypatch.setattr(export_module, "get_settings", lambda: settings)
    monkeypatch.setattr(export_module.redis_service, "get_json", AsyncMock(side_effect=RuntimeError("redis unavailable")))
    monkeypatch.setattr(export_module.redis_service, "set_json", AsyncMock(side_effect=RuntimeError("redis unavailable")))
    monkeypatch.setattr(
        export_module.data_analyst_service,
        "ask",
        AsyncMock(
            return_value={
                "runId": "run-1",
                "status": "SUCCEEDED",
                "sql": _sql(),
                "columns": ["date", "gross_paid_amount"],
                "rows": [{"date": date(2026, 8, 1), "gross_paid_amount": 100}],
                "lineage": ["analytics_sales_daily"],
                "explain": [{"type": "range"}],
                "warnings": [],
                "answer": "完成",
            }
        ),
    )
    service = export_module.AnalyticsExportService()

    job = await service.request(
        "最近七天销售额",
        admin_id="admin-a",
        permissions={"analytics:read", "analytics:export"},
    )
    for _ in range(20):
        current = await service.get(job["jobId"], admin_id="admin-a")
        if current["status"] in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0)
    assert current["status"] == "COMPLETED"
    content = await service.download(job["jobId"], admin_id="admin-a")
    payload = json.loads(content)
    assert payload["sqlHash"]
    assert payload["lineage"] == ["analytics_sales_daily"]
    with pytest.raises(PermissionError):
        await service.get(job["jobId"], admin_id="admin-b")


@pytest.mark.asyncio
async def test_analytics_export_requires_export_permission():
    from app.services.analytics_export_service import AnalyticsExportService

    with pytest.raises(PermissionError):
        await AnalyticsExportService().request(
            "最近七天销售额",
            admin_id="admin-a",
            permissions={"analytics:read"},
        )


def test_analytics_cursor_and_result_budget_fail_closed():
    settings = SimpleNamespace(internal_token="cursor-secret", analytics_cursor_ttl_seconds=900)
    token = _encode_result_cursor(
        settings=settings,
        admin_id="admin-a",
        sql_hash="sql-hash",
        offset=10,
    )
    assert _decode_result_cursor(
        token,
        settings=settings,
        admin_id="admin-a",
        sql_hash="sql-hash",
    ) == (10, None)
    assert _decode_result_cursor(
        token,
        settings=settings,
        admin_id="admin-b",
        sql_hash="sql-hash",
    ) == (None, "CURSOR_OWNER_MISMATCH")

    malformed_payload = base64.urlsafe_b64encode(
        b'{"v":1,"owner":"x","sqlHash":"sql-hash","offset":"bad","expiresAt":"bad"}'
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.internal_token.encode("utf-8"),
        malformed_payload.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert _decode_result_cursor(
        f"{malformed_payload}.{signature}",
        settings=settings,
        admin_id="admin-a",
        sql_hash="sql-hash",
    ) == (None, "CURSOR_INVALID")

    rows = [{"value": "a" * 20}, {"value": "b" * 20}]
    first_size = _json_size([rows[0]])
    total_size = _json_size(rows)
    page, byte_limited, row_too_large = _page_result_rows(
        rows,
        offset=0,
        page_size=10,
        max_bytes=(first_size + total_size) // 2,
    )
    assert page == [rows[0]]
    assert byte_limited is True
    assert row_too_large is False
    assert _page_result_rows(rows, offset=0, page_size=10, max_bytes=1)[2] is True
