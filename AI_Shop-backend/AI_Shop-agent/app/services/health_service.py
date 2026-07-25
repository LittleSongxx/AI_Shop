from __future__ import annotations

import asyncio

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.infra.http_client import get_client
from app.rag.index_contract import vector_index_contract
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.redis_service import redis_service

logger = structlog.get_logger()


class HealthService:

    async def _check_mysql(self) -> bool:
        try:
            async with acquire() as cur:
                await cur.execute("SELECT 1")
            return True
        except Exception as exc:
            logger.warning("health_mysql_failed", error=type(exc).__name__)
            return False

    async def _check_redis(self) -> bool:
        try:
            await redis_service.client.ping()
            return True
        except Exception as exc:
            logger.warning("health_redis_failed", error=type(exc).__name__)
            return False

    async def _check_rabbitmq(self) -> bool:
        try:
            import aio_pika

            connection = await aio_pika.connect_robust(
                get_settings().rabbitmq_url,
                timeout=3,
            )
            await connection.close()
            return True
        except Exception as exc:
            logger.warning("health_rabbitmq_failed", error=type(exc).__name__)
            return False

    async def _check_worker(self) -> bool:
        try:
            return await redis_service.worker_is_alive()
        except Exception as exc:
            logger.warning("health_worker_failed", error=type(exc).__name__)
            return False

    async def _check_java_gateway(self) -> bool:
        settings = get_settings()
        try:
            client = await get_client("health", timeout=3)
            response = await client.get(
                f"{settings.java_web_url.rstrip('/')}/actuator/health", timeout=3
            )
            return response.status_code < 400
        except Exception as exc:
            logger.warning("health_java_gateway_failed", error=type(exc).__name__)
            return False

    async def _check_mcp(self) -> bool:
        try:
            return await mcp_streamable_client.check_contract()
        except Exception as exc:
            logger.warning("health_mcp_failed", error=type(exc).__name__)
            return False

    async def _check_es_mapping(self) -> dict:
        return await vector_index_contract.check()

    async def check_dependencies(self) -> dict:
        settings = get_settings()
        mapping, java_ok, mcp_ok = await asyncio.gather(
            self._check_es_mapping(),
            self._check_java_gateway(),
            self._check_mcp(),
        )
        # Model providers are intentionally diagnostic-only. Their outage must
        # not make the process disappear from service discovery.
        return {
            "llm": bool(settings.llm_api_key.strip()),
            "embedding": bool(settings.embedding_api_key.strip()),
            "rerank": bool(settings.rerank_api_key.strip()),
            "elasticsearch": mapping,
            "javaGateway": java_ok,
            "mcp": mcp_ok,
        }

    async def check_readiness(self) -> dict:
        mysql, redis, rabbitmq, worker, java_gateway, mcp, mapping = await asyncio.gather(
            self._check_mysql(),
            self._check_redis(),
            self._check_rabbitmq(),
            self._check_worker(),
            self._check_java_gateway(),
            self._check_mcp(),
            self._check_es_mapping(),
        )
        checks = {
            "mysql": mysql,
            "redis": redis,
            "rabbitmq": rabbitmq,
            "worker": worker,
            "javaGateway": java_gateway,
            "mcp": mcp,
            "elasticsearchMapping": mapping,
        }
        ready = all(
            value is True or (isinstance(value, dict) and value.get("ok") is True)
            for value in checks.values()
        )
        return {"status": "ready" if ready else "not_ready", "ready": ready, "checks": checks}

    async def check_all(self) -> dict:
        readiness, dependencies = await asyncio.gather(
            self.check_readiness(), self.check_dependencies()
        )
        return {
            "status": "ok" if readiness["ready"] else "degraded",
            "ready": readiness["ready"],
            "checks": readiness["checks"],
            "dependencies": dependencies,
        }


health_service = HealthService()
