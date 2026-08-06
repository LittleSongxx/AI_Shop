from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.harness.metrics.runtime_sensors import (
    EPISODE_DROPPED_TOTAL,
    EPISODE_EVENT_TOTAL,
    EPISODE_QUEUE_DEPTH,
    EPISODE_WRITE_LATENCY,
)
from app.observability.telemetry import current_span_id, current_trace_id

logger = structlog.get_logger()

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])")
_SECRET_KEYS = frozenset(
    {
        "authorization",
        "password",
        "token",
        "access_token",
        "api_key",
        "secret",
        "cookie",
    }
)
_ID_KEYS = frozenset(
    {
        "userid",
        "user_id",
        "orderid",
        "order_id",
        "orderitemid",
        "order_item_id",
        "addressid",
        "address_id",
        "actiontoken",
        "action_token",
    }
)
_RAW_TEXT_KEYS = frozenset(
    {
        "message",
        "usermessage",
        "user_message",
        "usertext",
        "user_text",
        "assistantmessage",
        "assistant_message",
        "assistanttext",
        "assistant_text",
        "content",
        "messages",
        "address",
        "query",
    }
)


@dataclass(frozen=True)
class EpisodeContext:
    run_id: str
    message_id: int | None
    user_id: str
    force_keep: bool = False


_CURRENT_EPISODE: ContextVar[EpisodeContext | None] = ContextVar(
    "agent_episode", default=None
)


def new_run_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def bind_episode(
    run_id: str | None,
    *,
    message_id: int | None,
    user_id: str,
    force_keep: bool = False,
) -> Iterator[EpisodeContext | None]:
    if not run_id:
        yield None
        return
    context = EpisodeContext(
        run_id=str(run_id),
        message_id=message_id,
        user_id=str(user_id),
        force_keep=bool(force_keep),
    )
    token: Token = _CURRENT_EPISODE.set(context)
    try:
        yield context
    finally:
        _CURRENT_EPISODE.reset(token)


def current_episode() -> EpisodeContext | None:
    return _CURRENT_EPISODE.get()


def _utcnow_sql() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )[:-3]


def _stable_placeholder(kind: str, value: object) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<{kind.upper()}:{digest}>"


def _text_fingerprint(value: object) -> dict[str, object]:
    text = str(value or "")
    return {
        "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        "chars": len(text),
    }


