from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx
import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import circuit_registry
from app.visual.contracts import VisualIndexHit

logger = structlog.get_logger()


class VisualIndexError(RuntimeError):
    pass


class VisualProductIndex:
    def _url(self, path: str) -> str:
        base = get_settings().es_hosts.split(",")[0].rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def target_index_name(self) -> str:
        settings = get_settings()
        version = re.sub(r"[^a-z0-9_-]+", "-", settings.visual_index_model_version.lower())
        return f"{settings.visual_index_prefix}{version}"[:255]

    async def ensure_target_index(self, index_name: str | None = None) -> str:
        target = index_name or self.target_index_name()
        client = await get_client("visual_es", timeout=15)
        response = await client.head(self._url(target), timeout=10)
        if response.status_code == 404:
            create = await client.put(
                self._url(target), json=self._mapping(), timeout=30
            )
            if create.status_code not in {200, 201}:
                raise VisualIndexError(f"VISUAL_INDEX_CREATE_FAILED:{create.status_code}")
        elif response.status_code >= 400:
            raise VisualIndexError(f"VISUAL_INDEX_CHECK_FAILED:{response.status_code}")
        return target

    async def ensure_alias(self) -> str:
        settings = get_settings()
        target = await self.ensure_target_index()
        client = await get_client("visual_es", timeout=15)
        response = await client.get(
            self._url(f"_alias/{settings.visual_index_alias}"), timeout=10
        )
        if response.status_code == 404:
            await self.activate(target, require_documents=False)
            return target
        response.raise_for_status()
        aliases = response.json()
        if not isinstance(aliases, dict) or not aliases:
            raise VisualIndexError("VISUAL_INDEX_ALIAS_INVALID")
        return next(iter(aliases))

    async def status(self) -> dict[str, Any]:
        """Return a small, non-sensitive readiness snapshot for operations.

        This deliberately creates the versioned target mapping but never moves
        the serving alias. A new model version therefore keeps the old index
        live until a full backfill has passed its document-count check.
        """
        settings = get_settings()
        target = await self.ensure_target_index()
        client = await get_client("visual_es", timeout=15)
        alias_response = await client.get(
            self._url(f"_alias/{settings.visual_index_alias}"), timeout=10
        )
        alias_index: str | None = None
        if alias_response.status_code == 200:
            aliases = alias_response.json()
            if isinstance(aliases, dict) and aliases:
                alias_index = next(iter(aliases))
        elif alias_response.status_code != 404:
            alias_response.raise_for_status()
        count = await self.document_count(target)
        return {
            "targetIndex": target,
            "alias": settings.visual_index_alias,
            "aliasIndex": alias_index,
            "targetDocumentCount": count,
            "servingCurrentModel": alias_index == target and count > 0,
            "modelVersion": settings.visual_index_model_version,
        }

    async def activate(self, target: str, *, require_documents: bool = True) -> None:
        await self.ensure_target_index(target)
        client = await get_client("visual_es", timeout=15)
        count_response = await client.get(self._url(f"{target}/_count"), timeout=15)
        count_response.raise_for_status()
        count = int(count_response.json().get("count") or 0)
        if require_documents and count <= 0:
            raise VisualIndexError("VISUAL_INDEX_EMPTY_NOT_ACTIVATED")

        settings = get_settings()
        alias_response = await client.get(
            self._url(f"_alias/{settings.visual_index_alias}"), timeout=10
        )
        current = (
            list(alias_response.json())
            if alias_response.status_code == 200 and isinstance(alias_response.json(), dict)
            else []
        )
        actions = [
            {"remove": {"index": index, "alias": settings.visual_index_alias}}
            for index in current
            if index != target
        ]
        actions.append(
            {"add": {"index": target, "alias": settings.visual_index_alias, "is_write_index": True}}
        )
        response = await client.post(
            self._url("_aliases"), json={"actions": actions}, timeout=20
        )
        response.raise_for_status()

    async def search_knn(
        self,
        vector: list[float],
        *,
        document_type: str,
        size: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VisualIndexHit]:
        clauses: list[dict] = [
            {"term": {"documentType": document_type}},
            {"term": {"status": 1}},
            *self._filters(filters),
        ]
        body = {
            "size": min(max(int(size), 1), 100),
            "knn": {
                "field": "embedding",
                "query_vector": vector,
                "k": min(max(int(size), 1), 100),
                "num_candidates": min(max(int(size) * 4, 100), 10_000),
                "filter": {"bool": {"filter": clauses}},
            },
            "_source": self._source_fields(),
        }
        return await self._search(body, f"{document_type.lower()}_knn")

    async def search_text(
        self,
        query: str,
        *,
        size: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VisualIndexHit]:
        if not query.strip():
            return []
        clauses = [{"term": {"status": 1}}, *self._filters(filters)]
        body = {
            "size": min(max(int(size), 1), 100),
            "query": {
                "bool": {
                    "filter": clauses,
                    "must": [
                        {
                            "multi_match": {
                                "query": query[:500],
                                "fields": ["productName^4", "brand^3", "productText^1.5"],
                                "type": "best_fields",
                            }
                        }
                    ],
                }
            },
            "collapse": {"field": "productId"},
            "_source": self._source_fields(),
        }
        return await self._search(body, "text")

    async def exact_hash_hits(
        self, hashes: list[str], *, filters: dict[str, Any] | None = None
    ) -> list[VisualIndexHit]:
        valid = [value for value in hashes if re.fullmatch(r"[a-f0-9]{64}", value or "")]
        if not valid:
            return []
        clauses = [
            {"term": {"documentType": "IMAGE"}},
            {"term": {"status": 1}},
            *self._filters(filters),
        ]
        body = {
            "size": 20,
            "query": {
                "bool": {
                    "filter": clauses,
                    "should": [
                        {"terms": {"imageSha256": valid}},
                        {"terms": {"normalizedSha256": valid}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": self._source_fields(),
        }
        return await self._search(body, "exact_hash")

    async def replace_product(
        self,
        product_id: str,
        product_version: int,
        documents: list[dict],
        *,
        index_name: str | None = None,
    ) -> None:
        target = index_name or await self.ensure_alias()
        if not documents:
            await self.delete_product(
                product_id,
                product_version=product_version,
                index_name=target,
            )
            return
        version = int(product_version)
        bulk_lines: list[str] = []
        new_ids: list[str] = []
        for document in documents:
            suffix = f"{document.get('documentType')}:{document.get('coverIndex', 'fused')}"
            digest = hashlib.sha256(
                f"{product_id}:{suffix}".encode("utf-8")
            ).hexdigest()[:32]
            document_id = f"visual_{digest}"
            new_ids.append(document_id)
            source = {**document, "productId": product_id, "productVersion": version}
            # A product event can be delivered late after a newer update has
            # already been indexed. Stable document IDs plus this guarded
            # scripted upsert prevent that stale event from overwriting the
            # authoritative newer snapshot.
            bulk_lines.append(
                json.dumps(
                    {
                        "update": {
                            "_index": target,
                            "_id": document_id,
                            "retry_on_conflict": 3,
                        }
                    }
                )
            )
            bulk_lines.append(
                json.dumps(
                    {
                        "scripted_upsert": True,
                        "script": {
                            "lang": "painless",
                            "source": (
                                "if (ctx.op == 'create' || params.version >= "
                                "ctx._source.productVersion) { "
                                "ctx._source = params.document; } else { ctx.op = 'none'; }"
                            ),
                            "params": {"version": version, "document": source},
                        },
                        "upsert": source,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        payload = "\n".join(bulk_lines) + "\n"
        client = await get_client("visual_es", timeout=30)
        response = await client.post(
            self._url("_bulk"),
            content=payload.encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errors"):
            await self._delete_ids(target, new_ids)
            raise VisualIndexError("VISUAL_INDEX_BULK_PARTIAL_FAILURE")
        cleanup = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"productId": product_id}},
                        {"range": {"productVersion": {"lte": version}}},
                    ],
                    "must_not": [{"terms": {"_id": new_ids}}],
                }
            }
        }
        cleanup_response = await client.post(
            self._url(f"{target}/_delete_by_query?conflicts=proceed&refresh=true"),
            json=cleanup,
            timeout=30,
        )
        cleanup_response.raise_for_status()

    async def delete_product(
        self,
        product_id: str,
        *,
        product_version: int | None = None,
        index_name: str | None = None,
    ) -> None:
        target = index_name or await self.ensure_alias()
        clauses: list[dict] = [{"term": {"productId": product_id}}]
        if product_version is not None:
            clauses.append({"range": {"productVersion": {"lte": int(product_version)}}})
        client = await get_client("visual_es", timeout=15)
        response = await client.post(
            self._url(f"{target}/_delete_by_query?conflicts=proceed&refresh=true"),
            json={"query": {"bool": {"filter": clauses}}},
            timeout=30,
        )
        if response.status_code != 404:
            response.raise_for_status()

    async def document_count(self, index_name: str | None = None) -> int:
        target = index_name or get_settings().visual_index_alias
        client = await get_client("visual_es", timeout=10)
        response = await client.get(self._url(f"{target}/_count"), timeout=10)
        response.raise_for_status()
        return int(response.json().get("count") or 0)

    async def _search(self, body: dict, source: str) -> list[VisualIndexHit]:
        breaker = circuit_registry.get_or_create(
            "visual_es", failure_threshold=3, recovery_timeout=30
        )
        if not breaker.allow_request():
            raise VisualIndexError("VISUAL_INDEX_CIRCUIT_OPEN")
        try:
            client = await get_client("visual_es", timeout=15)
            response = await client.post(
                self._url(f"{get_settings().visual_index_alias}/_search"),
                json=body,
                timeout=15,
            )
            response.raise_for_status()
            hits = (response.json().get("hits") or {}).get("hits") or []
            breaker.record_success()
            return [self._to_hit(hit, source) for hit in hits if isinstance(hit, dict)]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            breaker.record_failure()
            logger.warning("visual_index_search_failed", source=source, error=type(exc).__name__)
            raise VisualIndexError("VISUAL_INDEX_UNAVAILABLE") from exc

    async def _delete_ids(self, target: str, document_ids: list[str]) -> None:
        if not document_ids:
            return
        client = await get_client("visual_es", timeout=10)
        try:
            await client.post(
                self._url(f"{target}/_delete_by_query?conflicts=proceed&refresh=true"),
                json={"query": {"ids": {"values": document_ids}}},
                timeout=20,
            )
        except httpx.HTTPError:
            logger.warning("visual_index_partial_cleanup_failed", count=len(document_ids))

    @staticmethod
    def _to_hit(hit: dict, source: str) -> VisualIndexHit:
        data = hit.get("_source") or {}
        score = float(hit.get("_score") or 0.0)
        cosine = max(-1.0, min(1.0, score * 2 - 1)) if source.endswith("knn") else None
        return VisualIndexHit(
            product_id=str(data.get("productId") or ""),
            document_id=str(hit.get("_id") or ""),
            document_type=str(data.get("documentType") or "IMAGE"),
            cover_index=(
                int(data["coverIndex"]) if data.get("coverIndex") is not None else None
            ),
            image_sha256=data.get("imageSha256"),
            normalized_sha256=data.get("normalizedSha256"),
            product_name=str(data.get("productName") or ""),
            category_id=(str(data["categoryId"]) if data.get("categoryId") else None),
            brand=(str(data["brand"]) if data.get("brand") else None),
            score=score,
            cosine=cosine,
            recall_source=source,
        )

    @staticmethod
    def _filters(filters: dict[str, Any] | None) -> list[dict]:
        source = filters or {}
        clauses: list[dict] = []
        if source.get("categoryId"):
            clauses.append({"term": {"categoryId": str(source["categoryId"])}})
        if source.get("brand"):
            clauses.append({"term": {"brand.keyword": str(source["brand"])}})
        if source.get("budgetMax") is not None:
            clauses.append({"range": {"minPrice": {"lte": float(source["budgetMax"])}}})
        if source.get("budgetMin") is not None:
            clauses.append({"range": {"maxPrice": {"gte": float(source["budgetMin"])}}})
        return clauses

    @staticmethod
    def _source_fields() -> list[str]:
        return [
            "productId",
            "documentType",
            "coverIndex",
            "imageSha256",
            "normalizedSha256",
            "productName",
            "categoryId",
            "brand",
        ]

    @staticmethod
    def _mapping() -> dict:
        settings = get_settings()
        return {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "dynamic": "strict",
                "_meta": {
                    "model": settings.visual_embedding_model,
                    "modelVersion": settings.visual_index_model_version,
                    "dimensions": settings.visual_embedding_dimensions,
                    "schemaVersion": 1,
                },
                "properties": {
                    "productId": {"type": "keyword"},
                    "documentType": {"type": "keyword"},
                    "coverIndex": {"type": "integer"},
                    "imageSha256": {"type": "keyword"},
                    "normalizedSha256": {"type": "keyword"},
                    "productName": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "productText": {"type": "text"},
                    "categoryId": {"type": "keyword"},
                    "brand": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "status": {"type": "integer"},
                    "minPrice": {"type": "scaled_float", "scaling_factor": 100},
                    "maxPrice": {"type": "scaled_float", "scaling_factor": 100},
                    "productVersion": {"type": "long"},
                    "modelVersion": {"type": "keyword"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": settings.visual_embedding_dimensions,
                        "index": True,
                        "similarity": "cosine",
                        "index_options": {"type": "hnsw", "m": 16, "ef_construction": 100},
                    },
                    "indexedAt": {"type": "date"},
                },
            },
        }


visual_product_index = VisualProductIndex()
