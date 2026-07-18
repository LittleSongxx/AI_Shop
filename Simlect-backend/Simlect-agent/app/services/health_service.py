import httpx
import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.services.redis_service import redis_service

logger = structlog.get_logger()

class HealthService:

    async def check_all(self) -> dict:
        checks: dict[str, bool | str] = {
            "redis": False,
            "mysql": False,
            "elasticsearch": False,
            "java_web": False,
            "status": "ok",
        }

        try:
            await redis_service.client.ping()
            checks["redis"] = True
        except Exception as e:
            logger.warning("health_redis_failed", error=str(e))
            checks["status"] = "degraded"

        try:
            async with acquire() as cur:
                await cur.execute("SELECT 1")
            checks["mysql"] = True
        except Exception as e:
            logger.warning("health_mysql_failed", error=str(e))
            checks["status"] = "degraded"

        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{settings.es_hosts.split(',')[0].rstrip('/')}/")
                checks["elasticsearch"] = resp.status_code < 500
        except Exception as e:
            logger.warning("health_es_failed", error=str(e))
            checks["status"] = "degraded"

        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{settings.java_web_url.rstrip('/')}/api/")
                checks["java_web"] = resp.status_code < 500
        except Exception as e:
            logger.warning("health_java_web_failed", error=str(e))
            checks["status"] = "degraded"

        if not checks["redis"] or not checks["mysql"]:
            checks["status"] = "unhealthy"
        return checks

health_service = HealthService()
