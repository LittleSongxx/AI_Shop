"""Isolated Elasticsearch index management for maturity evaluations."""

from __future__ import annotations

import json
from typing import Any, Iterable

from app.infra.http_client import get_client
from benchmarks.mature_eval.common import require_eval_index

WANDS_EMBEDDING_MAX_CHARS = 6_000


def product_text(product: dict[str, Any], *, dataset: str) -> str:
    if dataset == "wands":
        return "\n".join(
            str(product.get(key) or "").strip()
            for key in (
                "product_name",
                "product_class",
                "category hierarchy",
                "product_description",
                "product_features",
            )
            if str(product.get(key) or "").strip()
        )
    attributes = "，".join(
        f"{key}:{value}" for key, value in sorted((product.get("attributes") or {}).items())
    )
    return "。".join(
        value
        for value in (
            str(product.get("name") or "").strip(),
            str(product.get("description") or "").strip(),
            f"品牌:{product.get('brand')}",
            f"场景:{product.get('scenario')}",
            f"人群:{product.get('audience')}",
            attributes,
        )
        if value
    )


def product_embedding_text(product: dict[str, Any], *, dataset: str) -> str:
    text = product_text(product, dataset=dataset)
    if dataset == "wands":
        return text[:WANDS_EMBEDDING_MAX_CHARS]
    return text


def index_document(
    product: dict[str, Any],
    *,
    dataset: str,
    embedding: list[float],
) -> tuple[str, dict[str, Any]]:
    product_id = str(product.get("id") or product.get("product_id") or "")
    if not product_id:
        raise ValueError("evaluation product lacks an ID")
    if len(embedding) != 1024:
        raise ValueError(f"evaluation product {product_id} embedding must have 1024 dimensions")
    if dataset == "wands":
        name = str(product.get("product_name") or "")
        category = str(product.get("product_class") or "")
    else:
        name = str(product.get("name") or "")
        category = str(product.get("category") or "")
    return product_id, {
        "productId": product_id,
        "productName": name,
        "productDesc": product_text(product, dataset=dataset),
        "brand": str(product.get("brand") or ""),
        "category": category,
        "dataset": dataset,
        "embedding": embedding,
    }


class EvaluationIndexManager:
    def __init__(self, index: str, *, es_hosts: str, dimensions: int = 1024) -> None:
        self.index = require_eval_index(index)
        self.base_url = es_hosts.split(",")[0].rstrip("/")
        self.dimensions = int(dimensions)
        if self.dimensions != 1024:
            raise ValueError("maturity evaluation indices are locked to 1024 dimensions")

    def url(self, suffix: str = "") -> str:
        normalized = suffix if not suffix or suffix.startswith("/") else f"/{suffix}"
        return f"{self.base_url}/{self.index}{normalized}"

    @property
    def mapping(self) -> dict[str, Any]:
        return {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "productId": {"type": "keyword"},
                    "productName": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                    },
                    "productDesc": {"type": "text"},
                    "brand": {"type": "text"},
                    "category": {"type": "keyword"},
                    "dataset": {"type": "keyword"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": self.dimensions,
                        "index": True,
                        "similarity": "cosine",
                    },
                },
            },
        }

    async def ensure(self) -> dict[str, Any]:
        client = await get_client("mature_eval_es", timeout=30)
        response = await client.head(self.url(), timeout=15)
        if response.status_code == 404:
            created = await client.put(self.url(), json=self.mapping, timeout=30)
            created.raise_for_status()
        elif response.status_code >= 400:
            response.raise_for_status()
        mapping = await client.get(self.url("/_mapping"), timeout=15)
        mapping.raise_for_status()
        properties = (((mapping.json() or {}).get(self.index) or {}).get("mappings") or {}).get(
            "properties"
        ) or {}
        dims = ((properties.get("embedding") or {}).get("dims"))
        if dims != self.dimensions:
            raise RuntimeError(f"evaluation index {self.index} has embedding dims={dims}")
        return {"index": self.index, "dimensions": dims}

    async def count(self) -> int:
        client = await get_client("mature_eval_es", timeout=15)
        response = await client.get(self.url("/_count"), timeout=15)
        response.raise_for_status()
        return int((response.json() or {}).get("count") or 0)

    async def existing_ids(self, ids: Iterable[str]) -> set[str]:
        values = list(dict.fromkeys(str(value) for value in ids if str(value)))
        if not values:
            return set()
        client = await get_client("mature_eval_es", timeout=30)
        found: set[str] = set()
        for start in range(0, len(values), 500):
            response = await client.post(
                self.url("/_mget"),
                json={"ids": values[start : start + 500]},
                params={"_source": "false"},
                timeout=30,
            )
            response.raise_for_status()
            found.update(str(row["_id"]) for row in response.json().get("docs", []) if row.get("found"))
        return found

    async def bulk_upsert(self, documents: Iterable[tuple[str, dict[str, Any]]]) -> int:
        rows = list(documents)
        if not rows:
            return 0
        client = await get_client("mature_eval_es", timeout=60)
        indexed = 0
        for start in range(0, len(rows), 200):
            lines: list[str] = []
            batch = rows[start : start + 200]
            for document_id, document in batch:
                lines.append(json.dumps({"index": {"_index": self.index, "_id": document_id}}))
                lines.append(json.dumps(document, ensure_ascii=False, separators=(",", ":")))
            response = await client.post(
                f"{self.base_url}/_bulk",
                content=("\n".join(lines) + "\n").encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
                params={"refresh": "wait_for"},
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                failures = [item for item in payload.get("items", []) if (item.get("index") or {}).get("error")]
                raise RuntimeError(f"evaluation bulk indexing failed: {failures[:2]}")
            indexed += len(batch)
        return indexed
