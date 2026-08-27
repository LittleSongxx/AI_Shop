from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.episode_service import bind_episode
from app.services.mcp_streamable_client import McpStreamableClient
from app.services.mcp_trusted_context import (
    TRUSTED_TURN_META_KEY,
    TrustedTurnContextRejected,
    build_trusted_turn_meta,
    trusted_turn_context_from_meta,
)
from app.services.tool_invoke_result import ToolInvokeResult, parse_tool_wire


def _trusted_meta(*, user_id: str = "u1", run_id: str = "run-1") -> dict:
    with bind_episode(
        run_id,
        message_id=1,
        user_id=user_id,
        request_id="req-1",
        trusted_user_text="不要苹果，推荐安卓手机",
    ):
        meta = build_trusted_turn_meta(
            "SEARCH_PRODUCTS",
            {"userId": user_id, "runId": run_id, "requestId": "req-1"},
        )
    assert meta is not None
    return meta


def test_trusted_turn_meta_comes_only_from_bound_episode() -> None:
    assert build_trusted_turn_meta("SEARCH_PRODUCTS", {"userId": "u1"}) is None
    assert build_trusted_turn_meta("QUERY_ORDERS", {"userId": "u1"}) is None

    meta = _trusted_meta()

    assert meta[TRUSTED_TURN_META_KEY]["userText"] == "不要苹果，推荐安卓手机"
    assert "不要苹果" not in str(build_trusted_turn_meta("QUERY_ORDERS", {}))


def test_trusted_turn_meta_fails_closed_on_episode_user_mismatch() -> None:
    with bind_episode(
        "run-1",
        message_id=1,
        user_id="u1",
        trusted_user_text="安卓手机",
    ):
        with pytest.raises(TrustedTurnContextRejected, match="Episode user"):
            build_trusted_turn_meta("SEARCH_PRODUCTS", {"userId": "u2"})


def test_trusted_turn_meta_validates_call_bindings() -> None:
    context = trusted_turn_context_from_meta(
        _trusted_meta(),
        tool_name="SEARCH_PRODUCTS",
        arguments={
            "userId": "u1",
            "runId": "run-1",
            "requestId": "req-1",
        },
    )

    assert context is not None
    assert context.user_text == "不要苹果，推荐安卓手机"


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"userId": "attacker", "runId": "run-1"}, "user binding"),
        ({"userId": "u1", "requestId": "req-1"}, "run binding"),
        ({"userId": "u1", "runId": "run-other"}, "run binding"),
        ({"userId": "u1", "runId": "run-1"}, "request binding"),
        (
            {"userId": "u1", "runId": "run-1", "requestId": "req-other"},
            "request binding",
        ),
    ],
)
def test_trusted_turn_meta_rejects_cross_request_reuse(arguments: dict, match: str) -> None:
    with pytest.raises(TrustedTurnContextRejected, match=match):
        trusted_turn_context_from_meta(
            _trusted_meta(),
            tool_name="SEARCH_PRODUCTS",
            arguments=arguments,
        )


async def test_trusted_episode_context_is_isolated_between_concurrent_calls() -> None:
    async def read(user_id: str, text: str) -> str | None:
        with bind_episode(
            f"run-{user_id}",
            message_id=1,
            user_id=user_id,
            trusted_user_text=text,
        ):
            await asyncio.sleep(0.01)
            meta = build_trusted_turn_meta(
                "SEARCH_PRODUCTS", {"userId": user_id}
            )
            return meta[TRUSTED_TURN_META_KEY]["userText"] if meta else None

    assert await asyncio.gather(
        read("u-android", "安卓手机"), read("u-headphone", "索尼耳机")
    ) == [
        "安卓手机",
        "索尼耳机",
    ]


async def test_mcp_client_merges_trusted_turn_with_evaluation_meta(monkeypatch) -> None:
    captured: dict = {}

    class FakeSession:
        async def call_tool(self, name, args, *, meta=None):
            captured.update({"name": name, "args": args, "meta": meta})
            return SimpleNamespace(
                content=[SimpleNamespace(text=ToolInvokeResult(content="ok").to_wire())],
                isError=False,
            )

    client = McpStreamableClient()

    async def with_session(operation, *, what):
        assert what == "SEARCH_PRODUCTS"
        return await operation(FakeSession())

    monkeypatch.setattr(client, "_with_session", with_session)
    monkeypatch.setattr(
        "app.services.mcp_streamable_client.active_mcp_fault_meta",
        lambda: {"aishopEvaluationFaultCapability": "opaque-capability"},
    )
    with bind_episode(
        "run-1",
        message_id=1,
        user_id="u1",
        request_id="req-1",
        trusted_user_text="不要苹果，推荐安卓手机",
    ):
        result = await client.call_tool(
            "SEARCH_PRODUCTS",
            {
                "userId": "u1",
                "keyword": "手机",
                "runId": "run-1",
                "requestId": "req-1",
            },
        )

    assert result.content == "ok"
    assert captured["args"]["keyword"] == "手机"
    assert captured["meta"]["aishopEvaluationFaultCapability"] == "opaque-capability"
    assert (
        captured["meta"][TRUSTED_TURN_META_KEY]["userText"]
        == "不要苹果，推荐安卓手机"
    )


async def test_mcp_search_reads_trusted_text_from_injected_request_context(
    monkeypatch,
) -> None:
    from app.mcp_server import server

    search = AsyncMock(return_value=ToolInvokeResult(content="ok"))
    monkeypatch.setattr(server.tools, "tool_search_products", search)
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(meta=_trusted_meta())
    )

    wire = await server.search_products(
        "u1",
        "手机",
        requestId="req-1",
        runId="run-1",
        ctx=ctx,
    )

    assert parse_tool_wire(wire).content == "ok"
    assert search.await_args.kwargs["trusted_user_text"] == "不要苹果，推荐安卓手机"


def test_mcp_trusted_context_is_not_exposed_in_search_tool_schema() -> None:
    from app.mcp_server.server import mcp

    tool = mcp._tool_manager._tools["SEARCH_PRODUCTS"]
    assert tool.context_kwarg == "ctx"
    assert "ctx" not in tool.parameters["properties"]
    assert "trusted_user_text" not in tool.parameters["properties"]
