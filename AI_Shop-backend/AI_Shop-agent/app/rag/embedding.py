import contextvars
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import httpx
import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.rag.local_embedding import local_hash_embedding
from app.rag.runtime_trace import active_rag_runtime_trace
from app.resilience.circuit_breaker import circuit_registry
from app.services.redis_service import redis_service
from evaluation.core.fault_injection import fault_point

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
    response_records: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.response_records is None:
            self.response_records = []

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "bypassCache": self.bypass_cache,
            "requests": self.requests,
            "cacheHits": self.cache_hits,
            "providerRequests": self.provider_requests,
            "providerSuccesses": self.provider_successes,
            "providerFailures": self.provider_failures,
            "breakerRejections": self.breaker_rejections,
            "responseRecords": list(self.response_records or []),
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
    provider = getattr(settings, "embedding_provider", "openai")
    if provider == "local":
        return local_hash_embedding(query, settings.embedding_dimensions)
    cache_key = (
        f"mall:rag:embedding:{provider}:{settings.embedding_model}:"
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
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.cache_hit("embedding")
        return cached
    breaker = circuit_registry.get_or_create("embedding", failure_threshold=3, recovery_timeout=30)
    if not breaker.allow_request():
        if evaluation is not None:
            evaluation.provider_failures += 1
            evaluation.breaker_rejections += 1
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.fallback("embedding_breaker_open")
        return None
    try:
        injected_mode = fault_point("embedding")
        if injected_mode == "empty":
            return None
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.called("embedding")
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
        if runtime_trace is not None:
            runtime_trace.succeeded("embedding")
        if evaluation is not None:
            evaluation.provider_successes += 1
            evaluation.response_records.append(
                {
                    "inputSha256": _sha256(query),
                    "dimensions": len(vector),
                    "vectorSha256": hashlib.sha256(
                        json.dumps(
                            [round(float(item), 10) for item in vector],
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                }
            )
        await _set_cached_embedding(cache_key, vector)
        return vector
    except httpx.HTTPStatusError as e:
        breaker.record_failure()
        if runtime_trace is not None:
            runtime_trace.failed("embedding")
        if evaluation is not None:
            evaluation.provider_failures += 1
        detail = e.response.text[:300] if e.response is not None else str(e)
        logger.warning("embedding_failed", status=e.response.status_code if e.response else None, error=detail)
        return None
    except Exception as e:
        breaker.record_failure()
        if runtime_trace is not None:
            runtime_trace.failed("embedding")
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


async def embed_texts(texts: list[str], *, batch_size: int = 10) -> list[list[float] | None]:
    """Embed a bounded batch through the OpenAI-compatible Provider endpoint."""

    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    if getattr(get_settings(), "embedding_provider", "openai") == "local":
        return [await embed_text(value) for value in values]
    if _EVALUATION_STATS.get() is None:
        return [await embed_text(value) for value in values]
    if not 1 <= batch_size <= 10:
        raise ValueError("embedding batch_size must be between 1 and 10")
    settings = get_settings()
    evaluation = _EVALUATION_STATS.get()
    assert evaluation is not None
    output: list[list[float] | None] = [None] * len(values)
    for start in range(0, len(values), batch_size):
        batch = values[start : start + batch_size]
        non_empty = [(index, value) for index, value in enumerate(batch) if value]
        evaluation.requests += len(batch)
        if not non_empty:
            continue
        breaker = circuit_registry.get_or_create(
            "embedding", failure_threshold=3, recovery_timeout=30
        )
        if not breaker.allow_request():
            evaluation.provider_failures += 1
            evaluation.breaker_rejections += 1
            runtime_trace = active_rag_runtime_trace()
            if runtime_trace is not None:
                runtime_trace.fallback("embedding_breaker_open")
            continue
        try:
            injected_mode = fault_point("embedding")
            if injected_mode == "empty":
                continue
            runtime_trace = active_rag_runtime_trace()
            if runtime_trace is not None:
                runtime_trace.called("embedding")
            evaluation.provider_requests += 1
            client = await get_client("embedding", timeout=30)
            response = await client.post(
                f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                json={
                    "model": settings.embedding_model,
                    "input": [value for _index, value in non_empty],
                    "dimensions": settings.embedding_dimensions,
                    "encoding_format": "float",
                },
                timeout=30,
            )
            response.raise_for_status()
            rows = response.json().get("data") or []
            vectors: dict[int, list[float]] = {}
            for ordinal, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                provider_index = row.get("index", ordinal)
                if not isinstance(provider_index, int) or not 0 <= provider_index < len(non_empty):
                    continue
                vector = row.get("embedding")
                if not isinstance(vector, list):
                    continue
                vectors[provider_index] = [float(item) for item in vector]
            if len(vectors) != len(non_empty):
                raise ValueError(
                    f"embedding batch returned {len(vectors)}/{len(non_empty)} vectors"
                )
            breaker.record_success()
            if runtime_trace is not None:
                runtime_trace.succeeded("embedding")
            evaluation.provider_successes += 1
            for provider_index, vector in vectors.items():
                original_index, value = non_empty[provider_index]
                output[start + original_index] = vector
                evaluation.response_records.append(
                    {
                        "inputSha256": _sha256(value),
                        "dimensions": len(vector),
                        "vectorSha256": hashlib.sha256(
                            json.dumps(
                                [round(float(item), 10) for item in vector],
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        "batch": True,
                    }
                )
        except Exception as exc:
            breaker.record_failure()
            if runtime_trace is not None:
                runtime_trace.failed("embedding")
            evaluation.provider_failures += 1
            logger.warning("embedding_batch_failed", error=str(exc), batchSize=len(non_empty))
    return output


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
