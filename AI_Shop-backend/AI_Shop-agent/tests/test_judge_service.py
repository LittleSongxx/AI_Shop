from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config.settings import get_settings
from app.services.judge_service import JudgeService, _parse_result


def test_judge_result_parser_requires_all_bounded_dimensions():
    result = _parse_result(
        """```json
        {"groundedness":0.8,"relevance":0.9,"completeness":0.7,
         "constraintCompliance":1.0,"reason":"依据充分"}
        ```"""
    )

    assert result["groundedness"] == 0.8
    assert result["reason"] == "依据充分"
    with pytest.raises((KeyError, ValueError)):
        _parse_result('{"groundedness": 2}')


@pytest.mark.asyncio
async def test_judge_queue_is_bounded_and_drains_without_touching_user_path(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "judge_model", "judge-test")
    monkeypatch.setattr(settings, "judge_api_key", "test-only")
    monkeypatch.setattr(settings, "judge_queue_size", 10)
    monkeypatch.setattr(settings, "judge_sample_rate", 1.0)
    service = JudgeService()
    evaluate = AsyncMock()
    monkeypatch.setattr(service, "_evaluate", evaluate)

    await service.start()
    queued = service.enqueue(
        run_id="run-judge-1",
        message_id=1,
        user_text="问题",
        assistant="答案",
        intent="CHAT",
        tools_called=[],
        source_refs=[],
        verifier_passed=True,
    )
    await asyncio.sleep(0)
    await service.close()

    assert queued is True
    evaluate.assert_awaited_once()


def test_judge_enqueue_returns_false_when_shadow_path_is_disabled():
    service = JudgeService()

    assert service.enqueue(
        run_id="run-judge-1",
        message_id=1,
        user_text="问题",
        assistant="答案",
        intent="CHAT",
        tools_called=[],
        source_refs=[],
        verifier_passed=True,
    ) is False
