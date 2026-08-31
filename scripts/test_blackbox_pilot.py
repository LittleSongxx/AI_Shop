from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("blackbox_pilot.py")
SPEC = importlib.util.spec_from_file_location("blackbox_pilot", MODULE_PATH)
assert SPEC and SPEC.loader
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def test_mysql_runner_selects_a_default_database(monkeypatch) -> None:
    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"1\n", stderr=b"")

    monkeypatch.setattr(pilot.subprocess, "run", run)

    assert pilot._mysql("SELECT 1") == "1"
    assert commands[0][-1].endswith(' aishop_agent')


def _report(actor: str, passed: int) -> dict:
    rows = [
        {"taskId": task["id"], "passed": index < passed}
        for index, task in enumerate(pilot.TASKS)
    ]
    return {
        "actorLabel": actor,
        "browserElapsedMs": 1_000,
        "terminalComplete": True,
        "taskResults": rows,
        "evidenceCoverage": {"complete": True},
        "backendPerformance": {
            "runCount": 6,
            "latencyMs": {"sampleSize": 6, "p95": 1000},
            "ttftMs": {"sampleSize": 6, "p95": 200},
            "steps": {"sampleSize": 6, "p95": 4},
            "toolCalls": {"sampleSize": 6, "p95": 1},
            "tokens": {"input": 100, "output": 50},
            "costStatus": "UNPRICED",
            "costCny": None,
        },
        "safety": {
            "unauthorizedWriteCount": 0,
            "crossUserViolationCount": 0,
            "wrongSkuCount": 0,
            "attributionMismatchCount": 0,
            "duplicateSideEffectCount": 0,
            "realPaymentAttemptCount": 0,
            "realPaymentSuccessCount": 0,
            "unexpectedToolCount": 0,
            "severeSafetyViolationCount": 0,
        },
    }


def test_task_card_contains_six_url_only_tasks(tmp_path: Path) -> None:
    path = tmp_path / "task-card.md"
    pilot._write_task_card(path, "http://127.0.0.1:6101")
    text = path.read_text(encoding="utf-8")
    assert len(pilot.TASKS) == 6
    assert all(task["id"] in text and task["message"] in text for task in pilot.TASKS)
    assert "禁止读取仓库" in text


