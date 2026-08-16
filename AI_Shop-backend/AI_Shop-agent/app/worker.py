from __future__ import annotations

import asyncio
import json
import random
import signal
import time
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
from app.graph.runner import run_agent_graph
from app.harness.metrics.runtime_sensors import (
    AGENT_TASK_INFLIGHT,
    AGENT_TASK_TOTAL,
    measure_agent_stage,
    observe_agent_stage,
)
from app.infra.http_client import close_clients as close_http_clients
from app.observability.llm_metrics import reset_run_cost, snapshot_cost_summary
from app.observability.logging import configure_structured_logging
from app.observability.telemetry import shutdown_telemetry
from app.services.agent_queue_service import agent_queue_service
from app.services.agent_service import agent_orchestrator
from app.services.episode_service import bind_episode, episode_service
from app.services.java_internal_client import set_delegated_user_id
from app.services.judge_service import judge_service
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.message_service import agent_message_service, next_unresolved_count
from app.services.pending_action_service import pending_action_service
from app.services.redis_service import redis_service
from app.services.shopping_mission_service import initialize_category_need_schemas
from app.services.stream_service import stream_service
from app.services.task_service import agent_task_service
from app.utils.product_consult import parse_consult_card
from app.visual.consumer import visual_index_consumer

configure_structured_logging()
logger = structlog.get_logger()
TERMINAL_ERROR = "服务暂时不可用，已为您保留本次咨询记录，请稍后重试或转人工客服。"
_EPISODE_FULL_INTENTS = frozenset(
    {
        "REFUND",
        "REFUND_STATUS",
        "CONFIRM_RECEIPT",
        "CANCEL_ORDER",
        "PRODUCT_REVIEW",
        "RECOMMENT",
        "COMPLAINT",
        "PAYMENT_ISSUE",
        "DAMAGED_OR_WRONG_ITEM",
        "ADDRESS_CHANGE",
        "INVOICE",
        "AFTERSALES_UNKNOWN",
    }
)


class LeaseLostError(RuntimeError):
    """The claimed task or per-user lock is no longer owned by this execution."""


