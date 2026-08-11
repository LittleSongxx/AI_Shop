from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.resilience.circuit_breaker import CircuitBreaker, CircuitState
from app.visual.contracts import VisualProviderError
from app.visual.provider import (
    VisualProvider,
    _embedding_vector,
    _parse_grounding_payload,
    _rerank_items,
)


def _chat_payload(value: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": f"```json\n{json.dumps(value, ensure_ascii=False)}\n```"
                }
            }
        ]
    }


def test_grounding_parser_accepts_only_strict_non_overlapping_integer_boxes():
    subjects = _parse_grounding_payload(
        _chat_payload(
            {
                "subjects": [
                    {"label": "运动鞋", "bbox": [20, 30, 420, 480]},
                    {"label": "小数坐标", "bbox": [20.5, 30, 420, 480]},
                    {"label": "越界坐标", "bbox": [-1, 20, 400, 500]},
                    {"label": "面积太小", "bbox": [10, 10, 50, 50]},
                    {"label": "重复框", "bbox": [22, 32, 418, 478]},
                    {"label": "背包", "bbox": [520, 100, 920, 800]},
                ]
            }
        ),
        max_subjects=5,
    )

    assert [subject.label for subject in subjects] == ["运动鞋", "背包"]
    assert [subject.subject_id for subject in subjects] == ["subject_1", "subject_2"]
    assert subjects[0].bbox == (20, 30, 420, 480)


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"output": {"embeddings": [{"embedding": [0.1]}]}}, "DIMENSION_MISMATCH"),
        (
            {"output": {"embeddings": [{"embedding": [0.1, float("nan")]}]}},
            "INVALID_VECTOR",
        ),
        ({"output": {"embeddings": [{"embedding": [0.1, "0.2"]}]}}, "INVALID_VECTOR"),
        ({"output": {"embeddings": [{"embedding": [0.1, True]}]}}, "INVALID_VECTOR"),
    ],
)
def test_embedding_parser_rejects_wrong_dimension_and_non_numeric_values(payload, code):
    with pytest.raises(VisualProviderError, match=code):
        _embedding_vector(payload, 2)


def test_rerank_parser_rejects_forged_indexes_and_keeps_valid_order():
    items = _rerank_items(
        {
            "output": {
                "results": [
                    {"index": 2, "relevance_score": 0.91},
                    {"index": 1.5, "relevance_score": 0.99},
                    {"index": "1", "relevance_score": 0.98},
                    {"index": 3, "relevance_score": 0.97},
                    {"index": 0, "relevance_score": float("nan")},
                    {"index": 2, "relevance_score": 0.80},
                    {"index": 1, "relevance_score": 0.75},
                ]
            }
        },
        document_count=3,
    )

    assert [(item.index, item.relevance_score) for item in items] == [
        (2, 0.91),
        (1, 0.75),
    ]


def test_rerank_parser_fails_closed_when_every_item_is_invalid():
    with pytest.raises(VisualProviderError, match="EMPTY_RESPONSE"):
        _rerank_items(
            {"output": {"results": [{"index": 7, "relevance_score": 0.8}]}},
            document_count=2,
        )


class _SequenceClient:
    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def _response(status: int, payload: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload or {},
        request=httpx.Request("POST", "https://provider.example/v1"),
    )


@pytest.mark.asyncio
async def test_provider_retries_one_retryable_response_then_succeeds(monkeypatch):
    client = _SequenceClient([_response(429), _response(200, {"output": {}})])
    breaker = CircuitBreaker("visual-provider-test", failure_threshold=5, recovery_timeout=60)
    monkeypatch.setattr(
        "app.visual.provider.get_settings",
        lambda: SimpleNamespace(visual_provider_deadline_seconds=2.0),
    )
    monkeypatch.setattr("app.visual.provider.get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        "app.visual.provider.circuit_registry.get_or_create", lambda *_args, **_kwargs: breaker
    )
    monkeypatch.setattr("app.visual.provider.random.uniform", lambda *_args: 0.0)

    payload, attempts, state = await VisualProvider()._post_with_resilience(
        "embedding",
        "https://provider.example/v1",
        {"input": {}},
        api_key="secret",
        timeout=1.0,
    )

    assert payload == {"output": {}}
    assert attempts == 2
    assert client.calls == 2
    assert state == CircuitState.CLOSED.value
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_provider_opens_circuit_after_five_failed_logical_requests(monkeypatch):
    client = _SequenceClient([_response(503) for _ in range(10)])
    breaker = CircuitBreaker("visual-provider-trip-test", failure_threshold=5, recovery_timeout=60)
    monkeypatch.setattr(
        "app.visual.provider.get_settings",
        lambda: SimpleNamespace(visual_provider_deadline_seconds=2.0),
    )
    monkeypatch.setattr("app.visual.provider.get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        "app.visual.provider.circuit_registry.get_or_create", lambda *_args, **_kwargs: breaker
    )
    monkeypatch.setattr("app.visual.provider.random.uniform", lambda *_args: 0.0)
    provider = VisualProvider()

    for _ in range(5):
        with pytest.raises(VisualProviderError, match="TEMPORARILY_UNAVAILABLE"):
            await provider._post_with_resilience(
                "rerank",
                "https://provider.example/v1",
                {},
                api_key="secret",
                timeout=1.0,
            )

    assert breaker.state == CircuitState.OPEN
    assert client.calls == 10
    with pytest.raises(VisualProviderError, match="CIRCUIT_OPEN"):
        await provider._post_with_resilience(
            "rerank",
            "https://provider.example/v1",
            {},
            api_key="secret",
            timeout=1.0,
        )
    assert client.calls == 10


@pytest.mark.asyncio
async def test_provider_does_not_retry_non_retryable_4xx(monkeypatch):
    client = _SequenceClient([_response(400, {"message": "bad request"})])
    breaker = CircuitBreaker("visual-provider-4xx-test")
    monkeypatch.setattr(
        "app.visual.provider.get_settings",
        lambda: SimpleNamespace(visual_provider_deadline_seconds=2.0),
    )
    monkeypatch.setattr("app.visual.provider.get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(
        "app.visual.provider.circuit_registry.get_or_create", lambda *_args, **_kwargs: breaker
    )

    with pytest.raises(VisualProviderError, match="REQUEST_REJECTED"):
        await VisualProvider()._post_with_resilience(
            "grounding",
            "https://provider.example/v1",
            {},
            api_key="secret",
            timeout=1.0,
        )

    assert client.calls == 1
    assert breaker.state == CircuitState.CLOSED
