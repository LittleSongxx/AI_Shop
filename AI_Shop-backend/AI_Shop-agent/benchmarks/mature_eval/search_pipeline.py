"""Cold Search collection and zero-provider offline ablation replay."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from app.config.settings import get_settings
from app.evaluation.ranking import (
    aggregate_ranking_cases,
    aggregate_stage_latency,
    paired_ranking_comparison,
    ranking_case_metrics,
)
from app.infra.http_client import get_client
from app.rag.embedding import embed_text, embedding_evaluation_scope
from app.rag.rrf import rrf_score_at_rank
from app.services.product_search_query import normalize_product_search_query
from benchmarks.mature_eval.common import (
    atomic_write_json,
    read_gzip_json,
    require_eval_index,
    sha256_file,
    write_gzip_json,
)

DEFAULT_SEARCH_K_VALUES = (1, 3, 5, 10, 20)
DEFAULT_CANDIDATE_COUNTS = (8, 16, 24, 50)
DEFAULT_RRF_K_VALUES = (10, 60, 100)
DEFAULT_RERANK_TOP_N = (6, 12, 24)
SEARCH_VARIANTS = (
    "raw_bm25",
    "normalized_bm25",
    "vector",
    "rrf",
    "rrf_relevance_filter",
    "full_rerank",
)
WANDS_SEARCH_VARIANTS = ("raw_bm25", "vector", "rrf", "full_rerank")


@dataclass
class ProviderAudit:
    embedding_requests: int = 0
    rerank_requests: int = 0
    provider_calls_allowed: bool = True
    response_facts: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "embeddingRequests": self.embedding_requests,
            "rerankRequests": self.rerank_requests,
            "providerCallsAllowed": self.provider_calls_allowed,
            "responseFacts": list(self.response_facts),
        }


_ACTIVE_AUDIT: ProviderAudit | None = None


@contextmanager
def provider_audit(*, allow_calls: bool) -> Iterator[ProviderAudit]:
    global _ACTIVE_AUDIT
    if _ACTIVE_AUDIT is not None:
        raise RuntimeError("nested Search provider audit is not supported")
    audit = ProviderAudit(provider_calls_allowed=allow_calls)
    _ACTIVE_AUDIT = audit
    try:
        yield audit
    finally:
        _ACTIVE_AUDIT = None


def _provider_call(kind: str) -> None:
    if _ACTIVE_AUDIT is None:
        raise RuntimeError("Search Provider call must run inside provider_audit")
    if not _ACTIVE_AUDIT.provider_calls_allowed:
        raise RuntimeError("offline replay attempted a Provider call")
    if kind == "embedding":
        _ACTIVE_AUDIT.embedding_requests += 1
    elif kind == "rerank":
        _ACTIVE_AUDIT.rerank_requests += 1
    else:
        raise ValueError(f"unsupported provider kind: {kind}")


def rrf_merge_rankings(
    keyword: Sequence[str],
    vector: Sequence[str],
    *,
    rrf_k: int,
    limit: int,
) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in (keyword, vector):
        for rank, product_id in enumerate(ranked, 1):
            if not product_id:
                continue
            scores[product_id] = scores.get(product_id, 0.0) + rrf_score_at_rank(rank, rrf_k)
    return [
        product_id
        for product_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
            : max(1, limit)
        ]
    ]


def _product_text(product: Mapping[str, Any]) -> str:
    attributes = " ".join(
        str(value) for value in (product.get("attributes") or {}).values()
    ) if isinstance(product.get("attributes"), Mapping) else ""
    return " ".join(
        str(product.get(key) or "").casefold()
        for key in (
            "productName",
            "productDesc",
            "name",
            "description",
            "brand",
            "category",
            "categoryName",
            "scenario",
            "audience",
            "product_name",
            "product_class",
            "category hierarchy",
            "product_description",
            "product_features",
        )
    ) + f" {attributes.casefold()}"


def _query_terms(query: str) -> list[str]:
    values = [str(query or "").strip().casefold()]
    normalized = normalize_product_search_query(query).casefold()
    if normalized and normalized not in values:
        values.append(normalized)
    return [value for value in values if value]


def relevance_filter(
    ranked_ids: Sequence[str],
    products_by_id: Mapping[str, Mapping[str, Any]],
    query: str,
    *,
    constraints: Mapping[str, Any] | None = None,
) -> list[str]:
    if constraints:
        category = str(constraints.get("category") or "")
        price_min = constraints.get("priceMin")
        price_max = constraints.get("priceMax")

        def passes(product_id: str) -> bool:
            product = products_by_id.get(product_id, {})
            if category and str(product.get("category") or "") != category:
                return False
            try:
                price = float(product.get("price"))
            except (TypeError, ValueError):
                return price_min is None and price_max is None
            if price_min is not None and price < float(price_min):
                return False
            if price_max is not None and price > float(price_max):
                return False
            return True

        # Category and numeric ranges are deterministic hard filters. Scenario,
        # audience and semantic attributes remain ranking signals so grade-2
        # acceptable alternatives are not erased before reranking.
        return [product_id for product_id in ranked_ids if passes(product_id)]

    terms = _query_terms(query)
    if not terms:
        return list(ranked_ids)
    return [
        product_id
        for product_id in ranked_ids
        if any(term in _product_text(products_by_id.get(product_id, {})) for term in terms)
    ]


class SearchCollector:
    def __init__(self, index: str, *, es_hosts: str) -> None:
        self.index = require_eval_index(index)
        self.base_url = es_hosts.split(",")[0].rstrip("/")

    async def bm25(
        self,
        query: str,
        size: int,
        *,
        allowed_ids: Sequence[str] | None = None,
    ) -> tuple[list[str], float]:
        started = time.perf_counter()
        client = await get_client("mature_eval_es", timeout=30)
        text_query: dict[str, Any] = {
            "multi_match": {
                "query": query,
                "fields": ["productName^3", "productDesc", "brand^2", "category"],
                "type": "best_fields",
            }
        }
        search_query: dict[str, Any] = text_query
        if allowed_ids is not None:
            if not allowed_ids:
                return [], round((time.perf_counter() - started) * 1000, 4)
            search_query = {
                "bool": {
                    "must": [text_query],
                    "filter": [{"terms": {"productId": list(allowed_ids)}}],
                }
            }
        response = await client.post(
            f"{self.base_url}/{self.index}/_search",
            json={
                "size": size,
                "query": search_query,
                "_source": ["productId"],
            },
            timeout=30,
        )
        response.raise_for_status()
        ids = [
            str((hit.get("_source") or {}).get("productId") or hit.get("_id") or "")
            for hit in response.json().get("hits", {}).get("hits", [])
        ]
        return [item for item in ids if item], round((time.perf_counter() - started) * 1000, 4)

    async def vector(
        self,
        query: str,
        size: int,
        *,
        allowed_ids: Sequence[str] | None = None,
    ) -> tuple[list[str], list[float], dict[str, float]]:
        started = time.perf_counter()
        _provider_call("embedding")
        embedding_started = time.perf_counter()
        vector = await embed_text(query)
        embedding_ms = round((time.perf_counter() - embedding_started) * 1000, 4)
        if not vector:
            raise RuntimeError("embedding provider returned no query vector")
        if len(vector) != 1024:
            raise RuntimeError(f"query embedding has {len(vector)} dimensions")
        client = await get_client("mature_eval_es", timeout=30)
        search_started = time.perf_counter()
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": vector,
            "k": size,
            "num_candidates": max(100, size * 3),
        }
        if allowed_ids is not None:
            if not allowed_ids:
                return [], [float(value) for value in vector], {
                    "embedding": embedding_ms,
                    "vector": 0.0,
                    "vectorTotal": round((time.perf_counter() - started) * 1000, 4),
                }
            knn["filter"] = {"terms": {"productId": list(allowed_ids)}}
        response = await client.post(
            f"{self.base_url}/{self.index}/_search",
            json={
                "size": size,
                "knn": knn,
                "_source": ["productId"],
            },
            timeout=30,
        )
        response.raise_for_status()
        vector_ms = round((time.perf_counter() - search_started) * 1000, 4)
        ids = [
            str((hit.get("_source") or {}).get("productId") or hit.get("_id") or "")
            for hit in response.json().get("hits", {}).get("hits", [])
        ]
        return (
            [item for item in ids if item],
            [float(value) for value in vector],
            {
                "embedding": embedding_ms,
                "vector": vector_ms,
                "vectorTotal": round((time.perf_counter() - started) * 1000, 4),
            },
        )

    async def rerank(
        self,
        query: str,
        candidate_ids: Sequence[str],
        products_by_id: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        if not candidate_ids:
            return [], 0.0, {"status": "NOT_APPLICABLE", "reason": "no_candidates"}
        settings = get_settings()
        if not settings.rerank_api_key.strip():
            raise RuntimeError("RERANK_API_KEY is required for cold Search collection")
        documents = [_product_text(products_by_id[product_id])[:4000] for product_id in candidate_ids]
        _provider_call("rerank")
        started = time.perf_counter()
        client = await get_client("mature_eval_rerank", timeout=settings.rerank_timeout)
        response = await client.post(
            settings.rerank_base_url,
            headers={
                "Authorization": f"Bearer {settings.rerank_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.rerank_model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
                "instruct": settings.rerank_instruct,
            },
            timeout=settings.rerank_timeout,
        )
        response.raise_for_status()
        latency = round((time.perf_counter() - started) * 1000, 4)
        payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("rerank provider returned no results array")
        ranked: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in rows:
            index = row.get("index") if isinstance(row, dict) else None
            if not isinstance(index, int) or isinstance(index, bool) or index in seen:
                continue
            if not 0 <= index < len(candidate_ids):
                continue
            score = row.get("relevance_score", row.get("score"))
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            seen.add(index)
            ranked.append({"productId": candidate_ids[index], "score": float(score)})
        if len(ranked) != len(candidate_ids):
            raise RuntimeError(
                f"rerank returned {len(ranked)}/{len(candidate_ids)} valid candidates; fallback forbidden"
            )
        facts = {
            "status": "SUCCESS",
            "model": settings.rerank_model,
            "candidateCount": len(candidate_ids),
            "resultCount": len(ranked),
            "requestId": response.headers.get("x-request-id") or response.headers.get("request-id"),
        }
        if _ACTIVE_AUDIT is not None:
            _ACTIVE_AUDIT.response_facts.append(facts)
        return ranked, latency, facts


async def collect_cases(
    *,
    cases: Sequence[Mapping[str, Any]],
    products: Sequence[Mapping[str, Any]],
    index: str,
    output_path: Path,
    candidate_size: int = 50,
    rerank_rrf_k: int = 60,
) -> dict[str, Any]:
    """Collect one cold Provider pass and persist all replay inputs."""

    settings = get_settings()
    collector = SearchCollector(index, es_hosts=settings.es_hosts)
    products_by_id = {
        str(row.get("id") or row.get("product_id") or row.get("productId") or ""): row
        for row in products
    }
    previous: dict[str, Any] = {}
    if output_path.is_file():
        previous = read_gzip_json(output_path)
        if (
            previous.get("kind") != "search-cold-collection"
            or previous.get("index") != require_eval_index(index)
            or int(previous.get("candidateSize") or 0) != candidate_size
            or int(previous.get("collectionRrfK") or 0) != rerank_rrf_k
        ):
            raise ValueError("existing Search checkpoint does not match collection settings")
    rows = [dict(row) for row in previous.get("cases") or []]
    existing_by_id = {str(row.get("caseId") or ""): row for row in rows}
    requested_by_id = {
        str(case.get("id") or case.get("queryId") or ""): case for case in cases
    }
    if "" in requested_by_id or len(requested_by_id) != len(cases):
        raise ValueError("Search collection case IDs must be non-empty and unique")
    if not set(existing_by_id).issubset(requested_by_id):
        raise ValueError("Search checkpoint contains cases outside this collection")

    def merge_facts(current_audit: Mapping[str, Any], current_embedding: Mapping[str, Any]) -> dict[str, Any]:
        old = previous.get("providerFacts") or {}
        old_embedding = old.get("embedding") or {}
        merged = {
            "embeddingRequests": int(old.get("embeddingRequests") or 0)
            + int(current_audit.get("embeddingRequests") or 0),
            "rerankRequests": int(old.get("rerankRequests") or 0)
            + int(current_audit.get("rerankRequests") or 0),
            "providerCallsAllowed": True,
            "responseFacts": [
                *(old.get("responseFacts") or []),
                *(current_audit.get("responseFacts") or []),
            ],
            "embedding": {
                key: int(old_embedding.get(key) or 0) + int(current_embedding.get(key) or 0)
                for key in (
                    "requests",
                    "cacheHits",
                    "providerRequests",
                    "providerSuccesses",
                    "providerFailures",
                    "breakerRejections",
                )
            },
        }
        merged["embedding"]["bypassCache"] = True
        merged["embedding"]["responseRecords"] = [
            *(old_embedding.get("responseRecords") or []),
            *(current_embedding.get("responseRecords") or []),
        ]
        return merged

    def checkpoint(provider_facts: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "schemaVersion": 1,
            "kind": "search-cold-collection",
            "index": require_eval_index(index),
            "candidateSize": candidate_size,
            "collectionRrfK": rerank_rrf_k,
            "providerFacts": dict(provider_facts),
            "cases": rows,
        }
        write_gzip_json(output_path, payload)
        return payload

    with provider_audit(allow_calls=True) as audit, embedding_evaluation_scope(
        bypass_cache=True
    ) as embedding_stats:
        for case in cases:
            case_id = str(case.get("id") or case.get("queryId") or "")
            if case_id in existing_by_id:
                existing = existing_by_id[case_id]
                if existing.get("query") != case.get("query"):
                    raise ValueError(f"Search checkpoint query changed for {case_id}")
                continue
            query = str(case.get("query") or "")
            judged_pool = case.get("labelScope") == "judged-pool"
            # WANDS is an English external corpus and must not pass through the
            # Chinese commerce taxonomy normalizer.
            normalized = query if judged_pool else (normalize_product_search_query(query) or query)
            judged_ids = (
                list((case.get("relevanceGrades") or {}).keys())
                if judged_pool
                else None
            )
            raw_bm25_task = asyncio.create_task(
                collector.bm25(query, candidate_size, allowed_ids=judged_ids)
            )
            normalized_bm25_task = asyncio.create_task(
                collector.bm25(normalized, candidate_size, allowed_ids=judged_ids)
            )
            vector_task = asyncio.create_task(
                collector.vector(normalized, candidate_size, allowed_ids=judged_ids)
            )
            (raw_bm25, raw_ms), (normalized_bm25, normalized_ms), (
                vector,
                query_embedding,
                vector_latency,
            ) = await asyncio.gather(raw_bm25_task, normalized_bm25_task, vector_task)
            rrf_started = time.perf_counter()
            rrf = rrf_merge_rankings(
                normalized_bm25,
                vector,
                rrf_k=rerank_rrf_k,
                limit=candidate_size,
            )
            rrf_ms = round((time.perf_counter() - rrf_started) * 1000, 4)
            # Score the complete union once. Every candidate produced by any
            # offline RRF K/candidate-count setting therefore has a frozen
            # Provider score, while each case still makes a single rerank call.
            rerank_pool = list(
                dict.fromkeys(
                    [
                        *(raw_bm25 if judged_pool else normalized_bm25),
                        *vector,
                    ]
                )
            )
            reranked, rerank_ms, rerank_facts = await collector.rerank(
                query,
                rerank_pool,
                products_by_id,
            )
            rows.append(
                {
                    "caseId": case_id,
                    "query": query,
                    "normalizedQuery": normalized,
                    "split": case.get("split"),
                    "queryType": case.get("queryType"),
                    "constraints": case.get("constraints") or {},
                    "expectedNoResults": bool(case.get("expectedNoResults")),
                    "relevanceGrades": case.get("relevanceGrades") or {},
                    "labelScope": case.get("labelScope") or "full-catalog",
                    "judgedPoolSize": len(judged_ids) if judged_ids is not None else None,
                    "queryEmbedding": query_embedding,
                    "rawBm25": raw_bm25,
                    "normalizedBm25": normalized_bm25,
                    "vector": vector,
                    "rrfCollectionOrder": rrf,
                    "rerankCandidatePool": rerank_pool,
                    "rerank": reranked,
                    "rerankFacts": rerank_facts,
                    "stageLatencyMs": {
                        "rawBm25": raw_ms,
                        "normalizedBm25": normalized_ms,
                        **vector_latency,
                        "rrf": rrf_ms,
                        "rerank": rerank_ms,
                    },
                }
            )
            checkpoint(merge_facts(audit.snapshot(), embedding_stats.snapshot()))
        provider_facts = merge_facts(audit.snapshot(), embedding_stats.snapshot())
    embedding_facts = provider_facts["embedding"]
    if embedding_facts["cacheHits"] or embedding_facts["providerFailures"]:
        raise RuntimeError("Search cold collection embedding evidence is incomplete")
    if (
        embedding_facts["requests"] != len(rows)
        or embedding_facts["providerRequests"] != len(rows)
        or embedding_facts["providerSuccesses"] != len(rows)
        or provider_facts["embeddingRequests"] != len(rows)
        or provider_facts["rerankRequests"] != len(rows)
    ):
        raise RuntimeError("Search cold collection Provider call counts are incomplete")
    payload = checkpoint(provider_facts)
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".sha256.json"),
        {"path": output_path.name, "sha256": sha256_file(output_path)},
    )
    return payload


def _variant_ranking(
    row: Mapping[str, Any],
    products_by_id: Mapping[str, Mapping[str, Any]],
    *,
    variant: str,
    candidate_count: int,
    rrf_k: int,
    rerank_top_n: int,
) -> list[str]:
    raw_bm25 = list(row.get("rawBm25") or [])[:candidate_count]
    normalized_bm25 = list(row.get("normalizedBm25") or [])[:candidate_count]
    keyword = raw_bm25 if row.get("labelScope") == "judged-pool" else normalized_bm25
    vector = list(row.get("vector") or [])[:candidate_count]
    if variant == "raw_bm25":
        return raw_bm25
    if variant == "normalized_bm25":
        return normalized_bm25
    if variant == "vector":
        return vector
    rrf = rrf_merge_rankings(keyword, vector, rrf_k=rrf_k, limit=candidate_count)
    if variant == "rrf":
        return rrf
    filtered = (
        rrf
        if row.get("labelScope") == "judged-pool"
        else relevance_filter(
            rrf,
            products_by_id,
            str(row.get("normalizedQuery") or row.get("query") or ""),
            constraints=(
                row.get("constraints")
                if isinstance(row.get("constraints"), Mapping)
                else None
            ),
        )
    )
    if variant == "rrf_relevance_filter":
        return filtered
    if variant != "full_rerank":
        raise ValueError(f"unsupported Search variant: {variant}")
    rerank_score = {
        str(item["productId"]): float(item["score"])
        for item in row.get("rerank") or []
        if isinstance(item, Mapping) and item.get("productId") is not None
    }
    # The Provider ranked the full collection RRF pool. Restricting that frozen
    # order is a zero-cost Top-N replay; no Provider call occurs here.
    reranked = sorted(filtered, key=lambda item: (-rerank_score.get(item, float("-inf")), item))
    return reranked[:rerank_top_n]


def replay_collection(
    collection: Mapping[str, Any] | Path,
    *,
    products: Sequence[Mapping[str, Any]],
    variants: Sequence[str] = SEARCH_VARIANTS,
    candidate_counts: Sequence[int] = DEFAULT_CANDIDATE_COUNTS,
    rrf_k_values: Sequence[int] = DEFAULT_RRF_K_VALUES,
    rerank_top_n_values: Sequence[int] = DEFAULT_RERANK_TOP_N,
    k_values: Sequence[int] = DEFAULT_SEARCH_K_VALUES,
    split_filter: set[str] | None = None,
    dataset: str = "chinese",
) -> dict[str, Any]:
    """Replay ablations without any Provider or Elasticsearch calls."""

    payload = read_gzip_json(collection) if isinstance(collection, Path) else dict(collection)
    products_by_id = {
        str(row.get("id") or row.get("product_id") or row.get("productId") or ""): row
        for row in products
    }
    variant_rows: dict[str, list[dict[str, Any]]] = {}
    with provider_audit(allow_calls=False) as audit:
        for variant in variants:
            for candidate_count in candidate_counts:
                for rrf_k in (rrf_k_values if variant in {"rrf", "rrf_relevance_filter", "full_rerank"} else [60]):
                    for rerank_top_n in (rerank_top_n_values if variant == "full_rerank" else [candidate_count]):
                        key = f"{variant}:c{candidate_count}:rrf{rrf_k}:n{rerank_top_n}"
                        rows: list[dict[str, Any]] = []
                        for case in payload.get("cases") or []:
                            if split_filter and str(case.get("split")) not in split_filter:
                                continue
                            ranked = _variant_ranking(
                                case,
                                products_by_id,
                                variant=variant,
                                candidate_count=candidate_count,
                                rrf_k=rrf_k,
                                rerank_top_n=rerank_top_n,
                            )
                            judged_pool = case.get("labelScope") == "judged-pool"
                            if judged_pool:
                                judged = set((case.get("relevanceGrades") or {}).keys())
                                ranked = [product_id for product_id in ranked if product_id in judged]
                            metrics = ranking_case_metrics(
                                ranked,
                                case.get("relevanceGrades") or {},
                                k_values=k_values,
                                relevant_threshold=2 if dataset == "chinese" else 1,
                                expected_no_results=bool(case.get("expectedNoResults")),
                                judged_pool=judged_pool,
                            )
                            rows.append(
                                {
                                    "caseId": case["caseId"],
                                    "split": case.get("split"),
                                    "queryType": case.get("queryType"),
                                    "rankedIds": ranked,
                                    "metrics": metrics,
                                }
                            )
                        variant_rows[key] = rows
        provider_facts = audit.snapshot()

    variant_metrics = {
        key: aggregate_ranking_cases([row["metrics"] for row in rows])
        for key, rows in variant_rows.items()
        if rows
    }
    baseline_key = next(
        (
            key
            for key in variant_rows
            if key.startswith("rrf:c24:rrf60:")
        ),
        next(iter(variant_rows), None),
    )
    paired_deltas: dict[str, Any] = {}
    if baseline_key:
        baseline = [
            {"caseId": row["caseId"], **row["metrics"]}
            for row in variant_rows[baseline_key]
            if row["metrics"].get("applicable")
        ]
        for key, rows in variant_rows.items():
            candidate = [
                {"caseId": row["caseId"], **row["metrics"]}
                for row in rows
                if row["metrics"].get("applicable")
            ]
            if len(candidate) != len(baseline):
                continue
            comparisons = (
                ("ndcg", "ndcg", "5"),
                ("recall", "recall", "5"),
                ("reciprocalRank", "mrr", "10"),
            )
            for metric_name, metric_key, metric_k in comparisons:
                if not baseline or metric_k not in (baseline[0].get("metricsByK") or {}):
                    continue
                paired_deltas[f"{key}:{metric_key}@{metric_k}"] = paired_ranking_comparison(
                    baseline,
                    candidate,
                    metric_path=["metricsByK", metric_k, metric_name],
                )
    return {
        "schemaVersion": 1,
        "kind": "search-offline-replay",
        "labelScope": "judged-pool" if dataset == "wands" else "full-catalog",
        "providerFacts": provider_facts,
        "metricCurves": {
            key: metrics["metricCurves"] for key, metrics in variant_metrics.items()
        },
        "variantMetrics": variant_metrics,
        "pairedDeltas": paired_deltas,
        "confidenceIntervals": {
            key: value["confidenceInterval"] for key, value in paired_deltas.items()
        },
        "stageLatency": aggregate_stage_latency(payload.get("cases") or []),
        "cases": variant_rows,
    }


def choose_configuration(replay: Mapping[str, Any]) -> dict[str, Any]:
    """Select dev configuration using the documented lexicographic policy."""

    metrics = replay.get("variantMetrics") or {}
    if not metrics:
        raise ValueError("cannot select Search configuration from an empty replay")

    def score(item: tuple[str, Mapping[str, Any]]) -> tuple[float, float, float, float, str]:
        key, report = item
        curve_5 = (report.get("metricCurves") or {}).get("5") or {}
        curve_10 = (report.get("metricCurves") or {}).get("10") or {}
        latency = (replay.get("stageLatency") or {}).get("rerank") or {}
        return (
            float(curve_5.get("ndcg") or 0),
            float(curve_5.get("recall") or 0),
            float(curve_10.get("mrr") or 0),
            -float(latency.get("p95Ms") or 0),
            key,
        )

    selected_key, selected_metrics = max(metrics.items(), key=score)
    return {
        "selectedVariant": selected_key,
        "selectionOrder": ["NDCG@5", "Recall@5", "MRR@10", "P95 latency"],
        "selectedMetrics": selected_metrics,
    }
