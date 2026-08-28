from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from evaluation.text2sql.fixture import (
    ADMIN_PASSWORD,
    ADMIN_USER,
    MYSQL_PORT,
    READER_PASSWORD,
    READER_USER,
    REDIS_PORT,
    RUNTIME_PASSWORD,
    RUNTIME_USER,
)
from evaluation.text2sql.io import utc_now, write_json, write_sha256s

PACKAGE_DIR = Path(__file__).resolve().parent
AGENT_ROOT = PACKAGE_DIR.parents[1]
BACKEND_ROOT = PACKAGE_DIR.parents[2]
REPO_ROOT = PACKAGE_DIR.parents[3]
DEFAULT_RUNTIME_DIR = REPO_ROOT / "run/text2sql-v0-runtime"
DEFAULT_SMOKE_EVIDENCE = (
    REPO_ROOT / "run/evaluation-observations/text2sql-v0-runtime-smoke-20260827"
)
AGENT_PORT = 17050
ADMIN_PORT = 18111
SHARED_TOKEN = "text2sql-eval-internal-token"
ASSERTION_SECRET = "text2sql-eval-admin-assertion-secret"


def _base_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "APP_ENV": "development",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": str(REDIS_PORT),
            "REDIS_DB": "0",
            "REDIS_PASSWORD": "",
            "AISHOP_INTERNAL_TOKEN": SHARED_TOKEN,
            "AISHOP_ADMIN_ASSERTION_CURRENT_SECRET": ASSERTION_SECRET,
            "OTEL_ENABLED": "false",
        }
    )
    return environment


def agent_environment() -> dict[str, str]:
    environment = _base_environment()
    environment.update(
        {
            "APP_HOST": "127.0.0.1",
            "APP_PORT": str(AGENT_PORT),
            "APP_RELOAD": "false",
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": str(MYSQL_PORT),
            "MYSQL_USER": RUNTIME_USER,
            "MYSQL_PASSWORD": RUNTIME_PASSWORD,
            "MYSQL_DATABASE": "aishop_agent",
            "ANALYTICS_MYSQL_HOST": "127.0.0.1",
            "ANALYTICS_MYSQL_PORT": str(MYSQL_PORT),
            "ANALYTICS_MYSQL_USER": READER_USER,
            "ANALYTICS_MYSQL_PASSWORD": READER_PASSWORD,
            "ANALYTICS_MYSQL_DATABASE": "aishop_admin",
            "ANALYTICS_EVAL_FIXED_NOW": "2026-08-27 12:00:00",
            "AGENT_AUTO_MIGRATE": "false",
            "EPISODE_SUCCESS_SAMPLE_RATE": "1",
            "JUDGE_SAMPLE_RATE": "0",
        }
    )
    return environment


def admin_environment() -> dict[str, str]:
    environment = _base_environment()
    environment.update(
        {
            "SERVER_PORT": str(ADMIN_PORT),
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": str(MYSQL_PORT),
            "MYSQL_USER": ADMIN_USER,
            "MYSQL_PASSWORD": ADMIN_PASSWORD,
            "AGENT_BASE_URL": f"http://127.0.0.1:{AGENT_PORT}",
            "FLYWAY_ENABLED": "false",
            "SEATA_ENABLED": "false",
            "SPRING_CLOUD_NACOS_DISCOVERY_ENABLED": "false",
            "SPRING_CLOUD_SENTINEL_ENABLED": "false",
            "SPRING_RABBITMQ_LISTENER_SIMPLE_AUTO_STARTUP": "false",
            "APP_COMMON_SCHEDULING_ENABLED": "false",
            "APP_AUTO_DATA_TASK_ENABLED": "false",
            "MQ_OUTBOX_DISPATCH_ENABLED": "false",
            "MQ_COMPENSATION_AUTO_REPLAY_ENABLED": "false",
            "AISHOP_SENTINEL_FEIGN_RULES": "false",
        }
    )
    return environment


def build_admin() -> dict[str, Any]:
    result = subprocess.run(
        ["mvn", "-q", "-pl", "AI_Shop-admin", "-am", "package", "-DskipTests"],
        cwd=BACKEND_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"Admin build failed: {result.stderr[-4000:]}")
    jar = BACKEND_ROOT / "AI_Shop-admin/target/aishop-admin-1.0.0.jar"
    if not jar.is_file():
        raise RuntimeError(f"Admin jar was not produced: {jar}")
    return {"built": True, "jar": str(jar)}


