from unittest.mock import AsyncMock

import pytest

from benchmarks.mature_eval import indexing
from benchmarks.mature_eval.common import require_eval_index
from benchmarks.mature_eval.indexing import (
    WANDS_EMBEDDING_MAX_CHARS,
    EvaluationIndexManager,
    index_document,
    product_embedding_text,
)


def test_eval_index_prefix_protects_production_indices():
    assert require_eval_index("aishop_eval_chinese_v1") == "aishop_eval_chinese_v1"
    for unsafe in ("aishop-index", "aishop_vectorstore", "dk-document", ""):
        with pytest.raises(ValueError, match="aishop_eval_"):
            require_eval_index(unsafe)


def test_index_mapping_and_document_are_locked_to_1024_dimensions():
    manager = EvaluationIndexManager(
        "aishop_eval_fixture", es_hosts="http://localhost:9200"
    )
    assert manager.mapping["mappings"]["properties"]["embedding"]["dims"] == 1024
    product_id, document = index_document(
        {
            "id": "zh-earphone-01",
            "name": "测试耳机",
            "description": "测试描述",
            "category": "earphone",
            "brand": "澄屿",
            "scenario": "通勤",
            "audience": "上班族",
            "attributes": {"wear": "入耳式"},
        },
        dataset="chinese",
        embedding=[0.0] * 1024,
    )
    assert product_id == "zh-earphone-01"
    assert document["dataset"] == "chinese"
    assert len(document["embedding"]) == 1024

    with pytest.raises(ValueError, match="1024"):
        index_document(
            {"id": "broken", "name": "broken"},
            dataset="chinese",
            embedding=[0.0] * 3,
        )


def test_wands_embedding_text_is_bounded():
    product = {
        "product_name": "name",
        "product_class": "class",
        "product_description": "x" * (WANDS_EMBEDDING_MAX_CHARS + 500),
    }

    assert len(product_embedding_text(product, dataset="wands")) == WANDS_EMBEDDING_MAX_CHARS


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeClient:
    def __init__(self):
        self.head = AsyncMock(side_effect=[FakeResponse(status_code=404), FakeResponse()])
        self.put = AsyncMock(return_value=FakeResponse({"acknowledged": True}))
        self.get = AsyncMock(
            return_value=FakeResponse(
                {
                    "aishop_eval_fixture": {
                        "mappings": {"properties": {"embedding": {"dims": 1024}}}
                    }
                }
            )
        )
        self.post = AsyncMock(
            return_value=FakeResponse(
                {"docs": [{"_id": "existing", "found": True}]}
            )
        )


@pytest.mark.asyncio
async def test_ensure_is_idempotent_and_never_deletes(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(indexing, "get_client", AsyncMock(return_value=client))
    manager = EvaluationIndexManager(
        "aishop_eval_fixture", es_hosts="http://localhost:9200"
    )

    await manager.ensure()
    await manager.ensure()

    assert client.put.await_count == 1
    assert not hasattr(client, "delete")
    assert all("aishop_eval_fixture" in call.args[0] for call in client.head.await_args_list)


@pytest.mark.asyncio
async def test_existing_ids_uses_elasticsearch_9_compatible_source_parameter(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(indexing, "get_client", AsyncMock(return_value=client))
    manager = EvaluationIndexManager(
        "aishop_eval_fixture", es_hosts="http://localhost:9200"
    )

    found = await manager.existing_ids(["existing", "missing"])

    assert found == {"existing"}
    request = client.post.await_args
    assert request.kwargs["json"] == {"ids": ["existing", "missing"]}
    assert request.kwargs["params"] == {"_source": "false"}
