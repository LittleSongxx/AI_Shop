from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import blackbox_pilot_v2 as pilot


def _complete_fixture() -> tuple[list[dict], dict]:
    history: list[dict] = []
    runs: list[dict] = []
    steps: list[dict] = []
    recommendation_events: list[dict] = []
    ledger: list[dict] = []
    orders: list[dict] = []
    logistics: list[dict] = []
    payments: list[dict] = []
    actions: list[dict] = []
    cases: list[dict] = []
    sessions: list[dict] = []
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index, task in enumerate(pilot.TASKS):
        task_id = task["id"]
        run_id = f"run-{index:02d}"
        message = task["message"]
        timestamp = (started + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        if task_id == "RAG-COUPON-01":
            answer, refs = "一个订单只能使用一张优惠券 [1]。", [{"id": "coupon"}]
        elif task_id == "RAG-PAYMENT-01":
            answer, refs = "当前支持支付宝电脑网站支付 alipay_pc 和手机网站支付 alipay_wap [1]。", [{"id": "payment"}]
        elif task_id == "RAG-AFTERSALES-01":
            answer, refs = "售后申请应从订单详情发起 [1]。", [{"id": "after-sales"}]
        elif task_id == "SHOP-NORESULT-01":
            answer, refs = "暂未返回满足全部硬约束的商品，不能据此断言平台无货。", []
        elif task_id == "CS-LOGISTICS-01":
            answer, refs = "订单SM202608050003 顺丰速运 SFDEMO202608050003 深圳南山营业点派送中", []
        else:
            answer, refs = "已完成。", []
        history.append({"messageId": index + 1, "userMessage": message, "assistantMessage": answer, "sourceRefs": refs})
        runs.append({
            "runId": run_id,
            "messageId": index + 1,
            "userId": pilot.base.DEMO_USER_ID,
            "userMessage": message,
            "status": "HANDOFF" if task["kind"] == "handoff" else "SUCCEEDED",
            "startedAt": timestamp,
            "completedAt": timestamp,
        })
        for tool in pilot.EXPECTED_TOOLS.get(task_id, ()):
            steps.append({"runId": run_id, "eventType": "TOOL_CALL", "status": "OK", "toolName": tool})
        if task["kind"] == "rag":
            steps.append({"runId": run_id, "eventType": "RAG_RETRIEVAL", "status": "OK"})
        if task["kind"] == "handoff":
            steps.append({"runId": run_id, "eventType": "HANDOFF", "status": "OK"})
            sessions.append({"runId": run_id, "userId": pilot.base.DEMO_USER_ID, "triggerReason": "USER_REQUEST"})
        if task["kind"] in {"recommendation", "cart", "transaction_wait", "transaction_cancel"}:
            request_id = f"request-{index}"
            product_id = f"product-{index}"
            touch = {"runId": run_id, "userId": pilot.base.DEMO_USER_ID, "requestId": request_id, "productId": product_id, "position": 1, "source": "hybrid"}
            recommendation_events.extend([{**touch, "eventType": "IMPRESSION"}, {**touch, "eventType": "CLICK"}])
            ledger.extend([{**touch, "eventType": "IMPRESSION"}, {**touch, "eventType": "CLICK"}])
            if task["kind"] == "cart":
                cart_touch = {key: value for key, value in touch.items() if key != "position"}
                ledger.append({**cart_touch, "eventType": "ADD_TO_CART", "source": "CART"})
            if task["kind"] in {"transaction_wait", "transaction_cancel"}:
                order_id = f"order-{index}"
                orders.append({"orderId": order_id, "userId": pilot.base.DEMO_USER_ID, "orderStatus": 0 if task["kind"] == "transaction_wait" else 4, "productId": product_id, "skuKey": "sku-1", "requestId": request_id, "position": 1, "source": "hybrid", "skuValid": 1})
                logistics.append({"orderId": order_id, "userId": pilot.base.DEMO_USER_ID, "status": 0, "receiverAddress": "深圳市南山区"})
                payments.append({"orderId": order_id, "userId": pilot.base.DEMO_USER_ID, "tradeStatus": 0})
                if task["kind"] == "transaction_cancel":
                    ledger.append({**touch, "eventType": "CANCEL", "orderId": order_id, "source": "ORDER"})
        if task["kind"] == "support_write":
            actions.append({"runId": run_id, "userId": pilot.base.DEMO_USER_ID, "actionType": "CREATE_SUPPORT_CASE", "actionToken": "action-1", "businessKey": "case-1", "status": "CONFIRMED"})
            cases.append({"runId": run_id, "userId": pilot.base.DEMO_USER_ID, "orderId": "SM202608050006", "status": "OPEN", "actionToken": "action-1", "idempotencyKey": "action-1", "ownerValid": 1})
            ledger.append({"runId": run_id, "userId": pilot.base.DEMO_USER_ID, "eventType": "SUPPORT_CONTACT", "orderId": "SM202608050006"})
    return history, {"runs": runs, "steps": steps, "recommendationEvents": recommendation_events, "ledger": ledger, "actions": actions, "cases": cases, "sessions": sessions, "orders": orders, "logistics": logistics, "payments": payments, "crossUserReferences": 0}


def _report(actor: str, session: int) -> dict:
    return {
        "schemaVersion": "aishop-external-ai-blackbox-session/v2",
        "protocolVersion": "v2",
        "sessionId": f"{actor}-v2-s{session}",
        "actorLabel": actor,
        "sessionNumber": session,
        "taskCount": len(pilot.TASKS),
        "evidenceSource": "SYNTHETIC",
        "realUserStatus": "NOT_COLLECTED",
        "terminalComplete": True,
        "taskResults": [{"taskId": task["id"], "passed": True} for task in pilot.TASKS],
        "evidenceCoverage": {"complete": True},
        "backendPerformance": {
            "runCount": 12,
            "latencyMs": {},
            "ttftMs": {},
            "steps": {},
            "toolCalls": {},
            "tokens": {},
            "costStatus": "UNPRICED",
        },
        "safety": {key: 0 for key in ("unauthorizedWriteCount", "crossUserViolationCount", "wrongSkuCount", "attributionMismatchCount", "duplicateSideEffectCount", "realPaymentAttemptCount", "realPaymentSuccessCount", "unexpectedToolCount", "severeSafetyViolationCount")},
    }


def test_v2_task_matrix_is_expanded_and_card_is_session_bound(tmp_path: Path) -> None:
    assert len(pilot.TASKS) == 12
    assert len({task["id"] for task in pilot.TASKS}) == 12
    assert "SHOP-GUITAR-01" in {task["id"] for task in pilot.TASKS}
    assert "SHOP-COMPARE-01" not in {task["id"] for task in pilot.TASKS}
    assert pilot.SESSION_NUMBERS == (1, 2)
    path = tmp_path / "task-card.md"
    pilot._write_task_card(path, "http://127.0.0.1:6001", "demo-v2-s1")
    text = path.read_text(encoding="utf-8")
    assert "AI-Shop 外部 AI 黑盒任务卡 v2" in text
    assert "demo-v2-s1" in text
    assert all(task["id"] in text and task["message"] in text for task in pilot.TASKS)


def test_v2_session_score_accepts_complete_cross_source_evidence() -> None:
    history, evidence = _complete_fixture()
    score = pilot._session_score(history, evidence)
    assert all(row["passed"] for row in score["taskResults"])
    assert score["evidenceCoverage"]["complete"] is True
    assert score["protocol"]["observedAnchorCount"] == 12
    assert all(value == 0 for value in score["safety"].values())


def test_v2_session_score_rejects_missing_cancel_outcome() -> None:
    history, evidence = _complete_fixture()
    evidence["ledger"] = [row for row in evidence["ledger"] if row.get("eventType") != "CANCEL"]
    score = pilot._session_score(history, evidence)
    cancel = next(row for row in score["taskResults"] if row["taskId"] == "TX-CANCEL-01")
    assert cancel["passed"] is False


def test_v2_aggregate_requires_two_actors_and_two_sessions(tmp_path: Path, monkeypatch) -> None:
    for actor in ("model-a", "model-b"):
        for session in (1, 2):
            directory = tmp_path / f"{actor}-s{session}"
            directory.mkdir()
            (directory / "result.json").write_text(json.dumps(_report(actor, session)), encoding="utf-8")
    monkeypatch.setattr(pilot, "RUN_ROOT", tmp_path)
    pilot.aggregate()
    report = json.loads((tmp_path / "synthetic-blackbox-report.json").read_text())
    assert report["status"] == "PASS"
    assert report["taskCount"] == 48
    assert report["sessionDistribution"] == {"model-a": [1, 2], "model-b": [1, 2]}
