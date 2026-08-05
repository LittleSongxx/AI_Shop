#!/usr/bin/env python3
"""Prepare and verify the deterministic Smarlect local demo."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import redis
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENV = ROOT / "run" / "runtime.env"
LOCAL_ENV = ROOT / ".env.local"
SEED_SQL = ROOT / "AI_Shop-backend" / "data" / "03_smarlect_demo_seed.sql"
KNOWLEDGE_DIR = ROOT / "AI_Shop-backend" / "data" / "demo_knowledge"

DEMO_USER_EMAIL = "demo@smarlect.local"
DEMO_USER_PASSWORD = "Demo1234"
AI_DEMO_MESSAGE = (
    "我预算 4500 元，主要在通勤时使用，想买降噪耳机，请比较推荐并说明理由。"
)
FAQ_VECTOR_IDS = [f"faq{question_id}" for question_id in range(9001, 9007)]


class BootstrapError(RuntimeError):
    pass


def load_environment() -> dict[str, str]:
    if not RUNTIME_ENV.is_file():
        raise BootstrapError(f"运行时配置不存在：{RUNTIME_ENV}")
    values: dict[str, str] = {}
    for path in (RUNTIME_ENV, LOCAL_ENV):
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None:
                values[str(key)] = str(value)
    return values


def required(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise BootstrapError(f"缺少配置：{key}")
    return value


def seed_database() -> None:
    if not SEED_SQL.is_file():
        raise BootstrapError(f"演示 SQL 不存在：{SEED_SQL}")
    command = [
        "docker",
        "exec",
        "-i",
        "aishop-mysql",
        "sh",
        "-lc",
        'exec mysql --default-character-set=utf8mb4 -uroot -p"$MYSQL_ROOT_PASSWORD"',
    ]
    result = subprocess.run(
        command,
        input=SEED_SQL.read_bytes(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise BootstrapError(f"演示 SQL 导入失败：{detail[-1000:]}")


def response_data(response: httpx.Response, action: str) -> Any:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BootstrapError(f"{action}失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise BootstrapError(f"{action}失败：响应不是 JSON 对象")
    status = payload.get("status")
    code = payload.get("code", 200)
    if status == "error" or code not in (None, 200, "200"):
        raise BootstrapError(
            f"{action}失败：{payload.get('info') or payload.get('detail') or payload}"
        )
    return payload.get("data")


def captcha_code(
    client: httpx.Client,
    redis_client: redis.Redis,
    path: str,
    *,
    method: str,
) -> tuple[str, str]:
    response = client.request(method, path)
    data = response_data(response, "获取验证码")
    if not isinstance(data, dict) or not data.get("checkCodeKey"):
        raise BootstrapError("验证码接口未返回 checkCodeKey")
    key = str(data["checkCodeKey"])
    raw = redis_client.get(f"mall:checkcode:{key}")
    if raw is None:
        raise BootstrapError("Redis 中未找到验证码")
    text = str(raw).strip()
    try:
        decoded = json.loads(text)
        code = str(decoded)
    except json.JSONDecodeError:
        code = text.strip('"')
    if not code:
        raise BootstrapError("Redis 中的验证码为空")
    return key, code


def login_admin(
    client: httpx.Client,
    redis_client: redis.Redis,
    env: dict[str, str],
) -> None:
    key, code = captcha_code(
        client, redis_client, "/admin-api/account/checkCode", method="POST"
    )
    response_data(
        client.post(
            "/admin-api/account/login",
            data={
                "account": required(env, "ADMIN_ACCOUNT"),
                "password": required(env, "ADMIN_PASSWORD"),
                "checkCode": code,
                "checkCodeKey": key,
            },
        ),
        "管理员登录",
    )
    if not client.cookies.get("adminToken"):
        raise BootstrapError("管理员登录成功但未收到 adminToken Cookie")


def login_user(client: httpx.Client, redis_client: redis.Redis) -> None:
    key, code = captcha_code(
        client, redis_client, "/api/account/checkCode", method="GET"
    )
    response_data(
        client.post(
            "/api/account/login",
            data={
                "email": DEMO_USER_EMAIL,
                "password": DEMO_USER_PASSWORD,
                "checkCode": code,
                "checkCodeKey": key,
            },
        ),
        "演示用户登录",
    )
    if not client.cookies.get("token"):
        raise BootstrapError("演示用户登录成功但未收到 token Cookie")


def configure_rewards(client: httpx.Client) -> None:
    response_data(
        client.post(
            "/admin-api/signRewardConfig/saveConfig",
            data={"enabled": "true", "couponId": "SM_MEMBER_30", "streakDays": "7"},
        ),
        "保存连续签到奖励",
    )
    sign_config = response_data(
        client.post("/admin-api/signRewardConfig/getConfig"),
        "读取连续签到奖励",
    )
    if not isinstance(sign_config, dict) or (
        sign_config.get("couponId") != "SM_MEMBER_30"
        or int(sign_config.get("streakDays") or 0) != 7
        or sign_config.get("enabled") is not True
    ):
        raise BootstrapError(f"连续签到奖励回读不一致：{sign_config}")

    response_data(
        client.post(
            "/admin-api/memberLevelRewardConfig/saveConfig",
            data={
                "level2CouponId": "SM_MEMBER_30",
                "level3CouponId": "SM_MEMBER_100",
            },
        ),
        "保存会员升级礼",
    )
    member_config = response_data(
        client.post("/admin-api/memberLevelRewardConfig/getConfig"),
        "读取会员升级礼",
    )
    if not isinstance(member_config, dict) or (
        member_config.get("level2CouponId") != "SM_MEMBER_30"
        or member_config.get("level3CouponId") != "SM_MEMBER_100"
    ):
        raise BootstrapError(f"会员升级礼回读不一致：{member_config}")


def sync_sign_cache(client: httpx.Client) -> None:
    response_data(
        client.post("/admin-api/signRecord/syncAllFromDb", data={"force": "true"}),
        "同步签到统计缓存",
    )
    response_data(
        client.post(
            "/admin-api/signRecord/syncSignDatesFromDb",
            data={
                "syncEndDate": (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            },
        ),
        "同步签到日历缓存",
    )


def document_title(path: Path) -> str:
    first_line = path.read_text(encoding="utf-8").splitlines()[0].strip()
    return first_line.removeprefix("#").strip() or path.stem


def publish_knowledge(client: httpx.Client) -> tuple[int, int]:
    files = sorted(KNOWLEDGE_DIR.glob("*.md"))
    if len(files) != 4:
        raise BootstrapError(f"预期 4 份知识文档，实际找到 {len(files)} 份")
    published = 0
    existing = 0
    for path in files:
        data = response_data(
            client.post(
                "/admin-api/knowledge/upload",
                data={"title": document_title(path)},
                files={"file": (path.name, path.read_bytes(), "text/markdown")},
            ),
            f"上传知识文档 {path.name}",
        )
        if not isinstance(data, dict) or not data.get("documentId"):
            raise BootstrapError(f"上传 {path.name} 未返回 documentId")
        status = str(data.get("status") or "").upper()
        if status == "PUBLISHED":
            existing += 1
            continue
        if status != "READY":
            raise BootstrapError(f"知识文档 {path.name} 状态异常：{status or 'UNKNOWN'}")
        result = response_data(
            client.post(
                "/admin-api/knowledge/publish",
                data={"documentId": str(data["documentId"])},
            ),
            f"发布知识文档 {path.name}",
        )
        if not isinstance(result, dict) or result.get("status") != "PUBLISHED":
            raise BootstrapError(f"知识文档 {path.name} 发布状态异常：{result}")
        published += 1
    return published, existing


def sync_faq_vectors(env: dict[str, str]) -> None:
    search_url = f"http://127.0.0.1:{required(env, 'SEARCH_PORT')}"
    with httpx.Client(base_url=search_url, timeout=30, trust_env=False) as client:
        response_data(
            client.post(
                "/internal/search/tool/ragData",
                headers={"X-Internal-Token": required(env, "AISHOP_INTERNAL_TOKEN")},
            ),
            "触发 FAQ 向量同步",
        )


def vector_count(client: httpx.Client) -> int:
    response = client.get("/aishop_vectorstore/_count")
    try:
        response.raise_for_status()
        payload = response.json()
        return int(payload.get("count") or 0)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise BootstrapError(f"读取向量索引数量失败：{exc}") from exc


def wait_for_vectors(env: dict[str, str], wait_seconds: int) -> tuple[int, int]:
    es_url = f"http://127.0.0.1:{required(env, 'ES_PORT')}"
    deadline = time.monotonic() + wait_seconds
    last_total = 0
    last_faq = 0
    with httpx.Client(base_url=es_url, timeout=15, trust_env=False) as client:
        while time.monotonic() < deadline:
            last_total = vector_count(client)
            response = client.post(
                "/aishop_vectorstore/_mget", json={"ids": FAQ_VECTOR_IDS}
            )
            try:
                response.raise_for_status()
                documents = response.json().get("docs") or []
                last_faq = sum(1 for item in documents if item.get("found"))
            except (httpx.HTTPError, ValueError, TypeError):
                last_faq = 0
            if last_faq == len(FAQ_VECTOR_IDS) and last_total > last_faq:
                return last_total, last_faq
            time.sleep(2)
    raise BootstrapError(
        f"向量同步超时：总文档 {last_total}，演示 FAQ {last_faq}/{len(FAQ_VECTOR_IDS)}"
    )


def page_count(data: Any) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("totalCount", "total", "count"):
            if data.get(key) is not None:
                try:
                    return int(data[key])
                except (TypeError, ValueError):
                    pass
        if isinstance(data.get("list"), list):
            return len(data["list"])
    return 0


def verify_user_features(client: httpx.Client) -> dict[str, int]:
    checks = (
        ("地址", "GET", "/api/userAddress/loadDataList", {}, 1),
        ("收藏", "POST", "/api/userFavorite/loadFavorite", {"pageNo": "1"}, 4),
        ("浏览记录", "POST", "/api/userBrowse/loadBrowse", {"pageNo": "1"}, 6),
        ("购物车", "POST", "/api/productCart/loadProductCart", {"pageNo": "1"}, 3),
        ("优惠券", "POST", "/api/discountCoupon/loadUserCoupon", {"pageNo": "1"}, 5),
        ("订单", "POST", "/api/order/loadMyOrder", {"pageNo": "1"}, 7),
        (
            "待评价订单",
            "POST",
            "/api/order/loadMyOrder",
            {"pageNo": "1", "status": "8"},
            1,
        ),
        (
            "通知",
            "POST",
            "/api/userNotification/loadNotification",
            {"pageNo": "1"},
            4,
        ),
    )
    counts: dict[str, int] = {}
    for label, method, path, form, minimum in checks:
        response = client.request(method, path, data=form or None)
        count = page_count(response_data(response, f"检查{label}"))
        if count < minimum:
            raise BootstrapError(f"{label}演示数据不足：期望至少 {minimum}，实际 {count}")
        counts[label] = count

    member = response_data(client.get("/api/userMember/getProfile"), "检查会员资料")
    if not isinstance(member, dict) or int(member.get("levelCode") or 0) < 1:
        raise BootstrapError(f"会员资料异常：{member}")
    response_data(
        client.post(
            "/api/sign/getSignCalendar",
            data={"yearMonth": datetime.now().strftime("%Y%m")},
        ),
        "检查签到日历",
    )
    return counts


def load_history(client: httpx.Client) -> list[dict[str, Any]]:
    data = response_data(
        client.post("/api/agent/loadHistoryMessage", data={"pageNo": "1"}),
        "读取 AI 会话",
    )
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise BootstrapError("AI 会话接口返回格式异常")
    return [item for item in data["list"] if isinstance(item, dict)]


def normalize_agent_message(value: object) -> str:
    """Mirror the Agent input normalization used before messages are stored."""
    text = unicodedata.normalize("NFKC", value if isinstance(value, str) else "")
    text = "".join(
        char
        for char in text
        if char in "\n\r\t" or not unicodedata.category(char).startswith("C")
    )
    return re.sub(r"[ \t]+", " ", text).strip()


def find_demo_ai_message(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    expected = normalize_agent_message(AI_DEMO_MESSAGE)
    return next(
        (
            item
            for item in history
            if normalize_agent_message(item.get("userMessage")) == expected
        ),
        None,
    )


def ensure_ai_message(client: httpx.Client, wait_seconds: int) -> tuple[int, int]:
    history = load_history(client)
    existing = find_demo_ai_message(history)
    if existing and str(existing.get("assistantMessage") or "").strip():
        return int(existing["messageId"]), len(str(existing["assistantMessage"]))

    if existing:
        message_id = int(existing["messageId"])
    else:
        data = response_data(
            client.post(
                "/api/agent/sendMessage",
                data={"message": AI_DEMO_MESSAGE, "fromProduct": "false"},
            ),
            "发送 AI 演示消息",
        )
        if not isinstance(data, dict) or not data.get("messageId"):
            raise BootstrapError("AI 演示消息未返回 messageId")
        message_id = int(data["messageId"])
        immediate = str(data.get("assistantMessage") or "").strip()
        if immediate:
            return message_id, len(immediate)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(2)
        item = next(
            (row for row in load_history(client) if int(row.get("messageId") or 0) == message_id),
            None,
        )
        answer = str((item or {}).get("assistantMessage") or "").strip()
        if answer:
            return message_id, len(answer)
    raise BootstrapError(f"AI 演示消息 {message_id} 在 {wait_seconds}s 内未完成")


def check_services(env: dict[str, str]) -> None:
    gateway = f"http://127.0.0.1:{required(env, 'GATEWAY_PORT')}"
    agent = f"http://127.0.0.1:{required(env, 'AGENT_PORT')}"
    with httpx.Client(timeout=10, trust_env=False) as client:
        for label, url in (
            ("Gateway", f"{gateway}/actuator/health"),
            ("Agent", f"{agent}/health/ready"),
        ):
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise BootstrapError(f"{label} 未就绪：{exc}") from exc


def run(args: argparse.Namespace) -> None:
    env = load_environment()
    gateway_url = f"http://127.0.0.1:{required(env, 'GATEWAY_PORT')}"
    redis_client = redis.Redis(
        host=env.get("REDIS_HOST", "127.0.0.1"),
        port=int(required(env, "REDIS_PORT")),
        password=env.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

    print("[1/8] 检查本地服务")
    check_services(env)
    redis_client.ping()

    if args.skip_sql:
        print("[2/8] 跳过演示 SQL（--skip-sql）")
    else:
        print("[2/8] 导入幂等演示 SQL")
        seed_database()

    timeout = httpx.Timeout(30, connect=5)
    with httpx.Client(
        base_url=gateway_url, timeout=timeout, trust_env=False, follow_redirects=True
    ) as admin_client:
        print("[3/8] 登录管理员并配置签到、会员奖励")
        login_admin(admin_client, redis_client, env)
        configure_rewards(admin_client)
        sync_sign_cache(admin_client)

        print("[4/8] 上传并发布 Smarlect 知识文档")
        published, existing = publish_knowledge(admin_client)
        print(f"      新发布 {published} 份，已存在 {existing} 份")

    print("[5/8] 同步 FAQ 向量并等待索引完成")
    sync_faq_vectors(env)
    vector_total, faq_total = wait_for_vectors(env, args.wait_seconds)
    print(f"      向量索引 {vector_total} 条，其中演示 FAQ {faq_total} 条")

    with httpx.Client(
        base_url=gateway_url, timeout=timeout, trust_env=False, follow_redirects=True
    ) as user_client:
        print("[6/8] 登录演示用户并检查核心业务页面")
        login_user(user_client, redis_client)
        counts = verify_user_features(user_client)
        print("      " + "，".join(f"{name} {count}" for name, count in counts.items()))

        if args.skip_ai_message:
            print("[7/8] 跳过 AI 演示消息（--skip-ai-message）")
        else:
            print("[7/8] 创建或复用首条真实 AI 演示会话")
            message_id, answer_length = ensure_ai_message(user_client, args.wait_seconds)
            print(f"      消息 {message_id} 已完成，回复长度 {answer_length}")

    print("[8/8] Smarlect 本地演示初始化完成")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-sql", action="store_true", help="不重复导入演示 SQL")
    parser.add_argument(
        "--skip-ai-message", action="store_true", help="不创建或检查 AI 演示消息"
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="等待向量与 AI 任务的最长秒数（默认 120）",
    )
    args = parser.parse_args()
    if args.wait_seconds < 10 or args.wait_seconds > 600:
        parser.error("--wait-seconds 必须在 10..600 之间")
    return args


if __name__ == "__main__":
    try:
        run(parse_args())
    except (BootstrapError, redis.RedisError, OSError) as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
