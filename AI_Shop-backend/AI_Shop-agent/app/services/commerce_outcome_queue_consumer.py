from __future__ import annotations

import asyncio
import json

import aio_pika
import structlog
from aio_pika import ExchangeType
from pydantic import ValidationError

from app.config.settings import get_settings
from app.constants import (
    COMMERCE_OUTCOME_DEAD_KEY,
    COMMERCE_OUTCOME_DEAD_QUEUE,
    COMMERCE_OUTCOME_EXCHANGE,
    COMMERCE_OUTCOME_KEY,
    COMMERCE_OUTCOME_QUEUE,
)
from app.models.commerce_outcome import CommerceOutcomeBatchRequest
from app.services.agent_queue_service import agent_queue_service
from app.services.commerce_outcome_ledger_service import (
    commerce_outcome_ledger_service,
)

logger = structlog.get_logger()


class CommerceOutcomeQueueConsumer:
    async def start(
        self,
    ) -> tuple[
        aio_pika.abc.AbstractChannel,
        aio_pika.abc.AbstractQueue,
        str,
    ]:
        channel = await agent_queue_service.declare_channel()
        await channel.set_qos(prefetch_count=20)
        exchange = await channel.declare_exchange(
            COMMERCE_OUTCOME_EXCHANGE, ExchangeType.DIRECT, durable=True
        )
        queue_arguments: dict[str, object] = {
            "x-dead-letter-exchange": COMMERCE_OUTCOME_EXCHANGE,
            "x-dead-letter-routing-key": COMMERCE_OUTCOME_DEAD_KEY,
        }
        dead_arguments: dict[str, object] = {}
        if get_settings().rabbitmq_queue_type == "quorum":
            queue_arguments.update(
                {"x-queue-type": "quorum", "x-delivery-limit": 10}
            )
            dead_arguments["x-queue-type"] = "quorum"
        dead_queue = await channel.declare_queue(
            COMMERCE_OUTCOME_DEAD_QUEUE,
            durable=True,
            arguments=dead_arguments,
        )
        await dead_queue.bind(exchange, routing_key=COMMERCE_OUTCOME_DEAD_KEY)
        queue = await channel.declare_queue(
            COMMERCE_OUTCOME_QUEUE,
            durable=True,
            arguments=queue_arguments,
        )
        await queue.bind(exchange, routing_key=COMMERCE_OUTCOME_KEY)
        consumer_tag = await queue.consume(self._handle)
        return channel, queue, consumer_tag

    async def _handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            raw = json.loads(message.body.decode("utf-8"))
            batch = CommerceOutcomeBatchRequest.model_validate(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning(
                "commerce_outcome_message_rejected", error=type(exc).__name__
            )
            await message.reject(requeue=False)
            return

        try:
            results = await commerce_outcome_ledger_service.record_batch(batch.events)
            if any(result.get("status") == "PERSISTENCE_FAILED" for result in results):
                raise RuntimeError("commerce outcome persistence failed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "commerce_outcome_message_requeued",
                events=len(batch.events),
                error=type(exc).__name__,
            )
            await message.nack(requeue=True)
            return

        await message.ack()


commerce_outcome_queue_consumer = CommerceOutcomeQueueConsumer()
