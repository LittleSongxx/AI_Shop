import asyncio
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from prometheus_client import make_asgi_app

from app.api.exception_handlers import business_exception_handler
from app.api.routes import agent, attribution, commerce_outcomes, privacy, v1
from app.api.websocket import (
    admin_websocket_endpoint,
    start_ws_listener,
    stop_ws_listener,
    websocket_endpoint,
)
from app.config.settings import get_settings
from app.db.analytics_pool import close_analytics_pool, init_analytics_pool
from app.db.migrations import run_migrations
from app.db.pool import close_pool, init_pool
from app.exceptions import BusinessException
from app.graph.checkpoint.redis_saver import close_checkpointer
from app.infra.http_client import close_clients as close_http_clients
from app.memory.session_memory_service import session_memory_service
from app.observability.logging import configure_structured_logging
from app.observability.telemetry import configure_telemetry, shutdown_telemetry
from app.rag.retriever import (
    KNOWLEDGE_RELEASE_TOPIC,
    rag_retriever,
)
from app.services.agent_queue_service import agent_queue_service
from app.services.analytics_export_service import analytics_export_service
from app.services.episode_service import episode_service
from app.services.health_service import health_service
from app.services.judge_service import judge_service
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.privacy_job_service import privacy_job_service
from app.services.redis_service import redis_service
from app.services.shopping_mission_service import initialize_category_need_schemas

configure_structured_logging()

logger = structlog.get_logger()
_knowledge_listener_task: asyncio.Task | None = None
_warmup_task: asyncio.Task | None = None


def _log_warmup_result(task: asyncio.Task) -> None:
    """Surface warmup failures instead of letting the exception go unretrieved."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("faq_cache_warmup_failed", error=type(exc).__name__)


async def _knowledge_release_listener() -> None:
    pubsub = redis_service.client.pubsub()
    await pubsub.subscribe(KNOWLEDGE_RELEASE_TOPIC)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            # Java owns mall:knowledge:version as a durable release hint. The
            # Agent only observes the pub/sub event; deleting or expiring that
            # key here would erase the authority during a Java outage.
            logger.info("knowledge_release_observed", version=message.get("data"))
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
    global _knowledge_listener_task, _warmup_task

    get_settings().validate_runtime()
    await redis_service.connect()
    await init_pool()
    if get_settings().agent_auto_migrate:
        await asyncio.to_thread(run_migrations)
    await initialize_category_need_schemas()
    await privacy_job_service.resume_incomplete()
    # The governed views are owned by the Java Admin Flyway migration. When
    # DataAnalyst is enabled, deployment must migrate Admin first and provision
    # the view-only reader before Agent startup; this check intentionally fails
    # closed when that order is violated.
    await init_analytics_pool()
    await analytics_export_service.resume_incomplete()

    await episode_service.start()
    await judge_service.start()
    await session_memory_service.ensure_table()
    await start_ws_listener(redis_service.client)
    _knowledge_listener_task = asyncio.create_task(_knowledge_release_listener())
    # Keep a reference: the event loop only holds a weak one, so an unreferenced
    # task can be garbage collected before it finishes.
    _warmup_task = asyncio.create_task(rag_retriever.warmup_faq_cache())
    _warmup_task.add_done_callback(_log_warmup_result)
    logger.info("agent_service_started")
    yield

    for task in (_knowledge_listener_task, _warmup_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
    _knowledge_listener_task = None
    _warmup_task = None
    await stop_ws_listener()
    await agent_queue_service.close()
    await analytics_export_service.close()
    await close_checkpointer()
    await mcp_streamable_client.close()
    await close_http_clients()
    await judge_service.close()
    await episode_service.close()
    await privacy_job_service.close()
    await close_analytics_pool()
    await close_pool()
    await redis_service.close()
    shutdown_telemetry()
    logger.info("agent_service_stopped")

app = FastAPI(title="EShop Agent Python", lifespan=lifespan)
configure_telemetry(app)

# 接口限流有两层，都不在这里：
#   1. 网关 Sentinel 按路由做 QPS 流控（agent-http / agent-ws），是对外的第一道；
#   2. 应用内按「用户 + 动作」的配额走 rate_limit_service（Redis 固定窗口）。
# 原先这里挂的 slowapi 中间件绑的是本模块的 Limiter，而路由装饰器注册在
# app/api/routes/agent.py 自己的 Limiter 上，两个实例互不可见，中间件实际是空转。
app.add_exception_handler(BusinessException, business_exception_handler)

app.include_router(agent.router, prefix="/api")
app.include_router(v1.router, prefix="/api")
app.include_router(attribution.router)
app.include_router(commerce_outcomes.router)
app.include_router(privacy.router)

app.mount("/metrics", make_asgi_app())

@app.get("/health")
async def health():
    return await health_service.check_all()


@app.get("/health/live")
async def health_live():
    return {"status": "UP"}


@app.get("/health/ready")
async def health_ready():
    result = await health_service.check_readiness()
    if not result["ready"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/health/dependencies")
async def health_dependencies():
    return await health_service.check_dependencies()

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

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
    )
