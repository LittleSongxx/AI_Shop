from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.analytics_result_service import AnalyticsResultError
from app.services.data_analyst_service import _json_size, _page_result_rows
from app.services.sql_guard import AnalyticsAccessPolicy, validate_sql


def _sql() -> str:
    return (
        "SELECT date, gross_paid_amount FROM analytics_sales_daily "
        "WHERE date BETWEEN '2026-08-01' AND '2026-08-07' LIMIT 20"
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        internal_token="foundation-result-cursor-secret",
        analytics_cursor_ttl_seconds=900,
        analytics_export_ttl_seconds=86_400,
        analytics_max_rows=200,
        analytics_export_max_rows=200,
    )


class _MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    @property
    def client(self):
        return self

    async def set_json(self, key: str, value: object, _ttl: int) -> None:
        self.values[key] = copy.deepcopy(value)

    async def get_json(self, key: str):
        return copy.deepcopy(self.values.get(key))

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    async def scan_iter(self, *, match: str, count: int):
        prefix = match.removesuffix("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key


def _install_memory_redis(monkeypatch) -> _MemoryRedis:
    from app.services import analytics_export_service as export_module
    from app.services import analytics_result_service as result_module

    memory = _MemoryRedis()
    monkeypatch.setattr(result_module, "get_settings", _settings)
    monkeypatch.setattr(export_module, "get_settings", _settings)
    monkeypatch.setattr(result_module, "redis_service", memory)
    monkeypatch.setattr(export_module, "redis_service", memory)
    return memory


async def _frozen_result(*, permissions: set[str] | None = None) -> dict:
    from app.services.analytics_result_service import analytics_result_service

    permission_set = permissions or {"analytics:read", "analytics:export"}
    snapshot = await analytics_result_service.freeze(
        admin_id="admin-a",
        permissions=permission_set,
        tenant_id=None,
        data_as_of="2026-08-27T12:00:00.000000+08:00",
        columns=["date", "gross_paid_amount"],
        column_types={
            "date": {"type": "DATE"},
            "gross_paid_amount": {
                "type": "DECIMAL",
                "scale": 2,
                "displayScale": 2,
                "unit": "CNY",
            },
        },
        rows=[
            {"date": "2026-08-01", "gross_paid_amount": "100.25"},
            {"date": "2026-08-02", "gross_paid_amount": "160.50"},
        ],
        branches=[],
        lineage=["analytics_sales_daily"],
        queries=[{"sql": _sql()}],
    )
    assert snapshot is not None
    return snapshot


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
    policy = AnalyticsAccessPolicy.from_permissions({"analytics:read"}, tenant_id="tenant-a")
    result = validate_sql(_sql(), access_policy=policy)
    assert result.reason == "TENANT_SCOPE_REQUIRED"


def test_decimal_normalization_preserves_money_as_fixed_scale_string():
    from app.services.analytics_result_service import normalize_typed_rows

    rows, column_types = normalize_typed_rows(
        [{"date": "2026-08-01", "gross_paid_amount": Decimal("100.2")}],
        view="analytics_sales_daily",
        columns=["date", "gross_paid_amount"],
    )

    assert rows == [{"date": "2026-08-01", "gross_paid_amount": "100.20"}]
    assert column_types["gross_paid_amount"]["type"] == "DECIMAL"
    assert column_types["gross_paid_amount"]["unit"] == "CNY"


@pytest.mark.asyncio
async def test_v2_cursor_pages_frozen_rows_without_query_and_fails_closed(monkeypatch):
    from app.services import analytics_result_service as result_module

    _install_memory_redis(monkeypatch)
    snapshot = await _frozen_result()
    service = result_module.analytics_result_service
    token = service.cursor(snapshot, 1)

    page = await service.page(
        token,
        admin_id="admin-a",
        permissions={"analytics:read", "analytics:export"},
        tenant_id=None,
        page_size=50,
    )
    assert page["rows"] == [{"date": "2026-08-02", "gross_paid_amount": "160.50"}]
    assert page["resultSetId"] == snapshot["resultSetId"]
    assert page["resultHash"] == snapshot["resultHash"]

    with pytest.raises(AnalyticsResultError, match="签名") as tampered:
        await service.page(
            token[:-1] + ("0" if token[-1] != "0" else "1"),
            admin_id="admin-a",
            permissions={"analytics:read", "analytics:export"},
            tenant_id=None,
            page_size=50,
        )
    assert tampered.value.code == "CURSOR_INVALID"

    with pytest.raises(AnalyticsResultError) as owner:
        await service.page(
            token,
            admin_id="admin-b",
            permissions={"analytics:read", "analytics:export"},
            tenant_id=None,
            page_size=50,
        )
    assert owner.value.code == "RESULT_SET_OWNER_MISMATCH"

    monkeypatch.setattr(result_module.time, "time", lambda: snapshot["expiresAt"] + 1)
    with pytest.raises(AnalyticsResultError) as expired:
        await service.page(
            token,
            admin_id="admin-a",
            permissions={"analytics:read", "analytics:export"},
            tenant_id=None,
            page_size=50,
        )
    assert expired.value.code == "RESULT_SNAPSHOT_EXPIRED"
    assert expired.value.http_status == 410


@pytest.mark.asyncio
async def test_result_snapshot_redis_failure_has_explicit_503(monkeypatch):
    from app.services import analytics_result_service as result_module

    _install_memory_redis(monkeypatch)

    async def unavailable(_key: str):
        raise RuntimeError("redis down")

    monkeypatch.setattr(result_module.redis_service, "get_json", unavailable)
    with pytest.raises(AnalyticsResultError) as failure:
        await result_module.analytics_result_service.get(
            "ars_missing",
            admin_id="admin-a",
            permissions={"analytics:read"},
            tenant_id=None,
        )
    assert failure.value.code == "RESULT_SNAPSHOT_UNAVAILABLE"
    assert failure.value.http_status == 503


@pytest.mark.asyncio
async def test_export_is_async_same_result_owner_bound_and_hash_verified(monkeypatch):
    from app.services import analytics_export_service as export_module

    _install_memory_redis(monkeypatch)
    snapshot = await _frozen_result()
    service = export_module.AnalyticsExportService()
    permissions = {"analytics:read", "analytics:export"}

    job = await service.request(
        snapshot["resultSetId"],
        admin_id="admin-a",
        permissions=permissions,
    )
    for _ in range(30):
        current = await service.get(
            job["jobId"],
            admin_id="admin-a",
            permissions=permissions,
            tenant_id=None,
        )
        if current["status"] in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0)

    assert current["status"] == "COMPLETED"
    content = await service.download(
        job["jobId"],
        admin_id="admin-a",
        permissions=permissions,
        tenant_id=None,
    )
    payload = json.loads(content)
    assert payload["resultSetId"] == snapshot["resultSetId"]
    assert payload["resultHash"] == snapshot["resultHash"]
    assert payload["rows"] == snapshot["rows"]
    assert payload["rows"][0]["gross_paid_amount"] == "100.25"
    with pytest.raises(AnalyticsResultError) as owner:
        await service.get(
            job["jobId"],
            admin_id="admin-b",
            permissions=permissions,
            tenant_id=None,
        )
    assert owner.value.code == "EXPORT_OWNER_MISMATCH"
    await service.close()


