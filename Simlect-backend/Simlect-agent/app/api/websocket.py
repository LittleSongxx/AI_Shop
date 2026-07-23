import asyncio
import json

import structlog
from fastapi import WebSocket, WebSocketDisconnect

from app.auth.token_service import get_user_by_token
from app.constants import WS_MESSAGE_TOPIC_AGENT
from app.services.redis_service import redis_service
from app.utils.ws_token import resolve_ws_token as _resolve_ws_token

logger = structlog.get_logger()

class ConnectionManager:

    def __init__(self):

        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, ws: WebSocket) -> None:

        await ws.accept()
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

_listener_task: asyncio.Task | None = None

async def _topic_listener(redis_client) -> None:

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(WS_MESSAGE_TOPIC_AGENT)
    logger.info("ws_topic_subscribed", topic=WS_MESSAGE_TOPIC_AGENT)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            user_id = data.get("userId")
            if user_id:
                await manager.send_json(user_id, data)
    except asyncio.CancelledError:

        await pubsub.unsubscribe(WS_MESSAGE_TOPIC_AGENT)
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

async def websocket_endpoint(ws: WebSocket, query_token: str | None = None) -> None:

    token = _resolve_ws_token(
        query_token,
        ws.cookies.get("token"),
        ws.headers.get("token"),
    )
    if not token:
        logger.warning("ws_rejected", reason="token_required")
        await ws.close(code=1008, reason="token required")
        return
    user = await get_user_by_token(token)
    if not user or not user.user_id:
        logger.warning("ws_rejected", reason="invalid_token")
        await ws.close(code=1008, reason="invalid token")
        return
    user_id = user.user_id
    await manager.connect(user_id, ws)
    logger.info("ws_connected", user_id=user_id)
    try:
        while True:
            data = await ws.receive_text()
            if await _handle_heartbeat(user_id, ws, data):
                continue

    except WebSocketDisconnect:

        manager.disconnect(user_id, ws)
        logger.info("ws_disconnected", user_id=user_id)
