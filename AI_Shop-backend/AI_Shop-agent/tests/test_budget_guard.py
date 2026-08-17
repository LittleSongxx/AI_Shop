from __future__ import annotations

import asyncio

import pytest

from app.graph.budget_guard import (
    BudgetConfig,
    BudgetExceededError,
    BudgetGuard,
    active_budget_guard,
    bind_budget_guard,
    reset_budget_guard,
)
from app.graph.tracing import traced_node


def test_budget_config_rejects_disabled_dimensions_without_master_switch():
    with pytest.raises(ValueError, match="max_tokens"):
        BudgetConfig(max_tokens=0)
    with pytest.raises(ValueError, match="warn_threshold"):
        BudgetConfig(warn_threshold=1.0)


def test_budget_guard_counts_seeded_usage_and_real_steps():
    guard = BudgetGuard(BudgetConfig(max_tokens=100, max_cost_cny=1, max_steps=2))
    guard.seed_llm_usage(tokens=20, cost_cny=0.1)
    guard.check_before_step("entry")
    guard.record_step("entry", tokens=30, cost_cny=0.2)
    guard.check_before_step("build_context")
    guard.record_step("build_context", tokens=50, cost_cny=0.3)

    with pytest.raises(BudgetExceededError) as captured:
        guard.check_before_step("agent_loop")

    assert captured.value.dimension == "steps"
    assert guard.summary()["usage"] == {
        "tokensUsed": 100,
        "costUsedCny": 0.6,
        "stepsUsed": 2,
        "elapsedSeconds": pytest.approx(guard.usage.elapsed_seconds, abs=0.001),
    }


def test_budget_guard_uses_monotonic_deadline():
    now = [10.0]
    guard = BudgetGuard(
        BudgetConfig(deadline_seconds=5),
        clock=lambda: now[0],
    )
    now[0] = 15.0

    with pytest.raises(BudgetExceededError) as captured:
        guard.check_before_step("tools")

    assert captured.value.dimension == "deadline"
    assert captured.value.as_dict()["nextStep"] == "tools"


@pytest.mark.asyncio
async def test_budget_context_is_task_local():
    parent_guard = BudgetGuard(BudgetConfig(max_steps=4))
    token = bind_budget_guard(parent_guard)
    try:

        async def child() -> BudgetGuard | None:
            await asyncio.sleep(0)
            return active_budget_guard()

        assert await child() is parent_guard
    finally:
        reset_budget_guard(token)
    assert active_budget_guard() is None


@pytest.mark.asyncio
async def test_traced_node_records_observed_token_and_cost_delta(monkeypatch):
    snapshots = iter(
        [
            {"inputTokens": 10, "outputTokens": 5, "costCny": 0.1},
            {"inputTokens": 30, "outputTokens": 15, "costCny": 0.4},
        ]
    )
    monkeypatch.setattr("app.graph.tracing.snapshot_cost_summary", lambda: next(snapshots))
    guard = BudgetGuard(BudgetConfig(max_steps=4, max_tokens=100))
    context_token = bind_budget_guard(guard)

    async def node(_state):
        return {"route": "finalize"}

    try:
        result = await traced_node("example", node)({"message_id": 7})
    finally:
        reset_budget_guard(context_token)

    assert result == {"route": "finalize"}
    assert guard.usage.steps_used == 1
    assert guard.usage.tokens_used == 30
    assert guard.usage.cost_used_cny == pytest.approx(0.3)
