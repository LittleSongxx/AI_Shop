from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.graph.runner import _should_cleanup_checkpoint, run_agent_graph


@pytest.mark.parametrize(
    ("outcome", "expected"),
    (
        ("ok", True),
        ("handoff", True),
        ("human_support", True),
        ("cancelled", True),
        ("llm_error", False),
        ("graph_exception", False),
        (None, False),
    ),
)
def test_checkpoint_cleanup_is_limited_to_recoverable_terminal_outcomes(
    outcome: str | None, expected: bool
):
    assert _should_cleanup_checkpoint(outcome) is expected


@pytest.mark.asyncio
async def test_graph_exception_retains_checkpoint_for_ttl_recovery(monkeypatch):
    graph = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("boom")))
    checkpointer = SimpleNamespace(
        adelete_thread=AsyncMock(),
        ttl_seconds=3600,
    )

    monkeypatch.setattr("app.graph.runner.get_compiled_graph", lambda: graph)
    monkeypatch.setattr("app.graph.runner.get_checkpointer", lambda _client: checkpointer)
    monkeypatch.setattr("app.graph.runner._should_resume", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.graph.runner.redis_service", SimpleNamespace(client=object())
    )
    monkeypatch.setattr(
        "app.graph.runner.get_settings",
        lambda: SimpleNamespace(agent_budget_enabled=False),
    )
    monkeypatch.setattr("app.graph.runner.rt.parse_agent_message", lambda _msg: (None, "hello"))
    monkeypatch.setattr(
        "app.graph.runner.snapshot_cost_summary",
        lambda **_kwargs: {"inputTokens": 0, "outputTokens": 0},
    )
    monkeypatch.setattr("app.graph.runner.episode_service.record_step", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.update_run", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.finish_run", MagicMock())

    with pytest.raises(RuntimeError, match="boom"):
        await run_agent_graph(
            {
                "userId": "u1",
                "messageId": 9,
                "userMessage": "hello",
            }
        )

    # The fresh-run path clears any stale checkpoint before invocation; the
    # important invariant is that the failure does not trigger a second delete
    # in ``finally`` and therefore leaves newly written state under its TTL.
    checkpointer.adelete_thread.assert_awaited_once_with("u1:9")


@pytest.mark.asyncio
async def test_successful_graph_still_cleans_checkpoint(monkeypatch):
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "outcome": "ok",
                "intent": "CHAT",
                "tools_called": [],
            }
        )
    )
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock(), ttl_seconds=3600)

    monkeypatch.setattr("app.graph.runner.get_compiled_graph", lambda: graph)
    monkeypatch.setattr("app.graph.runner.get_checkpointer", lambda _client: checkpointer)
    monkeypatch.setattr("app.graph.runner._should_resume", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.graph.runner.redis_service", SimpleNamespace(client=object())
    )
    monkeypatch.setattr(
        "app.graph.runner.get_settings",
        lambda: SimpleNamespace(agent_budget_enabled=False),
    )
    monkeypatch.setattr("app.graph.runner.rt.parse_agent_message", lambda _msg: (None, "hello"))
    monkeypatch.setattr(
        "app.graph.runner.snapshot_cost_summary",
        lambda **_kwargs: {"inputTokens": 0, "outputTokens": 0},
    )
    monkeypatch.setattr("app.graph.runner.episode_service.record_step", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.update_run", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.finish_run", MagicMock())

    assert (
        await run_agent_graph(
            {
                "userId": "u1",
                "messageId": 10,
                "userMessage": "hello",
            }
        )
        == "ok"
    )
    # One delete before a fresh invocation and one after a successful terminal
    # outcome; this preserves the existing success cleanup semantics.
    assert checkpointer.adelete_thread.await_count == 2
    assert all(
        call.args == ("u1:10",)
        for call in checkpointer.adelete_thread.await_args_list
    )


@pytest.mark.asyncio
async def test_graph_without_explicit_outcome_keeps_checkpoint_but_preserves_legacy_ok(
    monkeypatch,
):
    graph = SimpleNamespace(ainvoke=AsyncMock(return_value={"tools_called": []}))
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock(), ttl_seconds=3600)
    monkeypatch.setattr("app.graph.runner.get_compiled_graph", lambda: graph)
    monkeypatch.setattr("app.graph.runner.get_checkpointer", lambda _client: checkpointer)
    monkeypatch.setattr("app.graph.runner._should_resume", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.graph.runner.redis_service", SimpleNamespace(client=object())
    )
    monkeypatch.setattr(
        "app.graph.runner.get_settings",
        lambda: SimpleNamespace(agent_budget_enabled=False),
    )
    monkeypatch.setattr(
        "app.graph.runner.snapshot_cost_summary",
        lambda **_kwargs: {"inputTokens": 0, "outputTokens": 0},
    )
    monkeypatch.setattr("app.graph.runner.episode_service.record_step", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.update_run", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.finish_run", MagicMock())

    assert (
        await run_agent_graph(
            {
                "userId": "u1",
                "messageId": 11,
                "userMessage": "hello",
            }
        )
        == "ok"
    )
    checkpointer.adelete_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_cancellation_cleans_checkpoint(monkeypatch):
    graph = SimpleNamespace(ainvoke=AsyncMock(side_effect=asyncio.CancelledError()))
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock(), ttl_seconds=3600)
    monkeypatch.setattr("app.graph.runner.get_compiled_graph", lambda: graph)
    monkeypatch.setattr("app.graph.runner.get_checkpointer", lambda _client: checkpointer)
    monkeypatch.setattr("app.graph.runner._should_resume", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.graph.runner.redis_service", SimpleNamespace(client=object())
    )
    monkeypatch.setattr(
        "app.graph.runner.get_settings",
        lambda: SimpleNamespace(agent_budget_enabled=False),
    )
    monkeypatch.setattr("app.graph.runner.rt.parse_agent_message", lambda _msg: (None, "hello"))
    monkeypatch.setattr(
        "app.graph.runner.episode_service.record_step", MagicMock()
    )
    monkeypatch.setattr("app.graph.runner.episode_service.update_run", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.finish_run", MagicMock())
    monkeypatch.setattr(
        "app.graph.runner.snapshot_cost_summary",
        lambda **_kwargs: {"inputTokens": 0, "outputTokens": 0},
    )

    with pytest.raises(asyncio.CancelledError):
        await run_agent_graph({"userId": "u1", "messageId": 12, "userMessage": "hello"})

    assert checkpointer.adelete_thread.await_count == 2
