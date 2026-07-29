import asyncio
import hashlib
import time
from typing import Any

import structlog

from app.config.settings import get_settings
from app.harness.metrics.runtime_sensors import (
    RAG_LATENCY,
    RAG_SEARCH_TOTAL,
)
from app.infra.http_client import get_client
from app.observability.telemetry import get_tracer
from app.rag.embedding import embed_text
from app.resilience.circuit_breaker import circuit_registry
from app.services.java_internal_client import java_internal_client
from app.services.redis_service import redis_service

logger = structlog.get_logger()
tracer = get_tracer()

PRODUCT_INDEX = "aishop-index"
KNOWLEDGE_VERSION_CACHE_KEY = "mall:knowledge:version"
KNOWLEDGE_RELEASE_TOPIC = "knowledge.release"

# Elasticsearch rejects a kNN search whose num_candidates exceeds this.
ES_MAX_NUM_CANDIDATES = 10_000

# RRF 的排名平滑常数。60 是 Cormack 等人原始论文的取值，也是 ES 自己 rrf retriever
# 的默认值；改它会让所有 RRF 阈值失去意义，所以定在这里供换算复用，不做成配置。
RRF_RANK_CONSTANT = 60


def cosine_to_es_score(cosine: float) -> float:
    """把 cosine 相似度换算成 ES 的 ``_score``。

    ES 对 ``cosineSimilarity`` 的打分是 ``(1 + cos) / 2``，把 [-1, 1] 映射到 [0, 1]。
    这层换算是 ES 的实现细节，不该泄漏到配置里让人心算——写 ``cos >= 0.3`` 是能复核的，
    写 ``_score >= 0.65`` 只能靠注释解释。
    """
    return (1.0 + max(-1.0, min(1.0, float(cosine)))) / 2.0


def rrf_score_at_rank(rank: int) -> float:
    """名次 ``rank``（从 1 起）在单一路召回里贡献的 RRF 分。

    用来把"至少进了某一路的前 N 名"这个可读的条件，翻译成 ``_has_enough_evidence``
    能比较的分数。
    """
    return 1.0 / (RRF_RANK_CONSTANT + max(int(rank), 1))


