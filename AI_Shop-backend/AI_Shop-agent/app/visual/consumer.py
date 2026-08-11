from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import aio_pika
import structlog
from aio_pika import ExchangeType, Message

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import (
    VISUAL_INDEX_DOCUMENT_TOTAL,
    VISUAL_INDEX_EVENT_TOTAL,
)
from app.services.agent_queue_service import agent_queue_service
from app.visual.index import visual_product_index
from app.visual.indexer import visual_catalog_indexer

logger = structlog.get_logger()

_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RETRY_HEADER = "x-visual-index-retry"


@dataclass(frozen=True)
class VisualProductIndexEvent:
    product_id: str
    product_version: int


def parse_visual_product_index_event(payload: object) -> VisualProductIndexEvent | None:
    """Accept only the existing RAG PRODUCT envelope at the MQ boundary.

    The product service publishes ``RagDataDTO`` to ``rag.exchange/rag.queue``.
    This consumer is bound to the same exchange with its own queue, so it must
    acknowledge FAQ and other traffic without competing with the Java RAG
    consumer. Malformed PRODUCT events return ``None`` and are dead-lettered.
    """
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or "").strip().lower()
    if kind != "product":
        return None
    product_id = str(payload.get("dataId") or payload.get("data_id") or "").strip()
    if not _PRODUCT_ID_RE.fullmatch(product_id):
        raise ValueError("VISUAL_INDEX_PRODUCT_ID_INVALID")
    raw_version = payload.get("version")
    try:
        product_version = int(raw_version)
    except (TypeError, ValueError):
        raise ValueError("VISUAL_INDEX_PRODUCT_VERSION_INVALID") from None
    if product_version <= 0:
        raise ValueError("VISUAL_INDEX_PRODUCT_VERSION_INVALID")
    return VisualProductIndexEvent(product_id=product_id, product_version=product_version)


