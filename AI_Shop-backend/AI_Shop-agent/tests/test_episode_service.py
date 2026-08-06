from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from app.config.settings import get_settings
from app.observability.telemetry import current_span_id, current_trace_id
from app.services.episode_service import (
    EpisodeService,
    bind_episode,
    sanitize_episode_payload,
)


def test_episode_payload_redacts_raw_text_credentials_and_business_ids():
    payload = sanitize_episode_payload(
        {
            "userMessage": "我的手机号是13812345678，订单 ABCD2026080712345678",
            "password": "not-for-storage",
            "orderId": "ABCD2026080712345678",
            "nested": {"email": "buyer@example.com"},
        }
    )

    assert payload["userMessage"]["chars"] > 0
    assert len(payload["userMessage"]["sha256"]) == 64
    assert payload["password"] == "<REDACTED>"
    assert payload["orderId"].startswith("<ORDERID:")
    assert payload["nested"]["email"] == "<EMAIL>"
    assert "13812345678" not in str(payload)
    assert "ABCD2026080712345678" not in str(payload)


def test_current_otel_ids_only_come_from_an_active_span():
    assert current_trace_id() is None
    assert current_span_id() is None

    context = SpanContext(
        trace_id=int("1" * 32, 16),
        span_id=int("2" * 16, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=trace.DEFAULT_TRACE_STATE,
    )
    with trace.use_span(NonRecordingSpan(context)):
        assert current_trace_id() == "1" * 32
        assert current_span_id() == "2" * 16


def test_episode_error_message_is_fingerprinted_before_enqueue():
    service = EpisodeService()
    service._queue = asyncio.Queue(maxsize=2)
    service._writer = AsyncMock()

    service.record_step(
        "ERROR",
        run_id="run-1",
        error_message="订单 ORDER2026080712345678 地址是测试路 1 号",
    )

    event = service._queue.get_nowait()
    assert event["error_message"].startswith("<ERROR:")
    assert "ORDER2026080712345678" not in event["error_message"]


@pytest.mark.asyncio
async def test_episode_writer_keeps_event_order_and_drains_on_shutdown(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "episode_enabled", True)
    monkeypatch.setattr(settings, "episode_flush_interval_ms", 10)
    service = EpisodeService()
    monkeypatch.setattr(service, "purge_expired", AsyncMock())
    batches: list[list[dict]] = []

    async def capture(batch: list[dict]) -> None:
        batches.append(list(batch))

    monkeypatch.setattr(service, "_flush", capture)
    await service.start()
    keep = service.start_run(
        run_id="run-1",
        message_id=1,
        user_id="u-1",
        session_id=None,
        intent="CHAT",
        queue_name="agent.low",
        force_keep=False,
    )
    with bind_episode(
        "run-1", message_id=1, user_id="u-1", force_keep=keep
    ):
        service.mark_running()
        service.record_step("NODE_TRANSITION", node_name="entry")
        service.finish_run("ok")
    await asyncio.sleep(0.03)
    await service.close()

    operations = [event["op"] for batch in batches for event in batch]
    assert operations == ["start", "running", "step", "finish"]
    assert service._writer is None


@pytest.mark.asyncio
async def test_episode_queue_full_is_fail_open(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "episode_enabled", True)
    service = EpisodeService()
    service._queue = asyncio.Queue(maxsize=1)
    service._writer = AsyncMock()

    service.record_step("FIRST", run_id="run-1")
    service.record_step("SECOND", run_id="run-1")

    assert service._queue.qsize() == 1
