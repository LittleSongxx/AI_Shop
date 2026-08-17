#!/usr/bin/env python3
"""Export two real, redacted Agent traces as interview-verifiable evidence."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import acquire, close_pool, init_pool  # noqa: E402
from app.harness.observation import redact_pii  # noqa: E402
from app.services.episode_query_service import episode_query_service  # noqa: E402

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT.parents[1] / "docs" / "evidence" / "agent-traces"
_ACTION_TOKEN = re.compile(r"act_[a-fA-F0-9]{32}")
_LONG_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{0,12}\d{10,}[A-Za-z0-9_-]*(?![A-Za-z0-9])")
_SECRET_KEY_PARTS = ("token", "password", "secret", "authorization", "cookie")
_IDENTITY_KEYS = {
    "userId",
    "user_id",
    "sessionId",
    "session_id",
    "orderId",
    "order_id",
    "orderItemId",
    "order_item_id",
    "businessKey",
    "targetId",
}


class TraceExportError(ValueError):
    pass


def _fingerprint(value: Any) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _redact_text(value: str) -> str:
    text, _count = redact_pii(value)
    text = _ACTION_TOKEN.sub("[ACTION_TOKEN]", text)
    return _LONG_IDENTIFIER.sub("[BUSINESS_ID]", text)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = str(key or "")
    lower_key = normalized_key.lower()
    if any(part in lower_key for part in _SECRET_KEY_PARTS):
        return "[REDACTED]" if value is not None else None
    if normalized_key in _IDENTITY_KEYS and value not in (None, ""):
        return _fingerprint(value)
    if isinstance(value, dict):
        return {
            str(child_key): redact_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


async def pending_actions_for_run(run_id: str) -> list[dict[str, Any]]:
    async with acquire() as cur:
        await cur.execute(
            """
            SELECT action_type,status,reconcile_attempts,reconcile_deadline,
                   last_reconcile_at,review_reason,created_at,updated_at
            FROM agent_pending_action
            WHERE run_id=%s
            ORDER BY created_at,action_token
            """,
            (run_id,),
        )
        rows = list(await cur.fetchall())
    return [
        {
            "actionType": row.get("action_type"),
            "status": row.get("status"),
            "reconcileAttempts": int(row.get("reconcile_attempts") or 0),
            "reconcileDeadline": _iso(row.get("reconcile_deadline")),
            "lastReconcileAt": _iso(row.get("last_reconcile_at")),
            "reviewReason": redact_value(row.get("review_reason"), key="reviewReason"),
            "createdAt": _iso(row.get("created_at")),
            "updatedAt": _iso(row.get("updated_at")),
        }
        for row in rows
    ]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds") if hasattr(value, "isoformat") else str(value)


def validate_trace_pair(
    success: Mapping[str, Any],
    success_pending: list[Mapping[str, Any]],
    unknown: Mapping[str, Any],
    unknown_pending: list[Mapping[str, Any]],
) -> None:
    errors: list[str] = []
    success_eval = success.get("episodeEvaluation") or {}
    success_facts = success_eval.get("facts") or {}
    if success_facts.get("actionType") != "REFUND":
        errors.append("success trace must be a refund action")
    if success_facts.get("actionProposed") is not True:
        errors.append("success trace has no durable proposal fact")
    if success_facts.get("userConfirmed") is not True:
        errors.append("success trace has no user-confirmation fact")
    if success_facts.get("remoteOutcomeKnown") is not True:
        errors.append("success trace remote outcome is not authoritative")
    if success_facts.get("actionOutcome") != "CONFIRMED":
        errors.append("success trace action outcome is not CONFIRMED")
    if not any(row.get("status") == "CONFIRMED" for row in success_pending):
        errors.append("success trace has no CONFIRMED MySQL pending-action row")

    unknown_eval = unknown.get("episodeEvaluation") or {}
    unknown_facts = unknown_eval.get("facts") or {}
    if unknown_facts.get("actionProposed") is not True:
        errors.append("unknown trace has no durable proposal fact")
    if unknown_facts.get("userConfirmed") is not True:
        errors.append("unknown trace has no user-confirmation fact")
    if unknown_facts.get("remoteOutcomeKnown") is True:
        errors.append("unknown trace incorrectly claims a known remote outcome")
    unknown_statuses = {str(row.get("status") or "") for row in unknown_pending}
    if not unknown_statuses & {"INCONCLUSIVE", "MANUAL_REVIEW"}:
        errors.append(
            "unknown trace needs an INCONCLUSIVE or MANUAL_REVIEW MySQL pending-action row"
        )
    if errors:
        raise TraceExportError("trace evidence contract invalid:\n- " + "\n- ".join(errors))


def _public_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return redact_value(
        {
            "stepId": step.get("stepId"),
            "eventType": step.get("eventType"),
            "nodeName": step.get("nodeName"),
            "roundNo": step.get("roundNo"),
            "status": step.get("status"),
            "modelName": step.get("modelName"),
            "toolName": step.get("toolName"),
            "callId": step.get("callId"),
            "errorCode": step.get("errorCode"),
            "latencyMs": step.get("latencyMs"),
            "input": step.get("input"),
            "output": step.get("output"),
            "occurredAt": step.get("occurredAt"),
            "agentId": step.get("agentId"),
            "artifactType": step.get("artifactType"),
            "handoffId": step.get("handoffId"),
        }
    )


def public_trace(
    label: str,
    episode: Mapping[str, Any],
    pending: list[Mapping[str, Any]],
) -> dict[str, Any]:
    conversation = episode.get("conversation") or {}
    return {
        "label": label,
        "run": redact_value(
            {
                "runId": episode.get("runId"),
                "traceId": episode.get("traceId"),
                "status": episode.get("status"),
                "outcome": episode.get("outcome"),
                "scenario": episode.get("scenario"),
                "intent": episode.get("intent"),
                "modelName": episode.get("modelName"),
                "inputTokens": episode.get("inputTokens"),
                "outputTokens": episode.get("outputTokens"),
                "costCny": episode.get("costCny"),
                "latencyMs": episode.get("latencyMs"),
                "ttftMs": episode.get("ttftMs"),
                "startedAt": episode.get("startedAt"),
                "completedAt": episode.get("completedAt"),
                "experiment": episode.get("experiment"),
            }
        ),
        "conversation": redact_value(
            {
                "userMessage": conversation.get("userMessage"),
                "assistantMessage": conversation.get("assistantMessage"),
                "bizType": conversation.get("bizType"),
                "sourceRefs": conversation.get("sourceRefs") or [],
            }
        ),
        "episodeEvaluation": redact_value(episode.get("episodeEvaluation") or {}),
        "pendingActions": redact_value(list(pending)),
        "steps": [_public_step(step) for step in episode.get("steps") or []],
        "handoffs": redact_value(episode.get("handoffs") or []),
        "children": redact_value(episode.get("children") or []),
    }


def build_bundle(
    success: Mapping[str, Any],
    success_pending: list[Mapping[str, Any]],
    unknown: Mapping[str, Any],
    unknown_pending: list[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_trace_pair(success, success_pending, unknown, unknown_pending)
    return {
        "schemaVersion": "aishop-interview-traces/v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "executionMode": "LIVE_FULL_STACK",
        "simulated": False,
        "redaction": {
            "pii": True,
            "businessIdentifiers": "sha256 fingerprint or placeholder",
            "actionTokens": "removed",
            "secrets": "removed",
        },
        "traces": [
            public_trace("confirmed_refund", success, success_pending),
            public_trace("unknown_outcome_manual_review", unknown, unknown_pending),
        ],
    }


def _event_chain(trace: Mapping[str, Any]) -> str:
    events = [
        str(step.get("eventType"))
        for step in trace.get("steps") or []
        if step.get("eventType")
        in {
            "INTENT_DECISION",
            "ORCHESTRATION_DECISION",
            "RAG_RETRIEVAL",
            "TOOL_CALL",
            "ACTION_PROPOSED",
            "ACTION_CONFIRMED_BY_USER",
            "ACTION_TERMINAL",
            "GRAPH_END",
        }
    ]
    return " -> ".join(events) or "No selected events"


def markdown_report(bundle: Mapping[str, Any]) -> str:
    lines = [
        "# AI_Shop controlled after-sales Agent traces",
        "",
        "- Source: persisted live Episodes and MySQL pending-action rows",
        "- Simulation: no",
        "- PII, credentials, action tokens, and business identifiers: redacted",
        "",
    ]
    for trace in bundle.get("traces") or []:
        run = trace.get("run") or {}
        pending = trace.get("pendingActions") or []
        evaluation = trace.get("episodeEvaluation") or {}
        lines.extend(
            [
                f"## {trace.get('label')}",
                "",
                f"- Run status: `{run.get('status')}`",
                f"- Intent: `{run.get('intent')}`",
                f"- Episode verdict: `{evaluation.get('verdict')}`",
                f"- Pending states: `{[row.get('status') for row in pending]}`",
                f"- Token usage: `{run.get('inputTokens')} + {run.get('outputTokens')}`",
                f"- Latency / TTFT: `{run.get('latencyMs')} ms / {run.get('ttftMs')} ms`",
                f"- Selected event chain: `{_event_chain(trace)}`",
                "",
            ]
        )
    return "\n".join(lines)


def write_bundle(bundle: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "traces.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_report(bundle) + "\n", encoding="utf-8")
    hashes = {
        json_path.name: hashlib.sha256(json_path.read_bytes()).hexdigest(),
        markdown_path.name: hashlib.sha256(markdown_path.read_bytes()).hexdigest(),
    }
    (output_dir / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items())),
        encoding="ascii",
    )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": bundle.get("generatedAt"),
        "executionMode": bundle.get("executionMode"),
        "simulated": bundle.get("simulated"),
        "sha256": hashes,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return hashes


async def export(args: argparse.Namespace) -> tuple[dict[str, Any], Path, dict[str, str]]:
    await init_pool()
    try:
        success = await episode_query_service.detail(args.success_refund_run_id)
        unknown = await episode_query_service.detail(args.unknown_outcome_run_id)
        if success is None:
            raise TraceExportError("confirmed refund run does not exist")
        if unknown is None:
            raise TraceExportError("unknown-outcome run does not exist")
        success_pending = await pending_actions_for_run(args.success_refund_run_id)
        unknown_pending = await pending_actions_for_run(args.unknown_outcome_run_id)
    finally:
        await close_pool()
    bundle = build_bundle(success, success_pending, unknown, unknown_pending)
    bundle_id = args.bundle_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / bundle_id
    hashes = write_bundle(bundle, output_dir)
    return bundle, output_dir, hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--success-refund-run-id", required=True)
    parser.add_argument("--unknown-outcome-run-id", required=True)
    parser.add_argument("--bundle-id")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    try:
        bundle, output_dir, hashes = asyncio.run(export(args))
    except TraceExportError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "outputDir": str(output_dir),
                "traceCount": len(bundle["traces"]),
                "sha256": hashes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
