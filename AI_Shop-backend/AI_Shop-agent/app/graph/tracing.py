from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.graph.budget_guard import active_budget_guard
from app.graph.state import AgentGraphState
from app.observability.llm_metrics import snapshot_cost_summary
from app.observability.telemetry import get_tracer
from app.services.episode_service import episode_service

tracer = get_tracer()


def traced_node(
    name: str,
    node: Callable[[AgentGraphState], Awaitable[dict[str, Any]]],
) -> Callable[[AgentGraphState], Awaitable[dict[str, Any]]]:
    async def invoke(state: AgentGraphState) -> dict[str, Any]:
        started = time.perf_counter()
        guard = active_budget_guard()
        before_cost = snapshot_cost_summary()
        with tracer.start_as_current_span(f"agent.node.{name}") as span:
            message_id = state.get("message_id")
            if message_id is not None:
                span.set_attribute("agent.message_id", int(message_id))
            span.set_attribute("agent.node", name)
            if guard is not None:
                guard.check_before_step(name)
            try:
                result = await node(state)
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1_000)
                span.record_exception(exc)
                episode_service.record_step(
                    "NODE_TRANSITION",
                    node_name=name,
                    round_no=state.get("react_round"),
                    status="ERROR",
                    input_data=_state_summary(state),
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    latency_ms=elapsed_ms,
                )
                raise
            finally:
                if guard is not None:
                    after_cost = snapshot_cost_summary()
                    guard.record_step(
                        name,
                        tokens=_token_delta(before_cost, after_cost),
                        cost_cny=max(
                            0.0,
                            float(after_cost.get("costCny") or 0.0)
                            - float(before_cost.get("costCny") or 0.0),
                        ),
                    )
            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            output = {
                "route": result.get("route"),
                "outcome": result.get("outcome"),
                "intent": result.get("intent"),
                "cancelled": result.get("cancelled"),
                "finished": result.get("finished"),
                "toolCalls": [
                    str(item.get("name") or "")
                    for item in (result.get("pending_tool_calls") or [])
                    if isinstance(item, dict)
                ],
            }
            episode_service.record_step(
                "NODE_TRANSITION",
                node_name=name,
                round_no=state.get("react_round"),
                input_data=_state_summary(state),
                output_data=output,
                latency_ms=elapsed_ms,
            )
            span.set_attribute("agent.route", str(result.get("route") or ""))
            return result

    invoke.__name__ = f"traced_{name}"
    return invoke


def _token_delta(before: dict[str, Any], after: dict[str, Any]) -> int:
    before_tokens = int(before.get("inputTokens") or 0) + int(
        before.get("outputTokens") or 0
    )
    after_tokens = int(after.get("inputTokens") or 0) + int(
        after.get("outputTokens") or 0
    )
    return max(0, after_tokens - before_tokens)


def _state_summary(state: AgentGraphState) -> dict[str, Any]:
    return {
        "messageId": state.get("message_id"),
        "route": state.get("route"),
        "intent": state.get("intent"),
        "requestMode": state.get("request_mode"),
        "round": state.get("react_round"),
        "toolsCalled": list(state.get("tools_called") or []),
        "hasRagEvidence": bool(state.get("rag_source_refs")),
        "hasPendingOrderReference": bool(state.get("pending_order_reference")),
    }