@pytest.mark.asyncio
async def test_export_resumes_from_embedded_snapshot_without_rerunning_sql(monkeypatch):
    from app.services import analytics_export_service as export_module

    memory = _install_memory_redis(monkeypatch)
    snapshot = await _frozen_result()
    permissions = {"analytics:read", "analytics:export"}
    job_id = "analytics-export-recovery-test"
    memory.values[f"{export_module._JOB_KEY_PREFIX}{job_id}"] = {
        "schemaVersion": "aishop-analytics-export-job/v2",
        "jobId": job_id,
        "status": "RUNNING",
        "ownerScopeHash": export_module.owner_scope_hash("admin-a", permissions, None),
        "resultSetId": snapshot["resultSetId"],
        "resultHash": snapshot["resultHash"],
        "snapshot": snapshot,
        "downloadable": False,
    }
    service = export_module.AnalyticsExportService()

    assert await service.resume_incomplete() == 1
    for _ in range(30):
        current = await service.get(
            job_id,
            admin_id="admin-a",
            permissions=permissions,
            tenant_id=None,
        )
        if current["status"] in {"COMPLETED", "FAILED"}:
            break
        await asyncio.sleep(0)
    assert current["status"] == "COMPLETED"
    assert current["recoveryCount"] == 1
    assert await service.download(
        job_id,
        admin_id="admin-a",
        permissions=permissions,
        tenant_id=None,
    )
    await service.close()


@pytest.mark.asyncio
async def test_export_requires_result_set_and_export_permission(monkeypatch):
    from app.services.analytics_export_service import AnalyticsExportService

    _install_memory_redis(monkeypatch)
    service = AnalyticsExportService()
    with pytest.raises(AnalyticsResultError) as permission:
        await service.request(
            "ars_test",
            admin_id="admin-a",
            permissions={"analytics:read"},
        )
    assert permission.value.code == "ANALYTICS_EXPORT_PERMISSION_DENIED"
    assert permission.value.http_status == 403

    with pytest.raises(AnalyticsResultError) as missing:
        await service.request(
            "",
            admin_id="admin-a",
            permissions={"analytics:read", "analytics:export"},
        )
    assert missing.value.code == "RESULT_SET_ID_REQUIRED"
    assert missing.value.http_status == 400


def test_result_byte_budget_fails_closed():
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