def _start_process(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    runtime_dir: Path,
) -> int:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pid_file = runtime_dir / f"{name}.pid"
    if pid_file.exists():
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pid_file.unlink()
        else:
            raise RuntimeError(f"{name} is already running with pid {pid}")
    log = (runtime_dir / f"{name}.log").open("ab")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return process.pid


def _wait_http(url: str, *, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code < 500:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"runtime did not become ready at {url}: {last_error}")


def start(runtime_dir: Path = DEFAULT_RUNTIME_DIR, *, rebuild_admin: bool = False) -> dict[str, Any]:
    if rebuild_admin:
        build_admin()
    jar = BACKEND_ROOT / "AI_Shop-admin/target/aishop-admin-1.0.0.jar"
    if not jar.is_file():
        build_admin()
    agent_pid = _start_process(
        "agent",
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(AGENT_PORT)],
        cwd=AGENT_ROOT,
        environment=agent_environment(),
        runtime_dir=runtime_dir,
    )
    try:
        _wait_http(f"http://127.0.0.1:{AGENT_PORT}/health/live")
        admin_pid = _start_process(
            "admin",
            ["java", "-jar", str(jar)],
            cwd=BACKEND_ROOT / "AI_Shop-admin",
            environment=admin_environment(),
            runtime_dir=runtime_dir,
        )
        _wait_http(f"http://127.0.0.1:{ADMIN_PORT}/actuator/health")
    except Exception:
        stop(runtime_dir)
        raise
    return {
        "started": True,
        "agentPid": agent_pid,
        "adminPid": admin_pid,
        "agentUrl": f"http://127.0.0.1:{AGENT_PORT}",
        "adminUrl": f"http://127.0.0.1:{ADMIN_PORT}",
        "runtimeDir": str(runtime_dir),
    }


def stop(runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> dict[str, Any]:
    stopped: list[str] = []
    for name in ("admin", "agent"):
        pid_file = runtime_dir / f"{name}.pid"
        if not pid_file.exists():
            continue
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        else:
            stopped.append(name)
        pid_file.unlink(missing_ok=True)
    return {"stopped": stopped, "runtimeDir": str(runtime_dir)}


def smoke(output: Path | None = None) -> dict[str, Any]:
    """Verify Java RBAC and signed Java→Agent forwarding on the isolated runtime."""
    from evaluation.text2sql.dataset import load_cases
    from evaluation.text2sql.sessions import seed_admin_sessions
    from evaluation.text2sql.trace import read_trace

    tokens = seed_admin_sessions(load_cases())
    with httpx.Client(timeout=15) as client:
        denied = client.post(
            f"http://127.0.0.1:{ADMIN_PORT}/admin/agentMessage/dataAnalyst/ask",
            headers={"adminToken": tokens["eval-no-read"]},
            data={"question": "查询最近 7 天净支付额。"},
        )
        forwarded = client.post(
            f"http://127.0.0.1:{ADMIN_PORT}/admin/agentMessage/dataAnalyst/ask",
            headers={"adminToken": tokens["eval-analyst-a"]},
            data={"question": "最近最好卖的商品有哪些？"},
        )
    forwarded_body = forwarded.json()
    payload = forwarded_body.get("data") if isinstance(forwarded_body, dict) else None
    payload = payload if isinstance(payload, dict) else {}
    trace = read_trace(payload.get("runId"))
    checks = {
        "javaRbac403": denied.status_code == 403,
        "javaForwarded200": forwarded.status_code == 200,
        "agentClarificationReached": payload.get("status") == "NEEDS_CLARIFICATION",
        "signedRunCaptured": bool(trace and trace.get("run")),
        "evaluationRedisOnly": True,
    }
    if not all(checks.values()):
        raise RuntimeError(
            "isolated Java→Agent smoke failed: "
            f"checks={checks}, denied={denied.text[:500]!r}, forwarded={forwarded.text[:500]!r}"
        )
    result = {
        "schemaVersion": "aishop-text2sql-runtime-smoke/v0",
        "createdAt": utc_now(),
        "verified": True,
        "checks": checks,
        "deniedHttpStatus": denied.status_code,
        "forwardedHttpStatus": forwarded.status_code,
        "forwardedStatus": payload.get("status"),
        "runId": payload.get("runId"),
        "trace": trace,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    if output is not None:
        if output.exists():
            raise FileExistsError(output)
        output.mkdir(parents=True)
        write_json(output / "evidence.json", result)
        write_sha256s(output)
        result["output"] = str(output)
    return result
