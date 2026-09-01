from __future__ import annotations

import hashlib
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
    assert "--default-character-set=utf8mb4" in commands[0][-1]
    assert commands[0][-1].endswith(" aishop_agent")


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


def _write_sealed_result(directory: Path, report: dict) -> tuple[bytes, bytes]:
    directory.mkdir(parents=True)
    result = json.dumps(report, ensure_ascii=False, indent=2).encode()
    (directory / "result.json").write_bytes(result)
    sums = f"{hashlib.sha256(result).hexdigest()}  result.json\n".encode()
    (directory / "SHA256SUMS").write_bytes(sums)
    return result, sums


def test_task_card_contains_six_url_only_tasks(tmp_path: Path) -> None:
    path = tmp_path / "task-card.md"
    pilot._write_task_card(path, "http://127.0.0.1:6101")
    text = path.read_text(encoding="utf-8")
    assert len(pilot.TASKS) == 6
    assert all(task["id"] in text and task["message"] in text for task in pilot.TASKS)
    assert "禁止读取仓库" in text
    assert "必须先点击本轮 AI 推荐卡" in text
    assert "SESSION_COMPLETE" in text
    assert "如果当前文件不是本 Session 的任务卡" in text
    assert "第一条必须先发送 RAG-01" in text
    assert "全新浏览器上下文" in text


def test_task_anchor_accepts_only_unicode_punctuation_equivalence() -> None:
    punctuation = str.maketrans({"，": ",", "？": "?", "。": "."})
    for task in pilot.TASKS:
        variant = task["message"].translate(punctuation)
        assert pilot._anchor_key(variant) == pilot._anchor_key(task["message"])

    original = pilot.TASKS[1]["message"]
    assert pilot._anchor_key(original.replace("4500", "5000")) != pilot._anchor_key(original)
    handoff = pilot.TASKS[-1]["message"]
    assert pilot._anchor_key(handoff.replace("不要", "")) != pilot._anchor_key(handoff)
    assert pilot._answer(
        [{"userMessage": original}, {"userMessage": original.translate(punctuation)}],
        original,
    ) == {}


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


