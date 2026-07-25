import pytest

from app.config.settings import Settings
from app.mcp_server.server import InternalTokenMiddleware
from app.services.mcp_streamable_client import McpStreamableClient


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
