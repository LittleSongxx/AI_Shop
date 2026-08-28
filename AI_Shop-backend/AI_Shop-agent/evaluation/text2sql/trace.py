from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from evaluation.text2sql.fixture import (
    RUNTIME_PASSWORD,
    RUNTIME_USER,
    _mysql_connection,
)


def _json_object(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_native(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_native(item) for item in value]
    return value


def _scan_estimate(steps: list[dict[str, Any]]) -> dict[str, Any]:
    estimates: list[dict[str, Any]] = []
    total = 0
    unavailable = False
    for step in steps:
        if step.get("eventType") != "DATA_ANALYST_EXPLAIN":
            continue
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        diagnostic = output.get("explainDiagnostic")
        if isinstance(diagnostic, dict) and diagnostic.get("status") == "UNAVAILABLE":
            unavailable = True
            estimates.append(
                {
                    "branchId": output.get("branchId"),
                    "estimatedRows": None,
                    "sqlHash": output.get("sqlHash"),
                    "status": step.get("status"),
                    "reasonCode": diagnostic.get("reasonCode"),
                }
            )
            continue
        branch_total = 0
        for row in output.get("rows") or []:
            if not isinstance(row, dict):
                continue
            try:
                branch_total += max(0, int(row.get("rows") or 0))
            except (TypeError, ValueError):
                continue
        total += branch_total
        estimates.append(
            {
                "branchId": output.get("branchId"),
                "estimatedRows": branch_total,
                "sqlHash": output.get("sqlHash"),
                "status": step.get("status"),
            }
        )
    return {"estimatedRows": None if unavailable else total, "branches": estimates}


def read_trace(run_id: str | None, *, wait_seconds: float = 2.0) -> dict[str, Any] | None:
    resolved = str(run_id or "").strip()
    if not resolved:
        return None
    deadline = time.monotonic() + max(0.0, wait_seconds)
    run: dict[str, Any] | None = None
    raw_steps: list[dict[str, Any]] = []
    while True:
        with _mysql_connection(
            user=RUNTIME_USER,
            password=RUNTIME_PASSWORD,
            database="aishop_agent",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT run_id, status, outcome, agent_id, agent_version, model_name,
                           input_tokens, output_tokens, cost_cny, latency_ms, ttft_ms,
                           started_at, completed_at, capture_level
                    FROM agent_run WHERE run_id=%s
                    """,
                    (resolved,),
                )
                run = cursor.fetchone()
                if run:
                    cursor.execute(
                        """
                        SELECT step_id, event_type, node_name, round_no, status,
                               input_json, output_json, model_name, tool_name,
                               error_code, latency_ms, occurred_at
                        FROM agent_step
                        WHERE run_id=%s ORDER BY occurred_at, step_id
                        """,
                        (resolved,),
                    )
                    raw_steps = list(cursor.fetchall())
        if run and (run.get("completed_at") is not None or time.monotonic() >= deadline):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if not run:
        return None
    steps = [
        {
            "stepId": row.get("step_id"),
            "eventType": row.get("event_type"),
            "nodeName": row.get("node_name"),
            "roundNo": row.get("round_no"),
            "status": row.get("status"),
            "input": _json_object(row.get("input_json")),
            "output": _json_object(row.get("output_json")),
            "modelName": row.get("model_name"),
            "toolName": row.get("tool_name"),
            "errorCode": row.get("error_code"),
            "latencyMs": row.get("latency_ms"),
            "occurredAt": row.get("occurred_at"),
        }
        for row in raw_steps
    ]
    plans = [
        step.get("output")
        for step in steps
        if step.get("eventType") == "DATA_ANALYST_PLAN"
        and step.get("status") == "OK"
        and isinstance(step.get("output"), dict)
    ]
    db_time_ms = sum(
        int(step.get("latencyMs") or 0)
        for step in steps
        if step.get("eventType") == "DATA_ANALYST_QUERY"
    )
    return _json_native(
        {
            "run": {
                "runId": run.get("run_id"),
                "status": run.get("status"),
                "outcome": run.get("outcome"),
                "agentId": run.get("agent_id"),
                "agentVersion": run.get("agent_version"),
                "modelName": run.get("model_name"),
                "inputTokens": run.get("input_tokens"),
                "outputTokens": run.get("output_tokens"),
                "costCny": run.get("cost_cny"),
                "latencyMs": run.get("latency_ms"),
                "ttftMs": run.get("ttft_ms"),
                "startedAt": run.get("started_at"),
                "completedAt": run.get("completed_at"),
                "captureLevel": run.get("capture_level"),
            },
            "plan": plans[-1] if plans else None,
            "dbTimeMs": db_time_ms,
            "scanEstimate": _scan_estimate(steps),
            "steps": steps,
        }
    )