class AgentWorker:

    def __init__(self) -> None:
        self._channels: list[aio_pika.abc.AbstractChannel] = []
        self._consumers: list[tuple[aio_pika.abc.AbstractQueue, str]] = []
        self._background_tasks: list[asyncio.Task] = []
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
        await initialize_category_need_schemas()
        await episode_service.start()
        await judge_service.start()
        try:
            await self._connect_queue_until_ready()
            await self._start_consumer(
                AGENT_QUEUE_HIGH, settings.agent_worker_high_concurrency
            )
            await self._start_consumer(
                AGENT_QUEUE_FAST, settings.agent_worker_fast_concurrency
            )
            await self._start_consumer(
                AGENT_QUEUE_LOW, settings.agent_worker_low_concurrency
            )
            visual_index_active = (
                settings.visual_search_enabled
                and settings.visual_index_consumer_enabled
                and bool(settings.visual_api_key.strip())
            )
            if visual_index_active:
                try:
                    channel, queue, consumer_tag = await visual_index_consumer.start()
                except Exception as exc:
                    # A visual queue/topology issue degrades only image search;
                    # customer text/after-sales Agent queues must remain alive.
                    logger.warning(
                        "visual_index_consumer_degraded",
                        error=type(exc).__name__,
                    )
                else:
                    self._channels.append(channel)
                    self._consumers.append((queue, consumer_tag))
                    bootstrap_task = asyncio.create_task(
                        visual_index_consumer.bootstrap_if_needed(),
                        name="visual-index-bootstrap",
                    )
                    bootstrap_task.add_done_callback(self._log_background_task_result)
                    self._background_tasks.append(bootstrap_task)
            else:
                if not settings.visual_search_enabled:
                    reason = "VISUAL_SEARCH_DISABLED"
                elif not settings.visual_index_consumer_enabled:
                    reason = "VISUAL_INDEX_CONSUMER_DISABLED"
                else:
                    reason = "VISUAL_API_KEY_NOT_CONFIGURED"
                try:
                    removed = await visual_index_consumer.remove_derived_queues()
                except Exception as exc:
                    logger.warning(
                        "visual_index_queue_cleanup_failed",
                        reason=reason,
                        error=type(exc).__name__,
                    )
                else:
                    logger.warning(
                        "visual_index_consumer_inactive",
                        reason=reason,
                        removed_queues=removed,
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

    async def _connect_queue_until_ready(self) -> None:
        """Keep a Worker alive through the broker's short startup/restart window.

        ``aio_pika.connect_robust`` reconnects an established connection, but its
        initial connection is fail-fast. RabbitMQ can already pass a node-ping
        health check before its AMQP listener accepts connections, so a Worker
        started in that window used to exit permanently. The API publish path
        remains fail-fast and returns PENDING_RECOVERY; only the background
        Worker waits here because it cannot do useful work without the broker.
        """

        delay_seconds = 1
        while True:
            try:
                await agent_queue_service.connect()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "agent_worker_queue_startup_retry",
                    retry_in_seconds=delay_seconds,
                    error=str(exc),
                )
                await agent_queue_service.close()
                await asyncio.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2, 5)

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
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            if self._background_tasks:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
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
            await judge_service.close()
            await episode_service.close()
            await close_pool()
            await redis_service.close()
        finally:
            # BatchSpanProcessor buffers spans; a graceful Worker stop must flush
            # its final task/LLM spans just like the API process does.
            shutdown_telemetry()
            logger.info("agent_worker_stopped")

    @staticmethod
    def _log_background_task_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "agent_worker_background_task_failed",
                task=task.get_name(),
                error=type(error).__name__,
            )

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
        episode_payload: dict = {}
        try:
            decoded = json.loads(message.body.decode("utf-8"))
            if isinstance(decoded, dict):
                episode_payload = decoded
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        with trace.get_tracer("aishop.agent").start_as_current_span(
            "agent.task.consume", context=ctx
        ) as span:
            run_id = str(episode_payload.get("runId") or "") or None
            raw_message_id = episode_payload.get("messageId")
            try:
                message_id = int(raw_message_id) if raw_message_id is not None else None
            except (TypeError, ValueError):
                message_id = None
            user_id = str(episode_payload.get("userId") or "")
            if message_id is not None:
                span.set_attribute("agent.message_id", message_id)
            if run_id:
                span.set_attribute("agent.run_id", run_id)
            force_keep = bool(episode_payload.get("episodeKeep")) or str(
                episode_payload.get("intent") or ""
            ) in _EPISODE_FULL_INTENTS
            with bind_episode(
                run_id,
                message_id=message_id,
                user_id=user_id,
                force_keep=force_keep,
            ):
                if run_id and message_id is not None and user_id:
                    episode_service.start_run(
                        run_id=run_id,
                        message_id=message_id,
                        user_id=user_id,
                        session_id=episode_payload.get("sessionId"),
                        intent=str(episode_payload.get("intent") or "") or None,
                        queue_name=queue_name,
                        force_keep=force_keep,
                    )
                episode_service.mark_running(run_id)
                episode_service.record_step(
                    "MQ_RECEIVE",
                    run_id=run_id,
                    node_name="worker",
                    input_data={
                        "queue": queue_name,
                        "redelivered": bool(message.redelivered),
                        "messageId": message_id,
                    },
                )
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
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning("agent_task_invalid_payload", queue=queue_name, error=str(exc))
            await message.reject(requeue=False)
            return

        # 委托身份（系统信道）：Java 内部接口的 X-Agent-User-Id 只信任这个值，
        # 不信任模型可见的 body。整个消费 task 内所有 java_internal 调用自动携带。
        # 每条消息在独立 task 中消费，contextvar 随 task 结束丢弃，无需显式清理。
        set_delegated_user_id(user_id)

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

        processing_started = time.perf_counter()
        enqueued_at_ms = payload.get("enqueuedAtEpochMs")
        enqueued_at_seconds: float | None = None
        if (
            isinstance(enqueued_at_ms, (int, float))
            and not isinstance(enqueued_at_ms, bool)
            and enqueued_at_ms > 0
        ):
            enqueued_at_seconds = float(enqueued_at_ms) / 1000
            observe_agent_stage("queue_wait", time.time() - enqueued_at_seconds)

        AGENT_TASK_INFLIGHT.labels(queue=queue_name).inc()
        AGENT_TASK_TOTAL.labels(queue=queue_name, result="started").inc()
        # 处理期间周期续租（任务租约 + 用户锁）；续租失败说明租约已被接管
        # （我们超时了），停手。任务租约和用户锁都可能先于总 deadline 到期，
        # 长任务不同时续这两份租约会让任务被接管或让同用户新消息并发进来。
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
                episode_service.finish_run("cancelled")
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
                await agent_message_service.reset_unresolved_count(message_id)
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
            total_seconds = (
                time.time() - enqueued_at_seconds
                if enqueued_at_seconds is not None
                else time.perf_counter() - processing_started
            )
            observe_agent_stage("total", total_seconds)

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
        # E 工作线：每次请求的 LLM 成本累计从意图识别之前开始
        # （意图识别是对话路径的固定成本），到 GRAPH_END 快照成 costSummary。
        reset_run_cost()
        decision = await self._refine_decision(payload)
        if decision.should_handoff:
            await agent_orchestrator._transfer_to_support(
                payload,
                payload.get("userMessage") or "",
                decision.model_dump(mode="json"),
            )
            # E 工作线：转人工不经过 run_agent_graph（快照只在 GRAPH_END），
            # 意图精炼已累计的 LLM 成本在这里补落成本摘要，否则 per-request
            # 摘要对转人工消息整类缺失。
            episode_service.record_step(
                "HANDOFF_COST",
                node_name="worker",
                status="OK",
                output_data={
                    "outcome": "handoff",
                    "tools": [],
                    "costSummary": snapshot_cost_summary(tools_called=[]),
                },
            )
            return "ok"
        return await run_agent_graph(payload)

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
        with measure_agent_stage("intent"):
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
                after_sales_workflow=True,
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
        previous_unresolved = max(
            0, int(payload.get("unresolvedCount") or 0) - 1
        )
        refined_unresolved = next_unresolved_count(refined, previous_unresolved)
        payload["unresolvedCount"] = refined_unresolved
        await agent_message_service.update_decision(
            int(payload["messageId"]),
            refined,
            refined_unresolved,
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
            episode_service.record_step(
                "WORKER_ERROR",
                node_name="worker",
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            episode_service.finish_run("worker_error", force_keep=True)
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
        episode_service.record_step(
            "DEADLINE",
            node_name="worker",
            status="ERROR",
            error_code="TASK_DEADLINE",
            error_message=error,
        )
        episode_service.finish_run("deadline", force_keep=True)
        await self._notify_terminal(message, payload)

    async def _notify_terminal(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        payload: dict,
    ) -> None:
        message_id = int(payload["messageId"])
        user_id = str(payload["userId"])
        await stream_service.push_error(user_id, message_id, TERMINAL_ERROR, "agent")
        await agent_message_service.reset_unresolved_count(message_id)
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
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    signal_installed = False

    def request_shutdown() -> None:
        logger.info("agent_worker_shutdown_requested", signal="SIGTERM")
        if current_task is not None and not current_task.done():
            current_task.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, request_shutdown)
        signal_installed = True
    except (NotImplementedError, RuntimeError):
        # add_signal_handler is unavailable on Windows and non-main threads.
        # Production and the bundled start/stop scripts run the Worker on Linux.
        pass

    try:
        await AgentWorker().run()
    except asyncio.CancelledError:
        logger.info("agent_worker_shutdown_completed", signal="SIGTERM")
    finally:
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
