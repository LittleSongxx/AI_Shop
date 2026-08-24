from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import Settings
from app.constants import MSG_STATUS_COMPLETE
from app.domain.intent.types import IntentDecision, IntentKind, NextAction
from app.services.agent_service import AgentOrchestrator
from app.services.message_service import AgentMessageService
from app.services.task_service import AgentTaskService
from app.worker import AgentWorker, LeaseLostError, TaskDeadlineError


class _Cursor:
    def __init__(self, *, rowcount: int = 1, one: dict | None = None, all_=None):
        self.rowcount = rowcount
        self.one = one
        self.all = [] if all_ is None else all_
        self.calls: list[tuple[str, tuple | None]] = []

    async def execute(self, sql: str, params: tuple | None = None) -> None:
        self.calls.append((sql, params))

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.all


class _SequencedCursor(_Cursor):
    def __init__(self, execute_results: list[int]):
        super().__init__()
        self.execute_results = list(execute_results)

    async def execute(self, sql: str, params: tuple | None = None) -> int:
        self.calls.append((sql, params))
        return self.execute_results.pop(0)


def _acquire_for(cursor: _Cursor):
    @asynccontextmanager
    async def acquire():
        yield cursor

    return acquire


@pytest.mark.asyncio
async def test_worker_fails_fast_when_metrics_port_is_already_in_use():
    worker = AgentWorker()
    connect = AsyncMock()
    settings = Settings(_env_file=None, data_analyst_enabled=False)
    with (
        patch("app.worker.get_settings", return_value=settings),
        patch("prometheus_client.start_http_server", side_effect=OSError("in use")),
        patch("app.observability.telemetry.configure_worker_telemetry"),
        patch("app.worker.redis_service.connect", connect),
    ):
        with pytest.raises(RuntimeError, match="WORKER_METRICS_PORT"):
            await worker.run()

    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_retries_the_initial_queue_connection_with_bounded_backoff():
    worker = AgentWorker()
    connect = AsyncMock(side_effect=[ConnectionError("listener not ready"), None])
    close = AsyncMock()
    sleep = AsyncMock()

    with (
        patch("app.worker.agent_queue_service.connect", connect),
        patch("app.worker.agent_queue_service.close", close),
        patch("app.worker.asyncio.sleep", sleep),
    ):
        await worker._connect_queue_until_ready()

    assert connect.await_count == 2
    close.assert_awaited_once()
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"{invalid-json", b"\xff\xfe"])
async def test_invalid_queue_payload_is_rejected_exactly_once(body: bytes):
    worker = AgentWorker()
    message = MagicMock()
    message.body = body
    message.reject = AsyncMock()
    message.ack = AsyncMock()
    message.nack = AsyncMock()

    await worker._process_message_inner("q", message)

    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()
    message.nack.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_failed_writes_dead_atomically_at_retry_limit():
    cursor = _Cursor(one={"retry_count": 5, "status": "DEAD"})
    with patch("app.services.task_service.acquire", _acquire_for(cursor)):
        result = await AgentTaskService().mark_failed(42, "boom", "owner-1")

    assert result == (5, True)
    update_sql, update_params = cursor.calls[0]
    assert "retry_count=retry_count+1" in update_sql
    assert "THEN 'DEAD'" in update_sql
    assert "lease_owner=%s" in update_sql
    assert "lease_until >= NOW()" in update_sql
    assert update_params[-1] == "owner-1"


@pytest.mark.asyncio
async def test_expired_lease_cannot_be_renewed_or_write_a_guarded_terminal_state():
    renew_cursor = _Cursor(rowcount=0)
    with patch("app.services.task_service.acquire", _acquire_for(renew_cursor)):
        renewed = await AgentTaskService().renew_lease(42, "owner-1", 60)

    assert renewed is False
    renew_sql, renew_params = renew_cursor.calls[0]
    assert "lease_until >= NOW()" in renew_sql
    assert renew_params == (60, 42, "owner-1")

    complete_cursor = _Cursor(rowcount=0)
    with patch("app.services.task_service.acquire", _acquire_for(complete_cursor)):
        completed = await AgentTaskService().mark_completed(42, "owner-1")

    assert completed is False
    complete_sql, _ = complete_cursor.calls[0]
    assert "lease_until >= NOW()" in complete_sql

    terminal_cursor = _Cursor(rowcount=0)
    with patch("app.services.task_service.acquire", _acquire_for(terminal_cursor)):
        terminal = await AgentTaskService().mark_terminal(42, "late result", "owner-1")

    assert terminal is False
    terminal_sql, _ = terminal_cursor.calls[0]
    assert "lease_until >= NOW()" in terminal_sql