class VisualIndexConsumer:
    """Dedicated, durable fan-out consumer for visual catalog indexing.

    It is intentionally separate from ``rag.queue``: both consumers receive a
    copy of a product change, and an unavailable visual provider can never steal
    or delay the text-RAG pipeline. Retry attempts are republished with a small
    bounded delay; after the budget is exhausted the original queue's DLX puts
    the event in ``visual.index.dlq`` for investigation/replay.
    """

    async def start(
        self,
    ) -> tuple[aio_pika.abc.AbstractChannel, aio_pika.abc.AbstractQueue, str]:
        settings = get_settings()
        channel = await agent_queue_service.declare_channel()
        await channel.set_qos(
            prefetch_count=max(1, settings.visual_index_consumer_concurrency)
        )
        exchange = await channel.declare_exchange(
            settings.visual_index_exchange,
            ExchangeType.DIRECT,
            durable=True,
        )
        dead_exchange_name = f"{settings.visual_index_queue}.dlx"
        dead_exchange = await channel.declare_exchange(
            dead_exchange_name,
            ExchangeType.DIRECT,
            durable=True,
        )
        dead_queue = await channel.declare_queue(
            settings.visual_index_dead_letter_queue,
            durable=True,
        )
        await dead_queue.bind(
            dead_exchange,
            routing_key=settings.visual_index_dead_letter_queue,
        )
        queue = await channel.declare_queue(
            settings.visual_index_queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": dead_exchange_name,
                "x-dead-letter-routing-key": settings.visual_index_dead_letter_queue,
            },
        )
        await queue.bind(exchange, routing_key=settings.visual_index_routing_key)
        consumer_tag = await queue.consume(self._handle)
        logger.info(
            "visual_index_consumer_started",
            queue=settings.visual_index_queue,
            exchange=settings.visual_index_exchange,
            routing_key=settings.visual_index_routing_key,
            dead_letter_queue=settings.visual_index_dead_letter_queue,
        )
        return channel, queue, consumer_tag

    async def bootstrap_if_needed(self) -> dict[str, Any] | None:
        """Build a new model-version index asynchronously when credentials exist.

        An absent cloud key is an expected local/deployment state. In that case
        visual search remains explicitly degraded but the customer Agent and the
        rest of the marketplace must continue serving normally.
        """
        settings = get_settings()
        if not settings.visual_index_backfill_on_start:
            logger.info("visual_index_bootstrap_skipped", reason="disabled")
            return None
        if not settings.visual_api_key.strip():
            logger.warning(
                "visual_index_bootstrap_degraded",
                code="VISUAL_EMBEDDING_NOT_CONFIGURED",
                event_name_cn="视觉能力已降级",
            )
            return None
        try:
            status = await visual_product_index.status()
            if status["servingCurrentModel"]:
                logger.info("visual_index_bootstrap_current", **status)
                return status
            result = await visual_catalog_indexer.rebuild()
            logger.info("visual_index_bootstrap_completed", **result)
            return result
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            logger.warning(
                "visual_index_bootstrap_degraded",
                code=type(exc).__name__,
                event_name_cn="视觉能力已降级",
            )
            return None

    async def _handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            VISUAL_INDEX_EVENT_TOTAL.labels(result="invalid_payload").inc()
            logger.warning("visual_index_event_invalid_payload")
            await message.reject(requeue=False)
            return

        try:
            event = parse_visual_product_index_event(payload)
        except ValueError as exc:
            VISUAL_INDEX_EVENT_TOTAL.labels(result="invalid_product_event").inc()
            logger.warning("visual_index_event_rejected", code=str(exc))
            await message.reject(requeue=False)
            return
        if event is None:
            VISUAL_INDEX_EVENT_TOTAL.labels(result="ignored_non_product").inc()
            await message.ack()
            return

        try:
            documents = await visual_catalog_indexer.index_product(
                event.product_id,
                product_version=event.product_version,
            )
            VISUAL_INDEX_EVENT_TOTAL.labels(result="indexed").inc()
            VISUAL_INDEX_DOCUMENT_TOTAL.inc(max(0, documents))
            logger.info(
                "visual_index_updated",
                product_id=event.product_id,
                product_version=event.product_version,
                documents=documents,
                event_name_cn="视觉索引已更新",
            )
            await message.ack()
        except asyncio.CancelledError:
            await message.nack(requeue=True)
            raise
        except Exception as exc:
            await self._retry_or_dead(message, event, exc)

    async def _retry_or_dead(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        event: VisualProductIndexEvent,
        exc: Exception,
    ) -> None:
        settings = get_settings()
        retry = _retry_count(message.headers or {})
        if retry >= settings.visual_index_max_retries:
            VISUAL_INDEX_EVENT_TOTAL.labels(result="dead_lettered").inc()
            logger.error(
                "visual_index_event_dead_lettered",
                product_id=event.product_id,
                product_version=event.product_version,
                retry=retry,
                error=type(exc).__name__,
            )
            await message.reject(requeue=False)
            return

        delay = min(
            settings.visual_index_retry_backoff_seconds * (2**retry),
            60.0,
        )
        if delay:
            await asyncio.sleep(delay)
        headers = dict(message.headers or {})
        headers[_RETRY_HEADER] = retry + 1
        try:
            channel = message.channel
            exchange = await channel.get_exchange(settings.visual_index_exchange)
            await exchange.publish(
                Message(
                    body=message.body,
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message.message_id,
                    headers=headers,
                    timestamp=int(time.time()),
                ),
                routing_key=settings.visual_index_routing_key,
            )
        except Exception:
            # The broker confirmation failed, therefore keep ownership of the
            # original message and ask RabbitMQ to redeliver it rather than
            # acknowledging a potentially lost catalog update.
            VISUAL_INDEX_EVENT_TOTAL.labels(result="retry_publish_failed").inc()
            await message.nack(requeue=True)
            return

        VISUAL_INDEX_EVENT_TOTAL.labels(result="retry_scheduled").inc()
        logger.warning(
            "visual_index_event_retry_scheduled",
            product_id=event.product_id,
            product_version=event.product_version,
            retry=retry + 1,
            delay_seconds=delay,
            error=type(exc).__name__,
        )
        await message.ack()


def _retry_count(headers: dict[str, Any]) -> int:
    raw = headers.get(_RETRY_HEADER, 0)
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(value, 100))


visual_index_consumer = VisualIndexConsumer()
