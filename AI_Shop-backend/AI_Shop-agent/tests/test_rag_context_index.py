import hashlib
import json

import pytest

from app.rag.retriever import evaluation_es_index_scope
from benchmarks.mature_eval.rag_context_index import (
    context_index_fingerprint,
    context_text,
    prepare_context_index,
)


def test_context_ablation_uses_original_content_for_evidence():
    doc = {
        "content": "前缀\n\n原始退款规则",
        "metadata": {
            "originalContent": "原始退款规则",
            "contextPrefix": "这是生成的检索前缀",
        },
    }
    assert context_text(doc, "original") == "原始退款规则"
    assert context_text(doc, "context_prefix") == "这是生成的检索前缀\n\n原始退款规则"


def test_context_index_fingerprint_is_stable_and_index_scope_is_protected():
    assert context_index_fingerprint("knowledge", "original", ["a", "b"]) == context_index_fingerprint("knowledge", "original", ["a", "b"])
    with evaluation_es_index_scope("aishop_eval_rag_original") as value:
        assert value == "aishop_eval_rag_original"
    with pytest.raises(ValueError):
        with evaluation_es_index_scope("aishop_vectorstore"):
            pass


def test_context_index_fingerprint_changes_with_source_content():
    assert context_index_fingerprint("knowledge", "original", ["1:aaa"]) != (
        context_index_fingerprint("knowledge", "original", ["1:bbb"])
    )


class _Response:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _ContextClient:
    def __init__(self, *, existing=False, fingerprint=None, count=0):
        self.existing = existing
        self.fingerprint = fingerprint
        self.count = count
        self.deleted = False
        self.created = False
        self.bulk = False

    async def head(self, *_args, **_kwargs):
        return _Response(status_code=200 if self.existing else 404)

    async def get(self, url, **_kwargs):
        if url.endswith("/_mapping"):
            return _Response(
                {
                    "aishop_eval_rag_original": {
                        "mappings": {
                            "_meta": {
                                "aishopEvaluation": {
                                    "fingerprint": self.fingerprint
                                }
                            }
                        }
                    }
                }
            )
        return _Response({"count": self.count})

    async def delete(self, *_args, **_kwargs):
        self.deleted = True
        return _Response()

    async def put(self, *_args, **_kwargs):
        self.created = True
        return _Response()

    async def post(self, *_args, **_kwargs):
        self.bulk = True
        return _Response({"errors": False})


@pytest.mark.asyncio
async def test_context_index_recovers_only_matching_incomplete_eval_index(
    monkeypatch,
):
    import benchmarks.mature_eval.rag_context_index as module

    source = {
        "content": "前缀\n\n原始退款规则",
        "metadata": {
            "originalContent": "原始退款规则",
            "contextPrefix": "前缀",
            "dataType": "knowledge",
        },
    }
    identity = "doc-1:" + hashlib.sha256(
        json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    fingerprint = context_index_fingerprint(
        "aishop_vectorstore", "original", [identity]
    )
    client = _ContextClient(
        existing=True, fingerprint=fingerprint, count=0
    )

    async def source_documents(_index, *, limit):
        assert limit == 500
        return [{"id": "doc-1", "source": source}]

    async def source_mapping(_index):
        return {"properties": {"embedding": {"type": "dense_vector"}}}

    async def get_client(*_args, **_kwargs):
        return client

    async def embeddings(texts, *, batch_size):
        assert texts == ["原始退款规则"]
        assert batch_size == 10
        return [[0.0] * 1024]

    monkeypatch.setattr(module, "_source_documents", source_documents)
    monkeypatch.setattr(module, "_source_mapping", source_mapping)
    monkeypatch.setattr(module, "get_client", get_client)
    monkeypatch.setattr(module, "embed_texts", embeddings)

    result = await prepare_context_index(
        source_index="aishop_vectorstore",
        target_index="aishop_eval_rag_original",
        mode="original",
    )

    assert result["reused"] is False
    assert client.deleted is True
    assert client.created is True
    assert client.bulk is True
