from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import (
    LLM_CALL_TOTAL,
    LLM_COST_CNY,
    LLM_LATENCY,
    LLM_TOKEN_TOTAL,
    LLM_UNPRICED_TOKEN_TOTAL,
    observe_agent_stage,
)
from app.observability.telemetry import get_tracer
from app.services.episode_service import episode_service

tracer = get_tracer()

# per-request 成本累计（E 工作线）：contextvar 随 worker task 隔离。
# reset_run_cost() 在每条消息处理开头调用（意图识别之前——它也是对话路径
# 的固定成本）；record_llm_usage 每次成功调用累计；收尾 snapshot 成
# costSummary 写进 GRAPH_END。压缩/condense 等异步调度任务从父 task 继承
# contextvar 引用，任务开头再次 reset 即与对话路径隔离，不会污染摘要。
_RUN_COST: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "run_cost", default=None
)


def reset_run_cost() -> None:
    """清空本 task 的累计（每条消息开头、异步调度任务开头各调一次）。"""
    # LangGraph can execute nodes in child asyncio tasks. Context variables are
    # copied into those tasks, but a value first created inside the child is not
    # propagated back to the parent. Seed one mutable accumulator here so child
    # calls and the parent GRAPH_END snapshot observe the same request-local data.
    _RUN_COST.set(
        {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cny": 0.0,
            "priced_calls": 0,
            "unpriced_calls": 0,
            "missing_usage_calls": 0,
            "usage_sources": set(),
            "missing_reasons": {},
            "models": set(),
        }
    )


def _run_cost_state() -> dict:
    state = _RUN_COST.get()
    if state is None:
        state = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_cny": 0.0,
            "priced_calls": 0,
            "unpriced_calls": 0,
            "missing_usage_calls": 0,
            "usage_sources": set(),
            "missing_reasons": {},
            "models": set(),
        }
        _RUN_COST.set(state)
    return state


def resolve_llm_model(llm: Any = None, configured_model: str | None = None) -> str:
    return str(
        getattr(llm, "model_name", None)
        or getattr(llm, "model", None)
        or configured_model
        or "unknown"
    )


