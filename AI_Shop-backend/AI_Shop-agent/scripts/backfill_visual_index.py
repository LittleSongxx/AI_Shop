from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config.settings import get_settings
from app.infra.http_client import close_clients
from app.visual.indexer import visual_catalog_indexer


async def backfill(concurrency: int) -> dict:
    settings = get_settings()
    if not settings.visual_search_enabled:
        return {"state": "DISABLED"}
    if not settings.visual_api_key.strip():
        raise RuntimeError("VISUAL_API_KEY_NOT_CONFIGURED")
    return await visual_catalog_indexer.rebuild(concurrency=concurrency)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the governed visual product index")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    concurrency = max(1, min(args.concurrency, 5))
    try:
        result = asyncio.run(backfill(concurrency))
    except Exception as exc:
        print(f"visual index backfill failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        asyncio.run(close_clients())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
