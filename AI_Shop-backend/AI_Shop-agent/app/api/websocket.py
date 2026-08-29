import asyncio
import json

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from app.auth.security import Principal, websocket_origin_allowed
from app.auth.token_service import get_admin_by_token, get_user_by_token
from app.config.settings import get_settings
from app.constants import WS_MESSAGE_TOPIC_ADMIN, WS_MESSAGE_TOPIC_AGENT
from app.services.rate_limit_service import rate_limit_service
from app.services.redis_service import redis_service
from app.utils.ws_token import resolve_ws_credentials

logger = structlog.get_logger()

class ConnectionManager:

    def __init__(self):

        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(
        self, user_id: str, ws: WebSocket, principal: Principal | None = None
    ) -> None:

        await ws.accept()
        if principal is not None:
            # Scope is server-owned; no value is read from a client frame.
            ws.scope["principal"] = principal.as_dict()
        self._connections.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: str, ws: WebSocket) -> None:

        conns = self._connections.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                self._connections.pop(user_id, None)

    async def send_json(self, user_id: str, data: dict) -> None:

        conns = self._connections.get(user_id, set())
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(user_id, ws)

manager = ConnectionManager()


class AdminConnectionManager:

    def __init__(self):
        self._connections: set[WebSocket] = set()

    async def connect(self, ws: WebSocket, principal: Principal | None = None) -> None:
        await ws.accept()
        if principal is not None:
            ws.scope["principal"] = principal.as_dict()
        self._connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    async def broadcast(self, data: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


admin_manager = AdminConnectionManager()

_listener_task: asyncio.Task | None = None

async def _topic_listener(redis_client) -> None:

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(WS_MESSAGE_TOPIC_AGENT, WS_MESSAGE_TOPIC_ADMIN)
    logger.info(
        "ws_topic_subscribed",
        topics=[WS_MESSAGE_TOPIC_AGENT, WS_MESSAGE_TOPIC_ADMIN],
    )
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if message.get("channel") == WS_MESSAGE_TOPIC_ADMIN:
                await admin_manager.broadcast(data)
            else:
                user_id = data.get("userId")
                if user_id:
                    await manager.send_json(user_id, data)
    except asyncio.CancelledError:

        await pubsub.unsubscribe(WS_MESSAGE_TOPIC_AGENT, WS_MESSAGE_TOPIC_ADMIN)
        raise

async def stop_ws_listener() -> None:

    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
    _listener_task = None

async def start_ws_listener(redis_client) -> None:

    global _listener_task
    await stop_ws_listener()
    _listener_task = asyncio.create_task(_topic_listener(redis_client))

async def _handle_heartbeat(user_id: str, ws: WebSocket, data: str) -> bool:

    if data.lower() != "ping":
        return False
    await redis_service.save_user_heartbeat(user_id)
    await ws.send_text("pong")
    return True


def _origin_is_allowed(ws: WebSocket, source: str) -> bool:
    settings = get_settings()
    return websocket_origin_allowed(
        ws.headers.get("origin"),
        token_source=source,  # type: ignore[arg-type]
        allowed_origins=settings.websocket_allowed_origins,
        request_host=ws.headers.get("host"),
        forwarded_proto=ws.headers.get("x-forwarded-proto"),
    )


async def _allow_frame(subject: str, *, admin: bool = False) -> bool:
    """Apply one Redis window to every inbound frame, including heartbeats."""

    settings = get_settings()
    scope = "wsAdminFrame" if admin else "wsFrame"
    key_subject = f"admin:{subject}" if admin else subject
    try:
        return await rate_limit_service.allow(
            key_subject,
            scope,
            settings.ws_rate_limit_window_seconds,
            settings.ws_rate_limit_max_messages,
        )
    except Exception as exc:
        # A rate-limit store outage must not silently become an unlimited
        # socket. Close the connection and let the browser reconnect later.
        logger.warning("ws_rate_limit_unavailable", scope=scope, error=type(exc).__name__)
        return False


def _frame_too_large(data: str) -> bool:
    try:
        size = len(data.encode("utf-8"))
    except UnicodeEncodeError:
        return True
    return size > get_settings().ws_max_frame_bytes

async def websocket_endpoint(ws: WebSocket, query_token: str | None = None) -> None:

    credentials = resolve_ws_credentials(
        query_token,
        ws.cookies.get("token"),
        ws.headers.get("token"),
    )
    if not credentials:
        logger.warning("ws_rejected", reason="token_required")
        await ws.close(code=1008, reason="token required")
        return
    if len(credentials.token) > 512:
        await ws.close(code=1008, reason="invalid token")
        return
    if not _origin_is_allowed(ws, credentials.source):
        logger.warning("ws_rejected", reason="origin_not_allowed", source=credentials.source)
        await ws.close(code=1008, reason="origin not allowed")
        return
    user = await get_user_by_token(credentials.token)
    if not user or not user.user_id:
        logger.warning("ws_rejected", reason="invalid_token")
        await ws.close(code=1008, reason="invalid token")
        return
    user.auth_source = credentials.source  # type: ignore[assignment]
    user_id = user.user_id
    await manager.connect(user_id, ws)
    ws.scope["principal"] = user.principal.as_dict()
    logger.info("ws_connected", user_id=user_id)
    try:
        while True:
            data = await ws.receive_text()
            if _frame_too_large(data):
                await ws.close(code=1009, reason="message too large")
                break
            if not await _allow_frame(user_id):
                await ws.close(code=1008, reason="rate limit exceeded")
                break
            if await _handle_heartbeat(user_id, ws, data):
                continue

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(user_id, ws)
        logger.info("ws_disconnected", user_id=user_id)


async def admin_websocket_endpoint(
    ws: WebSocket, query_token: str | None = None
) -> None:
    credentials = resolve_ws_credentials(
        query_token,
        ws.cookies.get("adminToken"),
        ws.headers.get("adminToken"),
    )
    if not credentials:
        await ws.close(code=1008, reason="admin token required")
        return
    if len(credentials.token) > 512 or not _origin_is_allowed(ws, credentials.source):
        await ws.close(code=1008, reason="origin or token invalid")
        return
    account = await get_admin_by_token(credentials.token)
    if not account:
        await ws.close(code=1008, reason="invalid admin token")
        return
    principal = Principal(account, "ADMIN", credentials.source)  # type: ignore[arg-type]
    await admin_manager.connect(ws)
    ws.scope["principal"] = principal.as_dict()
    logger.info("admin_ws_connected", account=account)
    try:
        while True:
            data = await ws.receive_text()
            if _frame_too_large(data):
                await ws.close(code=1009, reason="message too large")
                break
            if not await _allow_frame(account, admin=True):
                await ws.close(code=1008, reason="rate limit exceeded")
                break
            if data.lower() == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        admin_manager.disconnect(ws)
        logger.info("admin_ws_disconnected", account=account)
