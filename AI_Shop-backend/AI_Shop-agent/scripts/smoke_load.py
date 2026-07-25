from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def run_one(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    message: str,
    semaphore: asyncio.Semaphore,
) -> tuple[int, float]:
    async with semaphore:
        started = time.perf_counter()
        try:
            response = await client.post(
                url,
                headers={"token": token},
                data={"message": message},
            )
            return response.status_code, time.perf_counter() - started
        except httpx.HTTPError:
            return 0, time.perf_counter() - started


async def run(args: argparse.Namespace) -> None:
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [
            run_one(client, args.url, args.token, args.message, semaphore)
            for _ in range(max(1, args.requests))
        ]
        results = await asyncio.gather(*tasks)
    latencies = sorted(seconds for _, seconds in results)
    statuses: dict[int, int] = {}
    for status, _ in results:
        statuses[status] = statuses.get(status, 0) + 1
    p95_index = min(len(latencies) - 1, max(0, int(len(latencies) * 0.95) - 1))
    print(f"requests={len(results)} concurrency={max(1, args.concurrency)}")
    print(f"statuses={statuses}")
    print(
        "latency_seconds="
        f"avg:{statistics.mean(latencies):.3f} "
        f"p95:{latencies[p95_index]:.3f} "
        f"max:{max(latencies):.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Lightweight Agent API smoke load.")
    parser.add_argument("--url", default="http://127.0.0.1:7050/api/agent/sendMessage")
    parser.add_argument("--token", required=True, help="A valid development user token.")
    parser.add_argument("--message", default="推荐3000元以内的办公笔记本")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
