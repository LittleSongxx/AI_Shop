import asyncio
import hashlib
import time
from typing import Any

import httpx
import structlog

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import (
    RAG_HIT_RATE,
    RAG_LATENCY,
    RAG_SEARCH_TOTAL,
)
from app.rag.embedding import embed_text
from app.resilience.circuit_breaker import circuit_registry
from app.services.java_internal_client import java_internal_client
from app.services.redis_service import redis_service

logger = structlog.get_logger()

PRODUCT_INDEX = "simlect-index"
KNOWLEDGE_VERSION_CACHE_KEY = "mall:knowledge:version"
KNOWLEDGE_RELEASE_TOPIC = "knowledge.release"


class RagRetriever:

    def __init__(self):
        self._es_hosts = get_settings().es_hosts

    def _es_url(self, path: str) -> str:
        base = self._es_hosts.split(",")[0].rstrip("/")
        return f"{base}{path}"

    async def warmup_faq_cache(self) -> None:
        try:
            version = await self._knowledge_version()
            rows = await java_internal_client.top_faq(100)
            for row in rows:
                question = row.get("question")
                if question:
                    await self._set_faq_exact_cache(version, question, row)
            logger.info("faq_cache_warmed", count=len(rows), version=version)
        except Exception as exc:
            logger.warning("faq_cache_warmup_skipped", error=str(exc))

    async def search_faq(self, query: str, top_k: int | None = None) -> str:
        started = time.perf_counter()
        cleaned = self._rewrite_query(query)
        if not cleaned:
            self._observe_search(started, False, "empty")
            return ""

        version = await self._knowledge_version()
        exact = await self._exact_faq(cleaned, version)
        if exact:
            self._observe_search(started, True, "exact")
            return self._format_docs([self._faq_row_to_doc(exact, score=1.0)])

        cache_key = f"mall:rag:semantic:v{version}:{_sha256(cleaned)}"
        cached = await self._get_cache(cache_key)
        if cached:
            self._observe_search(started, True, "cache")
            return self._format_docs(cached)

        docs = await self._search_knowledge_docs(cleaned, top_k or get_settings().rag_top_k)
        if not self._has_enough_evidence(docs):
            self._observe_search(started, False, "hybrid")
            return ""
        await self._set_cache(cache_key, docs, get_settings().rag_cache_ttl_seconds)
        self._observe_search(started, True, "hybrid")
        return self._format_docs(docs)

    async def exact_faq_answer(self, query: str) -> dict | None:
        """Return a curated FAQ answer without invoking the LLM."""
        started = time.perf_counter()
        cleaned = self._rewrite_query(query)
        if not cleaned:
            self._observe_search(started, False, "exact_fast_path")
            return None
        version = await self._knowledge_version()
        try:
            row = await asyncio.wait_for(
                self._exact_faq(cleaned, version),
                timeout=get_settings().faq_fast_path_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("faq_fast_path_timeout", query_hash=_sha256(cleaned))
            self._observe_search(started, False, "exact_fast_path_timeout")
            return None
        answer = str((row or {}).get("answer") or "").strip()
        if not answer:
            self._observe_search(started, False, "exact_fast_path")
            return None
        self._observe_search(started, True, "exact_fast_path")
        return {
            "answer": answer,
            "question": row.get("question"),
            "questionId": row.get("question_id") or row.get("questionId"),
            "category": row.get("category"),
            "source": row.get("source") or "FAQ",
            "version": row.get("version") or version,
        }

    async def search_product_vector_ids(self, query: str, limit: int) -> list[str]:
        docs = await self._vector_search(query, "product", limit, threshold=0.4)
        ids = []
        for d in docs:
            meta = d.get("metadata") or {}
            pid = meta.get("productId") or meta.get("product_id")
            if pid and str(pid) not in ids:
                ids.append(str(pid))
        return ids

    async def search_product_keyword_ids(self, query: str, limit: int) -> list[str]:
        breaker = circuit_registry.get_or_create("es", failure_threshold=3, recovery_timeout=30)
        if not breaker.allow_request() or not query.strip():
            return []
        try:
            body = {
                "size": min(max(limit, 1), 50),
                "query": {
                    "bool": {
                        "should": [
                            {"match": {"productName": {"query": query, "boost": 2}}},
                            {"wildcard": {"productName.keyword": f"*{query}*"}},
                        ]
                    }
                },
                "_source": ["productId"],
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self._es_url(f"/{PRODUCT_INDEX}/_search"), json=body)
                resp.raise_for_status()
                hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            return [h["_source"]["productId"] for h in hits if h.get("_source", {}).get("productId")]
        except Exception as e:
            breaker.record_failure()
            logger.error("es_keyword_search_failed", error=str(e))
            return await self._product_search_fallback(query, limit)

    async def _search_knowledge_docs(self, query: str, limit: int) -> list[dict]:
        keyword_task = self._keyword_search_docs(query, ("faq", "knowledge"), limit)
        vector_task = self._vector_search(query, ("faq", "knowledge"), limit)
        keyword_docs, vector_docs = await asyncio.gather(keyword_task, vector_task)
        rrf_docs = self._rrf_docs([keyword_docs, vector_docs], limit=max(limit, 1))
        return await self._rerank(query, rrf_docs, min(get_settings().rerank_top_n, limit))

    async def _keyword_search_docs(
        self,
        query: str,
        data_types: tuple[str, ...],
        limit: int,
    ) -> list[dict]:
        breaker = circuit_registry.get_or_create("es", failure_threshold=3, recovery_timeout=30)
        if not breaker.allow_request() or not query.strip():
            return []
        try:
            body = {
                "size": min(max(limit, 1), 50),
                "query": {
                    "bool": {
                        "filter": [
                            {"terms": {"metadata.dataType": list(data_types)}},
                        ],
                        "should": [
                            {"match": {"content": {"query": query, "boost": 2}}},
                            {"match": {"text": {"query": query}}},
                            {"match": {"metadata.question": {"query": query, "boost": 3}}},
                            {"match": {"metadata.answer": {"query": query, "boost": 2}}},
                            {"match": {"metadata.title": {"query": query, "boost": 2}}},
                            {"match": {"metadata.heading": {"query": query}}},
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "_source": ["content", "metadata", "text"],
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self._es_url(f"/{get_settings().es_index}/_search"), json=body)
                resp.raise_for_status()
                hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            return [self._hit_to_doc(hit, "bm25") for hit in hits]
        except Exception as exc:
            breaker.record_failure()
            logger.error("es_keyword_search_failed", data_types=data_types, error=str(exc))
            return []

    async def _vector_search(
        self,
        query: str,
        data_type: str | tuple[str, ...],
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> list[dict]:
        settings = get_settings()
        k = top_k or settings.rag_top_k
        th = threshold if threshold is not None else settings.rag_score_threshold
        breaker = circuit_registry.get_or_create("es")
        if not breaker.allow_request():
            return []
        vector = await embed_text(query)
        if not vector:
            return []
        data_types = [data_type] if isinstance(data_type, str) else list(data_type)
        try:
            body = {
                "size": k,
                "knn": {
                    "field": "content_vector",
                    "query_vector": vector,
                    "k": k,
                    "num_candidates": k * 2,
                    "filter": {
                        "bool": {
                            "must": [{"terms": {"metadata.dataType": data_types}}]
                        }
                    },
                },
                "_source": ["content", "metadata", "text"],
            }
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    self._es_url(f"/{settings.es_index}/_search"),
                    json=body,
                )
                if resp.status_code == 404:
                    body["knn"]["field"] = "vector"
                    resp = await client.post(
                        self._es_url(f"/{settings.es_index}/_search"),
                        json=body,
                    )
                resp.raise_for_status()
                hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            results = []
            for hit in hits:
                score = float(hit.get("_score") or 0)
                if score < th:
                    continue
                results.append(self._hit_to_doc(hit, "vector"))
            return results
        except Exception as e:
            breaker.record_failure()
            logger.error("vector_search_failed", data_type=data_type, error=str(e))
            return []

    async def _rerank(self, query: str, docs: list[dict], limit: int) -> list[dict]:
        settings = get_settings()
        if not docs or not settings.rerank_api_key.strip():
            return docs[:limit]
        breaker = circuit_registry.get_or_create("rerank", failure_threshold=3, recovery_timeout=30)
        if not breaker.allow_request():
            return docs[:limit]
        try:
            candidates = [self._doc_text(doc)[:4000] for doc in docs]
            async with httpx.AsyncClient(timeout=settings.rerank_timeout) as client:
                resp = await client.post(
                    settings.rerank_base_url,
                    headers={
                        "Authorization": f"Bearer {settings.rerank_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.rerank_model,
                        "input": {
                            "query": query,
                            "documents": candidates,
                        },
                        "parameters": {
                            "return_documents": False,
                            "top_n": limit,
                        },
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
            output = payload.get("output") if isinstance(payload, dict) else {}
            results = output.get("results") if isinstance(output, dict) else None
            if not isinstance(results, list):
                raise ValueError("invalid rerank response")
            reranked = []
            for item in results:
                index = item.get("index")
                if isinstance(index, int) and 0 <= index < len(docs):
                    doc = dict(docs[index])
                    doc["score"] = float(
                        item.get("relevance_score")
                        or item.get("score")
                        or doc.get("score")
                        or 0
                    )
                    doc["source"] = "rerank"
                    reranked.append(doc)
            breaker.record_success()
            return reranked or docs[:limit]
        except Exception as exc:
            breaker.record_failure()
            logger.warning("rerank_failed_fallback_rrf", error=str(exc))
            return docs[:limit]

    async def _exact_faq(self, query: str, version: int) -> dict | None:
        cached = await self._get_faq_exact_cache(version, query)
        if cached:
            return cached
        try:
            row = await java_internal_client.exact_faq(query)
            if row:
                await self._set_faq_exact_cache(version, query, row)
            return row
        except Exception as exc:
            logger.warning("faq_exact_failed", error=str(exc))
            return None

    async def _knowledge_version(self) -> int:
        try:
            cached = await redis_service.client.get(KNOWLEDGE_VERSION_CACHE_KEY)
            if cached:
                return int(cached)
        except Exception:
            pass
        try:
            version = await java_internal_client.knowledge_version()
        except Exception:
            version = 1
        try:
            await redis_service.client.setex(KNOWLEDGE_VERSION_CACHE_KEY, 300, str(version))
        except Exception:
            pass
        return version

    def _observe_search(self, started: float, hit: bool, mode: str) -> None:
        RAG_HIT_RATE.set(1 if hit else 0)
        RAG_SEARCH_TOTAL.labels(result="hit" if hit else "miss", mode=mode).inc()
        RAG_LATENCY.observe(max(0.0, time.perf_counter() - started))

    async def _product_search_fallback(self, query: str, limit: int) -> list[str]:
        try:
            rows = await java_internal_client.search_on_sale(keyword=query, limit=limit)
            ids = []
            for r in rows:
                pid = r.get("product_id") or r.get("productId")
                if pid and str(pid) not in ids:
                    ids.append(str(pid))
            return ids
        except Exception as e:
            logger.error("product_search_fallback_failed", error=str(e))
            return []

    def _rrf_docs(self, ranked_groups: list[list[dict]], limit: int) -> list[dict]:
        scores: dict[str, float] = {}
        by_id: dict[str, dict] = {}
        for group in ranked_groups:
            for index, doc in enumerate(group):
                doc_id = str(doc.get("id") or "")
                if not doc_id:
                    continue
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (60 + index + 1)
                by_id.setdefault(doc_id, doc)
        sorted_ids = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        merged = []
        for doc_id, score in sorted_ids[:limit]:
            doc = dict(by_id[doc_id])
            doc["score"] = max(float(doc.get("score") or 0), score)
            doc["source"] = "rrf"
            merged.append(doc)
        return merged

    def _hit_to_doc(self, hit: dict, source: str) -> dict:
        src = hit.get("_source", {}) or {}
        metadata = src.get("metadata") or {}
        return {
            "id": hit.get("_id") or metadata.get("chunkId") or metadata.get("questionId"),
            "content": src.get("content") or src.get("text") or "",
            "metadata": metadata,
            "score": float(hit.get("_score") or 0),
            "source": source,
        }

    def _faq_row_to_doc(self, row: dict, score: float) -> dict:
        content = f"问题：{row.get('question', '')}\n答案：{row.get('answer', '')}"
        return {
            "id": f"faq_{row.get('question_id') or row.get('questionId') or _sha256(content)}",
            "content": content,
            "metadata": {
                "dataType": "faq",
                "questionId": row.get("question_id") or row.get("questionId"),
                "category": row.get("category"),
                "source": row.get("source") or "FAQ",
                "owner": row.get("owner"),
            },
            "score": score,
            "source": "exact_faq",
        }

    def _format_docs(self, docs: list[dict]) -> str:
        seen: set[str] = set()
        parts = []
        for doc in docs:
            text = self._doc_text(doc)
            if not text or text in seen:
                continue
            seen.add(text)
            metadata = doc.get("metadata") or {}
            source = (
                metadata.get("source")
                or metadata.get("title")
                or metadata.get("question")
                or metadata.get("dataType")
                or "知识库"
            )
            parts.append(f"[来源：{source}] {text}")
        return "\n\n".join(parts)

    def _doc_text(self, doc: dict) -> str:
        return str(doc.get("content") or doc.get("text") or "").strip()

    def _has_enough_evidence(self, docs: list[dict]) -> bool:
        if not docs:
            return False
        return max(float(doc.get("score") or 0) for doc in docs) >= get_settings().rag_score_threshold

    def _rewrite_query(self, query: str) -> str:
        text = (query or "").strip()
        replacements = {
            "这个": "",
            "那个": "",
            "它": "",
            "吗": "",
            "呢": "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return " ".join(text.split())

    async def _get_faq_exact_cache(self, version: int, query: str) -> dict | None:
        key = f"mall:rag:faq_exact:v{version}:{_sha256(self._normalize_question(query))}"
        return await self._get_cache(key)

    async def _set_faq_exact_cache(self, version: int, query: str, value: dict) -> None:
        key = f"mall:rag:faq_exact:v{version}:{_sha256(self._normalize_question(query))}"
        await self._set_cache(key, value, get_settings().faq_exact_cache_ttl_seconds)

    async def _get_cache(self, key: str) -> Any:
        try:
            return await redis_service.get_json(key)
        except Exception:
            return None

    async def _set_cache(self, key: str, value: Any, ttl: int) -> None:
        try:
            await redis_service.set_json(key, value, ttl, jitter_seconds=120)
        except Exception:
            pass

    def _normalize_question(self, query: str) -> str:
        return "".join(ch for ch in (query or "").lower() if ch.isalnum())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


rag_retriever = RagRetriever()