@pytest.mark.asyncio
async def test_load_pending_never_periodically_republishes_queued_tasks():
    cursor = _Cursor(all_=[])
    with patch("app.services.task_service.acquire", _acquire_for(cursor)):
        await AgentTaskService().load_pending()

    select_sql, _ = cursor.calls[0]
    assert "status='DISPATCHING'" in select_sql
    assert "status='PROCESSING'" in select_sql
    assert "'QUEUED'" not in select_sql


@pytest.mark.asyncio
async def test_recovery_publishes_only_after_dispatch_cas_succeeds():
    worker = AgentWorker()
    rows = [
        {"message_id": 1, "queue_name": "q", "payload": {"messageId": 1}},
        {"message_id": 2, "queue_name": "q", "payload": {"messageId": 2}},
    ]
    with (
        patch("app.worker.agent_task_service.load_pending", AsyncMock(return_value=rows)),
        patch(
            "app.worker.agent_task_service.mark_dispatching",
            AsyncMock(side_effect=[False, True]),
        ),
        patch("app.worker.agent_task_service.mark_queued", AsyncMock(return_value=True)) as queued,
        patch("app.worker.agent_queue_service.publish", AsyncMock()) as publish,
    ):
        await worker.recover_pending()

    publish.assert_awaited_once_with("q", {"messageId": 2})
    queued.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_lease_guard_cancels_operation_when_lease_is_lost():
    lease_lost = asyncio.Event()
    operation_cancelled = asyncio.Event()

    async def operation() -> str:
        try:
            await asyncio.Event().wait()
        finally:
            operation_cancelled.set()

    guarded = asyncio.create_task(AgentWorker._run_with_lease_guard(operation(), lease_lost))
    await asyncio.sleep(0)
    lease_lost.set()

    with pytest.raises(LeaseLostError):
        await guarded
    assert operation_cancelled.is_set()


@pytest.mark.asyncio
async def test_lease_guard_cancels_operation_when_worker_is_cancelled():
    operation_cancelled = asyncio.Event()

    async def operation() -> str:
        try:
            await asyncio.Event().wait()
        finally:
            operation_cancelled.set()

    guarded = asyncio.create_task(AgentWorker._run_with_lease_guard(operation(), asyncio.Event()))
    await asyncio.sleep(0)
    guarded.cancel()

    with pytest.raises(asyncio.CancelledError):
        await guarded
    assert operation_cancelled.is_set()


@pytest.mark.asyncio
async def test_lease_guard_cancels_and_joins_operation_at_task_deadline():
    operation_cancelled = asyncio.Event()

    async def operation() -> str:
        try:
            await asyncio.Event().wait()
        finally:
            operation_cancelled.set()

    with pytest.raises(TaskDeadlineError, match="during execution"):
        await AgentWorker._run_with_lease_guard(
            operation(),
            asyncio.Event(),
            timeout_seconds=0.001,
        )
    assert operation_cancelled.is_set()


def test_worker_deadline_remaining_is_bounded_and_malformed_fails_closed():
    future = datetime.now() + timedelta(seconds=5)
    remaining = AgentWorker._remaining_deadline_seconds({"deadlineAt": future.isoformat()})

    assert remaining is not None
    assert 0 < remaining <= 5
    assert AgentWorker._remaining_deadline_seconds({"deadlineAt": "invalid"}) == 0
    assert AgentWorker._deadline_expired({"deadlineAt": "invalid"}) is True


@pytest.mark.asyncio
async def test_worker_deadline_writes_terminal_without_retrying():
    worker = AgentWorker()
    payload = {
        "messageId": 12,
        "userId": "u1",
        "queueName": "q",
        "deadlineAt": (datetime.now() + timedelta(seconds=30)).isoformat(),
    }
    message = MagicMock()
    message.body = json.dumps(payload).encode()
    message.redelivered = False
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    message.reject = AsyncMock()

    async def deadline_guard(operation, *_args, **_kwargs):
        operation.close()
        raise TaskDeadlineError("deadline")

    with (
        patch("app.worker.agent_task_service.claim", AsyncMock(return_value=True)),
        patch(
            "app.worker.redis_service.acquire_agent_user_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.worker.agent_message_service.is_execution_cancelled",
            AsyncMock(return_value=False),
        ),
        patch("app.worker.redis_service.release_agent_user_lock", AsyncMock()),
        patch("app.worker.observe_agent_stage"),
        patch.object(
            worker,
            "_run_with_lease_guard",
            AsyncMock(side_effect=deadline_guard),
        ),
        patch.object(worker, "_finish_terminal", AsyncMock()) as finish,
        patch("app.worker.agent_task_service.mark_failed", AsyncMock()) as failed,
    ):
        await worker._process_message_inner("q", message)

    finish.assert_awaited_once()
    assert finish.await_args.args[:3] == (
        message,
        payload,
        "TASK_DEADLINE: 任务超过处理截止时间",
    )
    assert str(finish.await_args.args[3]).startswith("worker-")
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_renew_dependency_error_signals_lease_loss():
    worker = AgentWorker()
    lease_lost = asyncio.Event()

    with (
        patch("app.worker.asyncio.sleep", AsyncMock(return_value=None)),
        patch(
            "app.worker.agent_task_service.renew_lease",
            AsyncMock(side_effect=ConnectionError("database unavailable")),
        ),
    ):
        await worker._renew_lease_loop(7, "owner", 30, "user", "lock", 30, lease_lost)

    assert lease_lost.is_set()