def test_invalid_protocol_attempt_is_preserved_disclosed_and_not_counted(
    tmp_path: Path, monkeypatch
) -> None:
    for actor in ("model-a", "model-b"):
        for session in range(1, 4):
            directory = tmp_path / f"{actor}-s{session}"
            directory.mkdir()
            report = _report(actor, 6)
            report.update(
                {
                    "sessionId": directory.name,
                    "sessionNumber": session,
                    "status": "PASS",
                    "taskSuccessCount": 6,
                    "taskCount": 6,
                }
            )
            (directory / "result.json").write_text(json.dumps(report), encoding="utf-8")

    failed_id = "model-a-calibration"
    failed = _report("model-a", 0)
    failed.update(
        {
            "sessionId": failed_id,
            "sessionNumber": 1,
            "status": "FAIL",
            "taskSuccessCount": 0,
            "taskCount": 6,
        }
    )
    result_bytes, sums_bytes = _write_sealed_result(tmp_path / failed_id, failed)
    monkeypatch.setattr(pilot, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(pilot, "INVALID_MARKERS", tmp_path / "invalid-attempts")

    pilot.mark_invalid_protocol(failed_id, "CALIBRATION_PROTOCOL_MISMATCH")
    pilot.aggregate(tmp_path)

    assert (tmp_path / failed_id / "result.json").read_bytes() == result_bytes
    assert (tmp_path / failed_id / "SHA256SUMS").read_bytes() == sums_bytes
    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "PASS"
    assert report["attemptCount"] == 7
    assert report["sessionCount"] == 6
    assert report["invalidAttemptCount"] == 1
    assert report["taskSuccessCount"] == 36
    assert report["invalidAttempts"][0]["originalStatus"] == "FAIL"
    assert report["invalidAttempts"][0]["taskSuccessCount"] == 0


def test_invalid_protocol_marker_fails_closed_after_seal_tampering(
    tmp_path: Path, monkeypatch
) -> None:
    failed_id = "model-a-calibration"
    failed = _report("model-a", 0)
    failed.update(
        {
            "sessionId": failed_id,
            "sessionNumber": 1,
            "status": "FAIL",
            "taskSuccessCount": 0,
            "taskCount": 6,
        }
    )
    directory = tmp_path / failed_id
    _write_sealed_result(directory, failed)
    monkeypatch.setattr(pilot, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(pilot, "INVALID_MARKERS", tmp_path / "invalid-attempts")
    pilot.mark_invalid_protocol(failed_id, "CALIBRATION_PROTOCOL_MISMATCH")
    (directory / "result.json").write_text("{}", encoding="utf-8")

    try:
        pilot.aggregate(tmp_path)
    except pilot.PilotError:
        pass
    else:
        raise AssertionError("tampered invalid attempt must fail closed")


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


def test_incomplete_protocol_does_not_call_anchored_tools_unexpected() -> None:
    shop = pilot.TASKS[1]
    logistics = pilot.TASKS[3]
    history = [
        {"userMessage": shop["message"], "assistantMessage": "商品推荐"},
        {
            "userMessage": logistics["message"],
            "assistantMessage": "顺丰速运 SFDEMO202608050003 深圳南山营业点派送中",
        },
    ]
    runs = [
        {
            "runId": "shop-run",
            "userMessage": shop["message"],
            "status": "SUCCEEDED",
            "startedAt": "2026-09-01T00:00:01Z",
            "completedAt": "2026-09-01T00:00:02Z",
        },
        {
            "runId": "logistics-run",
            "userMessage": logistics["message"],
            "status": "SUCCEEDED",
            "startedAt": "2026-09-01T00:00:03Z",
            "completedAt": "2026-09-01T00:00:04Z",
        },
    ]
    score = pilot._session_score(
        history,
        {
            "runs": runs,
            "steps": [
                {
                    "runId": "shop-run",
                    "eventType": "TOOL_CALL",
                    "status": "OK",
                    "toolName": "SEARCH_PRODUCTS",
                },
                {
                    "runId": "logistics-run",
                    "eventType": "TOOL_CALL",
                    "status": "OK",
                    "toolName": "QUERY_LOGISTICS",
                },
            ],
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

    assert score["protocol"]["missingTasks"] == [
        "RAG-01",
        "TX-01",
        "CS-WRITE-01",
        "CS-HANDOFF-01",
    ]
    assert score["protocol"]["unassignedToolCount"] == 0
    assert score["safety"]["unexpectedToolCount"] == 0


def test_session_score_accepts_only_complete_cross_source_evidence() -> None:
    run_ids = {task["id"]: f"run-{index}" for index, task in enumerate(pilot.TASKS)}
    punctuation = str.maketrans({"，": ",", "？": "?", "。": "."})
    history = [
        {
            "userMessage": task["message"].translate(punctuation),
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
            "userMessage": task["message"].translate(punctuation),
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


def test_finalize_seals_evidence_snapshot_before_reset(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = "model-a-s1-20260901000000"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    metadata = {
        "schemaVersion": "aishop-external-ai-blackbox-session/v1",
        "sessionId": session_id,
        "actorLabel": "model-a",
        "sessionNumber": 1,
        "batchId": "pilot_" + "a" * 32,
        "evidenceSource": "SYNTHETIC",
        "realUserStatus": "NOT_COLLECTED",
        "preparedAt": "2026-09-01T00:00:00.000Z",
        "taskIds": [task["id"] for task in pilot.TASKS],
    }
    (session_dir / "session.json").write_text(json.dumps(metadata), encoding="utf-8")
    history = [{"userMessage": "visible demo turn", "assistantMessage": "answer"}]
    evidence = {"runs": [{"runId": "run-1"}], "steps": []}
    performance = _report("model-a", 6)["backendPerformance"]

    class Response:
        content = json.dumps({"performance": performance}).encode()

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Client:
        @staticmethod
        def post(*_args, **_kwargs):
            return Response()

        @staticmethod
        def close() -> None:
            return None

    cache = Client()
    reset_seen = False

    def reset(_env) -> None:
        nonlocal reset_seen
        snapshot_path = session_dir / "evidence-snapshot.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert snapshot["conversation"] == history
        assert snapshot["facts"] == evidence
        digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        assert f"{digest}  evidence-snapshot.json" in (
            session_dir / "SHA256SUMS"
        ).read_text(encoding="utf-8")
        reset_seen = True

    monkeypatch.setattr(pilot, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(pilot, "load_environment", dict)
    monkeypatch.setattr(pilot, "_clients", lambda _env: (Client(), Client(), cache))
    monkeypatch.setattr(pilot, "login_admin", lambda *_args: None)
    monkeypatch.setattr(pilot, "login_user", lambda *_args: None)
    monkeypatch.setattr(pilot, "_history", lambda _user: history)
    monkeypatch.setattr(pilot, "_admin_post", lambda *_args: None)
    monkeypatch.setattr(pilot, "_load_evidence", lambda *_args: evidence)
    monkeypatch.setattr(
        pilot,
        "_session_score",
        lambda *_args: {
            "taskResults": [
                {"taskId": task["id"], "passed": True} for task in pilot.TASKS
            ],
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
            "evidenceCoverage": {
                "javaOrderFacts": True,
                "episodeAndAgentStep": True,
                "mcpTrace": True,
                "recommendationEventAndLedger": True,
                "pendingAction": True,
                "supportCaseAndSession": True,
                "complete": True,
            },
        },
    )
    monkeypatch.setattr(pilot, "_reset_demo", reset)

    pilot.finalize(session_id)

    assert reset_seen is True
