from __future__ import annotations

import asyncio

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.domain.tool_policy import build_tool_manifest
from app.infra.http_client import get_client
from app.observability.telemetry import telemetry_status
from app.rag.index_contract import vector_index_contract
from app.services.episode_service import episode_service
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.redis_service import redis_service
from app.services.runtime_identity import current_runtime_identity
from app.visual.index import visual_product_index

logger = structlog.get_logger()


class HealthService:

    @staticmethod
    def _observability_status() -> dict:
        return {
            "otel": telemetry_status(),
            "episode": episode_service.status(),
        }

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

    async def _check_worker(self) -> dict:
        try:
            alive, metadata = await asyncio.gather(
                redis_service.worker_is_alive(),
                redis_service.worker_heartbeat_metadata(),
            )
            api_identity = current_runtime_identity()
            api_source = ((api_identity or {}).get("source") or {}).get("sha256")
            worker_source = ((metadata or {}).get("source") or {}).get("sha256")
            source_match = bool(
                alive
                and api_identity
                and metadata
                and api_identity.get("processRole") == "api"
                and metadata.get("processRole") == "worker"
                and api_source
                and api_source == worker_source
            )
            return {
                "ok": source_match,
                "alive": bool(alive),
                "sourceFingerprintMatch": source_match,
                "apiRuntimeIdentity": api_identity,
                "workerRuntimeIdentity": metadata,
                "reason": (
                    None
                    if source_match
                    else "WORKER_HEARTBEAT_METADATA_MISSING_OR_SOURCE_MISMATCH"
                ),
            }
        except Exception as exc:
            logger.warning("health_worker_failed", error=type(exc).__name__)
            return {
                "ok": False,
                "alive": False,
                "sourceFingerprintMatch": False,
                "apiRuntimeIdentity": current_runtime_identity(),
                "workerRuntimeIdentity": None,
                "reason": "WORKER_HEALTH_CHECK_ERROR",
            }

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

    async def _check_mcp_runtime(self) -> dict:
        """Require MCP's live source fingerprint to match the HTTP API.

        Product search and several write proposals run in a standalone MCP
        process.  A successful ``MCP_CONTRACT`` probe alone only proves that a
        compatible endpoint is reachable; it cannot prove that endpoint loaded
        the current business-rule source.
        """

        try:
            metadata = await mcp_streamable_client.runtime_identity()
            api_identity = current_runtime_identity()
            api_source = ((api_identity or {}).get("source") or {}).get("sha256")
            mcp_source = ((metadata or {}).get("source") or {}).get("sha256")
            source_match = bool(
                api_identity
                and metadata
                and api_identity.get("processRole") == "api"
                and metadata.get("processRole") == "mcp"
                and api_source
                and api_source == mcp_source
            )
            return {
                "ok": source_match,
                "sourceFingerprintMatch": source_match,
                "apiRuntimeIdentity": api_identity,
                "mcpRuntimeIdentity": metadata,
                "reason": (
                    None
                    if source_match
                    else "MCP_RUNTIME_IDENTITY_MISSING_OR_SOURCE_MISMATCH"
                ),
            }
        except Exception as exc:
            logger.warning("health_mcp_runtime_failed", error=type(exc).__name__)
            return {
                "ok": False,
                "sourceFingerprintMatch": False,
                "apiRuntimeIdentity": current_runtime_identity(),
                "mcpRuntimeIdentity": None,
                "reason": "MCP_RUNTIME_IDENTITY_CHECK_ERROR",
            }

    async def _check_es_mapping(self) -> dict:
        return await vector_index_contract.check()

    async def _check_visual_search(self) -> dict:
        settings = get_settings()
        # Keep diagnostic probes tolerant of narrow test/deployment settings
        # objects. The real Settings model always has these fields, while a
        # legacy health integration can intentionally omit optional features.
        if not getattr(settings, "visual_search_enabled", False):
            return {"state": "DISABLED"}
        if not str(getattr(settings, "visual_api_key", "") or "").strip():
            return {
                "state": "DEGRADED",
                "reason": "VISUAL_API_KEY_NOT_CONFIGURED",
            }
        try:
            status = await visual_product_index.status()
        except Exception as exc:
            logger.warning("health_visual_index_failed", error=type(exc).__name__)
            return {"state": "DEGRADED", "reason": "VISUAL_INDEX_UNAVAILABLE"}
        return {
            **status,
            "state": (
                "READY" if status.get("servingCurrentModel") else "DEGRADED"
            ),
            "reason": (
                None
                if status.get("servingCurrentModel")
                else "VISUAL_INDEX_BACKFILL_PENDING"
            ),
        }

    async def check_dependencies(self) -> dict:
        settings = get_settings()
        mapping, java_ok, mcp_ok, visual = await asyncio.gather(
            self._check_es_mapping(),
            self._check_java_gateway(),
            self._check_mcp(),
            self._check_visual_search(),
        )
        embedding_provider = getattr(settings, "embedding_provider", "openai")
        embedding_available = (
            embedding_provider == "local" or bool(settings.embedding_api_key.strip())
        )
        # Model providers are intentionally diagnostic-only. Their outage must
        # not make the process disappear from service discovery.
        return {
            "llm": bool(settings.llm_api_key.strip()),
            "embedding": embedding_available,
            "embeddingProvider": embedding_provider,
            "embeddingProductionReady": (
                embedding_provider == "openai"
                and bool(settings.embedding_api_key.strip())
            ),
            "rerank": bool(settings.rerank_api_key.strip()),
            "elasticsearch": mapping,
            "visualSearch": visual,
            "javaGateway": java_ok,
            "mcp": mcp_ok,
            # This is contract reachability, not proof that every business call
            # succeeds. The deeper registry reconciliation is available through
            # McpStreamableClient.tool_manifest().
            "toolManifest": build_tool_manifest(
                timeout_seconds=getattr(settings, "mcp_timeout", 20),
                registry_health="READY" if mcp_ok else "UNAVAILABLE",
            ),
            "observability": self._observability_status(),
        }

    async def check_readiness(self) -> dict:
        mysql, redis, rabbitmq, worker, java_gateway, mcp, mcp_runtime, mapping = await asyncio.gather(
            self._check_mysql(),
            self._check_redis(),
            self._check_rabbitmq(),
            self._check_worker(),
            self._check_java_gateway(),
            self._check_mcp(),
            self._check_mcp_runtime(),
            self._check_es_mapping(),
        )
        checks = {
            "mysql": mysql,
            "redis": redis,
            "rabbitmq": rabbitmq,
            "worker": worker,
            "javaGateway": java_gateway,
            "mcp": mcp,
            "mcpRuntime": mcp_runtime,
            "elasticsearchMapping": mapping,
        }
        ready = all(
            value is True or (isinstance(value, dict) and value.get("ok") is True)
            for value in checks.values()
        )
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "checks": checks,
            "runtimeIdentity": current_runtime_identity(),
        }

    async def check_all(self) -> dict:
        readiness, dependencies = await asyncio.gather(
            self.check_readiness(), self.check_dependencies()
        )
        return {
            "status": "ok" if readiness["ready"] else "degraded",
            "ready": readiness["ready"],
            "checks": readiness["checks"],
            "dependencies": dependencies,
            "observability": self._observability_status(),
        }


health_service = HealthService()
