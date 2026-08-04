#!/usr/bin/env python3
"""语义缓存命中盲评样本查看/导出工具。

B2 的消费端：retriever 按 RAG_CACHE_SAMPLE_RATE 把命中抽样推进 Redis
cap 队列 `mall:rag:cache:sample:v1:{YYYYMMDD}`（按天分 key，容量 200/天，
避免全局共享队列把早间样本冲掉）。本脚本把样本拉出来，逐条看
"用户问什么 → 命中了什么文档"——离线人工评审语义缓存误报率用。
评审周期建议一周一次；样本只保留 7 天（cap 队列 TTL），先导出再做分析。

用法：
    python scripts/inspect_rag_cache_samples.py            # 打印全部样本（近 7 天）
    python scripts/inspect_rag_cache_samples.py --days 3   # 只看最近 3 天
    python scripts/inspect_rag_cache_samples.py --dump samples.jsonl   # 导出 JSONL 供分析
    python scripts/inspect_rag_cache_samples.py --count    # 只看数量
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_KEY_PREFIX = "mall:rag:cache:sample:v1:"


async def fetch(days: int = 7) -> list[dict]:
    from app.services.redis_service import redis_service

    await redis_service.ensure_connected()
    samples: list[dict] = []
    today = datetime.now()
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        key = f"{SAMPLE_KEY_PREFIX}{day.strftime('%Y%m%d')}"
        raw = await redis_service.client.lrange(key, 0, -1)
        for item in raw:
            if isinstance(item, bytes):
                item = item.decode("utf-8", errors="replace")
            try:
                samples.append(json.loads(item))
            except json.JSONDecodeError:
                continue
    # cap 队列是 lpush：列表头是最新的，按时间升序展示更符合评审习惯
    return list(reversed(samples))


def render(sample: dict) -> str:
    ts = sample.get("ts")
    hits = sample.get("hitDocs") or []
    lines = [
        f"[{ts}] query: {sample.get('query') or ''}",
    ]
    for hit in hits[:5]:
        lines.append(
            f"    - id={hit.get('id')} source={hit.get('source')} score={hit.get('score')}"
        )
    if not hits:
        lines.append("    (无命中文档)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="查看语义缓存命中盲评样本")
    parser.add_argument("--days", type=int, default=7, help="回溯天数（默认 7）")
    parser.add_argument("--dump", type=Path, default=None, help="导出为 JSONL")
    parser.add_argument("--count", action="store_true", help="只打印数量")
    args = parser.parse_args()

    samples = asyncio.run(fetch(days=args.days))
    if args.count:
        print(f"样本数: {len(samples)}")
        return
    if args.dump:
        args.dump.write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples),
            encoding="utf-8",
        )
        print(f"已导出 {len(samples)} 条到 {args.dump}")
        return
    if not samples:
        print("队列为空——还没有缓存命中被抽样，或 RAG_CACHE_SAMPLE_RATE=0")
        return
    for s in samples:
        print(render(s))
        print()


if __name__ == "__main__":
    main()
