from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import get_settings
from app.memory.assistant_condense import condense_assistant_for_history
from app.memory.compress_service import CompressService
from app.memory.models import SessionMemory
from app.rag.query_rewriter import rewrite_for_rag


@pytest.mark.asyncio
async def test_query_rewriter_skips_http_when_chat_llm_key_is_missing(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    get_settings.cache_clear()
    get_client = AsyncMock(side_effect=AssertionError("HTTP must not be created"))
    monkeypatch.setattr("app.rag.query_rewriter.get_client", get_client)
    memory = SessionMemory(
        user_id="u1",
        summary={"narrative": "用户正在比较两款降噪耳机"},
    )

    try:
        rewritten = await rewrite_for_rag("它支持多设备连接吗", memory)
    finally:
        get_settings.cache_clear()

    assert rewritten == "它支持多设备连接吗"
    get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_assistant_condense_uses_bounded_fallback_without_llm_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "")
    get_settings.cache_clear()
    create_llm = AsyncMock(side_effect=AssertionError("LLM must not be created"))
    monkeypatch.setattr(
        "app.memory.assistant_condense.create_memory_llm",
        create_llm,
    )

    try:
        condensed = await condense_assistant_for_history("a" * 800, max_len=500)
    finally:
        get_settings.cache_clear()

    assert condensed == "a" * 500
    create_llm.assert_not_called()


@pytest.mark.asyncio
async def test_session_compress_is_not_scheduled_without_llm_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "")
    get_settings.cache_clear()
    create_task = MagicMock(side_effect=AssertionError("background task must not be created"))
    monkeypatch.setattr("app.memory.compress_service.asyncio.create_task", create_task)
    monkeypatch.setattr(
        "app.memory.compress_service.context_builder.estimate_context_tokens",
        lambda *_args: 20_000,
    )
    memory = SessionMemory(user_id="u1")

    try:
        await CompressService().maybe_schedule_compress(
            user_id="u1",
            memory=memory,
            working_turns=[],
            working_oldest_id=10,
            user_text="需要压缩的长对话",
            system_prompt="system",
        )
    finally:
        get_settings.cache_clear()

    assert memory.state["estimatedTokens"] == 20_000
    create_task.assert_not_called()
