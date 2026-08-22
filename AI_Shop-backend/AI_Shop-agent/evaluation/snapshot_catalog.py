from __future__ import annotations

import asyncio
import json

from evaluation.core.catalog import write_catalog_fixture


async def _main() -> None:
    value = await write_catalog_fixture()
    print(
        json.dumps(
            {
                "productCount": value["productCount"],
                "canonicalSha256": value["canonicalSha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