def sanitize_episode_payload(value: Any, *, key: str = "") -> Any:
    """Keep observable decisions without duplicating raw conversation or credentials."""
    normalized_key = key.lower().replace("-", "_")
    compact_key = normalized_key.replace("_", "")
    if normalized_key in _SECRET_KEYS or compact_key in {
        item.replace("_", "") for item in _SECRET_KEYS
    }:
        return "<REDACTED>"
    if normalized_key in _ID_KEYS or compact_key in {
        item.replace("_", "") for item in _ID_KEYS
    }:
        return None if value is None else _stable_placeholder(normalized_key, value)
    if normalized_key in _RAW_TEXT_KEYS or compact_key in {
        item.replace("_", "") for item in _RAW_TEXT_KEYS
    }:
        if normalized_key == "messages" and isinstance(value, (list, tuple)):
            roles = [
                str(getattr(item, "type", None) or getattr(item, "role", None) or "unknown")
                for item in value
            ]
            chars = sum(len(str(getattr(item, "content", "") or "")) for item in value)
            return {"count": len(value), "roles": roles[:32], "chars": chars}
        return _text_fingerprint(value)
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_episode_payload(child, key=str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_episode_payload(item, key=key) for item in list(value)[:100]]
    if isinstance(value, str):
        text = _PHONE_RE.sub("<PHONE>", value)
        text = _EMAIL_RE.sub("<EMAIL>", text)
        text = _LONG_IDENTIFIER_RE.sub(
            lambda match: _stable_placeholder("ID", match.group(0)), text
        )
        return text[:2_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return sanitize_episode_payload(value.model_dump(mode="json"), key=key)
        except Exception:
            pass
    return str(value)[:500]


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(
        sanitize_episode_payload(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


@lru_cache(maxsize=1)
def version_manifest() -> dict[str, str]:
    settings = get_settings()
    root = Path(__file__).resolve().parents[2]
    prompt_hash = hashlib.sha256()
    for relative in ("prompts/agent.txt", "prompts/user_intent.txt"):
        path = root / relative
        if path.exists():
            prompt_hash.update(path.read_bytes())
    return {
        "app": settings.app_version,
        "graph": settings.app_version,
        "prompt": prompt_hash.hexdigest(),
        "toolContract": "aishop-tools/v2",
        "memorySchema": "aishop-memory/v2",
    }


class EpisodeService:
    _STOP = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | object] | None = None
        self._writer: asyncio.Task[None] | None = None

    async def start(self) -> None:
        settings = get_settings()
        if not settings.episode_enabled or self._writer is not None:
            return
        self._queue = asyncio.Queue(maxsize=settings.episode_queue_size)
        self._writer = asyncio.create_task(
            self._writer_loop(), name="agent-episode-writer"
        )
        try:
            await self.purge_expired()
        except Exception as exc:
            logger.warning("episode_retention_cleanup_failed", error=type(exc).__name__)

    async def close(self) -> None:
        queue = self._queue
        writer = self._writer
        if queue is None or writer is None:
            return
        try:
            queue.put_nowait(self._STOP)
        except asyncio.QueueFull:
            EPISODE_DROPPED_TOTAL.labels(reason="shutdown_queue_full").inc()
            writer.cancel()
        try:
            await asyncio.wait_for(writer, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
        finally:
            self._queue = None
            self._writer = None
            EPISODE_QUEUE_DEPTH.set(0)

    async def purge_expired(self) -> None:
        days = get_settings().episode_retention_days
        async with acquire() as cur:
            await cur.execute(
                """
                DELETE s FROM agent_step s
                INNER JOIN agent_run r ON r.run_id=s.run_id
                WHERE r.started_at < DATE_SUB(NOW(), INTERVAL %s DAY)
                  AND r.dataset_eligible <> 'APPROVED'
                """,
                (days,),
            )
            await cur.execute(
                """
                DELETE FROM agent_run
                WHERE started_at < DATE_SUB(NOW(), INTERVAL %s DAY)
                  AND dataset_eligible <> 'APPROVED'
                """,
                (days,),
            )

    def start_run(
        self,
        *,
        run_id: str,
        message_id: int,
        user_id: str,
        session_id: str | None,
        intent: str | None,
        queue_name: str | None,
        force_keep: bool,
        experiment: dict | None = None,
    ) -> bool:
        rate = self._effective_success_sample_rate()
        sampled = force_keep or self._sample(run_id, rate)
        self._enqueue(
            {
                "op": "start",
                "run_id": run_id,
                "message_id": message_id,
                "user_id": user_id,
                "session_id": session_id,
                "trace_id": current_trace_id(),
                "intent": intent,
                "queue_name": queue_name,
                "version_json": _json(version_manifest()),
                "experiment_json": _json(experiment),
                "capture_level": "FULL" if sampled else "PENDING_SAMPLE",
                "started_at": _utcnow_sql(),
                "sampled": sampled,
            }
        )
        return sampled

    def mark_running(self, run_id: str | None = None) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if resolved_run_id:
            self._enqueue(
                {
                    "op": "running",
                    "run_id": resolved_run_id,
                    "trace_id": current_trace_id(),
                }
            )

    def record_step(
        self,
        event_type: str,
        *,
        run_id: str | None = None,
        node_name: str | None = None,
        round_no: int | None = None,
        status: str = "OK",
        input_data: Any = None,
        output_data: Any = None,
        model_name: str | None = None,
        tool_name: str | None = None,
        call_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if not resolved_run_id:
            return
        self._enqueue(
            {
                "op": "step",
                "run_id": resolved_run_id,
                "event_type": str(event_type)[:40],
                "node_name": str(node_name)[:64] if node_name else None,
                "round_no": round_no,
                "status": str(status)[:20],
                "span_id": current_span_id(),
                "input_json": _json(input_data),
                "output_json": _json(output_data),
                "model_name": str(model_name)[:128] if model_name else None,
                "tool_name": str(tool_name)[:64] if tool_name else None,
                "call_id": str(call_id)[:128] if call_id else None,
                "error_code": str(error_code)[:64] if error_code else None,
                # Exception strings can echo prompts, addresses, order numbers, or
                # provider payloads. Keep a stable correlation key, not the raw text.
                "error_message": (
                    _stable_placeholder("ERROR", error_message)
                    if error_message
                    else None
                ),
                "latency_ms": max(0, int(latency_ms)) if latency_ms is not None else None,
                "occurred_at": _utcnow_sql(),
            }
        )

    def add_llm_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_cny: float = 0,
        model_name: str | None = None,
        run_id: str | None = None,
    ) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if not resolved_run_id:
            return
        self._enqueue(
            {
                "op": "usage",
                "run_id": resolved_run_id,
                "input_tokens": max(0, int(input_tokens)),
                "output_tokens": max(0, int(output_tokens)),
                "cost_cny": max(0.0, float(cost_cny)),
                "model_name": str(model_name)[:128] if model_name else None,
            }
        )

    def update_run(
        self,
        *,
        run_id: str | None = None,
        intent: str | None = None,
        scenario: str | None = None,
        experiment: dict | None = None,
        quality: dict | None = None,
        reward_signals: dict | None = None,
    ) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if not resolved_run_id:
            return
        self._enqueue(
            {
                "op": "update",
                "run_id": resolved_run_id,
                "intent": intent,
                "scenario": scenario,
                "experiment_json": _json(experiment),
                "quality_json": _json(quality),
                "reward_signals_json": _json(reward_signals),
            }
        )

    def finish_run(
        self,
        outcome: str,
        *,
        run_id: str | None = None,
        status: str | None = None,
        latency_ms: int | None = None,
        force_keep: bool | None = None,
    ) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if not resolved_run_id:
            return
        context = current_episode()
        keep = bool(context.force_keep) if context and force_keep is None else bool(force_keep)
        terminal_status = status or self._status_for_outcome(outcome)
        self._enqueue(
            {
                "op": "finish",
                "run_id": resolved_run_id,
                "outcome": str(outcome)[:32],
                "status": terminal_status,
                "latency_ms": max(0, int(latency_ms)) if latency_ms is not None else None,
                "completed_at": _utcnow_sql(),
                "force_keep": keep,
            }
        )

    def _enqueue_for_run(self, op: str, *, run_id: str | None = None) -> None:
        resolved_run_id = self._resolve_run_id(run_id)
        if resolved_run_id:
            self._enqueue({"op": op, "run_id": resolved_run_id})

    def _enqueue(self, event: dict[str, Any]) -> None:
        queue = self._queue
        if queue is None or self._writer is None:
            return
        try:
            queue.put_nowait(event)
            EPISODE_QUEUE_DEPTH.set(queue.qsize())
            EPISODE_EVENT_TOTAL.labels(event=event["op"], result="queued").inc()
        except asyncio.QueueFull:
            EPISODE_DROPPED_TOTAL.labels(reason="queue_full").inc()
            EPISODE_EVENT_TOTAL.labels(event=event["op"], result="dropped").inc()

    async def _writer_loop(self) -> None:
        assert self._queue is not None
        queue = self._queue
        settings = get_settings()
        timeout = settings.episode_flush_interval_ms / 1_000
        stop = False
        while not stop:
            batch: list[dict[str, Any]] = []
            try:
                first = await queue.get()
            except asyncio.CancelledError:
                break
            if first is self._STOP:
                queue.task_done()
                break
            batch.append(first)  # type: ignore[arg-type]
            deadline = time.monotonic() + timeout
            while len(batch) < settings.episode_batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    break
                if item is self._STOP:
                    queue.task_done()
                    stop = True
                    break
                batch.append(item)  # type: ignore[arg-type]
            started = time.perf_counter()
            try:
                await self._flush(batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                EPISODE_DROPPED_TOTAL.labels(reason="db_error").inc(len(batch))
                for event in batch:
                    EPISODE_EVENT_TOTAL.labels(
                        event=event["op"], result="db_error"
                    ).inc()
                logger.warning(
                    "episode_batch_write_failed",
                    count=len(batch),
                    error=type(exc).__name__,
                )
            else:
                for event in batch:
                    EPISODE_EVENT_TOTAL.labels(
                        event=event["op"], result="written"
                    ).inc()
            finally:
                EPISODE_WRITE_LATENCY.observe(time.perf_counter() - started)
                for _ in batch:
                    queue.task_done()
                EPISODE_QUEUE_DEPTH.set(queue.qsize())

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        async with acquire() as cur:
            for event in batch:
                op = event["op"]
                if op == "start":
                    await cur.execute(
                        """
                        INSERT IGNORE INTO agent_run
                            (run_id, message_id, user_id, session_id, otel_trace_id,
                             status, intent, queue_name, version_json, experiment_json,
                             capture_level, started_at)
                        VALUES (%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            event["run_id"],
                            event["message_id"],
                            event["user_id"],
                            event["session_id"],
                            event["trace_id"],
                            event["intent"],
                            event["queue_name"],
                            event["version_json"],
                            event["experiment_json"],
                            event["capture_level"],
                            event["started_at"],
                        ),
                    )
                    await cur.execute(
                        """
                        UPDATE agent_run
                        SET otel_trace_id=COALESCE(%s,otel_trace_id),
                            intent=COALESCE(%s,intent),
                            queue_name=COALESCE(%s,queue_name)
                        WHERE run_id=%s
                        """,
                        (
                            event["trace_id"],
                            event["intent"],
                            event["queue_name"],
                            event["run_id"],
                        ),
                    )
                elif op == "running":
                    await cur.execute(
                        """
                        UPDATE agent_run SET status='RUNNING',
                            otel_trace_id=COALESCE(otel_trace_id, %s)
                        WHERE run_id=%s
                          AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED')
                        """,
                        (event["trace_id"], event["run_id"]),
                    )
                elif op == "step":
                    await cur.execute(
                        """
                        INSERT INTO agent_step
                            (run_id,event_type,node_name,round_no,status,span_id,
                             input_json,output_json,model_name,tool_name,call_id,
                             error_code,error_message,latency_ms,occurred_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        tuple(event[key] for key in (
                            "run_id", "event_type", "node_name", "round_no", "status",
                            "span_id", "input_json", "output_json", "model_name",
                            "tool_name", "call_id", "error_code", "error_message",
                            "latency_ms", "occurred_at",
                        )),
                    )
                elif op == "usage":
                    await cur.execute(
                        """
                        UPDATE agent_run
                        SET input_tokens=input_tokens+%s,
                            output_tokens=output_tokens+%s,
                            cost_cny=cost_cny+%s,
                            model_name=COALESCE(%s, model_name)
                        WHERE run_id=%s
                        """,
                        (
                            event["input_tokens"],
                            event["output_tokens"],
                            event["cost_cny"],
                            event["model_name"],
                            event["run_id"],
                        ),
                    )
                elif op == "update":
                    await cur.execute(
                        """
                        UPDATE agent_run
                        SET intent=COALESCE(%s,intent), scenario=COALESCE(%s,scenario),
                            experiment_json=JSON_MERGE_PATCH(
                                COALESCE(experiment_json,JSON_OBJECT()),
                                COALESCE(%s,JSON_OBJECT())),
                            quality_json=JSON_MERGE_PATCH(
                                COALESCE(quality_json,JSON_OBJECT()),
                                COALESCE(%s,JSON_OBJECT())),
                            reward_signals_json=JSON_MERGE_PATCH(
                                COALESCE(reward_signals_json,JSON_OBJECT()),
                                COALESCE(%s,JSON_OBJECT()))
                        WHERE run_id=%s
                        """,
                        (
                            event["intent"],
                            event["scenario"],
                            event["experiment_json"],
                            event["quality_json"],
                            event["reward_signals_json"],
                            event["run_id"],
                        ),
                    )
                elif op == "finish":
                    await cur.execute(
                        """
                        UPDATE agent_run
                        SET status=%s, outcome=%s, latency_ms=COALESCE(%s,latency_ms),
                            completed_at=%s,
                            capture_level=CASE
                                WHEN capture_level='PENDING_SAMPLE' AND %s=0
                                THEN 'SUMMARY' ELSE 'FULL' END
                        WHERE run_id=%s
                        """,
                        (
                            event["status"],
                            event["outcome"],
                            event["latency_ms"],
                            event["completed_at"],
                            1 if event["force_keep"] else 0,
                            event["run_id"],
                        ),
                    )
                    if not event["force_keep"] and event["status"] == "SUCCEEDED":
                        await cur.execute(
                            """
                            DELETE s FROM agent_step s
                            INNER JOIN agent_run r ON r.run_id=s.run_id
                            WHERE s.run_id=%s AND r.capture_level='SUMMARY'
                            """,
                            (event["run_id"],),
                        )

    @staticmethod
    def _resolve_run_id(run_id: str | None) -> str | None:
        if run_id:
            return str(run_id)
        context = current_episode()
        return context.run_id if context else None

    @staticmethod
    def _sample(run_id: str, rate: float) -> bool:
        if rate <= 0:
            return False
        if rate >= 1:
            return True
        bucket = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return bucket < rate

    @staticmethod
    def _status_for_outcome(outcome: str) -> str:
        normalized = str(outcome).lower()
        if normalized in {"ok", "faq", "completed", "success"}:
            return "SUCCEEDED"
        if normalized in {"cancelled", "canceled"}:
            return "CANCELLED"
        if normalized in {"handoff", "human_support"}:
            return "HANDOFF"
        if normalized in {"degraded", "overload"}:
            return "DEGRADED"
        return "FAILED"

    @staticmethod
    def _effective_success_sample_rate() -> float:
        settings = get_settings()
        if settings.app_env.lower() in {"development", "local", "test"}:
            return 1.0
        return settings.episode_success_sample_rate


episode_service = EpisodeService()
