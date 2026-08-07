from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import get_settings
from app.rag.image_describer import describe_image


@pytest.mark.asyncio
async def test_vlm_disabled_fails_open_to_text_only(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vlm_api_key", "")

    assert await describe_image("https://internal.example/support.jpg") is None


@pytest.mark.asyncio
async def test_vlm_timeout_or_provider_error_fails_open(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vlm_api_key", "test-key")
    client = AsyncMock()
    client.post.side_effect = TimeoutError("provider timeout")
    monkeypatch.setattr("app.rag.image_describer.get_client", AsyncMock(return_value=client))

    assert await describe_image("https://internal.example/support.jpg") is None


@pytest.mark.asyncio
async def test_vlm_description_is_plain_text_and_trimmed(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vlm_api_key", "test-key")
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": "  包装边角有明显裂痕。  "}}]
    }
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.post.return_value = response
    monkeypatch.setattr("app.rag.image_describer.get_client", AsyncMock(return_value=client))

    assert await describe_image("https://internal.example/support.jpg") == "包装边角有明显裂痕。"
    body = client.post.await_args.kwargs["json"]
    assert body["temperature"] == 0
    assert body["messages"][1]["content"][0]["type"] == "image_url"
