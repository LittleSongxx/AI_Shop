from types import SimpleNamespace

import pytest

from scripts.check_online_models import run


def _settings(*, rerank_api_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        llm_api_key="",
        embedding_api_key="",
        rerank_api_key=rerank_api_key,
        embedding_dimensions=1024,
    )


@pytest.mark.asyncio
async def test_rerank_component_does_not_require_other_provider_keys(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: _settings(rerank_api_key="configured-key"),
    )

    class _Retriever:
        async def _rerank(self, *_args, **_kwargs):
            return [{"id": "model-check", "score": 0.9, "source": "rerank"}]

    monkeypatch.setattr("app.rag.retriever.RagRetriever", _Retriever)

    assert await run(("rerank",)) == {"rerank": {"status": "ok", "count": 1}}


@pytest.mark.asyncio
async def test_rerank_component_rejects_silent_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: _settings(rerank_api_key="configured-key"),
    )

    class _Retriever:
        async def _rerank(self, *_args, **_kwargs):
            return [{"id": "model-check", "score": 0.1}]

    monkeypatch.setattr("app.rag.retriever.RagRetriever", _Retriever)

    with pytest.raises(RuntimeError, match="used its fallback"):
        await run(("rerank",))


@pytest.mark.asyncio
async def test_rerank_component_reports_only_its_missing_key(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: _settings(rerank_api_key=""),
    )

    with pytest.raises(RuntimeError, match=r"missing provider credentials: RERANK_API_KEY$"):
        await run(("rerank",))
