"""Build isolated Elasticsearch indices for Contextual Retrieval ablation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.rag.embedding import embed_texts, embedding_evaluation_scope
from benchmarks.mature_eval.common import require_eval_index


def _base_url() -> str:
    return get_settings().es_hosts.split(",")[0].rstrip("/")


def _url(index: str, suffix: str = "") -> str:
    return f"{_base_url()}/{index}{suffix}"


def _content(doc: Mapping[str, Any]) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), Mapping) else {}
    return str(metadata.get("originalContent") or doc.get("content") or doc.get("text") or "").strip()


def _context_prefix(doc: Mapping[str, Any]) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), Mapping) else {}
    return str(metadata.get("contextPrefix") or "").strip()


def context_text(doc: Mapping[str, Any], mode: str) -> str:
    if mode not in {"original", "context_prefix"}:
        raise ValueError("context mode must be original or context_prefix")
    original = _content(doc)
    if mode == "original":
        return original
    prefix = _context_prefix(doc)
    return f"{prefix}\n\n{original}".strip() if prefix else original


def context_index_fingerprint(
    source_index: str, mode: str, document_identities: Sequence[str]
) -> str:
    payload = json.dumps(
        {
            "sourceIndex": source_index,
            "mode": mode,
            "documents": list(document_identities),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _source_mapping(source_index: str) -> dict[str, Any]:
    client = await get_client("mature_eval_es", timeout=30)
    response = await client.get(_url(source_index, "/_mapping"), timeout=20)
    response.raise_for_status()
    payload = response.json() or {}
    row = payload.get(source_index) or {}
    mapping = row.get("mappings") or {}
    if not isinstance(mapping, dict):
        raise ValueError("source Elasticsearch mapping is invalid")
    return mapping


async def _source_documents(source_index: str, *, limit: int = 500) -> list[dict[str, Any]]:
    client = await get_client("mature_eval_es", timeout=30)
    body = {
        "size": min(max(limit, 1), 10_000),
        "query": {
            "bool": {
                "filter": [
                    {"terms": {"metadata.dataType.keyword": ["knowledge", "faq"]}},
                ]
            }
        },
        "_source": True,
    }
    response = await client.post(_url(source_index, "/_search"), json=body, timeout=30)
    response.raise_for_status()
    hits = (response.json() or {}).get("hits", {}).get("hits", [])
    rows: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        source = hit.get("_source")
        if not isinstance(source, Mapping):
            continue
        rows.append({"id": str(hit.get("_id") or ""), "source": dict(source)})
    return sorted(
        (row for row in rows if row["id"] and _content(row["source"])),
        key=lambda row: row["id"],
    )


async def prepare_context_index(
    *,
    source_index: str,
    target_index: str,
    mode: str,
    limit: int = 500,
) -> dict[str, Any]:
    """Copy published knowledge into a prefixed eval index with fresh vectors.

    Only ``aishop_eval_`` targets are accepted.  Production indices are read
    but never deleted or mutated.
    """

    require_eval_index(target_index)
    if mode not in {"original", "context_prefix"}:
        raise ValueError("context mode must be original or context_prefix")
    if target_index == source_index:
        raise ValueError("context evaluation target must differ from source index")
    docs = await _source_documents(source_index, limit=limit)
    if not docs:
        raise RuntimeError("source knowledge index returned no documents")
    document_identities = [
        f"{row['id']}:{hashlib.sha256(json.dumps(row['source'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()}"
        for row in docs
    ]
    fingerprint = context_index_fingerprint(
        source_index, mode, document_identities
    )
    mapping = await _source_mapping(source_index)
    mapping = dict(mapping)
    mapping["_meta"] = {
        "aishopEvaluation": {
            "kind": "rag-context-ablation",
            "sourceIndex": source_index,
            "mode": mode,
            "fingerprint": fingerprint,
        }
    }
    client = await get_client("mature_eval_es", timeout=30)
    exists = await client.head(_url(target_index), timeout=15)
    if exists.status_code == 200:
        existing_mapping = await client.get(_url(target_index, "/_mapping"), timeout=20)
        existing_mapping.raise_for_status()
        existing_payload = existing_mapping.json() or {}
        existing_meta = (
            ((existing_payload.get(target_index) or {}).get("mappings") or {}).get("_meta")
            or {}
        )
        current = (existing_meta.get("aishopEvaluation") or {}).get("fingerprint")
        if current != fingerprint:
            raise RuntimeError(
                f"target index {target_index} already belongs to another context dataset"
            )
        count_response = await client.get(_url(target_index, "/_count"), timeout=15)
        count_response.raise_for_status()
        count = int((count_response.json() or {}).get("count") or 0)
        if count == len(docs):
            return {
                "index": target_index,
                "mode": mode,
                "sourceIndex": source_index,
                "documentCount": len(docs),
                "fingerprint": fingerprint,
                "reused": True,
            }
        # A prior interrupted build may leave only part of an evaluation index.
        # The prefix and fingerprint have already been validated, so rebuilding
        # this exact target cannot affect production data.
        deleted = await client.delete(_url(target_index), timeout=30)
        deleted.raise_for_status()
    if exists.status_code != 404:
        exists.raise_for_status()
    created = await client.put(
        _url(target_index),
        json={"settings": {"number_of_shards": 1, "number_of_replicas": 0}, "mappings": mapping},
        timeout=30,
    )
    created.raise_for_status()

    texts = [context_text(row["source"], mode) for row in docs]
    with embedding_evaluation_scope(bypass_cache=True) as stats:
        vectors = await embed_texts(texts, batch_size=10)
        provider_facts = stats.snapshot()
    if len(vectors) != len(docs) or any(
        not vector or len(vector) != get_settings().embedding_dimensions for vector in vectors
    ):
        raise RuntimeError("context index embedding output is incomplete")
    vector_field = get_settings().es_vector_field
    bulk_lines: list[str] = []
    for row, text, vector in zip(docs, texts, vectors):
        source = dict(row["source"])
        metadata = dict(source.get("metadata") or {})
        original = _content(source)
        metadata["originalContent"] = original
        metadata["contextEnriched"] = mode == "context_prefix"
        source["metadata"] = metadata
        source["content"] = text
        source["text"] = text
        source[vector_field] = vector
        bulk_lines.extend(
            [
                json.dumps({"index": {"_index": target_index, "_id": row["id"]}}),
                json.dumps(source, ensure_ascii=False, separators=(",", ":")),
            ]
        )
    response = await client.post(
        f"{_base_url()}/_bulk",
        content=("\n".join(bulk_lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        params={"refresh": "wait_for"},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json() or {}
    if payload.get("errors"):
        raise RuntimeError("context evaluation bulk indexing failed")
    return {
        "index": target_index,
        "mode": mode,
        "sourceIndex": source_index,
        "documentCount": len(docs),
        "fingerprint": fingerprint,
        "providerFacts": provider_facts,
        "reused": False,
    }
