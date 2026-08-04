from __future__ import annotations

import asyncio
import json
import random
import uuid
from datetime import datetime
from typing import Awaitable, Callable

import aio_pika
import structlog

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_FAST, AGENT_QUEUE_HIGH, AGENT_QUEUE_LOW
from app.db.pool import close_pool, init_pool
from app.domain.intent.classifier import record_intent_metrics, resolve_intent
from app.domain.intent.types import IntentDecision
from app.harness.metrics.runtime_sensors import AGENT_TASK_INFLIGHT, AGENT_TASK_TOTAL
from app.infra.http_client import close_clients as close_http_clients
from app.observability.telemetry import shutdown_telemetry
from app.services.agent_engine import agent_engine
from app.services.agent_queue_service import agent_queue_service
from app.services.agent_service import agent_orchestrator
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.message_service import agent_message_service
from app.services.pending_action_service import pending_action_service
from app.services.redis_service import redis_service
from app.services.stream_service import stream_service
from app.services.task_service import agent_task_service
from app.utils.product_consult import parse_consult_card

logger = structlog.get_logger()
TERMINAL_ERROR = "服务暂时不可用，已为您保留本次咨询记录，请稍后重试或转人工客服。"


class LeaseLostError(RuntimeError):
    """The claimed task or per-user lock is no longer owned by this execution."""