@pytest.mark.asyncio
async def test_terminal_failure_notifies_without_second_terminal_write():
    worker = AgentWorker()
    message = MagicMock()
    message.ack = AsyncMock()
    payload = {"messageId": 9, "userId": "u1", "queueName": "q"}

    with (
        patch(
            "app.worker.agent_task_service.mark_failed",
            AsyncMock(return_value=(5, True)),
        ),
        patch("app.worker.agent_task_service.mark_terminal", AsyncMock()) as terminal,
        patch(
            "app.worker.agent_message_service.reset_unresolved_count",
            AsyncMock(),
        ),
        patch.object(worker, "_notify_terminal", AsyncMock()) as notify,
    ):
        await worker._retry_or_dead(message, payload, RuntimeError("boom"), "owner")

    terminal.assert_not_awaited()
    notify.assert_awaited_once_with(message, payload)


@pytest.mark.asyncio
async def test_worker_refine_recalculates_unresolved_count_from_new_decision():
    worker = AgentWorker()
    initial = IntentDecision(
        intent=IntentKind.CHAT,
        confidence=0.4,
        next_action=NextAction.HANDOFF_SUGGESTED,
        handoff_reason="LOW_CONFIDENCE",
        source="default",
    )
    refined = IntentDecision(
        intent=IntentKind.CHAT,
        confidence=0.9,
        next_action=NextAction.ANSWER,
        source="llm",
    )
    payload = {
        "messageId": 91,
        "userId": "u1",
        "userMessage": "支付方式有哪些",
        "unresolvedCount": 1,
        "intentDecision": initial.model_dump(mode="json"),
    }

    with (
        patch(
            "app.worker.agent_message_service.get_recent_intents",
            AsyncMock(return_value=[]),
        ),
        patch("app.worker.resolve_intent", AsyncMock(return_value=refined)),
        patch("app.worker.record_intent_metrics"),
        patch("app.worker.agent_message_service.update_decision", AsyncMock()) as update,
    ):
        result = await worker._refine_decision(payload)

    assert result == refined
    assert payload["unresolvedCount"] == 0
    update.assert_awaited_once_with(91, refined, 0)


@pytest.mark.asyncio
async def test_non_terminal_failure_is_scheduled_and_acked():
    worker = AgentWorker()
    message = MagicMock()
    message.ack = AsyncMock()
    payload = {"messageId": 10, "userId": "u1", "queueName": "q"}

    with (
        patch(
            "app.worker.agent_task_service.mark_failed",
            AsyncMock(return_value=(2, False)),
        ),
        patch("app.worker.random.uniform", return_value=0),
        patch("app.worker.agent_task_service.schedule_retry", AsyncMock()) as schedule,
    ):
        await worker._retry_or_dead(message, payload, RuntimeError("boom"), "owner")

    schedule.assert_awaited_once_with(10, 4)
    message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_guard_failure_is_treated_as_lease_loss():
    worker = AgentWorker()
    payload = {"messageId": 11, "userId": "u1", "queueName": "q"}
    message = MagicMock()
    message.body = json.dumps(payload).encode()
    message.redelivered = False
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    message.reject = AsyncMock()

    with (
        patch("app.worker.agent_task_service.claim", AsyncMock(return_value=True)),
        patch(
            "app.worker.redis_service.acquire_agent_user_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.worker.agent_message_service.is_execution_cancelled",
            AsyncMock(return_value=False),
        ),
        patch("app.worker.redis_service.release_agent_user_lock", AsyncMock()),
        patch("app.worker.observe_agent_stage") as observe_stage,
        patch.object(worker, "_execute_payload", AsyncMock(return_value="ok")),
        patch(
            "app.worker.agent_task_service.mark_completed",
            AsyncMock(return_value=False),
        ),
        patch("app.worker.agent_task_service.mark_failed", AsyncMock()) as failed,
    ):
        await worker._process_message_inner("q", message)

    message.ack.assert_awaited_once()
    message.nack.assert_not_awaited()
    failed.assert_not_awaited()
    observed_stages = [call.args[0] for call in observe_stage.call_args_list]
    assert observed_stages == ["total"]


