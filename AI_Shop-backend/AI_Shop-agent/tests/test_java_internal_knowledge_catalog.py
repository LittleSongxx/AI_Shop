from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.java_internal_client import JavaInternalClient


@pytest.mark.asyncio
async def test_historical_knowledge_catalog_passes_release_version_and_metadata(
    monkeypatch,
):
    client = JavaInternalClient()
    post_json = AsyncMock(
        return_value={
            "version": 17,
            "releaseName": "knowledge-v1",
            "catalogSha256": "a" * 64,
            "sourceReleaseVersion": 9,
            "activeDocumentIds": ["11"],
            "documents": [
                {
                    "documentId": "11",
                    "sourceName": "support.md",
                    "contentHash": "b" * 64,
                    "version": 1,
                    "domain": "SUPPORT",
                    "chunkCount": 4,
                    "indexSchemaVersion": 1,
                }
            ],
        }
    )
    monkeypatch.setattr(client, "post_json", post_json)

    result = await client.knowledge_catalog(release_version=17)

    post_json.assert_awaited_once_with(
        "/internal/search/knowledge/catalog", {"releaseVersion": 17}
    )
    assert result["version"] == 17
    assert result["release_name"] == "knowledge-v1"
    assert result["catalog_sha256"] == "a" * 64
    assert result["source_release_version"] == 9
    assert result["active_document_ids"] == ["11"]


@pytest.mark.asyncio
async def test_historical_knowledge_catalog_rejects_non_positive_version():
    with pytest.raises(ValueError, match="positive"):
        await JavaInternalClient().knowledge_catalog(release_version=0)