def record_llm_usage(
    response: Any,
    *,
    fallback: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Record a successful provider response and any real token usage it carries."""
    if response is None:
        return {}
    # LangChain structured output with include_raw=True returns
    # {raw, parsed, parsing_error}. Usage lives on raw, not on the wrapper dict.
    metric_response = response.get("raw") if isinstance(response, dict) else response
    metric_response = metric_response or response
    metadata = getattr(metric_response, "response_metadata", None) or {}
    usage_meta = getattr(metric_response, "usage_metadata", None)
    usage_source = "none"
    missing_reason: str | None = None
    if isinstance(usage_meta, dict) and (
        usage_meta.get("input_tokens") is not None
        or usage_meta.get("output_tokens") is not None
    ):
        prompt = usage_meta.get("input_tokens")
        completion = usage_meta.get("output_tokens")
        usage_source = (
            "langchain.usage_metadata"
            if prompt is not None and completion is not None
            else "langchain.usage_metadata_partial"
        )
    else:
        token_usage = metadata.get("token_usage")
        if isinstance(token_usage, dict):
            prompt = token_usage.get("prompt_tokens")
            completion = token_usage.get("completion_tokens")
            usage_source = (
                "response_metadata.token_usage"
                if prompt is not None and completion is not None
                else "response_metadata.token_usage_partial"
            )
        else:
            prompt = None
            completion = None
            missing_reason = "provider_omitted_usage"
    response_model = str(
        metadata.get("model_name")
        or getattr(metric_response, "model", None)
        or model
        or "unknown"
    )
    fallback_label = "true" if fallback else "false"
    pricing = get_settings().llm_pricing_cny_per_million_json.get(response_model)
    token_counts = {
        "input": prompt if isinstance(prompt, int) and prompt >= 0 else None,
        "output": completion if isinstance(completion, int) and completion >= 0 else None,
    }
    usage_reported = token_counts["input"] is not None and token_counts["output"] is not None
    if not usage_reported and missing_reason is None:
        missing_reason = "provider_partial_usage"
    if usage_reported:
        usage_status = "PRICED" if pricing is not None else "UNPRICED"
    else:
        usage_status = "MISSING_USAGE"
    total_cost = 0.0
    for kind, count in token_counts.items():
        if count is None:
            continue
        LLM_TOKEN_TOTAL.labels(
            kind=kind, model=response_model, fallback=fallback_label
        ).inc(count)
        if pricing is None:
            LLM_UNPRICED_TOKEN_TOTAL.labels(
                kind=kind, model=response_model, fallback=fallback_label
            ).inc(count)
            LLM_UNPRICED_TOKEN_TOTAL.labels(
                kind="total", model=response_model, fallback=fallback_label
            ).inc(count)
            continue
        cost = count * float(pricing[kind]) / 1_000_000
        total_cost += cost
        LLM_COST_CNY.labels(
            kind=kind, model=response_model, fallback=fallback_label
        ).inc(cost)
        LLM_COST_CNY.labels(
            kind="total", model=response_model, fallback=fallback_label
        ).inc(cost)
    LLM_CALL_TOTAL.labels(
        model=response_model,
        fallback=fallback_label,
        result="success",
    ).inc()
    episode_service.add_llm_usage(
        input_tokens=token_counts["input"] or 0,
        output_tokens=token_counts["output"] or 0,
        cost_cny=total_cost,
        model_name=response_model,
    )
    state = _run_cost_state()
    state["calls"] += 1
    if token_counts["input"] is not None:
        state["input_tokens"] += token_counts["input"]
    if token_counts["output"] is not None:
        state["output_tokens"] += token_counts["output"]
    state["cost_cny"] += total_cost
    if usage_status == "PRICED":
        state["priced_calls"] += 1
    elif usage_status == "UNPRICED":
        state["unpriced_calls"] += 1
    else:
        state["missing_usage_calls"] += 1
        reasons = state.setdefault("missing_reasons", {})
        reasons[missing_reason or "provider_usage_not_reported"] = (
            int(reasons.get(missing_reason or "provider_usage_not_reported", 0)) + 1
        )
    state.setdefault("usage_sources", set()).add(usage_source)
    state["models"].add(response_model)
    return {
        "model": response_model,
        "inputTokens": token_counts["input"],
        "outputTokens": token_counts["output"],
        "costCny": total_cost if usage_status == "PRICED" else None,
        "providerCalls": 1,
        "pricedCalls": 1 if usage_status == "PRICED" else 0,
        "unpricedCalls": 1 if usage_status == "UNPRICED" else 0,
        "missingUsageCalls": 1 if usage_status == "MISSING_USAGE" else 0,
        "costStatus": usage_status,
        "usageReported": usage_reported,
        "usageSource": usage_source,
        "missingReason": missing_reason,
    }


def snapshot_cost_summary(*, tools_called: list[str] | None = None) -> dict:
    """本次请求的 LLM 成本摘要（轻/重路径）。

    path 判定用"是否进入工具循环"而不是 LLM 调用次数：强制工具兜底路径
    （forced_tools）调工具后直接收尾、不再生成，LLM 次数与工具循环脱钩，
    按次数判会失真。轻路径（light）= 未调用工具的单轮对话；重路径
    （heavy）= 调用过工具；未产生任何成功调用时为 "none"。意图识别等
    固定成本计入 llmCalls。
    """
    state = _RUN_COST.get() or {}
    calls = int(state.get("calls") or 0)
    path = "heavy" if tools_called else ("light" if calls else "none")
    missing_usage_calls = int(state.get("missing_usage_calls") or 0)
    unpriced_calls = int(state.get("unpriced_calls") or 0)
    priced_calls = int(state.get("priced_calls") or 0)
    if missing_usage_calls:
        cost_status = "MISSING_USAGE"
        cost = None
    elif unpriced_calls:
        cost_status = "UNPRICED"
        cost = None
    elif priced_calls:
        cost_status = "PRICED"
        cost = round(float(state.get("cost_cny") or 0.0), 6)
    else:
        cost_status = "NOT_APPLICABLE"
        cost = None
    return {
        "path": path,
        "llmCalls": calls,
        "inputTokens": int(state.get("input_tokens") or 0),
        "outputTokens": int(state.get("output_tokens") or 0),
        "providerCalls": calls,
        "pricedCalls": priced_calls,
        "unpricedCalls": unpriced_calls,
        "missingUsageCalls": missing_usage_calls,
        "costCny": cost,
        "costStatus": cost_status,
        "usageSources": sorted(state.get("usage_sources") or [])
        if calls
        else ["not_applicable"],
        "missingReasons": dict(sorted((state.get("missing_reasons") or {}).items())),
        "notApplicableReason": "no_llm_call" if not calls else None,
        "models": sorted(state.get("models") or []),
    }


def record_llm_failure(model: str, *, fallback: bool = False) -> None:
    """Record one provider call that raised or was deliberately cancelled."""
    LLM_CALL_TOTAL.labels(
        model=str(model or "unknown"),
        fallback="true" if fallback else "false",
        result="error",
    ).inc()


async def invoke_llm_with_metrics(
    llm: Any,
    messages: list,
    *,
    fallback: bool = False,
    model: str | None = None,
    timeout_seconds: float | None = None,
):
    """Invoke a non-streaming LLM once within an application wall-clock bound."""
    resolved_model = resolve_llm_model(llm, model)
    effective_timeout = (
        get_settings().agent_llm_call_deadline_seconds
        if timeout_seconds is None
        else float(timeout_seconds)
    )
    if effective_timeout <= 0:
        raise ValueError("LLM call timeout_seconds must be positive")
    started = time.perf_counter()
    usage: dict[str, Any] = {}
    error: Exception | None = None
    with tracer.start_as_current_span("agent.llm.invoke") as span:
        span.set_attribute("gen_ai.request.model", resolved_model)
        span.set_attribute("agent.llm.fallback", bool(fallback))
        try:
            async with asyncio.timeout(effective_timeout):
                response = await llm.ainvoke(messages)
        except asyncio.CancelledError:
            record_llm_failure(resolved_model, fallback=fallback)
            episode_service.record_step(
                "LLM_CALL",
                node_name="llm",
                status="CANCELLED",
                input_data={
                    "messages": messages,
                    "fallback": fallback,
                    "hardDeadlineSeconds": effective_timeout,
                },
                output_data={
                    "usageStatus": "MISSING_USAGE",
                    "usageSource": "none",
                    "missingReason": "cancelled_before_usage",
                },
                model_name=resolved_model,
                error_code="CANCELLED",
                latency_ms=round((time.perf_counter() - started) * 1_000),
            )
            raise
        except Exception as exc:
            error = exc
            record_llm_failure(resolved_model, fallback=fallback)
            span.record_exception(exc)
            raise
        else:
            usage = record_llm_usage(
                response, fallback=fallback, model=resolved_model
            )
            return response
        finally:
            elapsed = max(0.0, time.perf_counter() - started)
            LLM_LATENCY.observe(elapsed)
            observe_agent_stage("generation", elapsed)
            if error is not None:
                episode_service.record_step(
                    "LLM_CALL",
                    node_name="llm",
                    status="ERROR",
                    input_data={
                        "messages": messages,
                        "fallback": fallback,
                        "hardDeadlineSeconds": effective_timeout,
                    },
                    output_data={
                        "usageStatus": "MISSING_USAGE",
                        "usageSource": "none",
                        "missingReason": (
                            "call_deadline_exceeded_before_usage"
                            if isinstance(error, TimeoutError)
                            else "provider_error_before_usage"
                        ),
                    },
                    model_name=resolved_model,
                    error_code=type(error).__name__,
                    error_message=str(error),
                    latency_ms=round(elapsed * 1_000),
                )
            elif usage:
                episode_service.record_step(
                    "LLM_CALL",
                    node_name="llm",
                    input_data={
                        "messages": messages,
                        "fallback": fallback,
                        "hardDeadlineSeconds": effective_timeout,
                    },
                    output_data=usage,
                    model_name=resolved_model,
                    latency_ms=round(elapsed * 1_000),
                )
