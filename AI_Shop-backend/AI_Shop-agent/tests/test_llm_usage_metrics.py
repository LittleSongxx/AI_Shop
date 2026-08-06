"""A4：record_llm_usage 必须从 langchain 真实字段读 usage/模型名。

回归动机：曾按 response_metadata["usage"] / ["model"] 读取（0.3.35 里并不存在
这两个键），导致 agent_llm_tokens_total 恒为 0、agent_llm_call_total{model}
恒为 unknown——两个核心指标静默空转。这里用构造的 AIMessage 覆盖三条路径：
非流式（token_usage）、流式（usage_metadata）、都没有 usage 时只计调用数，
以及异常路径只增加一次 result=error。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from prometheus_client import REGISTRY

from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services.agent_runtime import record_llm_usage, stream_llm_turn


def _read(name: str, labels: dict) -> float:
    """计数器是进程级累积的，断言一律用「测试前后差值」避免跨用例污染。"""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _delta(reads_before: dict, reads_after: dict, name: str, labels: dict) -> float:
    return reads_after[name][tuple(sorted(labels.items()))] - reads_before[name][
        tuple(sorted(labels.items()))
    ]


def _snapshot(queries: list[tuple[str, dict]]) -> dict:
    return {
        name: {
            tuple(sorted(labels.items())): _read(query_name, labels)
            for query_name, labels in queries
            if query_name == name
        }
        for name, _ in queries
    }


def _token_labels(kind: str, model: str, fallback: bool) -> tuple[str, dict]:
    return (
        "agent_llm_tokens_total",
        {"kind": kind, "model": model, "fallback": str(fallback).lower()},
    )


def _unpriced_labels(kind: str, model: str, fallback: bool) -> tuple[str, dict]:
    return (
        "agent_llm_unpriced_tokens_total",
        {"kind": kind, "model": model, "fallback": str(fallback).lower()},
    )


def _cost_labels(kind: str, model: str, fallback: bool) -> tuple[str, dict]:
    return (
        "agent_llm_cost_cny_total",
        {"kind": kind, "model": model, "fallback": str(fallback).lower()},
    )


def _call_labels(model: str, fallback: bool, result: str = "success") -> tuple[str, dict]:
    return (
        "agent_llm_call_total",
        {
            "model": model,
            "fallback": "true" if fallback else "false",
            "result": result,
        },
    )


def test_non_streaming_token_usage():
    """非流式路径：response_metadata["token_usage"]（OpenAI 命名）。"""
    token_input = _token_labels("input", "claude-opus-5", False)
    token_output = _token_labels("output", "claude-opus-5", False)
    unpriced_total = _unpriced_labels("total", "claude-opus-5", False)
    queries = [token_input, token_output, unpriced_total, _call_labels("claude-opus-5", False)]
    before = _snapshot(queries)
    msg = AIMessage(
        content="hi",
        response_metadata={
            "token_usage": {"prompt_tokens": 120, "completion_tokens": 34},
            "model_name": "claude-opus-5",
        },
    )
    record_llm_usage(msg)
    after = _snapshot(queries)

    assert _delta(before, after, *token_input) == 120
    assert _delta(before, after, *token_output) == 34
    assert _delta(before, after, *unpriced_total) == 154
    assert _delta(before, after, *_call_labels("claude-opus-5", False)) == 1


def test_streaming_usage_metadata():
    """流式路径：AIMessage.usage_metadata（input_tokens/output_tokens）。"""
    token_input = _token_labels("input", "unknown", True)
    token_output = _token_labels("output", "unknown", True)
    queries = [token_input, token_output, _call_labels("unknown", True)]
    before = _snapshot(queries)
    msg = AIMessage(
        content="hi",
        # langchain_core 0.3.x 校验要求 total_tokens 必填（真实流式响应恒有）
        usage_metadata={"input_tokens": 200, "output_tokens": 15, "total_tokens": 215},
        response_metadata={"finish_reason": "stop"},
    )
    record_llm_usage(msg, fallback=True)
    after = _snapshot(queries)

    assert _delta(before, after, *token_input) == 200
    assert _delta(before, after, *token_output) == 15
    assert _delta(before, after, *_call_labels("unknown", True)) == 1


def test_no_usage_still_counts_call():
    """没有 usage 字段时只记调用次数，不崩溃、不反推 token。"""
    token_input = _token_labels("input", "unknown", False)
    token_output = _token_labels("output", "unknown", False)
    queries = [token_input, token_output, _call_labels("unknown", False)]
    before = _snapshot(queries)
    msg = AIMessage(content="hi", response_metadata={"finish_reason": "stop"})
    record_llm_usage(msg)
    after = _snapshot(queries)

    assert _delta(before, after, *token_input) == 0
    assert _delta(before, after, *token_output) == 0
    assert _delta(before, after, *_call_labels("unknown", False)) == 1


def test_none_response_ignored():
    token_input = _token_labels("input", "unknown", False)
    before = _snapshot([token_input, _call_labels("unknown", False)])
    record_llm_usage(None)
    after = _snapshot([token_input, _call_labels("unknown", False)])

    assert _delta(before, after, *token_input) == 0
    assert _delta(before, after, *_call_labels("unknown", False)) == 0


@pytest.mark.parametrize("response_metadata", [
    {"usage": {"prompt_tokens": 99, "completion_tokens": 1}},  # 旧注释里的错误键名
    {"token_usage": {"total_tokens": 100}},                     # 缺 prompt/completion
])
def test_wrong_or_incomplete_keys_never_count_tokens(response_metadata):
    """旧注释声称的 "usage" 键在 0.3.35 不存在：即使出现也绝不误记。"""
    token_input = _token_labels("input", "unknown", False)
    token_output = _token_labels("output", "unknown", False)
    queries = [token_input, token_output, _call_labels("unknown", False)]
    before = _snapshot(queries)
    msg = AIMessage(content="hi", response_metadata=response_metadata)
    record_llm_usage(msg)
    after = _snapshot(queries)

    assert _delta(before, after, *token_input) == 0
    assert _delta(before, after, *token_output) == 0
    assert _delta(before, after, *_call_labels("unknown", False)) == 1


def test_configured_price_records_input_output_and_total_cost(monkeypatch):
    monkeypatch.setattr(
        "app.observability.llm_metrics.get_settings",
        lambda: SimpleNamespace(
            llm_pricing_cny_per_million_json={
                "priced-model": {"input": 2.0, "output": 8.0}
            }
        ),
    )
    cost_input = _cost_labels("input", "priced-model", False)
    cost_output = _cost_labels("output", "priced-model", False)
    cost_total = _cost_labels("total", "priced-model", False)
    unpriced = _unpriced_labels("total", "priced-model", False)
    queries = [cost_input, cost_output, cost_total, unpriced]
    before = _snapshot(queries)
    msg = AIMessage(
        content="ok",
        response_metadata={
            "model_name": "priced-model",
            "token_usage": {"prompt_tokens": 1_000, "completion_tokens": 500},
        },
    )

    record_llm_usage(msg)
    after = _snapshot(queries)

    assert _delta(before, after, *cost_input) == pytest.approx(0.002)
    assert _delta(before, after, *cost_output) == pytest.approx(0.004)
    assert _delta(before, after, *cost_total) == pytest.approx(0.006)
    assert _delta(before, after, *unpriced) == 0


def test_structured_output_wrapper_preserves_raw_usage():
    token_input = _token_labels("input", "schema-model", False)
    before = _snapshot([token_input])
    raw = AIMessage(
        content="",
        response_metadata={
            "model_name": "schema-model",
            "token_usage": {"prompt_tokens": 42, "completion_tokens": 3},
        },
    )

    record_llm_usage({"raw": raw, "parsed": {"intent": "CHAT"}})
    after = _snapshot([token_input])

    assert _delta(before, after, *token_input) == 42


@pytest.mark.asyncio
async def test_failed_invocation_counts_one_error_and_no_success():
    error_call = _call_labels("memory-model", False, "error")
    success_call = _call_labels("memory-model", False, "success")
    before = _snapshot([error_call, success_call])
    llm = SimpleNamespace(
        model_name="memory-model",
        ainvoke=AsyncMock(side_effect=RuntimeError("provider down")),
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await invoke_llm_with_metrics(llm, [])

    after = _snapshot([error_call, success_call])
    assert _delta(before, after, *error_call) == 1
    assert _delta(before, after, *success_call) == 0


@pytest.mark.asyncio
async def test_cancelled_stream_counts_one_error_and_no_success(monkeypatch):
    model = "stream-model"
    error_call = _call_labels(model, False, "error")
    success_call = _call_labels(model, False, "success")
    before = _snapshot([error_call, success_call])
    started = asyncio.Event()

    class _StreamingLlm:
        model_name = model

        async def astream(self, _messages):
            started.set()
            await asyncio.Event().wait()
            yield AIMessage(content="unreachable")

    monkeypatch.setattr(
        "app.services.agent_runtime.is_cancelled", AsyncMock(return_value=False)
    )
    task = asyncio.create_task(
        stream_llm_turn(_StreamingLlm(), [], "u1", 1, "hello", [])
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    after = _snapshot([error_call, success_call])
    assert _delta(before, after, *error_call) == 1
    assert _delta(before, after, *success_call) == 0
