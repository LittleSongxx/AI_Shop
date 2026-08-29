from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.api import websocket as websocket_module
from app.auth.security import (
    Principal,
    content_length_exceeds,
    csrf_origin_allowed,
    is_origin_allowed,
    websocket_origin_allowed,
)
from app.auth.token_service import TokenUserInfo, _payload_not_expired
from app.config.settings import Settings
from app.main import trust_boundary_guard


class _FakeWebSocket:
    def __init__(self, incoming, *, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.scope = {"type": "websocket"}
        self.incoming = list(incoming)
        self.accepted = False
        self.closed: list[tuple[int, str | None]] = []
        self.sent: list[str] = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=None):
        self.closed.append((code, reason))

    async def receive_text(self):
        if not self.incoming:
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        value = self.incoming.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def send_text(self, value):
        self.sent.append(value)


def test_principal_is_server_owned_and_constant_time_owner_check():
    principal = Principal("user-a")
    assert principal.owns("user-a")
    assert not principal.owns("user-b")
    assert principal.as_dict() == {
        "subject": "user-a",
        "kind": "USER",
        "authSource": "session",
    }
    assert TokenUserInfo(user_id="user-a").principal.owns("user-a")


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://shop.example.test", True),
        ("https://evil.example.test", False),
        ("null", False),
        ("https://shop.example.test/path", False),
    ],
)
def test_origin_allowlist_is_exact(origin, allowed):
    assert is_origin_allowed(
        origin,
        allowed_origins=["https://shop.example.test"],
    ) is allowed


def test_cookie_websocket_requires_origin_but_header_client_may_omit_it():
    assert not websocket_origin_allowed(None, token_source="cookie")
    assert websocket_origin_allowed(None, token_source="header")
    assert csrf_origin_allowed(
        "http://testserver", request_host="testserver", forwarded_proto="http"
    )


def test_input_limits_fail_closed_for_malformed_or_large_content_length():
    assert content_length_exceeds({"content-length": "65537"}, 65536)
    assert content_length_exceeds({"content-length": "not-a-number"}, 65536)
    assert not content_length_exceeds({}, 65536)


def test_expired_embedded_token_metadata_is_rejected(monkeypatch):
    monkeypatch.setattr("app.auth.token_service.time.time", lambda: 1000.0)
    assert not _payload_not_expired({"expiresAt": 999})
    assert _payload_not_expired({"expiresAt": 1001})
    assert not _payload_not_expired({"exp": "not-a-date"})


@pytest.mark.asyncio
async def test_websocket_rejects_cross_origin_before_authentication(monkeypatch):
    ws = _FakeWebSocket(
        [],
        headers={
            "origin": "https://evil.example.test",
            "host": "shop.example.test",
            "token": "token-a",
        },
    )
    auth = AsyncMock(side_effect=AssertionError("origin must be checked first"))
    monkeypatch.setattr(websocket_module, "get_user_by_token", auth)
    monkeypatch.setattr(
        websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            websocket_allowed_origins=["https://shop.example.test"],
            ws_max_frame_bytes=1024,
            ws_rate_limit_window_seconds=60,
            ws_rate_limit_max_messages=120,
        ),
    )

    await websocket_module.websocket_endpoint(ws)

    assert ws.closed and ws.closed[-1][0] == 1008
    auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_websocket_binds_authenticated_principal_and_limits_frames(monkeypatch):
    user = TokenUserInfo(user_id="user-a")
    ws = _FakeWebSocket(
        ["ping"],
        headers={"host": "testserver", "token": "token-a"},
    )
    manager = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=Mock(),
    )
    monkeypatch.setattr(websocket_module, "manager", manager)
    monkeypatch.setattr(websocket_module, "get_user_by_token", AsyncMock(return_value=user))
    monkeypatch.setattr(websocket_module, "_allow_frame", AsyncMock(return_value=True))
    monkeypatch.setattr(websocket_module.redis_service, "save_user_heartbeat", AsyncMock())
    monkeypatch.setattr(
        websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            websocket_allowed_origins=[],
            ws_max_frame_bytes=1024,
            ws_rate_limit_window_seconds=60,
            ws_rate_limit_max_messages=120,
        ),
    )

    await websocket_module.websocket_endpoint(ws)

    manager.connect.assert_awaited_once()
    assert manager.connect.await_args.args[0] == "user-a"
    assert ws.scope["principal"]["subject"] == "user-a"
    assert ws.sent == ["pong"]
    manager.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_websocket_closes_oversized_frame_without_processing_it(monkeypatch):
    ws = _FakeWebSocket(
        ["x" * 20],
        headers={"host": "testserver", "token": "token-a"},
    )
    user = TokenUserInfo(user_id="user-a")
    allow = AsyncMock(return_value=True)
    monkeypatch.setattr(websocket_module, "get_user_by_token", AsyncMock(return_value=user))
    monkeypatch.setattr(websocket_module, "_allow_frame", allow)
    monkeypatch.setattr(
        websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            websocket_allowed_origins=[],
            ws_max_frame_bytes=8,
            ws_rate_limit_window_seconds=60,
            ws_rate_limit_max_messages=120,
        ),
    )

    await websocket_module.websocket_endpoint(ws)

    assert ws.closed[-1][0] == 1009
    allow.assert_not_awaited()


@pytest.mark.asyncio
async def test_cookie_http_mutation_requires_same_origin(monkeypatch):
    settings = Settings(_env_file=None, data_analyst_enabled=False)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/agent/sendMessage",
        "headers": [
            (b"host", b"shop.example.test"),
            (b"cookie", b"token=session-a"),
            (b"content-length", b"12"),
            (b"origin", b"https://evil.example.test"),
        ],
    }
    request = Request(scope)
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        return PlainTextResponse("ok")

    response = await trust_boundary_guard(request, call_next)

    assert response.status_code == 403
    assert not called
