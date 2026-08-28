from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from evaluation.text2sql.contracts import Completion, Outcome, Text2SqlCase
from evaluation.text2sql.dataset import load_cases, verify_human_gold
from evaluation.text2sql.fixture import reset, source_data_fingerprint
from evaluation.text2sql.freeze import freeze_inputs
from evaluation.text2sql.io import utc_now, write_json, write_jsonl, write_sha256s
from evaluation.text2sql.runtime import ADMIN_PORT
from evaluation.text2sql.scoring import normalize_legacy_outcome, result_hash, score_case, summarize
from evaluation.text2sql.sessions import seed_admin_sessions
from evaluation.text2sql.trace import read_trace

_OWNER_SETUP_ANSWER_QUESTION = (
    "列出 2026-08-21 到 2026-08-27 每天的支付订单数、支付总额、"
    "已完成退款额和净支付额。"
)
_OWNER_SETUP_CLARIFICATION_QUESTION = "最近最好卖的商品有哪些？"


@dataclass(frozen=True)
class RunConfig:
    phase: Literal["pre-foundation", "post-foundation"]
    output: Path
    dataset: Path
    trials: int = 3
    java_base_url: str = f"http://127.0.0.1:{ADMIN_PORT}"
    timeout_seconds: float = 60.0


def _response_payload(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    return dict(data) if isinstance(data, dict) else dict(body)


def _completion(payload: dict[str, Any], outcome: str | None) -> str:
    explicit = payload.get("completion")
    if explicit in {item.value for item in Completion}:
        return str(explicit)
    if outcome is None:
        return Completion.FAILED.value
    if outcome != Outcome.ANSWER.value:
        return Completion.NOT_APPLICABLE.value
    if str(payload.get("status") or "") == "PARTIAL_METRIC_TREE":
        return Completion.PARTIAL.value
    return Completion.COMPLETE.value


def _post(
    client: httpx.Client,
    base_url: str,
    path: str,
    token: str,
    form: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = client.post(
            f"{base_url.rstrip('/')}{path}",
            headers={"adminToken": token},
            data={key: value for key, value in form.items() if value is not None},
        )
        try:
            body: Any = response.json()
        except ValueError:
            body = {"rawText": response.text}
        http_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        payload = _response_payload(body)
        trace = None
        trace_error = None
        try:
            trace = read_trace(payload.get("runId"))
        except Exception as exc:
            trace_error = f"{type(exc).__name__}: {exc}"
        return {
            "httpStatus": response.status_code,
            "body": body,
            "responseHeaders": {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                in {"content-type", "content-disposition", "x-request-id", "traceparent"}
            },
            "latencyMs": http_latency_ms,
            "error": None,
            "trace": trace,
            "traceError": trace_error,
        }
    except httpx.HTTPError as exc:
        return {
            "httpStatus": 0,
            "body": {},
            "latencyMs": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "trace": None,
            "traceError": "HTTP request failed before a runId was available",
        }


def _read_outcome(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None, str]:
    payload = _response_payload(raw.get("body"))
    outcome = normalize_legacy_outcome(payload, http_status=int(raw.get("httpStatus") or 0))
    return payload, outcome, _completion(payload, outcome)


def _page_flow(
    client: httpx.Client,
    config: RunConfig,
    case: Text2SqlCase,
    token: str,
    initial_payload: dict[str, Any],
) -> dict[str, Any]:
    cursor = initial_payload.get("nextCursor") or initial_payload.get("cursor")
    result_set_id = initial_payload.get("resultSetId")
    all_rows = list(initial_payload.get("rows") or [])
    pages = []
    for _ in range(20):
        if not cursor:
            break
        path = (
            "/admin/agentMessage/dataAnalyst/page"
            if config.phase == "post-foundation"
            else "/admin/agentMessage/dataAnalyst/ask"
        )
        raw = _post(
            client,
            config.java_base_url,
            path,
            token,
            {
                "question": case.question,
                "cursor": cursor,
                "pageSize": case.flow.page_size,
            },
        )
        payload, outcome, completion = _read_outcome(raw)
        pages.append({"raw": raw, "outcome": outcome, "completion": completion})
        if raw["httpStatus"] >= 400 or outcome != Outcome.ANSWER.value:
            break
        all_rows.extend(payload.get("rows") or [])
        next_cursor = payload.get("nextCursor") or payload.get("cursor")
        if not next_cursor or next_cursor == cursor:
            cursor = None
            break
        cursor = next_cursor
    return {
        "requested": True,
        "pages": pages,
        "allRows": all_rows,
        "completed": cursor is None,
        "snapshotBound": bool(result_set_id)
        and all(
            _response_payload(page["raw"]["body"]).get("resultSetId")
            == result_set_id
            for page in pages
        )
        and all(
            not any(
                step.get("eventType") in {"LLM_CALL", "DATA_ANALYST_QUERY"}
                for step in ((page["raw"].get("trace") or {}).get("steps") or [])
                if isinstance(step, dict)
            )
            for page in pages
        ),
        "resultSetId": result_set_id,
        "frozenResultHash": result_hash(
            {**initial_payload, "allRows": all_rows}
        ),
        "resultHashes": [
            result_hash(_response_payload(page["raw"]["body"])) for page in pages
        ],
    }


def _clarification_flow(
    client: httpx.Client,
    config: RunConfig,
    case: Text2SqlCase,
    token: str,
    initial_payload: dict[str, Any],
) -> dict[str, Any]:
    options = case.expected.clarification_options
    choice = options[0] if options else None
    if config.phase == "post-foundation":
        raw = _post(
            client,
            config.java_base_url,
            "/admin/agentMessage/dataAnalyst/clarify",
            token,
            {
                "clarificationToken": initial_payload.get("clarificationToken"),
                "choiceId": choice.choice_id if choice else None,
            },
        )
    else:
        suffix = choice.answer_suffix if choice else ""
        raw = _post(
            client,
            config.java_base_url,
            "/admin/agentMessage/dataAnalyst/ask",
            token,
            {"question": f"{case.question} {suffix}".strip()},
        )
    payload, outcome, completion = _read_outcome(raw)
    return {
        "requested": True,
        "choiceId": choice.choice_id if choice else None,
        "raw": raw,
        "payload": payload,
        "outcome": outcome,
        "completion": completion,
        "passed": outcome == case.flow.expected_second_outcome,
    }


def _export_flow(
    client: httpx.Client,
    config: RunConfig,
    case: Text2SqlCase,
    token: str,
    initial_payload: dict[str, Any],
) -> dict[str, Any]:
    form = (
        {"resultSetId": initial_payload.get("resultSetId")}
        if config.phase == "post-foundation"
        else {"question": case.question}
    )
    raw = _post(
        client,
        config.java_base_url,
        "/admin/agentMessage/dataAnalyst/export",
        token,
        form,
    )
    payload, outcome, completion = _read_outcome(raw)
    job_id = payload.get("jobId")
    polls: list[dict[str, Any]] = []
    terminal_payload = payload
    deadline = time.monotonic() + 30
    while job_id and str(terminal_payload.get("status") or "") in {
        "PENDING",
        "RUNNING",
    }:
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
        status_raw = _post(
            client,
            config.java_base_url,
            "/admin/agentMessage/dataAnalyst/export/status",
            token,
            {"jobId": job_id},
        )
        terminal_payload, status_outcome, status_completion = _read_outcome(status_raw)
        polls.append(
            {
                "raw": status_raw,
                "payload": terminal_payload,
                "outcome": status_outcome,
                "completion": status_completion,
            }
        )
        if status_raw["httpStatus"] >= 400:
            break
    download = None
    artifact: dict[str, Any] = {}
    if job_id and str(terminal_payload.get("status") or "") == "COMPLETED":
        download = _post(
            client,
            config.java_base_url,
            "/admin/agentMessage/dataAnalyst/export/download",
            token,
            {"jobId": job_id},
        )
        if isinstance(download.get("body"), dict):
            artifact = dict(download["body"])
    initial_result_set_id = initial_payload.get("resultSetId")
    exported_result_set_id = (
        artifact.get("resultSetId") or terminal_payload.get("resultSetId")
    )
    initial_frozen_hash = initial_payload.get("resultHash") or initial_payload.get(
        "resultSetHash"
    )
    exported_frozen_hash = artifact.get("resultHash") or artifact.get("resultSetHash")
    if not initial_frozen_hash and initial_result_set_id:
        initial_frozen_hash = result_hash(initial_payload)
    if not exported_frozen_hash and exported_result_set_id:
        exported_frozen_hash = result_hash(artifact)
    same_result_hash = bool(
        initial_result_set_id
        and exported_result_set_id == initial_result_set_id
        and initial_frozen_hash
        and exported_frozen_hash == initial_frozen_hash
    )
    return {
        "requested": True,
        "raw": raw,
        "payload": payload,
        "outcome": outcome,
        "completion": completion,
        "resultSetBound": bool(initial_payload.get("resultSetId")),
        "jobId": job_id,
        "polls": polls,
        "terminalPayload": terminal_payload,
        "download": download,
        "artifact": artifact,
        "sameResultHash": same_result_hash,
        "initialResultHash": initial_frozen_hash,
        "exportedResultHash": exported_frozen_hash,
    }


def _execute_case(
    client: httpx.Client,
    config: RunConfig,
    case: Text2SqlCase,
    token: str,
    *,
    all_tokens: dict[str, str] | None = None,
    source_fingerprint_before: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tags = set(case.slice_tags)
    owner_kind = next(
        (item for item in ("page_owner", "export_owner", "clarification_owner") if item in tags),
        None,
    )
    precondition = None
    if owner_kind:
        owner_token = (all_tokens or {}).get("eval-analyst-a")
        if not owner_token:
            raise RuntimeError("owner-bound evaluation requires eval-analyst-a session")
        setup_question = (
            _OWNER_SETUP_CLARIFICATION_QUESTION
            if owner_kind == "clarification_owner"
            else _OWNER_SETUP_ANSWER_QUESTION
        )
        setup_raw = _post(
            client,
            config.java_base_url,
            "/admin/agentMessage/dataAnalyst/ask",
            owner_token,
            {"question": setup_question, "pageSize": 2},
        )
        setup_payload, setup_outcome, setup_completion = _read_outcome(setup_raw)
        precondition = {
            "ownerAdminId": "eval-analyst-a",
            "raw": setup_raw,
            "payload": setup_payload,
            "outcome": setup_outcome,
            "completion": setup_completion,
        }
        if owner_kind == "page_owner":
            initial_path = (
                "/admin/agentMessage/dataAnalyst/page"
                if config.phase == "post-foundation"
                else "/admin/agentMessage/dataAnalyst/ask"
            )
            initial_form = {
                "question": setup_question,
                "cursor": setup_payload.get("nextCursor") or setup_payload.get("cursor"),
                "pageSize": 2,
            }
        elif owner_kind == "export_owner":
            initial_path = "/admin/agentMessage/dataAnalyst/export"
            initial_form = {"resultSetId": setup_payload.get("resultSetId")}
        else:
            initial_path = "/admin/agentMessage/dataAnalyst/clarify"
            options = setup_payload.get("clarificationOptions") or []
            choice_id = (
                options[0].get("choiceId")
                if options and isinstance(options[0], dict)
                else "paid_units"
            )
            initial_form = {
                "clarificationToken": setup_payload.get("clarificationToken"),
                "choiceId": choice_id,
            }
        initial = _post(
            client,
            config.java_base_url,
            initial_path,
            token,
            initial_form,
        )
    else:
        direct_export_deny = (
            case.expected.outcome is Outcome.DENY and case.flow.export_frozen_result
        )
        initial_path = (
            "/admin/agentMessage/dataAnalyst/export"
            if direct_export_deny
            else "/admin/agentMessage/dataAnalyst/ask"
        )
        initial = _post(
            client,
            config.java_base_url,
            initial_path,
            token,
            {"question": case.question},
        )
    payload, outcome, completion = _read_outcome(initial)
    normalized = dict(payload)
    normalized["outcome"] = outcome
    normalized["completion"] = completion
    flow: dict[str, Any] = {}
    if case.flow.traverse_all_pages and outcome == Outcome.ANSWER.value:
        flow["pagination"] = _page_flow(client, config, case, token, payload)
        normalized["allRows"] = flow["pagination"]["allRows"]
    if case.flow.follow_clarification and outcome == Outcome.CLARIFY.value:
        flow["clarification"] = _clarification_flow(
            client, config, case, token, payload
        )
    direct_export_deny = (
        case.expected.outcome is Outcome.DENY and case.flow.export_frozen_result
    )
    if (
        case.flow.export_frozen_result
        and not direct_export_deny
        and outcome == Outcome.ANSWER.value
    ):
        flow["export"] = _export_flow(client, config, case, token, payload)
    source_fingerprint_after = None
    fixture_unchanged = None
    if source_fingerprint_before is not None:
        source_fingerprint_after = source_data_fingerprint()
        fixture_unchanged = (
            source_fingerprint_after["sha256"] == source_fingerprint_before["sha256"]
        )
    scoring = score_case(
        case,
        normalized,
        http_status=int(initial["httpStatus"]),
        trace=initial.get("trace"),
        flow=flow,
        latency_ms=float(initial.get("latencyMs") or 0),
        fixture_unchanged=fixture_unchanged,
    )
    return {
        "caseId": case.case_id,
        "fixtureState": case.fixture_state,
        "expectedOutcome": case.expected.outcome.value,
        "expectedCompletion": case.expected.completion.value,
        "observedOutcome": outcome,
        "observedCompletion": completion,
        "initial": initial,
        "precondition": precondition,
        "normalized": normalized,
        "flow": flow,
        "sourceDataFingerprint": (
            {
                "before": source_fingerprint_before["sha256"],
                "after": source_fingerprint_after["sha256"],
                "unchanged": fixture_unchanged,
                **(
                    {"afterDetails": source_fingerprint_after}
                    if fixture_unchanged is False
                    else {}
                ),
            }
            if source_fingerprint_before is not None and source_fingerprint_after is not None
            else None
        ),
        "score": scoring,
    }


def run(config: RunConfig) -> dict[str, Any]:
    if config.output.exists():
        raise FileExistsError(config.output)
    cases = load_cases(config.dataset)
    if any(not case.lifecycle.startswith("HUMAN_") for case in cases):
        raise ValueError(
            "official Text2SQL baseline refuses AI draft labels; seal HUMAN_VERIFIED gold first"
        )
    verify_human_gold(config.dataset)
    if config.trials != 3:
        raise ValueError("Text2SQL V0 requires exactly three trials per version")
    config.output.mkdir(parents=True)
    freeze_inputs(config.dataset, config.output / "input-freeze")
    all_records: list[dict[str, Any]] = []
    with httpx.Client(timeout=config.timeout_seconds) as client:
        for trial in range(1, config.trials + 1):
            for state in ("base", "boundary", "empty"):
                reset(state)
                tokens = seed_admin_sessions(cases)
                source_fingerprint = source_data_fingerprint()
                for case in (item for item in cases if item.fixture_state == state):
                    record = _execute_case(
                        client,
                        config,
                        case,
                        tokens[case.actor.admin_id],
                        all_tokens=tokens,
                        source_fingerprint_before=(
                            source_fingerprint
                            if case.expected.outcome is Outcome.DENY
                            else None
                        ),
                    )
                    record["trial"] = trial
                    record["canonical"] = trial == 1
                    record["score"]["trial"] = trial
                    record["score"]["canonical"] = trial == 1
                    all_records.append(record)
                    if (record.get("sourceDataFingerprint") or {}).get("unchanged") is False:
                        reset(state)
                        tokens = seed_admin_sessions(cases)
                        source_fingerprint = source_data_fingerprint()
    score_rows = [record["score"] for record in all_records]
    canonical_scores = [
        record["score"] for record in all_records if record["canonical"]
    ]
    manifest = {
        "schemaVersion": "aishop-text2sql-evidence/v0",
        "createdAt": utc_now(),
        "phase": config.phase,
        "caseCount": len(cases),
        "trialCount": config.trials,
        "executionCount": len(all_records),
        "canonicalTrial": 1,
        "allInfrastructureFailuresRetained": True,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
        "summaryAllTrials": summarize(score_rows),
        "summaryCanonical": summarize(canonical_scores),
    }
    write_jsonl(config.output / "raw-responses.jsonl", all_records)
    write_jsonl(config.output / "scores.jsonl", score_rows)
    write_json(config.output / "manifest.json", manifest)
    write_sha256s(config.output)
    return {"output": str(config.output), **manifest}
