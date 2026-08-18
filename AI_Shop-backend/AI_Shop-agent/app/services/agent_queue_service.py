from __future__ import annotations

import json
from typing import Any

import aio_pika
from aio_pika import ExchangeType, Message

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_DEAD, AGENT_QUEUE_FAST, AGENT_QUEUE_HIGH, AGENT_QUEUE_LOW
from app.harness.metrics.runtime_sensors import AGENT_TASK_TOTAL


class AgentQueueService:

    def __init__(self) -> None:
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractRobustChannel | None = None

    async def connect(self) -> None:
        if self._connection and not self._connection.is_closed:
            return
        settings = get_settings()
        self._connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._declare(self._channel)

    async def close(self) -> None:
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._channel = None

    async def publish(self, queue_name: str, payload: dict[str, Any]) -> None:
        await self.connect()
        assert self._channel is not None
        headers = {
            "x-message-id": str(payload.get("messageId") or ""),
            "x-request-id": str(payload.get("requestId") or ""),
            "x-run-id": str(payload.get("runId") or ""),
            "x-episode-id": str(payload.get("episodeId") or payload.get("runId") or ""),
        }
        # P0-3: W3C traceparent 随消息传播，Worker 消费时据此续接父 span。
        # OTel 未启用时当前 span 不 recording，inject 是空操作。
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        if trace.get_current_span().is_recording():
            carrier: dict[str, str] = {}
            TraceContextTextMapPropagator().inject(carrier)
            headers.update(carrier)
        exchange = await self._channel.get_exchange(get_settings().agent_queue_exchange)
        await exchange.publish(
            Message(
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(payload.get("messageId") or ""),
                headers=headers,
            ),
            routing_key=queue_name,
        )
        AGENT_TASK_TOTAL.labels(queue=queue_name, result="published").inc()

    async def declare_channel(self):
        await self.connect()
        assert self._connection is not None
        channel = await self._connection.channel(publisher_confirms=True)
        await self._declare(channel)
        return channel

    async def _declare(self, channel) -> None:
        settings = get_settings()
        exchange = await channel.declare_exchange(
            settings.agent_queue_exchange, ExchangeType.DIRECT, durable=True
        )
        dead_exchange = await channel.declare_exchange(
            f"{settings.agent_queue_exchange}.dlx", ExchangeType.DIRECT, durable=True
        )
        queue_type = {"x-queue-type": settings.rabbitmq_queue_type}
        dead_queue = await channel.declare_queue(
            AGENT_QUEUE_DEAD, durable=True, arguments=queue_type
        )
        await dead_queue.bind(dead_exchange, routing_key=AGENT_QUEUE_DEAD)
        for queue_name in (AGENT_QUEUE_HIGH, AGENT_QUEUE_FAST, AGENT_QUEUE_LOW):
            queue = await channel.declare_queue(
                queue_name,
                durable=True,
                arguments={
                    **queue_type,
                    "x-dead-letter-exchange": f"{settings.agent_queue_exchange}.dlx",
                    "x-dead-letter-routing-key": AGENT_QUEUE_DEAD,
                },
            )
            await queue.bind(exchange, routing_key=queue_name)

    @staticmethod
    def queue_for_decision(decision) -> tuple[str, int]:
        if decision.should_handoff or decision.intent.value in {
            "COMPLAINT",
            "HUMAN_REQUEST",
            "PAYMENT_ISSUE",
            "DAMAGED_OR_WRONG_ITEM",
            "REFUND",
            "REFUND_STATUS",
            "QUERY_FULFILLMENT",
        }:
            return AGENT_QUEUE_HIGH, 100
        if decision.intent.value in {"CHAT", "INVOICE", "ADDRESS_CHANGE"}:
            return AGENT_QUEUE_FAST, 60
        return AGENT_QUEUE_LOW, 20


agent_queue_service = AgentQueueService()
