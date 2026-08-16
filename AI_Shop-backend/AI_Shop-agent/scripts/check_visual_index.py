from __future__ import annotations

import asyncio
import json
import sys

import aio_pika

from app.config.settings import get_settings
from app.infra.http_client import close_clients
from app.visual.index import visual_product_index


async def check() -> dict:
    settings = get_settings()
    if not settings.visual_search_enabled:
        return {"state": "DISABLED"}
    if not settings.visual_api_key.strip():
        return {
            "queueState": "DISABLED",
            "state": "DEGRADED",
            "reason": "VISUAL_API_KEY_NOT_CONFIGURED",
        }

    status = await visual_product_index.status()
    if not settings.visual_index_consumer_enabled:
        return {
            **status,
            "queueState": "DISABLED",
            "state": "DEGRADED",
            "reason": "VISUAL_INDEX_CONSUMER_DISABLED",
        }

    connection = await aio_pika.connect_robust(settings.rabbitmq_url, timeout=5)
    try:
        channel = await connection.channel()
        queue = await channel.declare_queue(
            settings.visual_index_queue,
            passive=True,
        )
        status["queue"] = settings.visual_index_queue
        status["queueMessages"] = queue.declaration_result.message_count
        status["queueConsumers"] = queue.declaration_result.consumer_count
        if not status.get("servingCurrentModel"):
            status.update(
                state="DEGRADED",
                reason="VISUAL_INDEX_BACKFILL_PENDING",
            )
        else:
            status.update(state="READY", reason=None)
        return status
    finally:
        await connection.close()


async def check_and_close() -> dict:
    try:
        return await check()
    finally:
        # Shared AsyncClients are bound to the loop where they were first used.
        # A one-shot operational script must close them before that loop exits.
        await close_clients()


def main() -> None:
    try:
        result = asyncio.run(check_and_close())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "state": "DEGRADED",
                    "reason": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
