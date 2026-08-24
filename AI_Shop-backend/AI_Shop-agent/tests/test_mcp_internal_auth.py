import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.config.settings import Settings
from app.mcp_server.server import InternalTokenMiddleware, _run_as_delegated_user
from app.services.java_internal_client import JavaInternalClient
from app.services.mcp_streamable_client import McpStreamableClient
from app.services.tool_invoke_result import ToolInvokeResult


@pytest.mark.asyncio
async def test_mcp_rejects_missing_internal_token():
    called = False
    events = []

    async def app(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(event):
        events.append(event)

    middleware = InternalTokenMiddleware(app, "secret")
    await middleware(
        {"type": "http", "method": "POST", "path": "/mcp", "headers": []},
        receive,
        send,
    )

    assert called is False
    assert events[0]["status"] == 401


def test_mcp_client_always_sends_internal_token(monkeypatch):
    monkeypatch.setenv("AISHOP_INTERNAL_TOKEN", "secret")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    client = McpStreamableClient()
    assert client._headers == {"X-Internal-Token": "secret"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_mcp_runtime_identity_requires_an_object_contract(monkeypatch):
    client = McpStreamableClient()
    expected = {
        "schemaVersion": "aishop-runtime-identity/v1",
        "processRole": "mcp",
        "source": {"sha256": "a" * 64},
    }
    monkeypatch.setattr(
        client,
        "call_tool",
        AsyncMock(return_value=ToolInvokeResult(content=json.dumps(expected))),
    )

    assert await client.runtime_identity() == expected

    client.call_tool = AsyncMock(return_value=ToolInvokeResult(content="[]"))
    with pytest.raises(RuntimeError, match="must be an object"):
        await client.runtime_identity()


@pytest.mark.asyncio
async def test_mcp_rebinds_the_verified_user_for_java_calls(monkeypatch):
    monkeypatch.setenv("AISHOP_INTERNAL_TOKEN", "secret")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    client = JavaInternalClient()

    async def inspect_headers():
        return client._headers()

    headers = await _run_as_delegated_user("u1", inspect_headers())

    assert headers == {
        "X-Internal-Token": "secret",
        "Content-Type": "application/json",
        "X-Agent-User-Id": "u1",
    }
    assert "X-Agent-User-Id" not in client._headers()
    get_settings.cache_clear()


def test_every_user_scoped_mcp_tool_rebinds_delegated_identity():
    server_path = Path(__file__).parents[1] / "app" / "mcp_server" / "server.py"
    module = ast.parse(server_path.read_text(encoding="utf-8"))

    missing: list[str] = []
    for node in module.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if not any(arg.arg == "userId" for arg in node.args.args):
            continue
        if not any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_run_as_delegated_user"
            for child in ast.walk(node)
        ):
            missing.append(node.name)

    assert missing == []


@pytest.mark.asyncio
async def test_mcp_session_is_reused_and_rebuilt_after_it_is_lost(monkeypatch):
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    from app.config.settings import get_settings
    from app.services.mcp_streamable_client import _SessionHolder

    monkeypatch.setenv("AISHOP_INTERNAL_TOKEN", "secret")
    get_settings.cache_clear()

    sessions = []

    class FakeSession:
        def __init__(self, generation: int) -> None:
            self.generation = generation
            self.calls = 0

        async def call_tool(self, name, args):
            self.calls += 1
            if self.generation == 0:
                # What a caller sees once the server has forgotten our session id.
                raise McpError(ErrorData(code=-32000, message="Connection closed"))
            return f"{name}-gen{self.generation}"

    async def fake_serve(self, conn):
        session = FakeSession(len(sessions))
        sessions.append(session)
        conn.session = session
        conn.ready.set()
        try:
            await conn.stop.wait()
        finally:
            conn.session = None
            conn.ready.set()

    monkeypatch.setattr(_SessionHolder, "_serve", fake_serve)

    client = McpStreamableClient()
    try:
        first = await client._holder.get()
        assert await client._holder.get() is first
        assert len(sessions) == 1, "the session must be held open, not rebuilt per call"

        result = await client._with_session(
            lambda session: session.call_tool("PING", {}), what="PING"
        )
        assert result == "PING-gen1"
        assert len(sessions) == 2, "a lost session must be rebuilt once"
        assert sessions[0].calls == 1
    finally:
        await client.close()
        get_settings.cache_clear()

    assert client._holder._conn is None


def test_production_settings_reject_default_secrets():
    settings = Settings(app_env="production")
    with pytest.raises(ValueError, match="AISHOP_INTERNAL_TOKEN"):
        settings.validate_runtime()
