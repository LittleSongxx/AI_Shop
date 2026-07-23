import hashlib

import httpx
import structlog

from app.config.settings import get_settings
from app.resilience.circuit_breaker import circuit_registry
from app.services.redis_service import redis_service

logger = structlog.get_logger()

async def embed_text(text: str) -> list[float] | None:

    settings = get_settings()
    breaker = circuit_registry.get_or_create("embedding", failure_threshold=3, recovery_timeout=30)
    query = (text or "").strip()
    if not breaker.allow_request() or not query:
        return None
    cache_key = (
        f"mall:rag:embedding:{settings.embedding_model}:"
        f"{settings.embedding_dimensions}:{_sha256(query)}"
    )
    cached = await _get_cached_embedding(cache_key)
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                json={
                    "model": settings.embedding_model,
                    "input": query,
                    "dimensions": settings.embedding_dimensions,
                    "encoding_format": "float",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            vector = data["data"][0]["embedding"]
            breaker.record_success()
            await _set_cached_embedding(cache_key, vector)
            return vector
    except httpx.HTTPStatusError as e:
        breaker.record_failure()
        detail = e.response.text[:300] if e.response is not None else str(e)
        logger.warning("embedding_failed", status=e.response.status_code if e.response else None, error=detail)
        return None
    except Exception as e:
        breaker.record_failure()
        logger.warning("embedding_failed", error=str(e))
        return None


async def _get_cached_embedding(key: str) -> list[float] | None:
    try:
        cached = await redis_service.get_json(key)
        if isinstance(cached, list):
            return [float(item) for item in cached]
    except Exception:
        return None
    return None


async def _set_cached_embedding(key: str, vector: list[float]) -> None:
    try:
        await redis_service.set_json(
            key,
            vector,
            get_settings().embedding_cache_ttl_seconds,
            jitter_seconds=600,
        )
    except Exception:
        pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
