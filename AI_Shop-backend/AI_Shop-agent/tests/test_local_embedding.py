import math
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.rag.embedding import embed_text
from app.rag.local_embedding import local_hash_embedding


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_local_hash_embedding_is_stable_normalized_and_lexical():
    query = local_hash_embedding("轻薄笔记本", 1024)
    same = local_hash_embedding("轻薄笔记本", 1024)
    related = local_hash_embedding("轻薄笔记本电脑 适合办公", 1024)
    unrelated = local_hash_embedding("厨房不锈钢炒锅", 1024)

    assert query == same
    assert len(query) == 1024
    assert math.sqrt(sum(value * value for value in query)) == pytest.approx(1.0)
    assert _cosine(query, related) > _cosine(query, unrelated)


def test_local_hash_embedding_matches_java_sparse_vector_contract():
    vector = local_hash_embedding("轻薄笔记本", 1024)

    assert [index for index, value in enumerate(vector) if value] == [
        50,
        132,
        172,
        182,
        187,
        520,
        573,
        604,
        627,
        711,
        773,
        788,
        821,
    ]
    assert vector[50] == pytest.approx(-0.629862666, abs=1e-7)
    assert vector[132] == pytest.approx(0.314931333, abs=1e-7)


@pytest.mark.asyncio
async def test_embed_text_uses_local_provider_without_network_or_redis(monkeypatch):
    settings = SimpleNamespace(
        embedding_provider="local",
        embedding_dimensions=1024,
        embedding_model="local-hash-v1",
        embedding_api_key="",
    )
    monkeypatch.setattr("app.rag.embedding.get_settings", lambda: settings)
    get_client = AsyncMock()
    get_cached = AsyncMock()
    monkeypatch.setattr("app.rag.embedding.get_client", get_client)
    monkeypatch.setattr("app.rag.embedding._get_cached_embedding", get_cached)

    vector = await embed_text("轻薄笔记本")

    assert vector == local_hash_embedding("轻薄笔记本", 1024)
    get_client.assert_not_awaited()
    get_cached.assert_not_awaited()
