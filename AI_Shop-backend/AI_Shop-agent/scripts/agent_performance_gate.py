"""End-to-end Agent WebSocket performance and terminal-state gate.

Open the WebSocket before each HTTP enqueue, correlate frames by messageId,
and query the persisted Episode for MQ queue wait. Deterministic mode enforces
the local hard SLO; live mode records latency and gates only terminal success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.harness.performance_gate import (  # noqa: E402
    DeterministicThresholds,
    evaluate_performance_gate,
    summarize_results,
)

_DIRECT_DELIVERY_STATES = frozenset({"FAQ_FAST_PATH", "HUMAN_SUPPORT", "DEGRADED", "DUPLICATE"})


class TokenPacer:
    def __init__(self, tokens: list[str], interval_seconds: float) -> None:
        self._interval = max(0.0, interval_seconds)
        self._locks = {token: asyncio.Lock() for token in tokens}
        self._next_send = {token: 0.0 for token in tokens}

    async def wait(self, token: str) -> None:
        async with self._locks[token]:
            now = asyncio.get_running_loop().time()
            delay = self._next_send[token] - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_send[token] = asyncio.get_running_loop().time() + self._interval


async def _queue_wait_ms(
    client: httpx.AsyncClient,
    *,
    trace_url: str,
    internal_token: str,
    run_id: str,
    delivery_state: str,
    timeout_seconds: float,
) -> float | None:
    if delivery_state in _DIRECT_DELIVERY_STATES:
        return 0.0
    if not internal_token or not run_id:
        return None
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = await client.post(
                trace_url,
                headers={"X-Internal-Token": internal_token},
                json={"runId": run_id},
            )
            payload = response.json()
            detail = payload.get("data") if payload.get("code") == 200 else None
            if isinstance(detail, dict):
                started = _parse_time(detail.get("startedAt"))
                receives = [
                    _parse_time(step.get("occurredAt"))
                    for step in detail.get("steps") or []
                    if step.get("eventType") == "MQ_RECEIVE"
                ]
                receives = [value for value in receives if value is not None]
                if started is not None and receives:
                    return max(0.0, (min(receives) - started).total_seconds() * 1_000)
        except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError):
            pass
        await asyncio.sleep(0.1)
    return None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def _run_one(
    index: int,
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    pacer: TokenPacer,
    token: str,
    http_url: str,
    ws_url: str,
    trace_url: str,
    internal_token: str,
    message: str,
    timeout_seconds: float,
    trace_timeout_seconds: float,
    require_queue_trace: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "index": index,
        "enqueueMs": None,
        "queueMs": None,
        "ttftMs": None,
        "totalMs": None,
        "terminalSuccess": False,
        "error": None,
    }
    await pacer.wait(token)
    async with semaphore:
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"token": token},
                open_timeout=timeout_seconds,
                close_timeout=2,
            ) as websocket:
                started = time.perf_counter()
                response = await client.post(
                    http_url,
                    headers={"token": token},
                    data={"message": message},
                )
                result["enqueueMs"] = (time.perf_counter() - started) * 1_000
                if response.status_code != 200:
                    result["error"] = f"HTTP_{response.status_code}"
                    return result
                payload = response.json()
                if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
                    result["error"] = f"APP_{payload.get('code', 'INVALID')}"
                    return result
                data = payload["data"]
                message_id = str(data.get("messageId") or "")
                run_id = str(data.get("runId") or "")
                delivery_state = str(data.get("deliveryState") or "")
                if not message_id:
                    result["error"] = "MISSING_MESSAGE_ID"
                    return result

                deadline = started + timeout_seconds
                while time.perf_counter() < deadline:
                    remaining = max(0.01, deadline - time.perf_counter())
                    raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    frame = json.loads(raw)
                    if str(frame.get("messageId") or "") != message_id:
                        continue
                    elapsed_ms = (time.perf_counter() - started) * 1_000
                    if result["ttftMs"] is None:
                        result["ttftMs"] = elapsed_ms
                    output_type = int(frame.get("outPutType", -1))
                    if output_type in {1, 2}:
                        result["totalMs"] = elapsed_ms
                        result["terminalSuccess"] = output_type == 1
                        if output_type == 2:
                            result["error"] = "TERMINAL_ERROR"
                        break
                if result["totalMs"] is None:
                    result["error"] = "TERMINAL_TIMEOUT"
                result["queueMs"] = await _queue_wait_ms(
                    client,
                    trace_url=trace_url,
                    internal_token=internal_token,
                    run_id=run_id,
                    delivery_state=delivery_state,
                    timeout_seconds=trace_timeout_seconds,
                )
                if require_queue_trace and result["queueMs"] is None and not result["error"]:
                    result["error"] = "QUEUE_TRACE_UNAVAILABLE"
                return result
        except asyncio.TimeoutError:
            result["error"] = "TIMEOUT"
        except (httpx.HTTPError, websockets.WebSocketException, json.JSONDecodeError) as exc:
            result["error"] = type(exc).__name__.upper()
        return result


async def run(args: argparse.Namespace, tokens: list[str]) -> dict[str, Any]:
    requests = args.requests or (100 if args.mode == "deterministic" else 20)
    concurrency = args.concurrency or (10 if args.mode == "deterministic" else 2)
    if len(tokens) < concurrency:
        raise ValueError(
            f"at least {concurrency} distinct test tokens are required; "
            "one user cannot represent concurrent load under the per-user rate limit"
        )
    internal_token = os.getenv(args.internal_token_env, "").strip()
    if args.mode == "deterministic" and not internal_token:
        raise ValueError(f"{args.internal_token_env} is required to measure persisted queue wait")

    semaphore = asyncio.Semaphore(concurrency)
    pacer = TokenPacer(tokens, args.per_token_interval)
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=max(20, concurrency * 3),
        max_keepalive_connections=max(10, concurrency * 2),
    )
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(
            *[
                _run_one(
                    index,
                    client=client,
                    semaphore=semaphore,
                    pacer=pacer,
                    token=tokens[index % len(tokens)],
                    http_url=args.http_url,
                    ws_url=args.ws_url,
                    trace_url=args.trace_url,
                    internal_token=internal_token,
                    message=args.message,
                    timeout_seconds=args.timeout,
                    trace_timeout_seconds=args.trace_timeout,
                    require_queue_trace=args.mode == "deterministic",
                )
                for index in range(requests)
            ]
        )

    summary = summarize_results(results)
    summary["errorBreakdown"] = dict(
        sorted(Counter(item.get("error") for item in results if item.get("error")).items())
    )
    gate = evaluate_performance_gate(
        summary,
        mode=args.mode,
        thresholds=DeterministicThresholds(
            enqueue_p95_ms=args.enqueue_p95_ms,
            queue_p95_ms=args.queue_p95_ms,
            ttft_p95_ms=args.ttft_p95_ms,
            total_p95_ms=args.total_p95_ms,
        ),
        live_success_rate=args.live_success_rate,
    )
    return {"summary": summary, "gate": gate}


def _tokens(args: argparse.Namespace) -> list[str]:
    values = [str(value).strip() for value in args.token or [] if str(value).strip()]
    if args.tokens_file:
        values.extend(
            line.strip()
            for line in args.tokens_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    values.extend(
        value.strip() for value in os.getenv("AGENT_PERF_TOKENS", "").split(",") if value.strip()
    )
    return list(dict.fromkeys(values))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("deterministic", "live"), default="deterministic")
    parser.add_argument("--http-url", default="http://127.0.0.1:7050/api/agent/sendMessage")
    parser.add_argument("--ws-url", default="ws://127.0.0.1:7050/ws")
    parser.add_argument(
        "--trace-url",
        default="http://127.0.0.1:7050/api/agent/admin/traceDetail",
    )
    parser.add_argument("--token", action="append", help="Repeat for distinct test users")
    parser.add_argument("--tokens-file", type=Path, help="One test-user token per line")
    parser.add_argument("--internal-token-env", default="INTERNAL_TOKEN")
    parser.add_argument("--message", default="推荐一款适合办公的键盘")
    parser.add_argument("--requests", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--trace-timeout", type=float, default=5)
    parser.add_argument("--per-token-interval", type=float, default=1.05)
    parser.add_argument("--enqueue-p95-ms", type=float, default=500)
    parser.add_argument("--queue-p95-ms", type=float, default=1_000)
    parser.add_argument("--ttft-p95-ms", type=float, default=2_000)
    parser.add_argument("--total-p95-ms", type=float, default=5_000)
    parser.add_argument("--live-success-rate", type=float, default=0.95)
    args = parser.parse_args()
    tokens = _tokens(args)
    if not tokens:
        parser.error("provide repeated --token, --tokens-file, or AGENT_PERF_TOKENS")
    try:
        report = asyncio.run(run(args, tokens))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
