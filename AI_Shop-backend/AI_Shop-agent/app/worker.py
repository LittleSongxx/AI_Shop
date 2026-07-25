from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Awaitable, Callable

import aio_pika
import structlog

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_FAST, AGENT_QUEUE_HIGH, AGENT_QUEUE_LOW
from app.db.pool import close_pool, init_pool
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.types import IntentDecision
from app.harness.metrics.runtime_sensors import AGENT_TASK_INFLIGHT, AGENT_TASK_TOTAL
from app.infra.http_client import close_clients as close_http_clients
from app.services.agent_engine import agent_engine
from app.services.agent_queue_service import agent_queue_service
from app.services.agent_service import agent_orchestrator
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.message_service import agent_message_service
from app.services.redis_service import redis_service
from app.services.stream_service import stream_service
from app.services.task_service import agent_task_service
from app.utils.product_consult import parse_consult_card

logger = structlog.get_logger()
TERMINAL_ERROR = "服务暂时不可用，已为您保留本次咨询记录，请稍后重试或转人工客服。"


class AgentWorker:

    def __init__(self) -> None:
        self._channels: list[aio_pika.abc.AbstractChannel] = []
        self._consumers: list[tuple[aio_pika.abc.AbstractQueue, str]] = []
        self._worker_id = f"worker-{uuid.uuid4().hex}"

    async def run(self) -> None:
        settings = get_settings()
        settings.validate_runtime()
        await redis_service.connect()
        await init_pool()
        await agent_queue_service.connect()
        try:
            await self._start_consumer(
                AGENT_QUEUE_HIGH, settings.agent_worker_high_concurrency
            )
            await self._start_consumer(
                AGENT_QUEUE_FAST, settings.agent_worker_fast_concurrency
            )
            await self._start_consumer(
                AGENT_QUEUE_LOW, settings.agent_worker_low_concurrency
            )
            await redis_service.set_worker_heartbeat(
                self._worker_id,
                settings.agent_worker_heartbeat_ttl_seconds,
            )
            logger.info(
                "agent_worker_started",
                worker_id=self._worker_id,
                high=settings.agent_worker_high_concurrency,
                fast=settings.agent_worker_fast_concurrency,
                low=settings.agent_worker_low_concurrency,
            )
            while True:
                await redis_service.set_worker_heartbeat(
                    self._worker_id,
                    settings.agent_worker_heartbeat_ttl_seconds,
                )
                await self.recover_pending()
                await asyncio.sleep(settings.agent_task_recovery_interval_seconds)
        finally:
            await self.close()

    async def close(self) -> None:
        for queue, consumer_tag in self._consumers:
            try:
                await queue.cancel(consumer_tag)
            except Exception as exc:
                logger.warning("agent_consumer_cancel_failed", error=str(exc))
        self._consumers.clear()
        for channel in self._channels:
            if not channel.is_closed:
                await channel.close()
        self._channels.clear()
        try:
            await redis_service.clear_worker_heartbeat(self._worker_id)
        except Exception as exc:
            logger.warning("agent_worker_heartbeat_clear_failed", error=str(exc))
        await agent_queue_service.close()
        await mcp_streamable_client.close()
        await close_http_clients()
        await close_pool()
        await redis_service.close()
        logger.info("agent_worker_stopped")

    async def recover_pending(self) -> None:
        rows = await agent_task_service.load_pending()
        for row in rows:
            payload = row.get("payload")
            if not isinstance(payload, dict):
                payload = await agent_message_service.get_message_for_task(
                    int(row["message_id"])
                )
            if not payload:
                await agent_task_service.mark_terminal(
                    int(row["message_id"]), "任务载荷不存在"
                )
                continue
            try:
                await agent_queue_service.publish(row["queue_name"], payload)
                await agent_task_service.mark_queued(int(row["message_id"]))
            except Exception as exc:
                logger.warning(
                    "agent_task_recovery_deferred",
                    message_id=row["message_id"],
                    error=str(exc),
                )
                break

    async def _start_consumer(self, queue_name: str, concurrency: int) -> None:
        channel = await agent_queue_service.declare_channel()
        await channel.set_qos(prefetch_count=max(1, concurrency))
        queue = await channel.get_queue(queue_name)
        consumer_tag = await queue.consume(self._handler(queue_name))
        self._channels.append(channel)
        self._consumers.append((queue, consumer_tag))

    def _handler(
        self, queue_name: str
    ) -> Callable[[aio_pika.abc.AbstractIncomingMessage], Awaitable[None]]:
        async def handle(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            await self._process_message(queue_name, message)

        return handle

    async def _process_message(
        self,
        queue_name: str,
        message: aio_pika.abc.AbstractIncomingMessage,
    ) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
            message_id = int(payload["messageId"])
            user_id = str(payload["userId"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("agent_task_invalid_payload", queue=queue_name, error=str(exc))
            await message.reject(requeue=False)
            return

        if not await agent_task_service.claim(message_id, message.redelivered):
            await message.ack()
            return

        lock_owner = f"{uuid.uuid4().hex}:{message_id}"
        locked = await redis_service.acquire_agent_user_lock(
            user_id,
            lock_owner,
            get_settings().agent_user_lock_ttl_seconds,
        )
        if not locked:
            await agent_task_service.release(message_id)
            AGENT_TASK_TOTAL.labels(queue=queue_name, result="lock_contended").inc()
            await asyncio.sleep(0.05)
            await message.nack(requeue=True)
            return

        AGENT_TASK_INFLIGHT.labels(queue=queue_name).inc()
        AGENT_TASK_TOTAL.labels(queue=queue_name, result="started").inc()
        try:
            if self._deadline_expired(payload):
                await self._finish_terminal(message, payload, "任务超过处理截止时间")
                return

            decision = await self._refine_decision(payload)
            if decision.should_handoff:
                await agent_orchestrator._transfer_to_support(
                    payload,
                    payload.get("userMessage") or "",
                    decision.model_dump(mode="json"),
                )
            else:
                await agent_engine.assistant_answer(payload)
            await agent_task_service.mark_completed(message_id)
            AGENT_TASK_TOTAL.labels(queue=queue_name, result="completed").inc()
            await message.ack()
        except asyncio.CancelledError:
            await agent_task_service.release(message_id)
            await message.nack(requeue=True)
            raise
        except Exception as exc:
            logger.exception(
                "agent_worker_task_failed",
                queue=queue_name,
                message_id=message_id,
                user_id=user_id,
                error=str(exc),
            )
            await self._retry_or_dead(message, payload, exc)
        finally:
            AGENT_TASK_INFLIGHT.labels(queue=queue_name).dec()
            await redis_service.release_agent_user_lock(user_id, lock_owner)

    async def _refine_decision(self, payload: dict) -> IntentDecision:
        raw = payload.get("intentDecision")
        decision = IntentDecision.model_validate(raw) if raw else None
        if decision and decision.source not in {"default", "llm"}:
            return decision

        card, user_text = parse_consult_card(payload.get("userMessage") or "")
        refined = await resolve_intent(
            str(payload["userId"]),
            user_text,
            from_product=bool(payload.get("fromProduct")),
            message_card=card,
            unresolved_count=max(0, int(payload.get("unresolvedCount") or 0) - 1),
            allow_llm=True,
        )
        payload["intentDecision"] = refined.model_dump(mode="json")
        payload["intent"] = refined.intent.value
        payload["intentConfidence"] = refined.confidence
        payload["sentiment"] = refined.sentiment.value
        payload["urgency"] = refined.urgency.value
        payload["riskLevel"] = refined.risk_level.value
        payload["nextAction"] = refined.next_action.value
        payload["handoffReason"] = refined.handoff_reason
        await agent_message_service.update_decision(
            int(payload["messageId"]),
            refined,
            int(payload.get("unresolvedCount") or 0),
        )
        return refined

    async def _retry_or_dead(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        payload: dict,
        exc: Exception,
    ) -> None:
        message_id = int(payload["messageId"])
        retry_count = await agent_task_service.mark_failed(message_id, str(exc))
        deadline_expired = self._deadline_expired(payload)
        if retry_count >= get_settings().agent_task_max_retries or deadline_expired:
            AGENT_TASK_TOTAL.labels(
                queue=str(payload.get("queueName") or "unknown"),
                result="dead",
            ).inc()
            await self._finish_terminal(message, payload, str(exc))
            return
        AGENT_TASK_TOTAL.labels(
            queue=str(payload.get("queueName") or "unknown"),
            result="retry",
        ).inc()
        try:
            await agent_queue_service.publish(payload["queueName"], payload)
            await agent_task_service.mark_queued(message_id)
            await message.ack()
        except Exception:
            await message.nack(requeue=True)

    async def _finish_terminal(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        payload: dict,
        error: str,
    ) -> None:
        message_id = int(payload["messageId"])
        user_id = str(payload["userId"])
        await agent_task_service.mark_terminal(message_id, error)
        await stream_service.push_error(user_id, message_id, TERMINAL_ERROR, "agent")
        await agent_message_service.complete_message(
            message_id, TERMINAL_ERROR, "agent", None
        )
        await message.reject(requeue=False)

    @staticmethod
    def _deadline_expired(payload: dict) -> bool:
        deadline = payload.get("deadlineAt")
        if not deadline:
            return False
        if isinstance(deadline, datetime):
            return deadline <= datetime.now()
        try:
            return datetime.fromisoformat(str(deadline)) <= datetime.now()
        except ValueError:
            return False


async def run_worker() -> None:
    await AgentWorker().run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