def knn_num_candidates(k: int, settings) -> int:
    """Per-shard candidate pool for an approximate kNN search.

    ES only requires ``num_candidates >= k``; a value close to that bound keeps
    latency down but lets the HNSW graph walk terminate early and miss relevant
    neighbours. Applying a floor as well as a multiple keeps recall usable at
    small k, and the ES ceiling is respected.
    """
    k = max(int(k), 1)
    candidates = max(k * settings.knn_num_candidates_factor, settings.knn_num_candidates_min)
    return max(k, min(candidates, ES_MAX_NUM_CANDIDATES))


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
        result = await self.search_faq_with_trace(query, top_k)
        return str(result.get("text") or "")

    async def search_faq_with_trace(
        self,
        query: str,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        """Search FAQ/knowledge and retain bounded evidence for observability."""
        started = time.perf_counter()
        cleaned = self._rewrite_query(query)
        if not cleaned:
            self._observe_search(started, False, "empty")
            return self._trace_result(cleaned, 1, "empty", False, [], started)

        version = await self._knowledge_version()
        exact = await self._exact_faq(cleaned, version)
        if exact:
            docs = [self._faq_row_to_doc(exact, score=1.0)]
            self._observe_search(started, True, "exact")
            return self._trace_result(cleaned, version, "exact", True, docs, started)

        cache_key = f"mall:rag:semantic:v{version}:{_sha256(cleaned)}"
        cached = await self._get_cache(cache_key)
        if cached:
            self._observe_search(started, True, "cache")
            return self._trace_result(cleaned, version, "cache", True, cached, started)

        docs = await self._search_knowledge_docs(cleaned, top_k or get_settings().rag_top_k)
        if not self._has_enough_evidence(docs):
            self._observe_search(started, False, "hybrid")
            return self._trace_result(cleaned, version, "hybrid", False, docs, started)
        await self._set_cache(cache_key, docs, get_settings().rag_cache_ttl_seconds)
        self._observe_search(started, True, "hybrid")
        return self._trace_result(cleaned, version, "hybrid", True, docs, started)

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
        docs = await self._vector_search(
            query,
            "product",
            limit,
            min_cosine=get_settings().rag_product_vector_min_cosine,
        )
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
                            # Multi-field BM25: productName carries the most signal,
                            # productDesc and brand widen recall for queries like
                            # "华为手机" where brand is stored separately.
                            # Unknown fields are silently ignored by ES.
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": ["productName^3", "productDesc^1", "brand^2"],
                                    "type": "best_fields",
                                }
                            },
                            # Wildcard on the keyword sub-field keeps exact-substring
                            # matches that tokenisation would otherwise miss.
                            {"wildcard": {"productName.keyword": f"*{query}*"}},
                        ]
                    }
                },
                "_source": ["productId"],
            }

            client = await get_client("es", timeout=15)
            resp = await client.post(
                self._es_url(f"/{PRODUCT_INDEX}/_search"), json=body, timeout=15
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            return [h["_source"]["productId"] for h in hits if h.get("_source", {}).get("productId")]
        except Exception as e:
            breaker.record_failure()
            logger.error("es_keyword_search_failed", error=str(e))
            return await self._product_search_fallback(query, limit)

    async def _search_knowledge_docs(self, query: str, limit: int) -> list[dict]:
        with tracer.start_as_current_span("rag.hybrid_search") as span:
            span.set_attribute("rag.query_length", len(query))
            span.set_attribute("rag.limit", limit)
            keyword_task = self._keyword_search_docs(query, ("faq", "knowledge"), limit)
            vector_task = self._vector_search(query, ("faq", "knowledge"), limit)
            keyword_docs, vector_docs = await asyncio.gather(keyword_task, vector_task)
            span.set_attribute("rag.keyword_hits", len(keyword_docs))
            span.set_attribute("rag.vector_hits", len(vector_docs))
            rrf_docs = self._rrf_docs([keyword_docs, vector_docs], limit=max(limit, 1))
            result = await self._rerank(query, rrf_docs, min(get_settings().rerank_top_n, limit))
            span.set_attribute("rag.result_count", len(result))
            return result

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
            client = await get_client("es", timeout=15)
            resp = await client.post(
                self._es_url(f"/{get_settings().es_index}/_search"), json=body, timeout=15
            )
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
        min_cosine: float | None = None,
    ) -> list[dict]:
        """kNN 召回。``min_cosine`` 是 cosine 相似度下限，不是 ES 的 ``_score``。

        参数原先叫 ``threshold`` 并直接和 ``_score`` 比。调用方传 0.4 看着像"要求四成
        相似"，实际是 ``cos >= -0.2``——比正交还低，等于没有过滤。改成显式的 cosine 语义
        以后，取值和名字对得上了。
        """
        settings = get_settings()
        k = top_k or settings.rag_top_k
        cosine_floor = (
            min_cosine if min_cosine is not None else settings.rag_vector_min_cosine
        )
        th = cosine_to_es_score(cosine_floor)
        breaker = circuit_registry.get_or_create("es")
        if not breaker.allow_request():
            return []
        vector = await embed_text(query)
        if not vector:
            # The ES call never happened, so hand back any half-open probe slot
            # instead of holding it until the reclaim timeout.
            breaker.release_probe()
            return []
        data_types = [data_type] if isinstance(data_type, str) else list(data_type)
        try:
            body = {
                "size": k,
                "knn": {
                    "field": settings.es_vector_field,
                    "query_vector": vector,
                    "k": k,
                    "num_candidates": knn_num_candidates(k, settings),
                    "filter": {
                        "bool": {
                            "must": [{"terms": {"metadata.dataType": data_types}}]
                        }
                    },
                },
                "_source": ["content", "metadata", "text"],
            }
            client = await get_client("es", timeout=20)
            resp = await client.post(
                self._es_url(f"/{settings.es_index}/_search"),
                json=body,
                timeout=20,
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
            client = await get_client("rerank", timeout=settings.rerank_timeout)
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
                timeout=settings.rerank_timeout,
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

    async def rerank_products(
        self,
        query: str,
        products: list[dict],
        limit: int,
    ) -> list[dict]:
        """Rerank a candidate product list with the cross-encoder API.

        Wraps each product as a lightweight document (name + first 200 chars of
        description) so the existing ``_rerank()`` infrastructure can be reused
        without any new HTTP client or circuit-breaker wiring.  Falls back to
        the original order if the API is unavailable or unconfigured.

        This is intentionally a thin shim: the heavy lifting (circuit breaker,
        silent fallback, response parsing) is already handled by ``_rerank()``.
        """
        if not products or len(products) <= limit:
            return products[:limit]
        docs = []
        for product in products:
            name = str(
                product.get("product_name") or product.get("productName") or ""
            ).strip()
            desc = str(
                product.get("product_desc") or product.get("productDesc") or ""
            )[:200].strip()
            content = f"{name}。{desc}" if desc else name
            docs.append({
                "id": str(product.get("product_id") or product.get("productId") or ""),
                "content": content,
                "score": 0.0,
                "source": "product_candidate",
            })
        reranked_docs = await self._rerank(query, docs, limit)
        id_to_product: dict[str, dict] = {
            str(p.get("product_id") or p.get("productId") or ""): p
            for p in products
        }
        result: list[dict] = []
        seen: set[str] = set()
        for doc in reranked_docs:
            pid = str(doc.get("id") or "")
            if pid and pid not in seen and pid in id_to_product:
                seen.add(pid)
                result.append(id_to_product[pid])
        # Guard against unexpected gaps in the reranker response (shouldn't
        # happen because _rerank already falls back to the original order).
        for p in products:
            pid = str(p.get("product_id") or p.get("productId") or "")
            if pid and pid not in seen:
                result.append(p)
        return result[:limit]

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
        RAG_SEARCH_TOTAL.labels(result="hit" if hit else "miss", mode=mode).inc()
        RAG_LATENCY.observe(max(0.0, time.perf_counter() - started))

    def _trace_result(
        self,
        query: str,
        version: int,
        mode: str,
        hit: bool,
        docs: list[dict],
        started: float,
    ) -> dict[str, Any]:
        refs = self._source_refs(docs, version)
        elapsed_ms = round(max(0.0, time.perf_counter() - started) * 1000, 2)
        return {
            "text": self._format_docs(docs) if hit else "",
            "source_refs": refs,
            "trace": {
                "queryHash": _sha256(query),
                "mode": mode,
                "hit": hit,
                "knowledgeVersion": version,
                "sourceCount": len(refs),
                "topScore": max(
                    (float(doc.get("score") or 0) for doc in docs),
                    default=0.0,
                ),
                "latencyMs": elapsed_ms,
            },
        }

    def _source_refs(self, docs: list[dict], version: int) -> list[dict]:
        refs: list[dict] = []
        seen: set[str] = set()
        for doc in docs[: get_settings().rerank_top_n]:
            metadata = doc.get("metadata") or {}
            doc_id = str(
                doc.get("id")
                or metadata.get("chunkId")
                or metadata.get("questionId")
                or ""
            )
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            data_type = str(metadata.get("dataType") or "knowledge")
            ref = {
                "type": "faq" if data_type == "faq" else "knowledge_chunk",
                "id": doc_id,
                "dataType": data_type,
                "source": (
                    metadata.get("source")
                    or metadata.get("title")
                    or metadata.get("question")
                    or "知识库"
                ),
                "retrieval": doc.get("source") or "unknown",
                "score": round(float(doc.get("score") or 0), 6),
                "knowledgeVersion": metadata.get("version") or version,
                "snippet": self._doc_text(doc)[:240],
            }
            for metadata_key, ref_key in (
                ("documentId", "documentId"),
                ("chunkId", "chunkId"),
                ("questionId", "questionId"),
                ("heading", "heading"),
            ):
                if metadata.get(metadata_key) is not None:
                    ref[ref_key] = metadata[metadata_key]
            refs.append(ref)
        return refs

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
        """Reciprocal Rank Fusion：只用名次，不用各路的原始分。

        ``score`` 融合后就是 RRF 分。原先这里写 ``max(原始分, RRF分)``，而 BM25 的
        ``_score`` 是 1~20、RRF 分最大 ~0.033，于是 max 永远取原始分——融合结果被自己
        覆盖掉了。列表顺序还是对的（排序用的是局部 ``scores``），但两件事因此坏了：

        1. ``_has_enough_evidence`` 拿 0.5 去比一个混着 BM25 分和 cosine 分的值，
           BM25 命中恒过、向量命中也已在上游筛过，那道闸门实际是空的；
        2. trace 里的 ``topScore`` 跨查询不可比——8.7 是好是坏取决于它来自哪一路。

        原始分留在 ``engineScore`` 里，排查单路召回质量时还需要它。
        """
        scores: dict[str, float] = {}
        by_id: dict[str, dict] = {}
        for group in ranked_groups:
            for index, doc in enumerate(group):
                doc_id = str(doc.get("id") or "")
                if not doc_id:
                    continue
                scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score_at_rank(index + 1)
                by_id.setdefault(doc_id, doc)
        sorted_ids = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        merged = []
        for doc_id, score in sorted_ids[:limit]:
            doc = dict(by_id[doc_id])
            doc["engineScore"] = float(doc.get("score") or 0)
            doc["score"] = score
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
        """证据够不够写进 prompt。

        按分数的来源分流，因为 rerank 之后和只做完 RRF 的分数量纲不同：
        rerank 给的是 0~1 归一相关性，可以直接和一个绝对阈值比；RRF 分是名次的倒数和，
        只能表达"至少在某一路里进了前 N 名"。用一个常量同时比这两种，其中一种必然失真。

        rerank 未配置或熔断时会静默回落到 RRF（见 ``_rerank``），所以这条兜底路径不是
        边缘情况——恰恰是没有 rerank key 的部署里的常态。
        """
        if not docs:
            return False
        settings = get_settings()
        top = max(docs, key=lambda doc: float(doc.get("score") or 0))
        top_score = float(top.get("score") or 0)
        if top.get("source") == "rerank":
            return top_score >= settings.rag_evidence_min_relevance
        if top.get("source") == "rrf":
            return top_score >= rrf_score_at_rank(settings.rag_evidence_min_rrf_rank)
        # 精确 FAQ 命中（score=1.0）等不经过融合的路径。
        return top_score >= settings.rag_evidence_min_relevance

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
