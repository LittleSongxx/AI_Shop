from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import structlog

from app.config.settings import get_settings
from app.services.analytics_catalog import (
    CATALOG_CONTENT_SHA256,
    CATALOG_VERSION,
    column_contract,
)
from app.services.redis_service import redis_service

_RESULT_KEY_PREFIX = "aishop:analytics:result:v2:"
_CURSOR_VERSION = 2
logger = structlog.get_logger()


class AnalyticsResultError(RuntimeError):
    def __init__(self, code: str, http_status: int, message: str):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def owner_scope_hash(
    admin_id: str,
    permissions: Iterable[str],
    tenant_id: str | None,
) -> str:
    payload = {
        "adminId": str(admin_id),
        "permissions": sorted({str(item).strip() for item in permissions if str(item).strip()}),
        "tenantId": str(tenant_id or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _cursor_secret() -> bytes:
    return str(get_settings().internal_token).encode("utf-8")


def _encode_cursor(
    *,
    result_set_id: str,
    scope_hash: str,
    offset: int,
    expires_at: int,
) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "resultSetId": result_set_id,
        "ownerScopeHash": scope_hash,
        "offset": max(0, int(offset)),
        "expiresAt": int(expires_at),
    }
    encoded = base64.urlsafe_b64encode(_canonical_bytes(payload)).decode("ascii").rstrip("=")
    signature = hmac.new(_cursor_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_cursor(token: str, *, expected_scope_hash: str) -> dict[str, Any]:
    encoded, separator, signature = str(token or "").partition(".")
    if not separator or not encoded or not signature:
        raise AnalyticsResultError("CURSOR_INVALID", 400, "分页游标无效")
    expected = hmac.new(_cursor_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AnalyticsResultError("CURSOR_INVALID", 400, "分页游标签名无效")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise AnalyticsResultError("CURSOR_INVALID", 400, "分页游标无效") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise AnalyticsResultError("CURSOR_VERSION_UNSUPPORTED", 400, "分页游标版本不受支持")
    try:
        expires_at = int(payload.get("expiresAt") or 0)
        offset = int(payload.get("offset"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalyticsResultError("CURSOR_INVALID", 400, "分页游标无效") from exc
    if expires_at < int(time.time()):
        raise AnalyticsResultError("RESULT_SNAPSHOT_EXPIRED", 410, "冻结结果已过期")
    if payload.get("ownerScopeHash") != expected_scope_hash:
        raise AnalyticsResultError("RESULT_SET_OWNER_MISMATCH", 403, "冻结结果不属于当前管理员范围")
    if offset < 0 or not str(payload.get("resultSetId") or ""):
        raise AnalyticsResultError("CURSOR_INVALID", 400, "分页游标无效")
    return payload


def _decimal_string(value: Any, contract: dict[str, Any]) -> str:
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid DECIMAL result value: {value!r}") from exc
    scale = contract.get("displayScale")
    if scale is None:
        scale = contract.get("scale")
    if scale is None:
        return format(decimal, "f")
    places = max(0, int(scale))
    quantum = Decimal(1).scaleb(-places)
    return format(decimal.quantize(quantum), f".{places}f")


def _inferred_contract(column: str, values: list[Any]) -> dict[str, Any]:
    value = next((item for item in values if item is not None), None)
    if isinstance(value, Decimal):
        money = any(token in column.lower() for token in ("amount", "price", "payable", "cost_cny"))
        return {
            "type": "DECIMAL",
            "scale": 2 if money else max(0, -value.as_tuple().exponent),
            **({"displayScale": 2, "unit": "CNY"} if money else {}),
        }
    if isinstance(value, bool):
        return {"type": "BOOLEAN"}
    if isinstance(value, int):
        return {"type": "INTEGER"}
    if isinstance(value, float):
        return {"type": "DECIMAL", "scale": 8}
    if isinstance(value, datetime):
        return {"type": "DATETIME"}
    if isinstance(value, date):
        return {"type": "DATE"}
    return {"type": "STRING"}


def normalize_typed_rows(
    rows: list[dict[str, Any]],
    *,
    view: str,
    columns: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    selected = list(columns or (list(rows[0]) if rows else []))
    types: dict[str, dict[str, Any]] = {}
    for column in selected:
        contract = column_contract(view, column)
        types[column] = contract or _inferred_contract(column, [row.get(column) for row in rows])
    output: list[dict[str, Any]] = []
    for row in rows:
        normalized: dict[str, Any] = {}
        for column in selected:
            value = row.get(column)
            kind = str(types[column].get("type") or "").upper()
            if value is None:
                normalized[column] = None
            elif kind == "DECIMAL":
                normalized[column] = _decimal_string(value, types[column])
            elif isinstance(value, datetime):
                normalized[column] = value.isoformat()
            elif isinstance(value, date):
                normalized[column] = value.isoformat()
            elif kind in {"INTEGER", "BIGINT"}:
                normalized[column] = int(value)
            else:
                normalized[column] = value
        output.append(normalized)
    return output, types


def result_hash(
    *, columns: list[str], column_types: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
) -> str:
    return hashlib.sha256(
        _canonical_bytes({"columns": columns, "columnTypes": column_types, "rows": rows})
    ).hexdigest()


class AnalyticsResultService:
    async def freeze(
        self,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
        data_as_of: str,
        columns: list[str],
        column_types: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
        branches: list[dict[str, Any]],
        lineage: list[str],
        queries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        settings = get_settings()
        ttl = int(settings.analytics_cursor_ttl_seconds)
        expires_at = int(time.time()) + ttl
        result_set_id = f"ars_{uuid.uuid4().hex}"
        scope_hash = owner_scope_hash(admin_id, permissions, tenant_id)
        snapshot = {
            "schemaVersion": "aishop-analytics-result/v2",
            "resultSetId": result_set_id,
            "ownerScopeHash": scope_hash,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "expiresAt": expires_at,
            "resultSnapshotExpiresAt": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "catalogVersion": CATALOG_VERSION,
            "catalogContentSha256": CATALOG_CONTENT_SHA256,
            "dataAsOf": data_as_of,
            "columns": columns,
            "columnTypes": column_types,
            "rows": rows,
            "branches": branches,
            "lineage": lineage,
            "queries": queries,
            "resultHash": result_hash(columns=columns, column_types=column_types, rows=rows),
        }
        try:
            await redis_service.set_json(f"{_RESULT_KEY_PREFIX}{result_set_id}", snapshot, ttl)
        except Exception as exc:
            logger.warning("analytics_result_snapshot_write_failed", error=type(exc).__name__)
            return None
        return snapshot

    async def _read(self, result_set_id: str) -> dict[str, Any]:
        try:
            value = await redis_service.get_json(f"{_RESULT_KEY_PREFIX}{result_set_id}")
        except Exception as exc:
            raise AnalyticsResultError(
                "RESULT_SNAPSHOT_UNAVAILABLE", 503, "冻结结果服务暂不可用"
            ) from exc
        if not isinstance(value, dict):
            raise AnalyticsResultError("RESULT_SNAPSHOT_EXPIRED", 410, "冻结结果不存在或已过期")
        if int(value.get("expiresAt") or 0) < int(time.time()):
            raise AnalyticsResultError("RESULT_SNAPSHOT_EXPIRED", 410, "冻结结果已过期")
        return value

    async def get(
        self,
        result_set_id: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
    ) -> dict[str, Any]:
        snapshot = await self._read(str(result_set_id or "").strip())
        expected = owner_scope_hash(admin_id, permissions, tenant_id)
        if snapshot.get("ownerScopeHash") != expected:
            raise AnalyticsResultError(
                "RESULT_SET_OWNER_MISMATCH", 403, "冻结结果不属于当前管理员范围"
            )
        return snapshot

    def cursor(self, snapshot: dict[str, Any], offset: int) -> str:
        return _encode_cursor(
            result_set_id=str(snapshot["resultSetId"]),
            scope_hash=str(snapshot["ownerScopeHash"]),
            offset=offset,
            expires_at=int(snapshot["expiresAt"]),
        )

    async def page(
        self,
        cursor: str,
        *,
        admin_id: str,
        permissions: Iterable[str],
        tenant_id: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        scope_hash = owner_scope_hash(admin_id, permissions, tenant_id)
        decoded = _decode_cursor(cursor, expected_scope_hash=scope_hash)
        snapshot = await self.get(
            str(decoded["resultSetId"]),
            admin_id=admin_id,
            permissions=permissions,
            tenant_id=tenant_id,
        )
        rows = list(snapshot.get("rows") or [])
        offset = int(decoded["offset"])
        size = max(1, min(int(page_size), int(get_settings().analytics_max_rows)))
        page_rows = rows[offset : offset + size]
        next_offset = offset + len(page_rows)
        next_cursor = self.cursor(snapshot, next_offset) if next_offset < len(rows) else None
        failed_branches = [
            branch
            for branch in snapshot.get("branches") or []
            if branch.get("status") not in {"SUCCEEDED", "EMPTY_RESULT"}
        ]
        return {
            "outcome": "ANSWER",
            "completion": "PARTIAL" if failed_branches else "COMPLETE",
            "status": "SUCCEEDED" if page_rows else "EMPTY_RESULT",
            "catalogVersion": snapshot.get("catalogVersion"),
            "dataAsOf": snapshot.get("dataAsOf"),
            "columnTypes": snapshot.get("columnTypes") or {},
            "resultSetId": snapshot.get("resultSetId"),
            "resultSnapshotExpiresAt": snapshot.get("resultSnapshotExpiresAt"),
            "resultHash": snapshot.get("resultHash"),
            "columns": snapshot.get("columns") or [],
            "rows": page_rows,
            "nextCursor": next_cursor,
            "page": {
                "offset": offset,
                "size": len(page_rows),
                "hasMore": bool(next_cursor),
                "totalRows": len(rows),
            },
            "warnings": ["PARTIAL_METRIC_TREE"] if failed_branches else [],
        }


analytics_result_service = AnalyticsResultService()
