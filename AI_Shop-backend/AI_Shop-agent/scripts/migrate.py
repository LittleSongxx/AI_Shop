"""Apply the current Agent schema and seed governed shopping contracts."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from app.db.migrations import run_migrations
    from app.db.pool import close_pool, init_pool
    from app.services.shopping_mission_service import initialize_category_need_schemas

    run_migrations()
    asyncio.run(_seed_category_schemas(init_pool, close_pool, initialize_category_need_schemas))


async def _seed_category_schemas(init_pool, close_pool, initialize) -> None:
    await init_pool()
    try:
        result = await initialize()
        if result.get("status") == "DEGRADED":
            raise RuntimeError(f"category schema seed degraded: {result}")
        print(f"Seeded {result.get('loaded', 0)} published category need schemas")
    finally:
        await close_pool()


if __name__ == "__main__":
    main()
