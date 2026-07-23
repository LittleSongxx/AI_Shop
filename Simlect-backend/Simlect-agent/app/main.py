import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, WebSocket
from prometheus_client import make_asgi_app
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.exception_handlers import business_exception_handler
from app.api.routes import agent
from app.api.websocket import (
    admin_websocket_endpoint,
    start_ws_listener,
    stop_ws_listener,
    websocket_endpoint,
)
from app.config.settings import get_settings
from app.db.pool import close_pool, init_pool
from app.exceptions import BusinessException
from app.graph.checkpoint.redis_saver import close_checkpointer
from app.rag.retriever import (
    KNOWLEDGE_RELEASE_TOPIC,
    KNOWLEDGE_VERSION_CACHE_KEY,
    rag_retriever,
)
from app.services.agent_queue_service import agent_queue_service
from app.services.health_service import health_service
from app.services.redis_service import redis_service

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)

logger = structlog.get_logger()
_knowledge_listener_task: asyncio.Task | None = None


async def _knowledge_release_listener() -> None:
    pubsub = redis_service.client.pubsub()
    await pubsub.subscribe(KNOWLEDGE_RELEASE_TOPIC)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            await redis_service.client.delete(KNOWLEDGE_VERSION_CACHE_KEY)
            logger.info("knowledge_version_cache_invalidated")
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(KNOWLEDGE_RELEASE_TOPIC)
            await pubsub.aclose()
        except Exception as exc:
            logger.warning("knowledge_release_listener_close_failed", error=str(exc))

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _knowledge_listener_task

    get_settings().validate_runtime()
    await redis_service.connect()
    await init_pool()
    from app.memory.session_memory_service import session_memory_service

    await session_memory_service.ensure_table()
    await start_ws_listener(redis_service.client)
    _knowledge_listener_task = asyncio.create_task(_knowledge_release_listener())
    asyncio.create_task(rag_retriever.warmup_faq_cache())
    logger.info("agent_service_started")
    yield

    if _knowledge_listener_task and not _knowledge_listener_task.done():
        _knowledge_listener_task.cancel()
        try:
            await _knowledge_listener_task
        except asyncio.CancelledError:
            pass
    _knowledge_listener_task = None
    await stop_ws_listener()
    await agent_queue_service.close()
    await close_checkpointer()
    await close_pool()
    await redis_service.close()
    logger.info("agent_service_stopped")

app = FastAPI(title="EShop Agent Python", version="1.0.0", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(BusinessException, business_exception_handler)

app.include_router(agent.router, prefix="/api")

app.mount("/metrics", make_asgi_app())

@app.get("/health")
async def health():
    return await health_service.check_all()

@app.websocket("/ws")
async def ws_route(ws: WebSocket, token: str | None = None):
    await websocket_endpoint(ws, token)

@app.websocket("/ws/")
async def ws_route_slash(ws: WebSocket, token: str | None = None):
    await websocket_endpoint(ws, token)


@app.websocket("/ws/admin")
async def admin_ws_route(ws: WebSocket, adminToken: str | None = None):
    await admin_websocket_endpoint(ws, adminToken)

if __name__ == "__main__":
    settings = get_settings()

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