class AgentWorker:

    def __init__(self) -> None:
        self._channels: list[aio_pika.abc.AbstractChannel] = []
        self._consumers: list[tuple[aio_pika.abc.AbstractQueue, str]] = []
        self._worker_id = f"worker-{uuid.uuid4().hex}"

    async def run(self) -> None:
        settings = get_settings()
        settings.validate_runtime()
        heartbeat_task: asyncio.Task[None] | None = None
        # P0-3: Worker 独立进程，此前从不初始化 telemetry，任务/LLM/MQ/工具链路
        # 在 trace 里断链；同时任务指标在 Worker 内更新，Prometheus 需要有独立
        # 抓取出口（默认 :7051，见 deploy/prometheus/prometheus.yml）。
        from prometheus_client import start_http_server

        from app.observability.telemetry import configure_worker_telemetry

        configure_worker_telemetry()
        try:
            start_http_server(settings.worker_metrics_port, addr="0.0.0.0")
            logger.info("worker_metrics_server_started", port=settings.worker_metrics_port)
        except OSError as exc:
            # 每个 Worker 都维护自己的进程内计数器。端口冲突后继续消费会让该实例
            # 完全脱离抓取，任务量、失败率和 LLM 用量都会被静默低估。
            logger.error(
                "worker_metrics_server_failed",
                port=settings.worker_metrics_port,
                error=str(exc),
            )
            raise RuntimeError(
                "Worker metrics port is unavailable; assign a unique "
                "WORKER_METRICS_PORT to each worker"
            ) from exc
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
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(settings.agent_worker_heartbeat_ttl_seconds),
                name=f"agent-worker-heartbeat:{self._worker_id}",
            )
            logger.info(
                "agent_worker_started",
                worker_id=self._worker_id,
                high=settings.agent_worker_high_concurrency,
                fast=settings.agent_worker_fast_concurrency,
                low=settings.agent_worker_low_concurrency,
            )
            reconcile_counter = 0
            reconcile_every = max(
                1, settings.pending_action_reconcile_interval_seconds
                // max(settings.agent_task_recovery_interval_seconds, 1)
            )
            while True:
                await self.recover_pending()
                # B1：周期性补悬挂在 EXECUTING 的待确认动作终态。
                reconcile_counter += 1
                if reconcile_counter >= reconcile_every:
                    reconcile_counter = 0
                    try:
                        reconciled = await pending_action_service.reconcile_stale_executing(
                            settings.pending_action_stale_seconds
                        )
                        if reconciled:
                            logger.warning(
                                "pending_action_reconciler_ran", reconciled=reconciled
                            )
                    except Exception as exc:
                        logger.warning(
                            "pending_action_reconcile_cycle_failed", error=str(exc)
                        )
                await asyncio.sleep(settings.agent_task_recovery_interval_seconds)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            await self.close()

    async def _heartbeat_loop(self, ttl_seconds: int) -> None:
        interval = max(int(ttl_seconds) // 3, 1)
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await redis_service.set_worker_heartbeat(
                        self._worker_id,
                        ttl_seconds,
                    )
                except Exception as exc:
                    # A transient Redis failure should not silently kill the heartbeat
                    # task; later iterations can restore readiness before the TTL ends.
                    logger.warning(
                        "agent_worker_heartbeat_refresh_failed",
                        worker_id=self._worker_id,
                        error=str(exc),
                    )
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        try:
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
        finally:
            # BatchSpanProcessor buffers spans; a graceful Worker stop must flush
            # its final task/LLM spans just like the API process does.
            shutdown_telemetry()
            logger.info("agent_worker_stopped")

    async def recover_pending(self) -> None:
        rows = await agent_task_service.load_pending()
        for row in rows:
            message_id = int(row["message_id"])
            if not await agent_task_service.mark_dispatching(message_id):
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                payload = await agent_message_service.get_message_for_task(
                    message_id
                )
            if not payload:
                await agent_task_service.mark_terminal(
                    message_id, "任务载荷不存在"
                )
                continue
            try:
                await agent_queue_service.publish(row["queue_name"], payload)
                await agent_task_service.mark_queued(message_id)
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
        # P0-3: 从 MQ 头里提取 traceparent 续接链路，HTTP → 入队 → Worker → LLM/MCP 贯通。
        from opentelemetry import trace
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        carrier = {}
        for key, value in (message.headers or {}).items():
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            carrier[str(key)] = str(value)
        ctx = TraceContextTextMapPropagator().extract(carrier)
        with trace.get_tracer("aishop.agent").start_as_current_span(
            "agent.task.consume", context=ctx
        ):
            await self._process_message_inner(queue_name, message)

    async def _process_message_inner(
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

        settings = get_settings()
        lease_seconds = settings.agent_task_lease_seconds
        # owner 必须能塞进 agent_task.lease_owner varchar(64)：worker_id
        # 已含 32 位随机，后缀 8 位即可区分同 worker 内的并发消费，总长 48。
        lease_owner = f"{self._worker_id}:{uuid.uuid4().hex[:8]}"
        if not await agent_task_service.claim(
            message_id, lease_owner, lease_seconds
        ):
            if message.redelivered:
                # 重投说明原执行方可能已崩溃但租约未过期（或原执行方仍在
                # 跑、只是消息被超时重投）。本份消息直接丢弃，不回队列——
                # 原执行方活着就会自己完成；原执行方死了，recover_pending
                # 会在租约过期后把任务重新入队。避免重投风暴，也避免永久悬挂。
                await message.nack(requeue=False)
            else:
                await message.ack()
            return

        lock_owner = f"{uuid.uuid4().hex}:{message_id}"
        locked = await redis_service.acquire_agent_user_lock(
            user_id,
            lock_owner,
            settings.agent_user_lock_ttl_seconds,
        )
        if not locked:
            await agent_task_service.release(message_id, lease_owner)
            AGENT_TASK_TOTAL.labels(queue=queue_name, result="lock_contended").inc()
            await asyncio.sleep(0.05)
            await message.nack(requeue=True)
            return

        AGENT_TASK_INFLIGHT.labels(queue=queue_name).inc()
        AGENT_TASK_TOTAL.labels(queue=queue_name, result="started").inc()
        # 处理期间周期续租（任务租约 + 用户锁）；续租失败说明租约已被接管
        # （我们超时了），停手。用户锁 TTL 比任务租约短（180s < 240s），
        # 长任务不续用户锁会让同用户新消息并发进来，所以一并续。
        lease_lost = asyncio.Event()
        renewer = asyncio.create_task(
            self._renew_lease_loop(
                message_id,
                lease_owner,
                lease_seconds,
                user_id,
                lock_owner,
                settings.agent_user_lock_ttl_seconds,
                lease_lost,
            )
        )
        try:
            if await agent_message_service.is_execution_cancelled(
                user_id, message_id
            ):
                # Redis cancellation is intentionally short-lived. The message
                # row is the durable guard that prevents a recovered/late task
                # from timing out or executing after the user cancelled it.
                await agent_task_service.mark_terminal(
                    message_id,
                    "用户取消",
                    lease_owner,
                    status="CANCELLED",
                )
                AGENT_TASK_TOTAL.labels(
                    queue=queue_name, result="cancelled"
                ).inc()
                await message.ack()
                return
            if self._deadline_expired(payload):
                await self._finish_terminal(
                    message, payload, "任务超过处理截止时间", lease_owner
                )
                return

            outcome = await self._run_with_lease_guard(
                self._execute_payload(payload), lease_lost
            )
            if outcome != "ok":
                # 图内部已经把错误文案推给了用户（P0-1）。这一轮不能记成功，
                # 也不自动重试——重试会向用户重复推送错误消息。
                cancelled = outcome == "cancelled"
                terminal_written = await agent_task_service.mark_terminal(
                    message_id,
                    f"agent outcome={outcome}",
                    lease_owner,
                    status="CANCELLED" if cancelled else "DEAD",
                )
                if not terminal_written:
                    raise LeaseLostError(f"task {message_id} lost before terminal write")
                AGENT_TASK_TOTAL.labels(
                    queue=queue_name,
                    result="cancelled" if cancelled else "terminal_outcome",
                ).inc()
                await message.ack()
                return
            completed = await agent_task_service.mark_completed(message_id, lease_owner)
            if not completed:
                raise LeaseLostError(f"task {message_id} lost before completion")
            AGENT_TASK_TOTAL.labels(queue=queue_name, result="completed").inc()
            await message.ack()
        except LeaseLostError:
            logger.warning(
                "agent_task_execution_stopped_lease_lost",
                queue=queue_name,
                message_id=message_id,
            )
            AGENT_TASK_TOTAL.labels(queue=queue_name, result="lease_lost").inc()
            await message.ack()
        except asyncio.CancelledError:
            await agent_task_service.release(message_id, lease_owner)
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
            await self._retry_or_dead(message, payload, exc, lease_owner)
        finally:
            renewer.cancel()
            # 等续租协程真正退出，避免事件循环关闭时 "Task was destroyed but
            # it is pending" 告警（P1 审查：cancel 后未 await）。
            await asyncio.gather(renewer, return_exceptions=True)
            AGENT_TASK_INFLIGHT.labels(queue=queue_name).dec()
            await redis_service.release_agent_user_lock(user_id, lock_owner)

    async def _renew_lease_loop(
        self,
        message_id: int,
        lease_owner: str,
        lease_seconds: int,
        user_id: str,
        lock_owner: str,
        user_lock_ttl_seconds: int,
        lease_lost: asyncio.Event,
    ) -> None:
        """处理期间周期续租（任务租约 + 用户锁）。

        续租失败说明租约已被接管（或用户锁丢失）。设置事件后，主协程会
        取消正在运行的图，阻止旧执行继续流式输出或产生工具副作用。
        """
        # 两份租约都必须在各自 TTL 的 1/3 内续上。只按较长的任务租约
        # 计算会让较短的用户锁在第一次续租前过期，破坏同用户串行执行。
        interval = max(min(lease_seconds, user_lock_ttl_seconds) // 3, 1)
        try:
            while True:
                await asyncio.sleep(interval)
                ok = await agent_task_service.renew_lease(
                    message_id, lease_owner, lease_seconds
                )
                if not ok:
                    logger.warning("agent_task_lease_lost", message_id=message_id)
                    lease_lost.set()
                    return
                lock_ok = await redis_service.renew_agent_user_lock(
                    user_id, lock_owner, user_lock_ttl_seconds
                )
                if not lock_ok:
                    logger.warning("agent_user_lock_lost", user_id=user_id)
                    lease_lost.set()
                    return
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # 续租依赖不可用时，不能假定租约仍归当前 Worker。否则本协程会在
            # 数据库租约过期、被其他 Worker 接管后继续调用工具或推送流式结果。
            logger.warning(
                "agent_task_lease_renew_failed",
                message_id=message_id,
                user_id=user_id,
                error=str(exc),
            )
            lease_lost.set()

    async def _execute_payload(self, payload: dict) -> str:
        decision = await self._refine_decision(payload)
        if decision.should_handoff:
            await agent_orchestrator._transfer_to_support(
                payload,
                payload.get("userMessage") or "",
                decision.model_dump(mode="json"),
            )
            return "ok"
        return await agent_engine.assistant_answer(payload)

    @staticmethod
    async def _run_with_lease_guard(
        operation: Awaitable[str], lease_lost: asyncio.Event
    ) -> str:
        work = asyncio.create_task(operation)
        lost_waiter = asyncio.create_task(lease_lost.wait())
        try:
            await asyncio.wait({work, lost_waiter}, return_when=asyncio.FIRST_COMPLETED)
            if lease_lost.is_set():
                if not work.done():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                raise LeaseLostError("task lease or user lock lost during execution")
            return await work
        finally:
            lost_waiter.cancel()
            # 上层 Worker 被关闭/取消时也必须停掉图执行；否则外层已经释放
            # 用户锁并重投消息，内部协程仍可能继续产生工具副作用。
            if not work.done():
                work.cancel()
            await asyncio.gather(lost_waiter, work, return_exceptions=True)

    async def _refine_decision(self, payload: dict) -> IntentDecision:
        raw = payload.get("intentDecision")
        decision = IntentDecision.model_validate(raw) if raw else None
        if decision and decision.source not in {"default", "llm"}:
            return decision

        card, user_text = parse_consult_card(payload.get("userMessage") or "")
        recent_intents = await agent_message_service.get_recent_intents(
            str(payload["userId"])
        )
        # record_metrics=False：指标由 send 路径按当时的决策计过（source=default
        # 的 CHAT 也在 send 路径计了）。这里重算不重复计数，只在与原决策不同
        # 时补计一次 refined——否则同一消息 INTENT_TOTAL/HANDOFF_TOTAL 双计，
        # 转人工率虚高（P1 审查）。
        refined = await resolve_intent(
            str(payload["userId"]),
            user_text,
            from_product=bool(payload.get("fromProduct")),
            message_card=card,
            unresolved_count=max(0, int(payload.get("unresolvedCount") or 0) - 1),
            allow_llm=True,
            session_intent=recent_intents[0] if recent_intents else None,
            recent_intents=recent_intents,
            record_metrics=False,
        )
        if decision is None or (
            refined.intent != decision.intent
            or refined.next_action != decision.next_action
            or refined.handoff_reason != decision.handoff_reason
        ):
            record_intent_metrics(refined)
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
        lease_owner: str,
    ) -> None:
        message_id = int(payload["messageId"])
        failure = await agent_task_service.mark_failed(
            message_id,
            str(exc),
            lease_owner,
            force_terminal=self._deadline_expired(payload),
        )
        if failure is None:
            # 租约已丢失（任务被其他 Worker 接管）——本份消息直接丢弃，
            # 新执行方负责重试/终态，这里不调度重试也不推送用户可见错误。
            logger.warning(
                "agent_task_lease_lost_skipping_retry", message_id=message_id
            )
            await message.ack()
            return
        retry_count, terminal = failure
        if terminal:
            AGENT_TASK_TOTAL.labels(
                queue=str(payload.get("queueName") or "unknown"),
                result="dead",
            ).inc()
            await self._notify_terminal(message, payload)
            return
        # P0-2a：退避 + 抖动，不立即重发——由 recover_pending 扫描在
        # next_retry_at 之后拉起。下游故障时不会形成重试风暴。
        delay = min(2 ** retry_count, 60) + random.uniform(0, 2)
        await agent_task_service.schedule_retry(message_id, int(delay))
        AGENT_TASK_TOTAL.labels(
            queue=str(payload.get("queueName") or "unknown"),
            result="retry_scheduled",
        ).inc()
        await message.ack()

    async def _finish_terminal(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        payload: dict,
        error: str,
        lease_owner: str | None = None,
    ) -> None:
        message_id = int(payload["messageId"])
        terminal_written = await agent_task_service.mark_terminal(
            message_id, error, lease_owner
        )
        # 终态写入带租约守卫：未命中说明任务已被其他 Worker 接管（P1 审查）。
        # 这时不能再向用户推送错误文案——新执行方正在正常处理，推了就是
        # 误报"服务不可用"。
        if not terminal_written:
            logger.warning(
                "agent_task_terminal_skipped_lease_lost",
                message_id=message_id,
                error=error,
            )
            await message.ack()
            return
        await self._notify_terminal(message, payload)

    async def _notify_terminal(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        payload: dict,
    ) -> None:
        message_id = int(payload["messageId"])
        user_id = str(payload["userId"])
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
