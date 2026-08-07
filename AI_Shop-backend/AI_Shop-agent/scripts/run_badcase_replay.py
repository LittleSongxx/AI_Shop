"""Run ACTIVE human-reviewed Badcase regression cases without an online LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import close_pool, init_pool  # noqa: E402
from app.services.regression_replay_service import regression_replay_service  # noqa: E402


async def run(case_id: int | None) -> dict:
    await init_pool()
    try:
        return await regression_replay_service.run_active(case_id)
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", type=int, help="Run one ACTIVE case")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.case_id))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed"] or result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
