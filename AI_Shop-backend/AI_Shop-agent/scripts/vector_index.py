from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def run(command: str, confirmed: bool) -> int:
    from app.rag.index_contract import vector_index_contract

    if command == "rebuild":
        if not confirmed:
            print("Refusing destructive rebuild without --yes.", file=sys.stderr)
            return 2
        result = await vector_index_contract.rebuild()
    else:
        result = await vector_index_contract.check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or rebuild the AI_Shop vector index.")
    parser.add_argument("command", choices=("check", "rebuild"))
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm that rebuilding deletes the current vector index.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.command, args.yes)))


if __name__ == "__main__":
    main()
