from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.embedding import embed_text, embed_texts, embedding_evaluation_scope
from app.rag.runtime_trace import rag_runtime_trace_scope


def _settings():
    return SimpleNamespace(
        embedding_model="text-embedding-v4",
        embedding_dimensions=3,
        embedding_base_url="https://embedding.example/v1",
        embedding_api_key="secret",
        embedding_cache_ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_embedding_evaluation_bypasses_cache_and_records_provider(monkeypatch):
    monkeypatch.setattr("app.rag.embedding.get_settings", _settings)
    cached = AsyncMock(return_value=[9.0, 9.0, 9.0])
    monkeypatch.setattr("app.rag.embedding._get_cached_embedding", cached)
    monkeypatch.setattr("app.rag.embedding._set_cached_embedding", AsyncMock())
    breaker = SimpleNamespace(
        allow_request=MagicMock(return_value=True),
        record_success=MagicMock(),
        record_failure=MagicMock(),
    )
    monkeypatch.setattr(
        "app.rag.embedding.circuit_registry.get_or_create",
        lambda *_args, **_kwargs: breaker,
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    client = SimpleNamespace(post=AsyncMock(return_value=Response()))
    monkeypatch.setattr(
        "app.rag.embedding.get_client", AsyncMock(return_value=client)
    )

    with rag_runtime_trace_scope() as runtime_trace:
        with embedding_evaluation_scope(bypass_cache=True) as stats:
            vector = await embed_text("查询")

    assert vector == [0.1, 0.2, 0.3]
    assert runtime_trace.public()["providerCalls"] == {"embedding": 1}
    assert runtime_trace.public()["providerSuccesses"] == {"embedding": 1}
    assert runtime_trace.public()["providerFailures"] == {}
    cached.assert_not_awaited()
    assert stats.snapshot() == {
        "bypassCache": True,
        "requests": 1,
        "cacheHits": 0,
        "providerRequests": 1,
        "providerSuccesses": 1,
        "providerFailures": 0,
        "breakerRejections": 0,
        "responseRecords": [
            {
                "inputSha256": "bcd6771e08ec7cc74c670181095f32c1d50035479bd7873700f211c04b175904",
                "dimensions": 3,
                "vectorSha256": "9e9de4ffd2c4e28c8ca3764d2567d8ba1e2c1efcc3f64eb11fd77fd5b51a31b3",
            }
        ],
    }


@pytest.mark.asyncio
async def test_embedding_cache_behavior_is_unchanged_outside_evaluation(monkeypatch):
    monkeypatch.setattr("app.rag.embedding.get_settings", _settings)
    cached = AsyncMock(return_value=[0.4, 0.5, 0.6])
    monkeypatch.setattr("app.rag.embedding._get_cached_embedding", cached)
    provider = AsyncMock()
    monkeypatch.setattr("app.rag.embedding.get_client", provider)

    with rag_runtime_trace_scope() as runtime_trace:
        vector = await embed_text("查询")

    assert vector == [0.4, 0.5, 0.6]
    cached.assert_awaited_once()
    provider.assert_not_awaited()
    assert runtime_trace.public()["providerCalls"] == {}
    assert runtime_trace.public()["providerCacheHits"] == {"embedding": 1}


@pytest.mark.asyncio
async def test_embedding_evaluation_can_explicitly_allow_cache(monkeypatch):
    monkeypatch.setattr("app.rag.embedding.get_settings", _settings)
    monkeypatch.setattr(
        "app.rag.embedding._get_cached_embedding",
        AsyncMock(return_value=[0.4, 0.5, 0.6]),
    )
    provider = AsyncMock()
    monkeypatch.setattr("app.rag.embedding.get_client", provider)

    with embedding_evaluation_scope(bypass_cache=False) as stats:
        vector = await embed_text("查询")

    assert vector == [0.4, 0.5, 0.6]
    assert stats.snapshot()["cacheHits"] == 1
    assert stats.snapshot()["providerRequests"] == 0
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_batch_uses_one_provider_request_per_batch(monkeypatch):
    monkeypatch.setattr("app.rag.embedding.get_settings", _settings)
    monkeypatch.setattr("app.rag.embedding._set_cached_embedding", AsyncMock())
    breaker = SimpleNamespace(
        allow_request=MagicMock(return_value=True),
        record_success=MagicMock(),
        record_failure=MagicMock(),
    )
    monkeypatch.setattr(
        "app.rag.embedding.circuit_registry.get_or_create",
        lambda *_args, **_kwargs: breaker,
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                ]
            }

    client = SimpleNamespace(post=AsyncMock(return_value=Response()))
    monkeypatch.setattr("app.rag.embedding.get_client", AsyncMock(return_value=client))

    with embedding_evaluation_scope(bypass_cache=True) as stats:
        vectors = await embed_texts(["a", "b"], batch_size=10)

    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert stats.snapshot()["requests"] == 2
    assert stats.snapshot()["providerRequests"] == 1
    assert stats.snapshot()["providerSuccesses"] == 1
    assert len(stats.snapshot()["responseRecords"]) == 2
    client.post.assert_awaited_once()
    assert client.post.await_args.kwargs["json"]["input"] == ["a", "b"]


@pytest.mark.asyncio
async def test_embedding_batch_rejects_provider_limit_excess():
    with embedding_evaluation_scope(bypass_cache=True):
        with pytest.raises(ValueError, match="between 1 and 10"):
            await embed_texts(["a"], batch_size=20)
