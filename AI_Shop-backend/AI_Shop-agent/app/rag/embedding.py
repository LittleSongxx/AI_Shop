import contextvars
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import httpx
import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import circuit_registry
from app.services.redis_service import redis_service

logger = structlog.get_logger()


@dataclass
class EmbeddingEvaluationStats:
    """Per-task provider facts collected only by explicit evaluation runners."""

    bypass_cache: bool
    requests: int = 0
    cache_hits: int = 0
    provider_requests: int = 0
    provider_successes: int = 0
    provider_failures: int = 0
    breaker_rejections: int = 0

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "bypassCache": self.bypass_cache,
            "requests": self.requests,
            "cacheHits": self.cache_hits,
            "providerRequests": self.provider_requests,
            "providerSuccesses": self.provider_successes,
            "providerFailures": self.provider_failures,
            "breakerRejections": self.breaker_rejections,
        }


_EVALUATION_STATS: contextvars.ContextVar[EmbeddingEvaluationStats | None] = (
    contextvars.ContextVar("embedding_evaluation_stats", default=None)
)


@contextmanager
def embedding_evaluation_scope(
    *, bypass_cache: bool = True
) -> Iterator[EmbeddingEvaluationStats]:
    """Collect real-provider facts without changing the default application path."""

    stats = EmbeddingEvaluationStats(bypass_cache=bypass_cache)
    token = _EVALUATION_STATS.set(stats)
    try:
        yield stats
    finally:
        _EVALUATION_STATS.reset(token)

async def embed_text(text: str) -> list[float] | None:

    settings = get_settings()
    query = (text or "").strip()
    if not query:
        return None
    evaluation = _EVALUATION_STATS.get()
    if evaluation is not None:
        evaluation.requests += 1
    cache_key = (
        f"mall:rag:embedding:{settings.embedding_model}:"
        f"{settings.embedding_dimensions}:{_sha256(query)}"
    )
    # Cache is consulted before the breaker: a cached vector is still valid while
    # the provider is down, and it avoids reserving a half-open probe slot for a
    # call that never reaches the network.
    cached = (
        None
        if evaluation is not None and evaluation.bypass_cache
        else await _get_cached_embedding(cache_key)
    )
    if cached:
        if evaluation is not None:
            evaluation.cache_hits += 1
        return cached
    breaker = circuit_registry.get_or_create("embedding", failure_threshold=3, recovery_timeout=30)
    if not breaker.allow_request():
        if evaluation is not None:
            evaluation.provider_failures += 1
            evaluation.breaker_rejections += 1
        return None
    try:
        if evaluation is not None:
            evaluation.provider_requests += 1
        client = await get_client("embedding", timeout=30)
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
        if evaluation is not None:
            evaluation.provider_successes += 1
        await _set_cached_embedding(cache_key, vector)
        return vector
    except httpx.HTTPStatusError as e:
        breaker.record_failure()
        if evaluation is not None:
            evaluation.provider_failures += 1
        detail = e.response.text[:300] if e.response is not None else str(e)
        logger.warning("embedding_failed", status=e.response.status_code if e.response else None, error=detail)
        return None
    except Exception as e:
        breaker.record_failure()
        if evaluation is not None:
            evaluation.provider_failures += 1
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
