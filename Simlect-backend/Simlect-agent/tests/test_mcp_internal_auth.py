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
    monkeypatch.setenv("SIMLECT_INTERNAL_TOKEN", "secret")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    client = McpStreamableClient()
    assert client._headers == {"X-Internal-Token": "secret"}
    get_settings.cache_clear()


def test_production_settings_reject_default_secrets():
    settings = Settings(app_env="production")
    with pytest.raises(ValueError, match="SIMLECT_INTERNAL_TOKEN"):
        settings.validate_runtime()
