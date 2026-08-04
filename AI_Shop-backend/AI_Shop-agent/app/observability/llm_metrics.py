from __future__ import annotations

import asyncio
import time
from typing import Any

from app.harness.metrics.runtime_sensors import (
    LLM_CALL_TOTAL,
    LLM_LATENCY,
    LLM_TOKEN_TOTAL,
)


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
) -> None:
    """Record a successful provider response and any real token usage it carries."""
    if response is None:
        return
    metadata = getattr(response, "response_metadata", None) or {}
    usage_meta = getattr(response, "usage_metadata", None)
    if isinstance(usage_meta, dict) and (
        usage_meta.get("input_tokens") is not None
        or usage_meta.get("output_tokens") is not None
    ):
        prompt = usage_meta.get("input_tokens")
        completion = usage_meta.get("output_tokens")
    else:
        token_usage = metadata.get("token_usage")
        if isinstance(token_usage, dict):
            prompt = token_usage.get("prompt_tokens")
            completion = token_usage.get("completion_tokens")
        else:
            prompt = None
            completion = None
    if isinstance(prompt, int) and prompt >= 0:
        LLM_TOKEN_TOTAL.labels(kind="prompt").inc(prompt)
    if isinstance(completion, int) and completion >= 0:
        LLM_TOKEN_TOTAL.labels(kind="completion").inc(completion)
    response_model = str(
        metadata.get("model_name")
        or getattr(response, "model", None)
        or model
        or "unknown"
    )
    LLM_CALL_TOTAL.labels(
        model=response_model,
        fallback="true" if fallback else "false",
        result="success",
    ).inc()


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
):
    """Invoke a non-streaming LLM and account for the call exactly once."""
    resolved_model = resolve_llm_model(llm, model)
    started = time.perf_counter()
    try:
        response = await llm.ainvoke(messages)
    except asyncio.CancelledError:
        record_llm_failure(resolved_model, fallback=fallback)
        raise
    except Exception:
        record_llm_failure(resolved_model, fallback=fallback)
        raise
    else:
        record_llm_usage(response, fallback=fallback, model=resolved_model)
        return response
    finally:
        LLM_LATENCY.observe(max(0.0, time.perf_counter() - started))
