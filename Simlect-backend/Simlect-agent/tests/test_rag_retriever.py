import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config.settings import get_settings
from app.rag.retriever import RagRetriever


@pytest.mark.asyncio
async def test_exact_faq_fast_path_returns_traceable_source(monkeypatch):
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_knowledge_version", AsyncMock(return_value=7))
    monkeypatch.setattr(
        retriever,
        "_exact_faq",
        AsyncMock(
            return_value={
                "question": "发票在哪里申请",
                "answer": "请在订单详情页申请发票。",
                "question_id": 12,
                "category": "invoice",
                "source": "ADMIN",
            }
        ),
    )

    result = await retriever.exact_faq_answer("发票在哪里申请？")

    assert result == {
        "answer": "请在订单详情页申请发票。",
        "question": "发票在哪里申请",
        "questionId": 12,
        "category": "invoice",
        "source": "ADMIN",
        "version": 7,
    }


@pytest.mark.asyncio
async def test_exact_faq_fast_path_times_out_without_blocking_chat(monkeypatch):
    monkeypatch.setenv("FAQ_FAST_PATH_TIMEOUT_SECONDS", "0.01")
    get_settings.cache_clear()
    retriever = RagRetriever()
    monkeypatch.setattr(retriever, "_knowledge_version", AsyncMock(return_value=1))

    async def slow_exact(*_args):
        await asyncio.sleep(0.1)
        return {"answer": "不应返回"}

    monkeypatch.setattr(retriever, "_exact_faq", slow_exact)
    try:
        assert await retriever.exact_faq_answer("配送范围是什么") is None
    finally:
        get_settings.cache_clear()
