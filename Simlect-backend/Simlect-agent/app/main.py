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
from app.exceptions import BusinessException

from app.api.websocket import start_ws_listener, stop_ws_listener, websocket_endpoint

from app.config.settings import get_settings

from app.db.pool import close_pool, init_pool

from app.services.health_service import health_service

from app.graph.checkpoint.redis_saver import close_checkpointer
from app.services.redis_service import redis_service

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)

logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):

    await redis_service.connect()
    await init_pool()
    from app.memory.session_memory_service import session_memory_service

    await session_memory_service.ensure_table()
    await start_ws_listener(redis_service.client)
    logger.info("agent_service_started")
    yield

    await stop_ws_listener()
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

if __name__ == "__main__":
    settings = get_settings()

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)
