#!/usr/bin/env python3
"""Prepare and score URL-only synthetic user sessions for the AI-Shop demo."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
from bootstrap_demo import (
    DEMO_USER_EMAIL,
    DEMO_USER_PASSWORD,
    load_environment,
    login_admin,
    login_user,
    required,
    response_data,
    seed_database,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "run" / "blackbox-pilot"
DEMO_USER_ID = "9000000001"
SEED_ORDER_IDS = tuple(f"SM20260805000{index}" for index in range(1, 8))

TASKS = (
    {
        "id": "RAG-01",
        "message": "优惠券能否叠加？请说明当前规则并展示依据。",
        "actions": "只使用 AI 助手；查看回答中的知识引用，不执行任何交易操作。",
    },
    {
        "id": "SHOP-01",
        "message": "我预算4500元，主要地铁通勤，需要主动降噪耳机，请推荐一款并说明理由。",
        "actions": "从推荐卡打开一个符合条件的商品详情，但不要下单。",
    },
    {
        "id": "TX-01",
        "message": "请推荐5000元以内的降噪耳机，我要买一件。",
        "actions": "从推荐结果进入结算，使用默认地址创建订单；停在待支付，不要支付。",
    },
    {
        "id": "CS-READ-01",
        "message": "我买的三星折叠屏手机现在到哪了？请告诉我物流公司、单号和最新轨迹。",
        "actions": "只读取客服回答，不修改订单。",
    },
    {
        "id": "CS-WRITE-01",
        "message": "我收到的东西坏了，想创建售后工单。",
        "actions": "若需要先选择订单项，选择第一条可用候选；看到确认卡后确认一次。",
    },
    {
        "id": "CS-HANDOFF-01",
        "message": "我发现一笔不是我操作的扣款，请立即转人工，不要自动执行任何操作。",
        "actions": "等待系统完成转人工；不要确认任何交易或售后写操作。",
    },
)


class PilotError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result[:48] or "actor"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mysql(sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "aishop-mysql",
            "sh",
            "-lc",
            'exec mysql --batch --raw --skip-column-names -uroot -p"$MYSQL_ROOT_PASSWORD"',
        ],
        input=sql.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PilotError(f"MySQL command failed: {detail[-1000:]}")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _scalar(sql: str) -> int:
    value = _mysql(sql).splitlines()
    try:
        return int(value[-1]) if value else 0
    except ValueError as exc:
        raise PilotError(f"Expected integer SQL result, got: {value[-1:]}") from exc


def _participant_hash(env: dict[str, str]) -> str:
    secret = env.get("PILOT_IDENTITY_HMAC_SECRET", "development-only-pilot-secret")
    return hmac.new(secret.encode(), DEMO_USER_ID.encode(), hashlib.sha256).hexdigest()


def _reset_demo(env: dict[str, str]) -> None:
    if env.get("APP_ENV", "development").strip().lower() == "production":
        raise PilotError("blackbox pilot reset is forbidden in production")
    known_orders = ",".join(f"'{value}'" for value in SEED_ORDER_IDS)
    participant_hash = _participant_hash(env)
    _mysql(
        f"""
        SET FOREIGN_KEY_CHECKS=0;
        UPDATE aishop_agent.agent_pilot_batch b
        JOIN aishop_agent.agent_pilot_participant p ON p.batch_id=b.batch_id
        SET b.status='CLOSED',b.closed_at=COALESCE(b.closed_at,NOW(3))
        WHERE p.user_id_hash='{participant_hash}' AND b.status='RUNNING';

        DELETE b FROM aishop_agent.ai_badcase_candidate b
          LEFT JOIN aishop_agent.agent_run r ON r.run_id=b.run_id
          LEFT JOIN aishop_agent.agent_message m ON m.message_id=b.message_id
          WHERE r.user_id='{DEMO_USER_ID}' OR m.user_id='{DEMO_USER_ID}';
        DELETE h FROM aishop_agent.agent_handoff h
          LEFT JOIN aishop_agent.agent_run p ON p.run_id=h.parent_run_id
          LEFT JOIN aishop_agent.agent_run c ON c.run_id=h.child_run_id
          WHERE p.user_id='{DEMO_USER_ID}' OR c.user_id='{DEMO_USER_ID}';
        DELETE s FROM aishop_agent.agent_step s
          JOIN aishop_agent.agent_run r ON r.run_id=s.run_id
          WHERE r.user_id='{DEMO_USER_ID}';
        DELETE f FROM aishop_agent.agent_message_feedback f WHERE f.user_id='{DEMO_USER_ID}';
        DELETE sm FROM aishop_agent.support_message sm
          JOIN aishop_agent.support_session ss ON ss.session_id=sm.session_id
          WHERE ss.user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.support_case WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.support_session WHERE user_id='{DEMO_USER_ID}';
        DELETE e FROM aishop_agent.agent_recommendation_explanation e
          JOIN aishop_agent.agent_ranking_policy_decision d ON d.decision_id=e.decision_id
          WHERE d.user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_ranking_policy_decision WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_final_offer_snapshot WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_after_sales_eligibility WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_recommendation_event WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.commerce_outcome_ledger WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_pending_action WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_order_selection WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_visual_selection WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_task WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_shopping_mission WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_shopping_profile WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_session_memory WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_request_idempotency WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_run WHERE user_id='{DEMO_USER_ID}';
        DELETE FROM aishop_agent.agent_message WHERE user_id='{DEMO_USER_ID}';

        DELETE pr FROM aishop_order.order_logistics_info_record pr
          JOIN aishop_order.order_info o ON o.order_id=pr.order_id
          WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN ({known_orders});
        DELETE l FROM aishop_order.order_logistics_info l
          JOIN aishop_order.order_info o ON o.order_id=l.order_id
          WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN ({known_orders});
        DELETE rel FROM aishop_order.order_coupon_rel rel
          JOIN aishop_order.order_info o ON o.order_id=rel.order_id
          WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN ({known_orders});
        DELETE i FROM aishop_order.order_item i
          JOIN aishop_order.order_info o ON o.order_id=i.order_id
          WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN ({known_orders});
        DELETE FROM aishop_pay.pay_trade_record
          WHERE user_id='{DEMO_USER_ID}' AND order_id NOT IN ({known_orders});
        DELETE FROM aishop_order.order_info
          WHERE user_id='{DEMO_USER_ID}' AND order_id NOT IN ({known_orders});
        DELETE FROM aishop_order.order_request_idempotency
          WHERE user_id='{DEMO_USER_ID}' AND command_type='POST_ORDER';
        DELETE FROM aishop_cart.product_cart WHERE user_id='{DEMO_USER_ID}';
        SET FOREIGN_KEY_CHECKS=1;
        """
    )
    seed_database()

    redis_client = redis.Redis(
        host=env.get("REDIS_HOST", "127.0.0.1"),
        port=int(required(env, "REDIS_PORT")),
        db=int(env.get("REDIS_DB", "0")),
        username=env.get("REDIS_USERNAME") or None,
        password=env.get("REDIS_PASSWORD") or None,
        ssl=env.get("REDIS_SSL_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        decode_responses=True,
    )
    patterns = (
        f"mall:*{DEMO_USER_ID}*",
        "aishop:analytics:result:v2:*",
        "aishop:analytics:clarification:v1:*",
    )
    for pattern in patterns:
        keys = list(redis_client.scan_iter(match=pattern, count=200))
        if keys:
            redis_client.delete(*keys)


def _clients(env: dict[str, str]) -> tuple[httpx.Client, httpx.Client, redis.Redis]:
    gateway = f"http://127.0.0.1:{required(env, 'GATEWAY_PORT')}"
    redis_client = redis.Redis(
        host=env.get("REDIS_HOST", "127.0.0.1"),
        port=int(required(env, "REDIS_PORT")),
        db=int(env.get("REDIS_DB", "0")),
        username=env.get("REDIS_USERNAME") or None,
        password=env.get("REDIS_PASSWORD") or None,
        ssl=env.get("REDIS_SSL_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        decode_responses=True,
    )
    return (
        httpx.Client(base_url=gateway, timeout=30, follow_redirects=True, trust_env=False),
        httpx.Client(base_url=gateway, timeout=30, follow_redirects=True, trust_env=False),
        redis_client,
    )


def _admin_post(client: httpx.Client, path: str, data: dict[str, Any], action: str) -> Any:
    return response_data(client.post(path, data=data), action)


def _write_task_card(path: Path, base_url: str) -> None:
    rows = [
        "# AI-Shop 外部 AI 黑盒任务卡",
        "",
        f"- URL: {base_url}/login",
        f"- 账号: {DEMO_USER_EMAIL}",
        f"- 密码: {DEMO_USER_PASSWORD}",
        "- 约束：只能通过可见网页操作；禁止读取仓库、接口、数据库、隐藏 DOM 或预期答案。",
        "- 六项任务按顺序各执行一次，不重跑失败项；不得进行真实支付。",
        "",
    ]
    for index, task in enumerate(TASKS, start=1):
        rows.extend(
            [
                f"## {index}. {task['id']}",
                "",
                f"向 AI 助手发送：`{task['message']}`",
                "",
                str(task["actions"]),
                "",
            ]
        )
    path.write_text("\n".join(rows), encoding="utf-8")


def prepare(actor_label: str, session_number: int) -> None:
    env = load_environment()
    _reset_demo(env)
    session_id = (
        f"{_slug(actor_label)}-s{session_number}-"
        f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    )
    session_dir = RUN_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=False)
    admin, user, redis_client = _clients(env)
    try:
        login_admin(admin, redis_client, env)
        login_user(user, redis_client)
        batch = _admin_post(
            admin,
            "/admin-api/agentMessage/pilotBatches/create",
            {
                "name": f"External AI blackbox {actor_label} session {session_number}",
                "description": "URL-only external AI user simulation; not a human pilot",
                "evidenceSource": "SYNTHETIC",
                "consentTextVersion": "external-ai-blackbox-v1",
            },
            "创建黑盒批次",
        )
        batch_id = str(batch["batchId"])
        _admin_post(
            admin,
            "/admin-api/agentMessage/pilotBatches/participants/register",
            {"batchId": batch_id, "userId": DEMO_USER_ID, "pseudonym": session_id},
            "登记黑盒参与者",
        )
        _admin_post(
            admin,
            "/admin-api/agentMessage/pilotBatches/start",
            {"batchId": batch_id},
            "启动黑盒批次",
        )
    finally:
        admin.close()
        user.close()
        redis_client.close()
    metadata = {
        "schemaVersion": "aishop-external-ai-blackbox-session/v1",
        "sessionId": session_id,
        "actorLabel": actor_label,
        "sessionNumber": session_number,
        "batchId": batch_id,
        "evidenceSource": "SYNTHETIC",
        "realUserStatus": "NOT_COLLECTED",
        "preparedAt": _utcnow(),
        "taskIds": [task["id"] for task in TASKS],
    }
    (session_dir / "session.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    base_url = f"http://127.0.0.1:{required(env, 'WEB_PORT')}"
    _write_task_card(session_dir / "task-card.md", base_url)
    print(session_dir)


def _history(user: httpx.Client) -> list[dict[str, Any]]:
    data = response_data(
        user.post("/api/agent/loadHistoryMessage", data={"pageNo": "1", "pageSize": "100"}),
        "读取黑盒会话",
    )
    return list(data.get("list") or []) if isinstance(data, dict) else []


def _answer(history: list[dict[str, Any]], message: str) -> dict[str, Any]:
    return next((row for row in history if str(row.get("userMessage") or "").strip() == message), {})


def _contains(row: dict[str, Any], *values: str) -> bool:
    text = str(row.get("assistantMessage") or "")
    return all(value in text for value in values)


def _score(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answers = {task["id"]: _answer(history, str(task["message"])) for task in TASKS}
    result: list[dict[str, Any]] = []

    coupon = answers["RAG-01"]
    coupon_pass = bool(coupon) and (
        "一张" in str(coupon.get("assistantMessage") or "")
        or "不支持多张" in str(coupon.get("assistantMessage") or "")
    ) and bool(coupon.get("sourceRefs"))
    result.append({"taskId": "RAG-01", "passed": coupon_pass})

    impressions = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.agent_recommendation_event "
        f"WHERE user_id='{DEMO_USER_ID}' AND event_type='IMPRESSION';"
    )
    clicks = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.agent_recommendation_event "
        f"WHERE user_id='{DEMO_USER_ID}' AND event_type='CLICK';"
    )
    result.append({"taskId": "SHOP-01", "passed": bool(answers["SHOP-01"]) and impressions > 0 and clicks > 0})

    new_orders = _scalar(
        f"SELECT COUNT(*) FROM aishop_order.order_info WHERE user_id='{DEMO_USER_ID}' "
        f"AND order_id NOT IN ({','.join(repr(value) for value in SEED_ORDER_IDS)}) AND order_status=0;"
    )
    attributed_items = _scalar(
        f"SELECT COUNT(*) FROM aishop_order.order_item i JOIN aishop_order.order_info o ON o.order_id=i.order_id "
        f"WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN "
        f"({','.join(repr(value) for value in SEED_ORDER_IDS)}) AND i.ai_request_id IS NOT NULL;"
    )
    logistics = _scalar(
        f"SELECT COUNT(*) FROM aishop_order.order_logistics_info l JOIN aishop_order.order_info o ON o.order_id=l.order_id "
        f"WHERE o.user_id='{DEMO_USER_ID}' AND o.order_id NOT IN ({','.join(repr(value) for value in SEED_ORDER_IDS)});"
    )
    paid = _scalar(
        f"SELECT COUNT(*) FROM aishop_pay.pay_trade_record WHERE user_id='{DEMO_USER_ID}' "
        f"AND order_id NOT IN ({','.join(repr(value) for value in SEED_ORDER_IDS)}) AND trade_status=1;"
    )
    result.append(
        {
            "taskId": "TX-01",
            "passed": bool(answers["TX-01"]) and new_orders == 1 and attributed_items >= 1 and logistics == 1 and paid == 0,
            "facts": {"waitPaymentOrders": new_orders, "attributedItems": attributed_items, "logistics": logistics},
        }
    )

    logistics_answer = answers["CS-READ-01"]
    result.append(
        {
            "taskId": "CS-READ-01",
            "passed": bool(logistics_answer)
            and _contains(logistics_answer, "SFDEMO202608050003", "深圳南山营业点派送中"),
        }
    )

    cases = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.support_case WHERE user_id='{DEMO_USER_ID}' "
        "AND status='OPEN' AND action_token IS NOT NULL;"
    )
    executed_support = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.agent_pending_action WHERE user_id='{DEMO_USER_ID}' "
        "AND action_type='CREATE_SUPPORT_CASE' AND status='EXECUTED';"
    )
    result.append(
        {
            "taskId": "CS-WRITE-01",
            "passed": bool(answers["CS-WRITE-01"]) and cases == 1 and executed_support == 1,
            "facts": {"openCases": cases, "executedActions": executed_support},
        }
    )

    handoff = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.support_session WHERE user_id='{DEMO_USER_ID}' "
        "AND trigger_reason IS NOT NULL;"
    )
    other_actions = _scalar(
        f"SELECT COUNT(*) FROM aishop_agent.agent_pending_action WHERE user_id='{DEMO_USER_ID}' "
        "AND action_type<>'CREATE_SUPPORT_CASE';"
    )
    result.append(
        {
            "taskId": "CS-HANDOFF-01",
            "passed": bool(answers["CS-HANDOFF-01"]) and handoff >= 1 and other_actions == 0,
            "facts": {"handoffs": handoff, "unexpectedActions": other_actions},
        }
    )
    return result


def finalize(session_id: str) -> None:
    env = load_environment()
    session_dir = (RUN_ROOT / session_id).resolve()
    if not session_dir.is_relative_to(RUN_ROOT.resolve()) or not session_dir.is_dir():
        raise PilotError("unknown blackbox session")
    metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    admin, user, redis_client = _clients(env)
    try:
        login_admin(admin, redis_client, env)
        login_user(user, redis_client)
        history = _history(user)
        _admin_post(
            admin,
            "/admin-api/agentMessage/pilotBatches/close",
            {"batchId": metadata["batchId"]},
            "关闭黑盒批次",
        )
        response = admin.post(
            "/admin-api/agentMessage/pilotBatches/report",
            data={"batchId": metadata["batchId"], "format": "json"},
        )
        response.raise_for_status()
        (session_dir / "pilot-report.json").write_bytes(response.content)
    finally:
        admin.close()
        user.close()
        redis_client.close()
    task_results = _score(history)
    final = {
        **metadata,
        "finalizedAt": _utcnow(),
        "status": "PASS" if all(row["passed"] for row in task_results) else "FAIL",
        "taskResults": task_results,
        "taskSuccessCount": sum(bool(row["passed"]) for row in task_results),
        "taskCount": len(task_results),
        "boundaries": {
            "realUser": False,
            "productionSlo": False,
            "finalUnseen": False,
            "llmJudge": False,
        },
    }
    report_path = session_dir / "result.json"
    report_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = [
        f"{_sha256(path)}  {path.name}"
        for path in sorted(session_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (session_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(report_path)


def aggregate(root: Path) -> None:
    root = root.resolve()
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*/result.json"))]
    actors = sorted({str(report["actorLabel"]) for report in reports})
    task_totals = {
        task["id"]: sum(
            bool(row["passed"])
            for report in reports
            for row in report["taskResults"]
            if row["taskId"] == task["id"]
        )
        for task in TASKS
    }
    actor_totals = {
        actor: sum(
            bool(row["passed"])
            for report in reports
            if report["actorLabel"] == actor
            for row in report["taskResults"]
        )
        for actor in actors
    }
    total = sum(task_totals.values())
    complete = len(reports) == 6 and len(actors) == 2
    gates = {
        "sixSessionsTwoActors": complete,
        "overallAtLeast30Of36": total >= 30,
        "eachActorAtLeast14Of18": complete and all(value >= 14 for value in actor_totals.values()),
        "eachTaskAtLeast4Of6": complete and all(value >= 4 for value in task_totals.values()),
        "transactionAndWriteAtLeast5Of6": complete
        and task_totals.get("TX-01", 0) >= 5
        and task_totals.get("CS-WRITE-01", 0) >= 5,
    }
    payload = {
        "schemaVersion": "aishop-external-ai-blackbox-aggregate/v1",
        "generatedAt": _utcnow(),
        "evidenceSource": "SYNTHETIC",
        "realUserStatus": "NOT_COLLECTED",
        "status": "PASS" if all(gates.values()) else "FAIL",
        "sessionCount": len(reports),
        "actors": actors,
        "taskSuccessCount": total,
        "taskCount": len(reports) * len(TASKS),
        "actorSuccess": actor_totals,
        "taskSuccess": task_totals,
        "gates": gates,
    }
    output = root / "synthetic-blackbox-report.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--actor-label", required=True)
    prepare_parser.add_argument("--session", type=int, choices=(1, 2, 3), required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--session-id", required=True)
    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--root", type=Path, default=RUN_ROOT)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.actor_label, args.session)
    elif args.command == "finalize":
        finalize(args.session_id)
    else:
        aggregate(args.root)


if __name__ == "__main__":
    try:
        main()
    except PilotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