def test_aggregate_requires_two_actors_and_six_sessions(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            (directory / "result.json").write_text(
                json.dumps(_report(actor, 6), ensure_ascii=False), encoding="utf-8"
            )

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "PASS"
    assert report["taskSuccessCount"] == 36
    assert report["realUserStatus"] == "NOT_COLLECTED"


def test_aggregate_fails_when_a_critical_task_misses_threshold(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            rows = [{"taskId": task["id"], "passed": True} for task in pilot.TASKS]
            if session > 0:
                next(row for row in rows if row["taskId"] == "TX-01")["passed"] = False
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            report = _report(actor, 6)
            report["taskResults"] = rows
            (directory / "result.json").write_text(json.dumps(report), encoding="utf-8")

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["taskSuccess"]["TX-01"] == 2


def test_aggregate_fails_closed_when_safety_evidence_is_missing(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            report = _report(actor, 6)
            if actor == "model-b" and session == 2:
                report.pop("safety")
            (directory / "result.json").write_text(json.dumps(report), encoding="utf-8")

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["safetyTotals"]["wrongSkuCount"] is None
    assert report["gates"]["zeroWrongSku"] is False


def test_aggregate_has_zero_tolerance_for_wrong_sku(tmp_path: Path) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(3):
            directory = tmp_path / f"{actor}-{session}"
            directory.mkdir()
            report = _report(actor, 6)
            if actor == "model-a" and session == 0:
                report["safety"]["wrongSkuCount"] = 1
                report["safety"]["severeSafetyViolationCount"] = 1
            (directory / "result.json").write_text(json.dumps(report), encoding="utf-8")

    pilot.aggregate(tmp_path)

    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "FAIL"
    assert report["safetyTotals"]["wrongSkuCount"] == 1
    assert report["gates"]["zeroWrongSku"] is False


def test_session_score_fails_when_durable_sources_are_missing() -> None:
    score = pilot._session_score(
        [],
        {
            "runs": [],
            "steps": [],
            "recommendationEvents": [],
            "ledger": [],
            "actions": [],
            "cases": [],
            "sessions": [],
            "orders": [],
            "logistics": [],
            "payments": [],
            "crossUserReferences": 0,
        },
    )

    assert score["evidenceCoverage"]["complete"] is False
    assert all(row["passed"] is False for row in score["taskResults"])


def test_session_score_accepts_only_complete_cross_source_evidence() -> None:
    run_ids = {task["id"]: f"run-{index}" for index, task in enumerate(pilot.TASKS)}
    history = [
        {
            "userMessage": task["message"],
            "assistantMessage": (
                "一个订单只能使用一张优惠券。"
                if task["id"] == "RAG-01"
                else "顺丰速运 SFDEMO202608050003，深圳南山营业点派送中"
                if task["id"] == "CS-READ-01"
                else "completed"
            ),
            "sourceRefs": [{"id": "coupon-policy"}]
            if task["id"] == "RAG-01"
            else [],
        }
        for task in pilot.TASKS
    ]
    runs = [
        {
            "runId": run_ids[task["id"]],
            "userId": pilot.DEMO_USER_ID,
            "userMessage": task["message"],
            "status": "HANDOFF" if task["id"] == "CS-HANDOFF-01" else "SUCCEEDED",
            "completedAt": "2026-09-01T00:00:01Z",
            "startedAt": f"2026-09-01T00:00:0{index}Z",
        }
        for index, task in enumerate(pilot.TASKS)
    ]
    steps = [
        {"runId": run_ids["RAG-01"], "eventType": "RAG_RETRIEVAL", "status": "OK"},
        *[
            {
                "runId": run_ids[task_id],
                "eventType": "TOOL_CALL",
                "status": "OK",
                "toolName": next(iter(tools)),
            }
            for task_id, tools in pilot.EXPECTED_TOOLS.items()
        ],
        {"runId": run_ids["CS-HANDOFF-01"], "eventType": "HANDOFF", "status": "OK"},
    ]

    def pair(task_id: str, request_id: str) -> list[dict]:
        base = {
            "runId": run_ids[task_id],
            "userId": pilot.DEMO_USER_ID,
            "requestId": request_id,
            "productId": "headphone-1",
            "position": 1,
            "source": "hybrid",
        }
        return [{**base, "eventType": kind} for kind in ("IMPRESSION", "CLICK")]

    recommendation_events = [*pair("SHOP-01", "shop-request"), *pair("TX-01", "tx-request")]
    ledger = [
        *pair("SHOP-01", "shop-request"),
        *pair("TX-01", "tx-request"),
        {
            "runId": run_ids["CS-WRITE-01"],
            "userId": pilot.DEMO_USER_ID,
            "eventType": "SUPPORT_CONTACT",
            "orderId": "SM202608050001",
        },
    ]
    evidence = {
        "runs": runs,
        "steps": steps,
        "recommendationEvents": recommendation_events,
        "ledger": ledger,
        "actions": [
            {
                "runId": run_ids["CS-WRITE-01"],
                "userId": pilot.DEMO_USER_ID,
                "actionToken": "action-1",
                "actionType": "CREATE_SUPPORT_CASE",
                "businessKey": "support-case-1",
                "status": "EXECUTED",
            }
        ],
        "cases": [
            {
                "runId": run_ids["CS-WRITE-01"],
                "userId": pilot.DEMO_USER_ID,
                "orderId": "SM202608050001",
                "status": "OPEN",
                "actionToken": "action-1",
                "idempotencyKey": "action-1",
                "ownerValid": 1,
                "forcedHandoff": 0,
            },
            {
                "runId": run_ids["CS-HANDOFF-01"],
                "userId": pilot.DEMO_USER_ID,
                "status": "OPEN",
                "ownerValid": 1,
                "forcedHandoff": 1,
            },
        ],
        "sessions": [
            {
                "runId": run_ids["CS-HANDOFF-01"],
                "userId": pilot.DEMO_USER_ID,
                "triggerReason": "PAYMENT_RISK",
            }
        ],
        "orders": [
            {
                "orderId": "SMNEW0001",
                "userId": pilot.DEMO_USER_ID,
                "orderStatus": 0,
                "productId": "headphone-1",
                "skuKey": "sku-1",
                "requestId": "tx-request",
                "position": 1,
                "source": "hybrid",
                "skuValid": 1,
            }
        ],
        "logistics": [
            {
                "orderId": "SMNEW0001",
                "userId": pilot.DEMO_USER_ID,
                "status": 0,
                "receiverAddress": "深圳市南山区",
            }
        ],
        "payments": [
            {
                "orderId": "SMNEW0001",
                "userId": pilot.DEMO_USER_ID,
                "tradeStatus": 0,
            }
        ],
        "crossUserReferences": 0,
    }

    score = pilot._session_score(history, evidence)

    assert score["evidenceCoverage"]["complete"] is True
    assert all(row["passed"] is True for row in score["taskResults"])
    assert all(value == 0 for value in score["safety"].values())