@pytest.mark.asyncio
async def test_cancel_task_is_durable_and_scoped_to_authenticated_user():
    cursor = _Cursor(rowcount=1)
    with patch("app.services.task_service.acquire", _acquire_for(cursor)):
        cancelled = await AgentTaskService().cancel(12, "u1")

    assert cancelled is True
    sql, params = cursor.calls[0]
    assert "status='CANCELLED'" in sql
    assert "lease_owner=NULL" in sql
    assert "user_id=%s" in sql
    assert params == (12, "u1")


@pytest.mark.asyncio
async def test_terminal_write_cannot_overwrite_a_cancelled_task():
    cursor = _Cursor(rowcount=0)
    with patch("app.services.task_service.acquire", _acquire_for(cursor)):
        written = await AgentTaskService().mark_terminal(12, "payload missing")

    assert written is False
    sql, _ = cursor.calls[0]
    assert "status IN" in sql
    assert "'CANCELLED'" not in sql


@pytest.mark.asyncio
async def test_worker_close_always_flushes_telemetry():
    worker = AgentWorker()
    with (
        patch("app.worker.redis_service.clear_worker_heartbeat", AsyncMock()),
        patch("app.worker.agent_queue_service.close", AsyncMock()),
        patch("app.worker.mcp_streamable_client.close", AsyncMock()),
        patch("app.worker.close_http_clients", AsyncMock()),
        patch("app.worker.close_pool", AsyncMock()),
        patch("app.worker.redis_service.close", AsyncMock()),
        patch("app.worker.shutdown_telemetry") as shutdown,
    ):
        await worker.close()

    shutdown.assert_called_once_with()


@pytest.mark.asyncio
async def test_worker_heartbeat_loop_survives_transient_refresh_failure():
    worker = AgentWorker()
    refreshed = asyncio.Event()
    calls = 0
    real_sleep = asyncio.sleep

    async def refresh(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("redis unavailable")
        refreshed.set()

    async def no_wait(_seconds):
        await real_sleep(0)

    with (
        patch(
            "app.worker.redis_service.set_worker_heartbeat",
            AsyncMock(side_effect=refresh),
        ),
        patch("app.worker.asyncio.sleep", side_effect=no_wait),
    ):
        task = asyncio.create_task(worker._heartbeat_loop(6))
        await asyncio.wait_for(refreshed.wait(), timeout=1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert calls >= 2


@pytest.mark.asyncio
async def test_durable_message_cancellation_acks_without_running_payload():
    worker = AgentWorker()
    payload = {"messageId": 13, "userId": "u1", "queueName": "q"}
    message = MagicMock()
    message.body = json.dumps(payload).encode()
    message.redelivered = False
    message.ack = AsyncMock()
    message.nack = AsyncMock()
    message.reject = AsyncMock()

    with (
        patch("app.worker.agent_task_service.claim", AsyncMock(return_value=True)),
        patch(
            "app.worker.redis_service.acquire_agent_user_lock",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.worker.agent_message_service.is_execution_cancelled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.worker.agent_task_service.mark_terminal",
            AsyncMock(return_value=True),
        ),
        patch("app.worker.redis_service.release_agent_user_lock", AsyncMock()),
        patch.object(worker, "_execute_payload", AsyncMock()) as execute,
    ):
        await worker._process_message_inner("q", message)

    execute.assert_not_awaited()
    message.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_api_transitions_message_before_cancelling_owned_task():
    orchestrator = AgentOrchestrator()
    with (
        patch(
            "app.services.agent_service.rate_limit_service.allow",
            AsyncMock(return_value=True),
        ),
        patch("app.services.agent_service.redis_service.set_cancel_flag", AsyncMock()),
        patch(
            "app.services.agent_service.agent_message_service.cancel_message",
            AsyncMock(return_value=True),
        ),
        patch("app.services.agent_service.agent_task_service.cancel", AsyncMock()) as cancel_task,
    ):
        await orchestrator.cancel_message("u1", 14)

    cancel_task.assert_awaited_once_with(14, "u1")


@pytest.mark.asyncio
async def test_complete_message_never_overwrites_cancelled_or_interrupted_status():
    cursor = _SequencedCursor([0, 1])
    with patch("app.services.message_service.acquire", _acquire_for(cursor)):
        await AgentMessageService().complete_message(15, "late answer", "agent")

    assert len(cursor.calls) == 2
    fallback_sql, fallback_params = cursor.calls[1]
    assert "WHERE message_id=%s AND status=%s" in fallback_sql
    assert fallback_params is not None
    assert fallback_params[-1] == MSG_STATUS_COMPLETE
