"""Locked, governance-first evaluation for the Text2SQL experiment.

This module deliberately scores safety and evidence completeness separately
from answer quality. A missing provider trace is not converted into a zero
quality score and cannot be presented as a successful live run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.services.analytics_catalog import CATALOG, allowed_plan_fields
from app.services.sql_guard import validate_sql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("datasets") / "text2sql_v1.jsonl"
LOCK_PATH = Path(__file__).with_name("datasets") / "text2sql_v1.lock.json"


@dataclass(frozen=True)
class Text2SqlCase:
    case_id: str
    category: str
    kind: str
    raw: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cases(path: Path = DATASET_PATH) -> list[Text2SqlCase]:
    cases: list[Text2SqlCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number} must contain an object")
        cases.append(
            Text2SqlCase(
                case_id=str(row.get("id") or ""),
                category=str(row.get("category") or ""),
                kind=str(row.get("kind") or ""),
                raw=row,
            )
        )
    return cases


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"invalid locked date: {value}") from exc


def validate_contract(
    cases: list[Text2SqlCase],
    dataset_path: Path = DATASET_PATH,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids = [case.case_id for case in cases]
    if not cases or "" in ids or len(ids) != len(set(ids)):
        errors.append("cases must have unique non-empty IDs")
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported text2sql lock schema")
    if _sha256(dataset_path) != lock.get("datasetSha256"):
        errors.append("text2sql dataset SHA does not match lock")
    if len(cases) != int(lock.get("caseCount") or 0):
        errors.append("case count does not match lock")

    category_counts: dict[str, int] = {}
    security_count = 0
    for case in cases:
        row = case.raw
        category_counts[case.category] = category_counts.get(case.category, 0) + 1
        if case.kind == "security":
            security_count += 1
        if not str(row.get("question") or "").strip():
            errors.append(f"{case.case_id}: question is required")
        expected = row.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case.case_id}: expected object is required")
            continue
        if "sqlAllowed" not in expected:
            errors.append(f"{case.case_id}: expected.sqlAllowed is required")
        try:
            _parse_date(row.get("startDate"))
            _parse_date(row.get("endDate"))
        except ValueError as exc:
            errors.append(f"{case.case_id}: {exc}")
        view = str(row.get("semanticView") or "")
        if case.kind == "positive" and view not in CATALOG:
            errors.append(f"{case.case_id}: positive case has unknown semantic view")
        if view in CATALOG:
            fields = allowed_plan_fields(view)
            selected = set(row.get("metrics") or ()) | set(row.get("dimensions") or ())
            if case.kind == "positive" and not selected.issubset(fields):
                errors.append(f"{case.case_id}: positive case has unknown field")

    if category_counts != dict(lock.get("categoryCounts") or {}):
        errors.append("category counts do not match lock")
    if security_count != int(lock.get("securityCaseCount") or 0):
        errors.append("security case count does not match lock")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "schemaVersion": 1,
        "caseCount": len(cases),
        "categoryCounts": category_counts,
        "securityCaseCount": security_count,
        "datasetSha256": _sha256(dataset_path),
        "thresholds": lock.get("thresholds") or {},
    }


def _mean(values: list[bool]) -> float:
    return sum(1 for item in values if item) / len(values) if values else 0.0


def _trace_value(trace: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in trace:
            return trace[name]
    return None


def _trace_complete(prediction: dict[str, Any], required: list[str]) -> bool:
    trace = prediction.get("trace")
    if not isinstance(trace, dict):
        return False
    for field in required:
        value = _trace_value(
            trace,
            field,
            field[0].lower() + field[1:] if field else field,
            _snake(field),
        )
        if value is None or value == "":
            return False
    return _provider_complete(prediction)


def _provider_complete(prediction: dict[str, Any]) -> bool:
    trace = prediction.get("trace")
    if not isinstance(trace, dict):
        return False
    trace_provider = _trace_value(trace, "providerComplete", "provider_complete")
    top_provider = _trace_value(
        prediction, "providerComplete", "provider_complete"
    )
    fallback_used = any(
        _trace_value(payload, "fallbackUsed", "fallback_used") is True
        for payload in (prediction, trace)
    )
    if fallback_used or trace_provider is not True:
        return False
    return top_provider is not False


def _snake(value: str) -> str:
    output = []
    for char in value:
        if char.isupper():
            output.append("_")
        output.append(char.lower())
    return "".join(output).lstrip("_")


def _security_passes(case: Text2SqlCase, prediction: dict[str, Any]) -> bool:
    expected = case.raw["expected"]
    expected_allowed = bool(expected.get("sqlAllowed"))
    if case.kind == "positive":
        sql = str(prediction.get("sql") or "")
        try:
            guard = validate_sql(
                sql,
                expected_view=str(case.raw.get("semanticView") or "") or None,
                expected_start_date=_parse_date(case.raw.get("startDate")),
                expected_end_date=_parse_date(case.raw.get("endDate")),
            )
        except Exception:
            return False
        return expected_allowed and guard.allowed

    blocked_reason = str(
        prediction.get("blockedReason") or prediction.get("blocked_reason") or ""
    )
    expected_reason = str(expected.get("expectedReason") or "")
    if expected_reason.startswith("DATA_ANALYST_") or expected_reason == "TENANT_SCOPE_REQUIRED":
        return blocked_reason == expected_reason
    sql = str(prediction.get("sql") or "")
    guard = validate_sql(sql)
    if expected_allowed:
        return guard.allowed
    return not guard.allowed and (
        not expected_reason or guard.reason == expected_reason
    )


def evaluate_predictions(
    cases: list[Text2SqlCase],
    predictions: dict[str, Any],
    *,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    required_trace = [str(item) for item in lock.get("requiredTraceFields") or ()]
    safety: list[bool] = []
    result_quality: list[bool] = []
    narrative: list[bool] = []
    traces: list[bool] = []
    executed = 0
    provider_complete = 0
    case_results: list[dict[str, Any]] = []
    for case in cases:
        prediction = predictions.get(case.case_id)
        if not isinstance(prediction, dict):
            prediction = {}
        is_executed = bool(prediction.get("executed", True)) and bool(prediction)
        executed += int(is_executed)
        provider_ok = _provider_complete(prediction)
        provider_complete += int(provider_ok)
        safe = _security_passes(case, prediction)
        result_ok = prediction.get("resultCorrect") is True or prediction.get("result_correct") is True
        narrative_ok = prediction.get("narrativeConsistent") is True or prediction.get("narrative_consistent") is True
        if case.raw.get("expected", {}).get("requiresCausalCaution"):
            narrative_ok = narrative_ok and bool(
                prediction.get("causalCaution") or prediction.get("causal_caution")
            )
        trace_ok = _trace_complete(prediction, required_trace)
        safety.append(safe)
        result_quality.append(bool(result_ok))
        narrative.append(bool(narrative_ok))
        traces.append(trace_ok)
        case_results.append(
            {
                "id": case.case_id,
                "category": case.category,
                "status": "PASSED" if all((safe, result_ok, narrative_ok, trace_ok)) else "FAILED",
                "sqlSafe": safe,
                "resultCorrect": bool(result_ok),
                "narrativeConsistent": bool(narrative_ok),
                "traceComplete": trace_ok,
                "executed": is_executed,
                "providerComplete": provider_ok,
            }
        )
    report = {
        "caseCount": len(cases),
        "executedCount": executed,
        "executionCompleteness": executed / len(cases) if cases else 0.0,
        "sqlSafetyRate": _mean(safety),
        "resultCorrectness": _mean(result_quality),
        "narrativeConsistency": _mean(narrative),
        "traceCompleteness": _mean(traces),
        "providerCompleteness": provider_complete / len(cases) if cases else 0.0,
        "cases": case_results,
    }
    report["gateFailures"] = gate_failures(report, lock.get("thresholds") or {})
    report["promotionStatus"] = (
        "ELIGIBLE_FOR_REVIEW" if not report["gateFailures"] else "GOVERNED_EXPERIMENT"
    )
    return report


def gate_failures(report: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for metric, threshold in thresholds.items():
        actual = report.get(metric)
        if actual is None or float(actual) < float(threshold):
            failures.append(f"{metric}={actual} is below {threshold}")
    return failures


def prepare(
    cases: list[Text2SqlCase] | None = None,
    dataset_path: Path = DATASET_PATH,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    loaded = cases if cases is not None else load_cases(dataset_path)
    return validate_contract(loaded, dataset_path, lock_path)
