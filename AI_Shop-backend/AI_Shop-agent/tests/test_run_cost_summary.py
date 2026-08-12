"""E 工作线：per-request LLM 成本摘要（轻/重路径）。

摘要的数字与 record_llm_usage 的指标口径同源（同一份 token/定价），
path 判定用"是否进入工具循环"而非调用次数——forced 兜底路径调工具后
直接收尾、不再生成，按次数判会失真。异步调度任务（压缩/condense）开头
reset 后与对话路径隔离，不计入触发它的那次请求。
"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.observability.llm_metrics import (
    record_llm_usage,
    reset_run_cost,
    snapshot_cost_summary,
)

PRICED = {"priced-model": {"input": 2.0, "output": 8.0}}


def _priced_response(*, input_tokens: int, output_tokens: int, model: str = "priced-model"):
    return AIMessage(
        content="hi",
        response_metadata={
            "model_name": model,
            "token_usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
            },
        },
    )


@pytest.fixture(autouse=True)
def _pricing(monkeypatch):
    monkeypatch.setattr(
        "app.observability.llm_metrics.get_settings",
        lambda: SimpleNamespace(llm_pricing_cny_per_million_json=PRICED),
    )
    reset_run_cost()
    yield
    reset_run_cost()


def test_snapshot_after_two_calls_aggregates_cost_and_tokens():
    record_llm_usage(_priced_response(input_tokens=1_000, output_tokens=500))
    record_llm_usage(_priced_response(input_tokens=500, output_tokens=250))

    summary = snapshot_cost_summary()
    assert summary["path"] == "light"  # 无工具 → 轻路径
    assert summary["llmCalls"] == 2
    assert summary["inputTokens"] == 1_500
    assert summary["outputTokens"] == 750
    # (1000*2 + 500*8 + 500*2 + 250*8) / 1e6 = (2000+4000+1000+2000)/1e6
    assert summary["costCny"] == pytest.approx(0.009)
    assert summary["models"] == ["priced-model"]


def test_heavy_path_when_tools_called_even_with_single_call():
    record_llm_usage(_priced_response(input_tokens=100, output_tokens=50))
    summary = snapshot_cost_summary(tools_called=["QUERY_ORDERS"])
    assert summary["path"] == "heavy"
    assert summary["llmCalls"] == 1


def test_none_path_without_any_call():
    assert snapshot_cost_summary() == {
        "path": "none",
        "llmCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "costCny": 0.0,
        "models": [],
    }


def test_models_deduplicated_and_sorted():
    record_llm_usage(_priced_response(input_tokens=1, output_tokens=1, model="b-model"))
    record_llm_usage(_priced_response(input_tokens=1, output_tokens=1, model="a-model"))
    record_llm_usage(_priced_response(input_tokens=1, output_tokens=1, model="b-model"))
    summary = snapshot_cost_summary(tools_called=[])
    assert summary["llmCalls"] == 3
    assert summary["models"] == ["a-model", "b-model"]


def test_reset_clears_accumulator_between_requests():
    record_llm_usage(_priced_response(input_tokens=100, output_tokens=50))
    assert snapshot_cost_summary()["llmCalls"] == 1
    reset_run_cost()
    assert snapshot_cost_summary()["path"] == "none"


def test_unpriced_tokens_count_calls_but_zero_cost():
    record_llm_usage(
        AIMessage(
            content="hi",
            response_metadata={
                "model_name": "unpriced-model",
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    summary = snapshot_cost_summary()
    assert summary["llmCalls"] == 1
    assert summary["inputTokens"] == 10
    assert summary["costCny"] == 0.0
    assert summary["models"] == ["unpriced-model"]


def test_worker_reset_and_snapshot_flow():
    """模拟 worker 请求流程：reset → 意图识别 → 图内调用 → 快照。"""
    reset_run_cost()
    record_llm_usage(_priced_response(input_tokens=300, output_tokens=120))  # 意图识别
    record_llm_usage(_priced_response(input_tokens=900, output_tokens=300))  # 生成
    summary = snapshot_cost_summary(tools_called=["SEARCH_PRODUCTS"])
    assert summary["path"] == "heavy"
    assert summary["llmCalls"] == 2
    assert summary["inputTokens"] == 1_200
    # (300*2 + 120*8 + 900*2 + 300*8)/1e6 = (600+960+1800+2400)/1e6
    assert summary["costCny"] == pytest.approx(0.00576)


def test_task_isolation_between_parallel_requests():
    """contextvar 随 task 隔离：并发请求互不污染。"""
    results = {}

    async def request(tag: str, count: int):
        reset_run_cost()
        for _ in range(count):
            record_llm_usage(_priced_response(input_tokens=10, output_tokens=5))
        results[tag] = snapshot_cost_summary()["llmCalls"]

    async def main():
        await asyncio.gather(request("alice", 2), request("bob", 3))

    asyncio.run(main())
    assert results == {"alice": 2, "bob": 3}


def test_scheduled_task_reset_isolates_from_triggering_request():
    """压缩/condense 异步任务开头 reset 后，其调用不计入触发它的请求。"""
    async def request_flow():
        reset_run_cost()
        record_llm_usage(_priced_response(input_tokens=100, output_tokens=50))  # 对话
        # 模拟异步调度任务：继承父 task 的 contextvar 引用，但开头 reset。
        async def scheduled():
            reset_run_cost()
            record_llm_usage(_priced_response(input_tokens=999, output_tokens=999))
            return snapshot_cost_summary()["llmCalls"]

        task = asyncio.create_task(scheduled())
        scheduled_calls = await task
        summary = snapshot_cost_summary()
        return scheduled_calls, summary["llmCalls"], summary["inputTokens"]

    scheduled_calls, request_calls, request_input = asyncio.run(request_flow())
    assert scheduled_calls == 1
    assert request_calls == 1
    assert request_input == 100
