from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

_CATALOG_FILE = (
    Path(__file__).resolve().parents[1] / "resources" / "analytics-catalog-v0.provisional.json"
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_catalog() -> dict[str, Any]:
    document = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
    declared_hash = str(document.get("contentSha256") or "")
    hash_input = dict(document)
    hash_input.pop("contentSha256", None)
    actual_hash = hashlib.sha256(_canonical_bytes(hash_input)).hexdigest()
    if declared_hash != actual_hash:
        raise RuntimeError(
            "analytics catalog content hash mismatch: "
            f"declared={declared_hash or '<missing>'}, actual={actual_hash}"
        )
    if document.get("lifecycle") != "PROVISIONAL":
        raise RuntimeError("analytics catalog must remain PROVISIONAL in V0")
    if document.get("development") is not True or document.get("provisional") is not True:
        raise RuntimeError("analytics V0 catalog release boundary is invalid")
    if document.get("releaseGateEligible") is not False:
        raise RuntimeError("analytics V0 catalog cannot be release-gate eligible")
    views = document.get("views")
    if not isinstance(views, dict) or len(views) != 10:
        raise RuntimeError("analytics V0 catalog must contain exactly ten governed views")
    return document


CATALOG_DOCUMENT = _load_catalog()
CATALOG_VERSION = str(CATALOG_DOCUMENT["catalogVersion"])
CATALOG_CONTENT_SHA256 = str(CATALOG_DOCUMENT["contentSha256"])
CATALOG_TIMEZONE = str(CATALOG_DOCUMENT["timezone"])
CATALOG_CURRENCY = str(CATALOG_DOCUMENT["currency"])


def _compatibility_catalog() -> dict[str, dict[str, object]]:
    """Expose the legacy planner shape from the versioned catalog document."""

    output: dict[str, dict[str, object]] = {}
    for view_name, raw_view in CATALOG_DOCUMENT["views"].items():
        view = dict(raw_view)
        physical: dict[str, str] = {}
        derived: dict[str, dict[str, str]] = {}
        for column_name, raw_column in (view.get("columns") or {}).items():
            column = dict(raw_column)
            sql_expression = column.get("sqlExpression")
            if sql_expression:
                derived[column_name] = {
                    "definition": str(column.get("description") or column_name),
                    "sql_expression": str(sql_expression),
                }
            else:
                physical[column_name] = str(column.get("description") or column_name)
        output[view_name] = {
            "description": str(view.get("description") or ""),
            "date_column": str(view.get("dateColumn") or ""),
            "requires_date_filter": bool(view.get("requiresDateFilter")),
            "columns": physical,
            "derived_metrics": derived,
            "answerability": str(view.get("answerability") or ""),
            "date_ownership": str(view.get("dateOwnership") or ""),
            "must_disclose": list(view.get("mustDisclose") or []),
            "forbidden_claims": list(view.get("forbiddenClaims") or []),
            "required_permission": str(view.get("requiredPermission") or ""),
            "export_permission": str(view.get("exportPermission") or ""),
        }
    return output


CATALOG: dict[str, dict[str, object]] = _compatibility_catalog()


def catalog_prompt() -> str:
    lines: list[str] = []
    for name, item in CATALOG.items():
        columns = item["columns"]
        definitions = ", ".join(f"{column}={meaning}" for column, meaning in columns.items())
        derived = item.get("derived_metrics") or {}
        derived_definitions = ", ".join(
            f"{metric}={spec.get('definition')}；固定表达式={spec.get('sql_expression')}"
            for metric, spec in derived.items()
        )
        derived_text = f" 受治理派生指标: {derived_definitions}" if derived_definitions else ""
        boundaries = "；".join(str(item) for item in item.get("forbidden_claims") or [])
        boundary_text = f" 禁止解释: {boundaries}" if boundaries else ""
        lines.append(
            f"{name}: {item['description']} 字段: {definitions}{derived_text}{boundary_text}"
        )
    return "\n".join(lines)


def view_contract(view: str) -> dict[str, Any]:
    raw = (CATALOG_DOCUMENT.get("views") or {}).get(view)
    return deepcopy(raw) if isinstance(raw, dict) else {}


def column_contract(view: str, column: str) -> dict[str, Any]:
    raw = (view_contract(view).get("columns") or {}).get(column)
    return deepcopy(raw) if isinstance(raw, dict) else {}


def allowed_columns(view: str) -> frozenset[str]:
    contract = view_contract(view)
    columns = contract.get("columns") or {}
    return frozenset(
        str(name).lower()
        for name, spec in columns.items()
        if not isinstance(spec, dict) or not spec.get("sqlExpression")
    )


def allowed_plan_fields(view: str) -> frozenset[str]:
    contract = view_contract(view)
    return frozenset(str(name).lower() for name in (contract.get("columns") or {}))


def result_column_types(view: str, columns: list[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for name in columns:
        contract = column_contract(view, name)
        if contract:
            output[name] = contract
    return output


def disclosure_contract(views: list[str]) -> dict[str, list[str]]:
    must_disclose: list[str] = []
    forbidden_claims: list[str] = []
    for view in views:
        contract = view_contract(view)
        must_disclose.extend(str(item) for item in contract.get("mustDisclose") or [])
        forbidden_claims.extend(str(item) for item in contract.get("forbiddenClaims") or [])
    return {
        "mustDisclose": list(dict.fromkeys(must_disclose)),
        "forbiddenClaims": list(dict.fromkeys(forbidden_claims)),
    }
