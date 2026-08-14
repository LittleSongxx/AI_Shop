import asyncio
import contextvars
import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import structlog

from app.config.settings import get_settings
from app.harness.guardrails.channel_guard import scan_external_content
from app.harness.guardrails.query_security import separate_explicit_attack_suffix
from app.harness.metrics.runtime_sensors import (
    RAG_CHANNEL_CONTAMINATED,
    RAG_LATENCY,
    RAG_SEARCH_TOTAL,
)
from app.infra.http_client import get_client
from app.observability.telemetry import get_tracer
from app.rag.ab_test import get_rag_overrides
from app.rag.canonical_facts import get_canonical_fact_catalog
from app.rag.embedding import embed_text
from app.rag.evidence_selector import evidence_item_limit, select_minimal_evidence
from app.rag.fact_metadata import get_fact_metadata_catalog
from app.rag.grounding import (
    EvidenceState,
    GroundingEnvelope,
    QueryPlan,
)
from app.rag.policy import runtime_rag_policy
from app.rag.query_expander import deterministic_query_variants, expand_query
from app.rag.query_planner import PlannedRagQuery, plan_rag_query
from app.rag.rrf import rrf_score_at_rank
from app.rag.runtime_trace import (
    RagRuntimeTrace,
    active_rag_runtime_trace,
    rag_runtime_trace_scope,
)
from app.resilience.circuit_breaker import circuit_registry
from app.services.java_internal_client import java_internal_client
from app.services.redis_service import redis_service

logger = structlog.get_logger()
tracer = get_tracer()

PRODUCT_INDEX = "aishop-index"
# Java owns this key. The Agent may read it, but must never refresh or expire it.
KNOWLEDGE_VERSION_CACHE_KEY = "mall:knowledge:version"
# A separate, bounded fallback containing the last catalog that Java returned
# successfully. Keeping it separate prevents an Agent outage from changing the
# authoritative release counter.
KNOWLEDGE_CATALOG_LKG_CACHE_KEY = "mall:agent:knowledge:catalog:last_known_good"
KNOWLEDGE_CATALOG_LKG_TTL_SECONDS = 24 * 60 * 60
KNOWLEDGE_RELEASE_TOPIC = "knowledge.release"

# Elasticsearch rejects a kNN search whose num_candidates exceeds this.
ES_MAX_NUM_CANDIDATES = 10_000

# ES 熔断器参数。registry.get_or_create 是"先到先得"：参数只在首次创建时生效，
# 之后传什么都被忽略。原来 BM25/向量/商品关键词三处各传各的（3/30 vs 默认 5/60），
# 并发下谁先创建谁的阈值就生效，熔断行为不确定。收敛成一份参数。
ES_BREAKER_ARGS = {"failure_threshold": 3, "recovery_timeout": 30}


def _canonical_fact_ids_for_doc(doc: dict[str, Any]) -> frozenset[str]:
    metadata = doc.get("metadata") or {}
    return get_canonical_fact_catalog().facts_for_ref(
        {
            "type": metadata.get("dataType"),
            "questionId": metadata.get("questionId"),
            "source": metadata.get("source") or metadata.get("sourceName"),
            "heading": metadata.get("heading"),
        }
    )


def _promote_canonical_hint_docs(
    ranked_groups: list[list[dict[str, Any]]],
    fused_docs: list[dict[str, Any]],
    *,
    fact_hints: tuple[str, ...] | list[str],
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep explicitly routed canonical facts from being lost at RRF truncation."""

    preferred = {str(value) for value in fact_hints if str(value)}
    if not preferred or limit < 1:
        return fused_docs[: max(0, limit)], []
    promoted: list[dict[str, Any]] = []
    promoted_ids: list[str] = []
    seen: set[str] = set()
    for group in ranked_groups:
        for doc in group:
            identity = str(doc.get("id") or "")
            if not identity or identity in seen:
                continue
            if not preferred.intersection(_canonical_fact_ids_for_doc(doc)):
                continue
            promoted.append(doc)
            promoted_ids.append(identity)
            seen.add(identity)
    combined = [*promoted, *fused_docs]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for doc in combined:
        identity = str(doc.get("id") or "")
        if not identity or identity in selected_ids:
            continue
        selected.append(doc)
        selected_ids.add(identity)
        if len(selected) == limit:
            break
    return selected, promoted_ids


def _rerank_query_with_fact_hints(
    query: str,
    fact_hints: tuple[str, ...] | list[str],
) -> str:
    """Append trusted catalog titles to a routed rerank query, never eval labels."""

    catalog = get_fact_metadata_catalog()
    titles = [
        catalog.facts[fact_id].aliases[0]
        for fact_id in fact_hints
        if fact_id in catalog.facts and catalog.facts[fact_id].aliases
    ]
    if not titles:
        return query
    return (
        f"{query}\n检索目标：{'；'.join(dict.fromkeys(titles))}。"
        "优先直接回答、约束或明确否定该命题的证据。"
    )


@dataclass
class RerankEvaluationStats:
    """Provider/fallback accounting enabled only inside live evaluations."""

    eligible_requests: int = 0
    provider_requests: int = 0
    provider_successes: int = 0
    provider_failures: int = 0
    fallback_count: int = 0
    fallback_reasons: dict[str, int] = field(default_factory=dict)
    response_records: list[dict[str, Any]] = field(default_factory=list)
    instruction_override: str | None = None
    rerank_top_n: int | None = None
    evidence_threshold: float | None = None
    top_score_margin: float | None = None

    def fallback(self, reason: str) -> None:
        self.fallback_count += 1
        self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "eligibleRequests": self.eligible_requests,
            "providerRequests": self.provider_requests,
            "providerSuccesses": self.provider_successes,
            "providerFailures": self.provider_failures,
            "fallbackCount": self.fallback_count,
            "fallbackReasons": dict(sorted(self.fallback_reasons.items())),
            "responseRecords": list(self.response_records),
        }


_RERANK_EVALUATION_STATS: contextvars.ContextVar[RerankEvaluationStats | None] = (
    contextvars.ContextVar("rerank_evaluation_stats", default=None)
)

_ES_KNOWLEDGE_INDEX_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "rag_knowledge_index_override", default=None
)


@contextmanager
def evaluation_es_index_scope(index_name: str | None) -> Iterator[str | None]:
    """Route an evaluation to an isolated ``aishop_eval_`` knowledge index.

    Production calls leave this unset and continue using ``ES_INDEX``.  The
    prefix check prevents an evaluation typo from writing or reading a
    production index while still allowing two immutable context-retrieval
    variants to run in the same process.
    """

    if index_name is not None:
        value = str(index_name).strip()
        if not value.startswith("aishop_eval_"):
            raise ValueError("evaluation knowledge index must start with aishop_eval_")
    else:
        value = None
    token = _ES_KNOWLEDGE_INDEX_OVERRIDE.set(value)
    try:
        yield value
    finally:
        _ES_KNOWLEDGE_INDEX_OVERRIDE.reset(token)


def _knowledge_index_name(settings: Any) -> str:
    return _ES_KNOWLEDGE_INDEX_OVERRIDE.get() or settings.es_index


@contextmanager
def rerank_evaluation_scope(
    *,
    instruction: str | None = None,
    rerank_top_n: int | None = None,
    evidence_threshold: float | None = None,
    top_score_margin: float | None = None,
) -> Iterator[RerankEvaluationStats]:
    """Account live rerank calls and optionally isolate an eval-only instruction."""

    if rerank_top_n is not None and rerank_top_n < 1:
        raise ValueError("evaluation rerank_top_n must be positive")
    if evidence_threshold is not None and not 0 <= evidence_threshold <= 1:
        raise ValueError("evaluation evidence_threshold must be between 0 and 1")
    if top_score_margin is not None and not 0 <= top_score_margin <= 1:
        raise ValueError("evaluation top_score_margin must be between 0 and 1")
    stats = RerankEvaluationStats(
        instruction_override=instruction,
        rerank_top_n=rerank_top_n,
        evidence_threshold=evidence_threshold,
        top_score_margin=top_score_margin,
    )
    token = _RERANK_EVALUATION_STATS.set(stats)
    try:
        yield stats
    finally:
        _RERANK_EVALUATION_STATS.reset(token)


class KnowledgeCatalogUnavailable(RuntimeError):
    """The active knowledge catalog could not be established safely."""


def cosine_to_es_score(cosine: float) -> float:
    """把 cosine 相似度换算成 ES 的 ``_score``。

    ES 对 ``cosineSimilarity`` 的打分是 ``(1 + cos) / 2``，把 [-1, 1] 映射到 [0, 1]。
    这层换算是 ES 的实现细节，不该泄漏到配置里让人心算——写 ``cos >= 0.3`` 是能复核的，
    写 ``_score >= 0.65`` 只能靠注释解释。
    """
    return (1.0 + max(-1.0, min(1.0, float(cosine)))) / 2.0


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

    def normalize_query(self, query: str) -> str:
        """Canonical query used by retrieval and per-turn duplicate prevention."""
        return self._rewrite_query(query)

    def query_key(self, query: str) -> str:
        """Punctuation-insensitive key for duplicate retrieval prevention."""
        return self._normalize_question(self.normalize_query(query))

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

    async def search_faq(self, query: str, top_k: int | None = None, category_filter: list[str] | None = None) -> str:
        result = await self.search_faq_with_trace(query, top_k, category_filter=category_filter)
        return str(result.get("text") or "")

    async def search_faq_with_trace(
        self,
        query: str,
        top_k: int | None = None,
        category_filter: list[str] | None = None,
        bucket: str = "A",
        include_evaluation_candidates: bool = False,
        query_variants: list[str] | None = None,
        security_flags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        policy = runtime_rag_policy()
        runtime_trace = RagRuntimeTrace(policy_fingerprint=policy.fingerprint())
        with rag_runtime_trace_scope(runtime_trace):
            result = await self._search_faq_with_trace(
                query,
                top_k,
                category_filter=category_filter,
                bucket=bucket,
                include_evaluation_candidates=include_evaluation_candidates,
                query_variants=query_variants,
                security_flags=security_flags,
            )
        trace = result.setdefault("trace", {})
        trace["runtime"] = runtime_trace.public()
        return result

    async def _search_faq_with_trace(
        self,
        query: str,
        top_k: int | None = None,
        category_filter: list[str] | None = None,
        bucket: str = "A",
        include_evaluation_candidates: bool = False,
        query_variants: list[str] | None = None,
        security_flags: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """Search FAQ/knowledge and retain bounded evidence for observability.

        ``bucket`` 是 A/B 分桶（默认 "A" = 基线）。A/B 覆盖参数（rag_top_k /
        rerank_top_n）在这里统一生效，并写进语义缓存键——不同策略的检索结果
        绝不能互相污染（P0-4：旧缓存键只有版本+查询哈希，filter/top_k/分桶/
        rerank 配置变了也会命中旧结果）。
        """
        started = time.perf_counter()
        settings = get_settings()
        policy = runtime_rag_policy()
        overrides = get_rag_overrides(bucket)
        effective_top_k = int(overrides.get("rag_top_k") or top_k or settings.rag_top_k)
        evaluation = _RERANK_EVALUATION_STATS.get()
        rerank_top_n = int(
            evaluation.rerank_top_n
            if evaluation is not None and evaluation.rerank_top_n is not None
            else overrides.get("rerank_top_n") or policy.rerank_top_n
        )

        original_query = str(query or "")
        separation = separate_explicit_attack_suffix(original_query)
        collected_security_flags = list(security_flags or [])
        collected_security_flags.extend(separation.security_flags)
        cleaned = self.normalize_query(separation.safe_query)
        planning_started = time.perf_counter()
        planned_query = plan_rag_query(
            cleaned,
            max_subquestions=policy.max_subquestions,
        )
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.observe(
                "decomposition", (time.perf_counter() - planning_started) * 1000
            )
            runtime_trace.route = planned_query.route
            runtime_trace.observations["plannedQuery"] = planned_query.public(
                actual_variant_count=len(planned_query.deterministic_variants),
                llm_expansion_calls=0,
            )
        if not cleaned:
            self._observe_search(started, False, "empty")
            return self._trace_result(
                cleaned,
                0,
                "empty",
                False,
                [],
                started,
                original_query=original_query,
                security_flags=collected_security_flags,
            )

        # Direct callers such as evaluation runners do not pass through the API
        # input guard. A pure attack therefore fails closed here; an explicit
        # mixed suffix has already been separated above and the legitimate prefix
        # continues through retrieval.
        direct_verdict = scan_external_content(original_query)
        if direct_verdict.contaminated and not separation.separated:
            collected_security_flags.extend(direct_verdict.matched_rules)
            self._observe_search(started, False, "input_quarantine")
            return self._trace_result(
                cleaned,
                0,
                "input_quarantine",
                False,
                [],
                started,
                original_query=original_query,
                security_flags=collected_security_flags,
                forced_evidence_state=EvidenceState.QUARANTINED,
            )

        catalog_started = time.perf_counter()
        catalog = await self._knowledge_catalog()
        if runtime_trace is not None:
            runtime_trace.observe("catalog", (time.perf_counter() - catalog_started) * 1000)
        try:
            version = int(catalog["version"]) if catalog else await self._knowledge_version()
        except KnowledgeCatalogUnavailable:
            # Exact FAQ is independently authoritative. A zero sentinel is
            # observable in trace and is never used to authorize knowledge ES
            # documents.
            version = 0
        exact_started = time.perf_counter()
        exact = await self._exact_faq(cleaned, version)
        if runtime_trace is not None:
            runtime_trace.observe("exactFaq", (time.perf_counter() - exact_started) * 1000)
        if exact:
            docs = [self._faq_row_to_doc(exact, score=1.0)]
            self._observe_search(started, True, "exact")
            return self._trace_result(
                cleaned,
                version,
                "exact",
                True,
                docs,
                started,
                bucket=bucket,
                evaluation_candidates=docs if include_evaluation_candidates else None,
                original_query=original_query,
                security_flags=collected_security_flags,
                normalization_rules=(
                    ("explicit_attack_suffix_removed",) if separation.separated else ()
                ),
            )

        cache_key = self._semantic_cache_key(
            cleaned, version, effective_top_k, category_filter, bucket, settings, rerank_top_n
        )
        cached = None if include_evaluation_candidates else await self._get_cache(cache_key)
        if cached:
            # 缓存里的 FAQ 可能已过期（发布后新版本号才会换 key），读出来再滤一遍。
            cached = self._filter_catalog(self._filter_expired(cached), catalog)
            cached = self._filter_evidence_docs(
                cached,
                preferred_fact_ids=planned_query.fact_hints,
            )
            if not cached:
                # 缓存条目全部已过生效窗口（effectiveEnd 已过）——这是"假命中"：
                # 不能返回空证据、也不能把 hit=True 记进命中率/盲评样本（P1 审查），
                # 回源混合检索重新出结果。
                logger.warning("rag_cache_hit_all_expired", query=cleaned[:80])
            else:
                # B2：按比例抽样命中记录进盲评队列——语义缓存的失败是静默的，
                # 不抽样盲评就发现不了误报（行业建议按周评审误报率）。
                await self._sample_cache_hit(cleaned, cached)
                if runtime_trace is not None:
                    runtime_trace.cache_hits += 1
                self._observe_search(started, True, "cache")
                return self._trace_result(
                    cleaned,
                    version,
                    "cache",
                    True,
                    cached,
                    started,
                    bucket=bucket,
                    original_query=original_query,
                    security_flags=collected_security_flags,
                    variant_count=0,
                    normalization_rules=(
                        ("explicit_attack_suffix_removed",) if separation.separated else ()
                    ),
                )

        extra_filters: list[dict] | None = (
            [{"terms": {"metadata.category": category_filter}}] if category_filter else None
        )
        # P0-5：knowledge 文档按"已发布版本"过滤（version <= 当前版本，见
        # _search_knowledge_docs 的 range 条件），版本号即发布提交点。
        # Java 发布端先写"下一版本盖章"的切片、成功后才 bump 版本号——
        # 未 bump 前新切片（v=当前+1）对检索端不可见，中途失败=从未发布；
        # FAQ 不受版本控制（生命周期由 rag_question 表 + 生效时间管理），永远可见。
        # 目录版本和活跃文档集合由 Java 端权威目录门控；缓存命中也必须再次经过
        # 当前目录过滤，避免旧版本结果在发布或归档后继续返回。
        # 如果 Java 与 last-known-good 目录都不可用，只允许 FAQ 分支继续，
        # 绝不能用固定版本或无目录检索可能已经归档的知识切片。
        candidates = await self._search_knowledge_docs(
            cleaned,
            effective_top_k,
            extra_filters=extra_filters,
            rerank_top_n=rerank_top_n,
            version_filter=version if catalog else None,
            active_document_ids=(catalog or {}).get("active_document_ids") if catalog else None,
            knowledge_enabled=bool(catalog),
            queries=query_variants,
            planned_query=planned_query,
        )
        if catalog is not None:
            candidates = self._filter_catalog(candidates, catalog)
        docs = self._filter_evidence_docs(
            candidates,
            preferred_fact_ids=planned_query.fact_hints,
        )
        if not docs:
            self._observe_search(started, False, "hybrid")
            return self._trace_result(
                cleaned,
                version,
                "hybrid",
                False,
                candidates,
                started,
                bucket=bucket,
                evaluation_candidates=candidates if include_evaluation_candidates else None,
                original_query=original_query,
                security_flags=collected_security_flags,
                variant_count=int(
                    (runtime_trace.observations.get("plannedQuery") or {}).get(
                        "actualVariantCount", len(planned_query.deterministic_variants)
                    )
                ) if runtime_trace is not None else len(planned_query.deterministic_variants),
                normalization_rules=(
                    ("explicit_attack_suffix_removed",) if separation.separated else ()
                ),
            )
        if not include_evaluation_candidates:
            await self._set_cache(cache_key, docs, settings.rag_cache_ttl_seconds)
        self._observe_search(started, True, "hybrid")
        return self._trace_result(
            cleaned,
            version,
            "hybrid",
            True,
            docs,
            started,
            bucket=bucket,
            candidate_count=len(candidates),
            evaluation_candidates=candidates if include_evaluation_candidates else None,
            original_query=original_query,
            security_flags=collected_security_flags,
            variant_count=int(
                (runtime_trace.observations.get("plannedQuery") or {}).get(
                    "actualVariantCount", len(planned_query.deterministic_variants)
                )
            ) if runtime_trace is not None else len(planned_query.deterministic_variants),
            normalization_rules=(
                ("explicit_attack_suffix_removed",) if separation.separated else ()
            ),
        )

    async def _query_variants(
        self,
        query: str,
        supplied: list[str] | None = None,
    ) -> list[str]:
        variants = await expand_query(query)
        result: list[str] = []
        seen: set[str] = set()
        for value in [query, *(supplied or []), *variants]:
            cleaned = self.normalize_query(str(value or ""))
            key = self._normalize_question(cleaned)
            if not cleaned or not key or key in seen:
                continue
            verdict = scan_external_content(cleaned)
            if verdict.contaminated:
                continue
            seen.add(key)
            result.append(cleaned)
            if len(result) == 3:
                break
        return result or [query]

    def _semantic_cache_key(
        self,
        cleaned: str,
        version: int,
        top_k: int,
        category_filter: list[str] | None,
        bucket: str,
        settings,
        rerank_top_n: int,
    ) -> str:
        """语义缓存的键 = 所有会改变检索结果的配置维度的指纹。

        旧实现只有 ``v{version}:sha256(cleaned)``——换 top_k、加类别过滤、
        换 A/B 分桶或 rerank 配置都会命中上一次策略的结果，把错误证据写进回答。
        这里把影响检索的参数全部折叠进 key；rerank 折叠启用状态、模型、地址、
        API 格式和任务指令（这些变化都会改变结果），但不折叠 API key。
        """
        payload = {
            "q": cleaned,
            "top_k": top_k,
            "filters": category_filter or [],
            "bucket": bucket,
            "rerank_top_n": rerank_top_n,
            "rerank_model": settings.rerank_model,
            "rerank_url": settings.rerank_base_url,
            "rerank_api_format": settings.rerank_api_format,
            "rerank_instruct": settings.rerank_instruct,
            "rerank_enabled": bool(settings.rerank_api_key.strip()),
            "knowledge_index": _knowledge_index_name(settings),
            "rag_policy_fingerprint": runtime_rag_policy().fingerprint(),
            "min_cosine": settings.rag_vector_min_cosine,
            "knn_factor": settings.knn_num_candidates_factor,
            "knn_min": settings.knn_num_candidates_min,
            # 证据闸门阈值也折进 key：运营收紧 rag_evidence_min_relevance /
            # rag_evidence_min_rrf_rank 后，旧标准写入的缓存条目不应继续按
            # 命中输出（P1 审查：阈值调整最长滞后一个 TTL）。
            "min_relevance": settings.rag_evidence_min_relevance,
            "top_score_margin": settings.rag_evidence_top_score_margin,
            "min_rrf_rank": settings.rag_evidence_min_rrf_rank,
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return f"mall:rag:semantic:v{version}:{_sha256(canonical)}"

    async def _sample_cache_hit(self, cleaned: str, cached: list[dict]) -> None:
        """按 RAG_CACHE_SAMPLE_RATE 抽样缓存命中，供离线盲评误报率。"""
        import random

        if not cached:
            # 空证据绝不进盲评队列——假命中样本会污染误报率统计（P1 审查）。
            return
        settings = get_settings()
        if settings.rag_cache_sample_rate <= 0 or random.random() > settings.rag_cache_sample_rate:
            return
        try:
            sample = {
                "ts": int(time.time() * 1000),
                "query": cleaned[:120],
                "hitDocs": [
                    {
                        "id": doc.get("id"),
                        "source": str((doc.get("metadata") or {}).get("source") or ""),
                        "score": round(float(doc.get("score") or 0), 4),
                    }
                    for doc in cached[:5]
                ],
            }
            # 按天分 key：全局共享一个 cap 200 的队列，日命中量大时早间样本
            # 会被全部冲掉，"按周评审"只剩最近几小时（P1 审查）。按天分 key
            # 后每天的样本独立留存。
            day_key = time.strftime("%Y%m%d")
            await redis_service._push_capped(
                f"mall:rag:cache:sample:v1:{day_key}",
                sample,
                event="cache_hit_sample",
                user_id="",
            )
        except Exception as exc:
            logger.debug("rag_cache_sample_failed", error=str(exc))

    async def exact_faq_answer(self, query: str) -> dict | None:
        """Return a curated FAQ answer without invoking the LLM."""
        started = time.perf_counter()
        cleaned = self._rewrite_query(query)
        if not cleaned:
            self._observe_search(started, False, "exact_fast_path")
            self._finalize_search(False, "exact_fast_path")
            return None
        settings = get_settings()
        try:
            # The latency budget covers both authoritative version resolution and
            # the FAQ lookup. Otherwise a slow version endpoint could block the
            # supposedly-fast path for the Java client's full HTTP timeout before
            # the old wait_for() even started.
            async with asyncio.timeout(settings.faq_fast_path_timeout_seconds):
                try:
                    version = await self._knowledge_version()
                except KnowledgeCatalogUnavailable as exc:
                    # The release version only partitions Redis cache entries. It
                    # must not make the Java-owned exact FAQ endpoint unavailable.
                    # Version 0 bypasses cache reads/writes, so a stale Redis hint
                    # can never authorize an old exact answer during an outage.
                    version = 0
                    logger.warning("faq_fast_path_version_unavailable", error=str(exc))
                row = await self._exact_faq(cleaned, version)
        except TimeoutError:
            logger.warning("faq_fast_path_timeout", query_hash=_sha256(cleaned))
            self._observe_search(started, False, "exact_fast_path_timeout")
            self._finalize_search(False, "exact_fast_path_timeout")
            return None
        answer = str((row or {}).get("answer") or "").strip()
        if not answer:
            self._observe_search(started, False, "exact_fast_path")
            self._finalize_search(False, "exact_fast_path")
            return None
        # A2 通道检疫：快路径答案直达用户、无 LLM 边界，是知识库投毒的最高杠杆
        # 出口。投毒的 FAQ 行（在混合路径会被 _trace_result 剔除）在这里必须
        # 按"无证据"处理落回正常 LLM 路径，不能原样放行；污染痕迹入指标与日志，
        # 命中计数按最终结论记为 miss。
        question = str((row or {}).get("question") or "").strip()
        verdict = scan_external_content(f"{question}\n{answer}" if question else answer)
        if verdict.contaminated:
            rules = sorted(verdict.matched_rules)
            RAG_CHANNEL_CONTAMINATED.labels(rules=",".join(rules)).inc()
            logger.warning(
                "rag_exact_faq_quarantined",
                query=cleaned[:80],
                question_id=row.get("question_id") or row.get("questionId"),
                rules=rules,
                text_length=len(answer),
            )
            self._observe_search(started, False, "exact_fast_path")
            self._finalize_search(False, "exact_fast_path")
            return None
        self._observe_search(started, True, "exact_fast_path")
        self._finalize_search(True, "exact_fast_path")
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
        breaker = circuit_registry.get_or_create("es", **ES_BREAKER_ARGS)
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

    async def _search_knowledge_docs(
        self,
        query: str,
        limit: int,
        extra_filters: list[dict] | None = None,
        rerank_top_n: int | None = None,
        version_filter: int | None = None,
        active_document_ids: list[str] | None = None,
        knowledge_enabled: bool = True,
        queries: list[str] | None = None,
        planned_query: PlannedRagQuery | None = None,
    ) -> list[dict]:
        with tracer.start_as_current_span("rag.hybrid_search") as span:
            span.set_attribute("rag.query_length", len(query))
            span.set_attribute("rag.limit", limit)

            policy = runtime_rag_policy()
            plan = planned_query or plan_rag_query(
                query, max_subquestions=policy.max_subquestions
            )
            if queries is None and planned_query is None:
                effective_queries = [query]
            else:
                effective_queries = list(
                    dict.fromkeys(
                        queries
                        or list(plan.deterministic_variants)
                        or deterministic_query_variants(query)
                    )
                )[: policy.max_query_variants]
            span.set_attribute("rag.query_variants", len(effective_queries))
            runtime_trace = active_rag_runtime_trace()
            if runtime_trace is not None:
                runtime_trace.observations["knowledgeIndex"] = _knowledge_index_name(
                    get_settings()
                )

            # P0-5 版本过滤：knowledge 必须「已发布」（version <= 当前版本），
            # FAQ 不受版本约束。注意是 range(lte) 而不是 term(==)：
            # Java 发布端每次发布给新切片盖「全局下一版本」章（releaseVersion()+1），
            # 若用精确匹配，发布文档 B 后文档 A（盖章 v2 < 当前 v3）会立即从
            # 检索里消失——版本号是全局单调的，精确匹配只留得下「最近一次 bump
            # 的文档」。lte 下：在途未 bump 的切片（v=当前+1）天然不可见，
            # bump 后新旧切片共存；归档靠「删切片 + bump 后状态变更」保证不可见。
            # metadata.version 是数值类型（ES 动态映射），range 用数值直配。
            effective_filters = list(extra_filters or [])
            if knowledge_enabled and version_filter is not None:
                active_ids = [str(value) for value in (active_document_ids or []) if str(value)]
                # An empty active set is a valid catalog state, but using an
                # impossible sentinel avoids relying on ES-specific handling of
                # an empty `terms` array.
                document_filter = {
                    "terms": {
                        "metadata.documentId.keyword": active_ids
                        or ["__no_active_document__"]
                    }
                }
                effective_filters.append(
                    {
                        "bool": {
                            "should": [
                                {"term": {"metadata.dataType.keyword": "faq"}},
                                {
                                    "bool": {
                                        "must": [
                                            {"term": {"metadata.dataType.keyword": "knowledge"}},
                                            {"term": {"metadata.status.keyword": "PUBLISHED"}},
                                            {
                                                "range": {
                                                    "metadata.version": {
                                                        "lte": int(version_filter)
                                                    }
                                                }
                                            },
                                            document_filter,
                                        ]
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )

            async def _hybrid_one(q: str) -> tuple[list[dict], list[dict], list[dict]]:
                data_types = ("faq", "knowledge") if knowledge_enabled else ("faq",)
                kw, vec = await asyncio.gather(
                    self._keyword_search_docs(
                        q, data_types, limit, effective_filters
                    ),
                    self._vector_search(
                        q, data_types, limit, extra_filters=effective_filters
                    ),
                )
                merged = self._rrf_docs([kw, vec], limit=max(limit, 1))
                # 过期 FAQ 在并进最终融合之前就滤掉：先限量再过滤会让过期项
                # 白白占住 top-k 名额，把有效文档挤出候选池。
                return kw, vec, self._filter_expired(merged)

            initial_groups = await asyncio.gather(
                *[_hybrid_one(q) for q in effective_queries]
            )
            ranked_groups = [group[2] for group in initial_groups]
            bm25_ids = {
                str(doc.get("id") or "") for group in initial_groups for doc in group[0]
            }
            vector_ids = {
                str(doc.get("id") or "") for group in initial_groups for doc in group[1]
            }
            candidate_domains = {
                str((doc.get("metadata") or {}).get("domain") or "").upper()
                for group in ranked_groups
                for doc in group
                if str((doc.get("metadata") or {}).get("domain") or "")
            }
            missing_domains = set(plan.domains) - candidate_domains
            low_dual_route_overlap = not bool(bm25_ids.intersection(vector_ids))
            expansion_reasons = list(plan.expansion_reasons)
            if not ranked_groups or not any(ranked_groups):
                expansion_reasons.append("no_initial_candidates")
            if missing_domains:
                expansion_reasons.append("missing_domain_coverage")
            if low_dual_route_overlap:
                expansion_reasons.append("low_bm25_vector_overlap")

            # A low BM25/vector overlap is only a signal.  Do not pay for an
            # online expansion on a clear question when both channels already
            # produced a healthy candidate pool; expand only when coverage is
            # demonstrably thin or the planner marked a complex/negative
            # workflow.
            initial_candidate_count = len({
                str(doc.get("id") or "")
                for group in ranked_groups
                for doc in group
                if str(doc.get("id") or "")
            })
            needs_adaptive_expansion = bool(
                not any(ranked_groups)
                or missing_domains
                or any(
                    reason in expansion_reasons
                    for reason in (
                        "multiple_business_subquestions",
                        "capability_or_negative_claim",
                        "multi_step_workflow",
                    )
                )
                or (low_dual_route_overlap and initial_candidate_count < max(3, limit // 2))
            )
            llm_expansion_calls = 0
            if policy.adaptive_expansion and queries is None and needs_adaptive_expansion:
                llm_expansion_calls = 1
                expansion_started = time.perf_counter()
                expanded = await expand_query(query)
                if runtime_trace is not None:
                    runtime_trace.observe(
                        "llmExpansion",
                        (time.perf_counter() - expansion_started) * 1000,
                    )
                additions = [
                    value
                    for value in expanded
                    if value not in effective_queries
                ][: max(0, policy.max_query_variants - len(effective_queries))]
                if additions:
                    additional_groups = await asyncio.gather(
                        *[_hybrid_one(value) for value in additions]
                    )
                    effective_queries.extend(additions)
                    initial_groups.extend(additional_groups)
                    ranked_groups.extend(group[2] for group in additional_groups)
            if runtime_trace is not None:
                runtime_trace.observations["plannedQuery"] = plan.public(
                    actual_variant_count=len(effective_queries),
                    llm_expansion_calls=llm_expansion_calls,
                )
                runtime_trace.observations["adaptiveExpansionReasons"] = list(
                    dict.fromkeys(expansion_reasons)
                )

            rrf_started = time.perf_counter()
            rrf_docs = self._rrf_docs(list(ranked_groups), limit=max(limit, 1))
            hint_candidate_groups = [
                route
                for group in initial_groups
                for route in (group[2], group[0], group[1])
            ]
            rrf_docs, promoted_hint_ids = _promote_canonical_hint_docs(
                hint_candidate_groups,
                rrf_docs,
                fact_hints=plan.fact_hints,
                limit=max(limit, 1),
            )
            if runtime_trace is not None:
                runtime_trace.observe("rrf", (time.perf_counter() - rrf_started) * 1000)
                runtime_trace.observations["candidateChannels"] = {
                    "bm25": [
                        self._source_refs([doc], version_filter or 0)[0]
                        for group in initial_groups
                        for doc in group[0]
                        if self._source_refs([doc], version_filter or 0)
                    ],
                    "vector": [
                        self._source_refs([doc], version_filter or 0)[0]
                        for group in initial_groups
                        for doc in group[1]
                        if self._source_refs([doc], version_filter or 0)
                    ],
                    "rrf": [
                        self._source_refs([doc], version_filter or 0)[0]
                        for doc in rrf_docs
                        if self._source_refs([doc], version_filter or 0)
                    ],
                }
                runtime_trace.observations["canonicalHintPromotions"] = (
                    promoted_hint_ids
                )
            rrf_docs = self._filter_expired(rrf_docs)
            span.set_attribute("rag.active_hits", len(rrf_docs))
            if not rrf_docs:
                return []
            canonical = False
            if len(rrf_docs) == 1:
                try:
                    from app.rag.canonical_facts import get_canonical_fact_catalog

                    ref = self._source_refs([rrf_docs[0]], version_filter or 0)[0]
                    canonical = bool(get_canonical_fact_catalog().facts_for_ref(ref))
                except (IndexError, ValueError):
                    canonical = False
            if canonical:
                if runtime_trace is not None:
                    runtime_trace.observations["rerankSkipped"] = "unique_canonical_hit"
                return rrf_docs[:1]
            rerank_query = _rerank_query_with_fact_hints(query, plan.fact_hints)
            effective_rerank_top_n = (
                len(rrf_docs)
                if plan.fact_hints
                else min(rerank_top_n or policy.rerank_top_n, limit)
            )
            if runtime_trace is not None:
                runtime_trace.observations["rerankRouting"] = {
                    "factHints": list(plan.fact_hints),
                    "configuredTopN": rerank_top_n or policy.rerank_top_n,
                    "effectiveTopN": effective_rerank_top_n,
                    "queryAugmented": rerank_query != query,
                }
            result = await self._rerank(
                rerank_query,
                rrf_docs,
                effective_rerank_top_n,
            )
            if result and any(doc.get("source") != "rerank" for doc in result):
                if runtime_trace is not None:
                    runtime_trace.fallback("rerank_not_completed")
                logger.error("rag_rerank_required_but_fallback_returned")
                return []
            if runtime_trace is not None:
                runtime_trace.observations.setdefault("candidateChannels", {})[
                    "rerank"
                ] = [
                    self._source_refs([doc], version_filter or 0)[0]
                    for doc in result
                    if self._source_refs([doc], version_filter or 0)
                ]
            span.set_attribute("rag.result_count", len(result))
            return result

    async def _keyword_search_docs(
        self,
        query: str,
        data_types: tuple[str, ...],
        limit: int,
        extra_filters: list[dict] | None = None,
    ) -> list[dict]:
        stage_started = time.perf_counter()
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.called("elasticsearchBm25")
        breaker = circuit_registry.get_or_create("es", **ES_BREAKER_ARGS)
        if not breaker.allow_request() or not query.strip():
            if runtime_trace is not None:
                runtime_trace.observe("bm25", (time.perf_counter() - stage_started) * 1000)
                if query.strip():
                    runtime_trace.fallback("bm25_breaker_open")
            return []
        try:
            body = {
                "size": min(max(limit, 1), 50),
                "query": {
                    "bool": {
                        "filter": [
                            {"terms": {"metadata.dataType.keyword": list(data_types)}},
                            *(extra_filters or []),
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
                self._es_url(
                    f"/{_knowledge_index_name(get_settings())}/_search"
                ),
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            if runtime_trace is not None:
                runtime_trace.observe("bm25", (time.perf_counter() - stage_started) * 1000)
            return [self._hit_to_doc(hit, "bm25") for hit in hits]
        except Exception as exc:
            breaker.record_failure()
            logger.error("es_keyword_search_failed", data_types=data_types, error=str(exc))
            if runtime_trace is not None:
                runtime_trace.observe("bm25", (time.perf_counter() - stage_started) * 1000)
                runtime_trace.fallback("bm25_provider_error")
            return []

    async def _vector_search(
        self,
        query: str,
        data_type: str | tuple[str, ...],
        top_k: int | None = None,
        min_cosine: float | None = None,
        extra_filters: list[dict] | None = None,
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
        # 熔断参数与 _keyword_search_docs/_hybrid_one 收敛到同一份（P1 审查：
        # 此前此处仍是默认 5/60，asyncio.gather 并发下先到先得，参数不唯一）。
        breaker = circuit_registry.get_or_create("es", **ES_BREAKER_ARGS)
        if not breaker.allow_request():
            runtime_trace = active_rag_runtime_trace()
            if runtime_trace is not None:
                runtime_trace.fallback("vector_breaker_open")
            return []
        runtime_trace = active_rag_runtime_trace()
        if runtime_trace is not None:
            runtime_trace.called("embedding")
        embedding_started = time.perf_counter()
        vector = await embed_text(query)
        if runtime_trace is not None:
            runtime_trace.observe("embedding", (time.perf_counter() - embedding_started) * 1000)
        if not vector:
            # The ES call never happened, so hand back any half-open probe slot
            # instead of holding it until the reclaim timeout.
            breaker.release_probe()
            if runtime_trace is not None:
                runtime_trace.fallback("embedding_empty")
            return []
        data_types = [data_type] if isinstance(data_type, str) else list(data_type)
        try:
            vector_started = time.perf_counter()
            if runtime_trace is not None:
                runtime_trace.called("elasticsearchVector")
            body = {
                "size": k,
                "knn": {
                    "field": settings.es_vector_field,
                    "query_vector": vector,
                    "k": k,
                    "num_candidates": knn_num_candidates(k, settings),
                    "filter": {
                        "bool": {
                            "must": [
                                {"terms": {"metadata.dataType.keyword": data_types}},
                                *(extra_filters or []),
                            ]
                        }
                    },
                },
                "_source": ["content", "metadata", "text"],
            }
            client = await get_client("es", timeout=20)
            resp = await client.post(
                self._es_url(f"/{_knowledge_index_name(settings)}/_search"),
                json=body,
                timeout=20,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
            breaker.record_success()
            if runtime_trace is not None:
                runtime_trace.observe("vector", (time.perf_counter() - vector_started) * 1000)
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
            if runtime_trace is not None:
                runtime_trace.observe("vector", (time.perf_counter() - vector_started) * 1000)
                runtime_trace.fallback("vector_provider_error")
            return []

    async def _rerank(
        self,
        query: str,
        docs: list[dict],
        limit: int,
        *,
        instruction_override: str | None = None,
    ) -> list[dict]:
        settings = get_settings()
        if not docs or limit <= 0:
            return []
        evaluation = _RERANK_EVALUATION_STATS.get()
        instruction = (
            instruction_override
            if instruction_override is not None
            else evaluation.instruction_override
            if evaluation is not None and evaluation.instruction_override is not None
            else getattr(settings, "rerank_instruct", "")
        )
        if evaluation is not None:
            evaluation.eligible_requests += 1
        runtime_trace = active_rag_runtime_trace()
        fallback = docs[:limit]
        if not settings.rerank_api_key.strip():
            if evaluation is not None:
                evaluation.fallback("unconfigured")
            if runtime_trace is not None:
                runtime_trace.fallback("rerank_unconfigured")
            return fallback
        breaker = circuit_registry.get_or_create("rerank", failure_threshold=3, recovery_timeout=30)
        if not breaker.allow_request():
            if evaluation is not None:
                evaluation.fallback("breaker_open")
            if runtime_trace is not None:
                runtime_trace.fallback("rerank_breaker_open")
            return fallback
        try:
            rerank_started = time.perf_counter()
            if runtime_trace is not None:
                runtime_trace.called("rerank")
            if evaluation is not None:
                evaluation.provider_requests += 1
            # ES searched the enriched content, but a generated contextual prefix
            # is never evidence. Rerank sees only the immutable original body.
            candidates = [self._doc_text(doc)[:4000] for doc in docs]
            top_n = min(limit, len(candidates))
            if settings.rerank_api_format == "compatible":
                request_body: dict[str, Any] = {
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": candidates,
                    "top_n": top_n,
                }
                if instruction.strip():
                    request_body["instruct"] = instruction.strip()
            else:
                request_body = {
                    "model": settings.rerank_model,
                    "input": {
                        "query": query,
                        "documents": candidates,
                    },
                    "parameters": {
                        "return_documents": False,
                        "top_n": top_n,
                    },
                }
            client = await get_client("rerank", timeout=settings.rerank_timeout)
            resp = await client.post(
                settings.rerank_base_url,
                headers={
                    "Authorization": f"Bearer {settings.rerank_api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=settings.rerank_timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            if settings.rerank_api_format == "compatible":
                results = payload.get("results") if isinstance(payload, dict) else None
            else:
                output = payload.get("output") if isinstance(payload, dict) else None
                results = output.get("results") if isinstance(output, dict) else None
            if not isinstance(results, list):
                raise ValueError("invalid rerank response")
            reranked = []
            seen_indexes: set[int] = set()
            for item in results:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                if (
                    isinstance(index, int)
                    and not isinstance(index, bool)
                    and 0 <= index < len(docs)
                    and index not in seen_indexes
                ):
                    doc = dict(docs[index])
                    score = item.get("relevance_score")
                    if score is None:
                        score = item.get("score")
                    if score is None:
                        score = doc.get("score") or 0
                    try:
                        doc["score"] = float(score)
                    except (TypeError, ValueError):
                        continue
                    doc["source"] = "rerank"
                    reranked.append(doc)
                    seen_indexes.add(index)
            if not reranked:
                raise ValueError("rerank response contains no valid results")
            breaker.record_success()
            if runtime_trace is not None:
                runtime_trace.observe("rerank", (time.perf_counter() - rerank_started) * 1000)
            if evaluation is not None:
                evaluation.provider_successes += 1
                evaluation.response_records.append(
                    {
                        "queryHash": _sha256(query),
                        "instructionHash": _sha256(instruction),
                        "candidateIds": [str(doc.get("id") or "") for doc in docs],
                        "results": [
                            {
                                "id": str(doc.get("id") or ""),
                                "score": round(float(doc.get("score") or 0), 8),
                            }
                            for doc in reranked
                        ],
                    }
                )
            return reranked
        except Exception as exc:
            breaker.record_failure()
            if evaluation is not None:
                evaluation.provider_failures += 1
                evaluation.fallback("provider_error")
            logger.warning("rerank_failed_fallback_rrf", error=str(exc))
            if runtime_trace is not None:
                runtime_trace.observe("rerank", (time.perf_counter() - rerank_started) * 1000)
                runtime_trace.fallback("rerank_provider_error")
            return fallback

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
        if not products or len(products) <= 1:
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
                product = id_to_product[pid]
                product["_search_rerank_source"] = (
                    "rerank" if doc.get("source") == "rerank" else "rrf_fallback"
                )
                result.append(product)
        # Guard against unexpected gaps in the reranker response (shouldn't
        # happen because _rerank already falls back to the original order).
        for p in products:
            pid = str(p.get("product_id") or p.get("productId") or "")
            if pid and pid not in seen:
                p["_search_rerank_source"] = "rrf_fallback"
                result.append(p)
        return result[:limit]

    async def _exact_faq(self, query: str, version: int) -> dict | None:
        if version > 0:
            cached = await self._get_faq_exact_cache(version, query)
            if cached:
                return cached
        try:
            row = await java_internal_client.exact_faq(query)
            if row and version > 0:
                await self._set_faq_exact_cache(version, query, row)
            return row
        except Exception as exc:
            logger.warning("faq_exact_failed", error=str(exc))
            return None

    async def _read_release_hint(self) -> tuple[int | None, bool]:
        """Read Java's release hint without ever writing it.

        The boolean distinguishes an absent key from a Redis failure/invalid
        value. During a Java outage an LKG catalog is only safe when we know the
        hint read succeeded; otherwise a newer release could be hidden.
        """
        try:
            cached = await redis_service.client.get(KNOWLEDGE_VERSION_CACHE_KEY)
        except Exception as exc:
            logger.warning("knowledge_release_hint_unavailable", error=str(exc))
            return None, False
        if cached is None or str(cached).strip() == "":
            return None, True
        try:
            version = int(cached)
        except (TypeError, ValueError):
            logger.error("knowledge_release_hint_invalid", value=str(cached)[:64])
            return None, False
        if version < 1:
            logger.error("knowledge_release_hint_invalid", value=version)
            return None, False
        return version, True

    async def _read_last_known_good_catalog(self) -> dict[str, Any] | None:
        try:
            raw = await redis_service.client.get(KNOWLEDGE_CATALOG_LKG_CACHE_KEY)
        except Exception as exc:
            logger.warning("knowledge_catalog_lkg_unavailable", error=str(exc))
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(payload, dict):
                raise ValueError("catalog is not an object")
            version = int(payload.get("version"))
            active_ids = payload.get("active_document_ids")
            if active_ids is None:
                active_ids = payload.get("activeDocumentIds")
            if not isinstance(active_ids, list) or version < 1:
                raise ValueError("catalog fields are invalid")
            normalized_ids: list[str] = []
            for value in active_ids:
                item = str(value).strip() if value is not None else ""
                if not item:
                    raise ValueError("catalog contains an empty document id")
                if item not in normalized_ids:
                    normalized_ids.append(item)
            raw_documents = payload.get("documents")
            if raw_documents is not None and not isinstance(raw_documents, list):
                raise ValueError("catalog documents are invalid")
            result = {
                "version": version,
                "active_document_ids": normalized_ids,
            }
            if raw_documents is not None:
                result["documents"] = self._normalize_catalog_documents(raw_documents)
            return result
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error("knowledge_catalog_lkg_invalid", error=str(exc))
            return None

    async def _save_last_known_good_catalog(self, catalog: dict[str, Any]) -> None:
        payload = json.dumps(catalog, ensure_ascii=False, sort_keys=True)
        try:
            # Only the Agent-owned key receives a TTL.
            await redis_service.client.setex(
                KNOWLEDGE_CATALOG_LKG_CACHE_KEY,
                KNOWLEDGE_CATALOG_LKG_TTL_SECONDS,
                payload,
            )
        except Exception as exc:
            logger.warning("knowledge_catalog_lkg_save_failed", error=str(exc))

    async def _knowledge_catalog(self) -> dict[str, Any] | None:
        """Resolve a safe release snapshot for knowledge retrieval.

        Java is authoritative and is queried on every resolution. The Redis hint
        is not a freshness proof because its after-commit broadcast is explicitly
        best-effort; returning an equal-version LKG without consulting Java could
        keep an archived document visible for the whole LKG TTL. If Java is
        unavailable, an LKG is accepted only when the successfully-read hint does
        not prove it stale. ``None`` means the knowledge branch must be disabled.
        """
        hint, hint_ok = await self._read_release_hint()
        lkg = await self._read_last_known_good_catalog()

        try:
            raw_catalog = await java_internal_client.knowledge_catalog()
            if not isinstance(raw_catalog, dict):
                raise ValueError("Java knowledge catalog is not an object")
            version = int(raw_catalog["version"])
            raw_ids = raw_catalog.get("active_document_ids")
            if raw_ids is None:
                raw_ids = raw_catalog.get("activeDocumentIds")
            if not isinstance(raw_ids, list):
                raise ValueError("Java knowledge catalog document IDs are invalid")
            active_ids: list[str] = []
            for value in raw_ids:
                item = str(value).strip() if value is not None else ""
                if not item:
                    raise ValueError("Java knowledge catalog contains an empty document id")
                if item not in active_ids:
                    active_ids.append(item)
            catalog = {
                "version": version,
                "active_document_ids": active_ids,
            }
            if "documents" in raw_catalog:
                catalog["documents"] = self._normalize_catalog_documents(
                    raw_catalog.get("documents") or []
                )
            if hint is not None and version < hint:
                raise ValueError(
                    f"Java knowledge catalog regressed below release hint: {version} < {hint}"
                )
            await self._save_last_known_good_catalog(catalog)
            return catalog
        except Exception as exc:
            if hint_ok and lkg and (hint is None or int(lkg["version"]) >= hint):
                logger.warning(
                    "knowledge_catalog_using_lkg",
                    version=lkg["version"],
                    hint=hint,
                    error=str(exc),
                )
                return lkg
            logger.error(
                "knowledge_catalog_unavailable_fail_closed",
                hint=hint,
                hint_read_ok=hint_ok,
                error=str(exc),
            )
            return None

    @staticmethod
    def _normalize_catalog_documents(documents: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for value in documents:
            if not isinstance(value, dict):
                raise ValueError("catalog document is invalid")
            document_id = str(
                value.get("document_id") or value.get("documentId") or ""
            ).strip()
            if not document_id:
                raise ValueError("catalog document id is invalid")
            normalized.append(
                {
                    "document_id": document_id,
                    "source_name": str(
                        value.get("source_name") or value.get("sourceName") or ""
                    ).strip(),
                    "content_hash": str(
                        value.get("content_hash") or value.get("contentHash") or ""
                    ).strip().lower(),
                    "version": int(value.get("version") or 0),
                    "domain": str(value.get("domain") or "GENERAL").strip().upper(),
                    "chunk_count": int(
                        value.get("chunk_count") or value.get("chunkCount") or 0
                    ),
                }
            )
        return normalized

    async def _knowledge_version(self) -> int:
        """Resolve the authoritative version used to partition FAQ caches.

        The Redis release key is only a best-effort notification written after
        the database transaction commits. It can therefore lag behind Java and
        must not select an exact-FAQ cache namespace: doing so could serve an old
        answer for the full six-hour cache TTL after a failed broadcast.
        """
        hint, _hint_ok = await self._read_release_hint()
        lkg = await self._read_last_known_good_catalog()
        try:
            version = await java_internal_client.knowledge_version()
        except Exception as exc:
            raise KnowledgeCatalogUnavailable(
                "knowledge release version unavailable"
            ) from exc
        known_versions = [
            value
            for value in (
                hint,
                int(lkg["version"]) if lkg else None,
            )
            if value is not None
        ]
        if known_versions and version < max(known_versions):
            raise KnowledgeCatalogUnavailable(
                "knowledge release version regressed below a previously observed version"
            )
        return version

    def _filter_catalog(
        self,
        docs: list[dict],
        catalog: dict[str, Any] | None,
    ) -> list[dict]:
        """Apply the same release/catalog gate to semantic-cache hits."""
        active_ids = set(str(value) for value in (catalog or {}).get("active_document_ids", []))
        version = int((catalog or {}).get("version") or 0)
        catalog_domains = {
            str(item.get("document_id") or ""): str(
                item.get("domain") or "GENERAL"
            ).upper()
            for item in (catalog or {}).get("documents", [])
            if isinstance(item, dict) and item.get("document_id")
        }
        filtered: list[dict] = []
        for doc in docs:
            metadata = doc.get("metadata") or {}
            data_type = str(metadata.get("dataType") or "").lower()
            if data_type == "faq":
                filtered.append(doc)
                continue
            if catalog is None:
                # Unknown/missing data types are treated as knowledge and are
                # removed while the authoritative catalog is unavailable.
                continue
            document_id = str(metadata.get("documentId") or "")
            if str(metadata.get("status") or "").upper() != "PUBLISHED":
                continue
            expected_domain = catalog_domains.get(document_id)
            actual_domain = str(metadata.get("domain") or "").upper()
            if expected_domain is not None and actual_domain != expected_domain:
                continue
            try:
                document_version = int(metadata.get("version"))
            except (TypeError, ValueError):
                continue
            if document_id in active_ids and document_version <= version:
                filtered.append(doc)
        return filtered

    def _observe_search(self, started: float, hit: bool, mode: str) -> None:
        # 只记录时延。hit/miss 计数统一在 _finalize_search——检疫在 _trace_result
        # 内发生，全部片段被剔除时"检索命中"与"证据可用"是两回事，必须在最终
        # 结论确定后计数（M2：避免整组检疫时 hit 虚高、trace 与指标口径背离）。
        RAG_LATENCY.observe(max(0.0, time.perf_counter() - started))

    def _finalize_search(self, hit: bool, mode: str) -> None:
        RAG_SEARCH_TOTAL.labels(result="hit" if hit else "miss", mode=mode).inc()

    def _trace_result(
        self,
        query: str,
        version: int,
        mode: str,
        hit: bool,
        docs: list[dict],
        started: float,
        bucket: str = "A",
        candidate_count: int | None = None,
        evaluation_candidates: list[dict] | None = None,
        original_query: str | None = None,
        security_flags: list[str] | tuple[str, ...] | None = None,
        variant_count: int = 1,
        normalization_rules: tuple[str, ...] = (),
        forced_evidence_state: EvidenceState | None = None,
    ) -> dict[str, Any]:
        # A2 通道检疫：注入模型上下文的必须是洁净证据集。命中注入话术的片段
        # 在这里剔除而不是拒绝整个检索——知识库被投毒是"该修的文档问题"，
        # 不该让所有用户的检索一起挂掉。污染痕迹写进 trace（不记原文），
        # 全部被检疫干净时按"无证据"处理。注意 caller 的 _observe_search
        # 已按原模式计数，整组被剔除时该例会多记一次 hit，由
        # RAG_CHANNEL_CONTAMINATED 指标补足信号——投毒本身就是告警级事件。
        contamination: list[dict] = []
        if hit:
            kept: list[dict] = []
            for doc in docs:
                text = self._doc_text(doc)
                metadata = doc.get("metadata") or {}
                source = str(
                    metadata.get("source")
                    or metadata.get("title")
                    or metadata.get("question")
                    or ""
                )
                # 来源行（[来源：…] 前缀）与正文都会进上下文，一并扫描。
                verdict = scan_external_content(f"{source}\n{text}" if source else text)
                if verdict.contaminated:
                    doc_id = str(
                        doc.get("id")
                        or metadata.get("chunkId")
                        or metadata.get("questionId")
                        or ""
                    )
                    rules = sorted(verdict.matched_rules)
                    contamination.append(
                        {"id": doc_id, "source": source or "知识库", "rules": rules}
                    )
                    RAG_CHANNEL_CONTAMINATED.labels(rules=",".join(rules)).inc()
                    logger.warning(
                        "rag_channel_quarantined",
                        query=query[:80],
                        doc_id=doc_id,
                        rules=rules,
                        text_length=len(text),
                    )
                    continue
                kept.append(doc)
            if contamination:
                docs = kept
                hit = bool(kept)
        # 最终结论确定（检疫可能把 hit 反转成 miss）后才计数，指标与 trace 同源。
        self._finalize_search(hit, mode)
        # Only accepted documents are evidence and may be shown as citations.
        # Quarantined candidates are observable through quarantineCount/contamination
        # (with matched rules) and the RAG_CHANNEL_CONTAMINATED metric; candidateCount
        # and topScore are the "clean candidates that entered the evidence gate"
        # numbers, so a quarantined doc no longer counts toward either.
        clean_candidate_count = len(docs)
        refs: list[dict[str, Any]] = []
        paired_docs: list[dict[str, Any]] = []
        if hit:
            for doc in docs:
                row = self._source_refs([doc], version)
                if row:
                    paired_docs.append(doc)
                    refs.append(row[0])
        runtime_trace = active_rag_runtime_trace()
        planned_observation = (
            (runtime_trace.observations.get("plannedQuery") or {})
            if runtime_trace is not None
            else {}
        )
        selection_started = time.perf_counter()
        policy = runtime_rag_policy()
        planned_subquestions = tuple(planned_observation.get("subquestions") or ())
        evidence_limit = evidence_item_limit(
            planned_subquestions,
            configured_max=policy.max_evidence_items,
            preferred_fact_ids=planned_observation.get("factHints") or (),
            candidates=refs,
        )
        selection = (
            select_minimal_evidence(
                paired_docs,
                refs,
                query_domains=planned_observation.get("domains") or [],
                preferred_fact_ids=planned_observation.get("factHints") or [],
                max_items=evidence_limit,
                max_chars=policy.max_evidence_chars,
            )
            if hit
            else None
        )
        evidence_items = selection.items if selection else ()
        if selection:
            docs = list(selection.documents)
            refs = list(selection.refs)
            hit = bool(evidence_items)
        if runtime_trace is not None:
            runtime_trace.observe(
                "evidenceSelection", (time.perf_counter() - selection_started) * 1000
            )
            if selection:
                runtime_trace.observations["evidenceSelection"] = {
                    **selection.trace(),
                    "configuredMaxItems": policy.max_evidence_items,
                    "effectiveMaxItems": evidence_limit,
                    "subquestionCount": len(planned_subquestions),
                }
        evidence_state = forced_evidence_state or (
            EvidenceState.SUPPORTED if hit and evidence_items else EvidenceState.INSUFFICIENT
        )
        if contamination and not evidence_items:
            evidence_state = EvidenceState.QUARANTINED
        envelope = GroundingEnvelope(
            evidence_state=evidence_state,
            evidence_items=evidence_items,
            query_plan=QueryPlan(
                original_query_hash=_sha256(original_query if original_query is not None else query),
                safe_business_query=query,
                variant_count=max(0, int(variant_count)),
                normalization_rules=normalization_rules,
                subquestions=planned_subquestions,
                domains=tuple(planned_observation.get("domains") or ()),
                route=str(planned_observation.get("route") or "GENERAL"),
                expansion_reasons=tuple(
                    planned_observation.get("expansionReasons") or ()
                ),
                fact_hints=tuple(planned_observation.get("factHints") or ()),
                llm_expansion_calls=int(
                    planned_observation.get("llmExpansionCalls") or 0
                ),
                policy_fingerprint=(
                    runtime_trace.policy_fingerprint
                    if runtime_trace is not None
                    else None
                ),
            ),
            security_flags=tuple(security_flags or ()),
        )
        elapsed_ms = round(max(0.0, time.perf_counter() - started) * 1000, 2)
        result = {
            "text": self._format_docs(docs) if hit else "",
            "source_refs": refs,
            "trace": {
                "queryHash": _sha256(query),
                "mode": mode,
                "hit": hit,
                "knowledgeVersion": version,
                "bucket": bucket,
                "sourceCount": len(refs),
                "candidateCount": clean_candidate_count
                if candidate_count is None
                else candidate_count,
                "quarantineCount": len(contamination),
                "contamination": contamination,
                "topScore": max(
                    (float(doc.get("score") or 0) for doc in docs),
                    default=0.0,
                ),
                "latencyMs": elapsed_ms,
            },
            **envelope.result_fields(),
        }
        if evaluation_candidates is not None:
            result["_evaluationCandidateRefs"] = self._source_refs(
                evaluation_candidates, version
            )
            if runtime_trace is not None:
                result["_evaluationChannels"] = runtime_trace.observations.get(
                    "candidateChannels", {}
                )
        return result

    def _source_refs(self, docs: list[dict], version: int) -> list[dict]:
        # 传入的 docs 就是最终注入 prompt 的证据集合（已按 effective
        # rerank_top_n / limit 截断）。此前这里再按 settings.rerank_top_n
        # 切片，A/B 桶覆盖了 rerank_top_n 时溯源/盲评数据与真实注入不一致
        # （P1 审查：_source_refs 观测失真）。
        refs: list[dict] = []
        seen: set[str] = set()
        for doc in docs:
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
                ("domain", "domain"),
            ):
                if metadata.get(metadata_key) is not None:
                    ref[ref_key] = metadata[metadata_key]
            try:
                from app.rag.canonical_facts import get_canonical_fact_catalog

                fact_ids = sorted(get_canonical_fact_catalog().facts_for_ref(ref))
                if fact_ids:
                    ref["factIds"] = fact_ids
            except ValueError:
                pass
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
        metadata = doc.get("metadata") or {}
        original = metadata.get("originalContent")
        if original is not None and str(original).strip():
            return str(original).strip()
        return str(doc.get("content") or doc.get("text") or "").strip()

    def _has_enough_evidence(self, docs: list[dict]) -> bool:
        """证据够不够写进 prompt。

        按分数的来源分流，因为 rerank 之后和只做完 RRF 的分数量纲不同：
        rerank 给的是 0~1 归一相关性，可以直接和一个绝对阈值比；RRF 分是名次的倒数和，
        只能表达"至少在某一路里进了前 N 名"。用一个常量同时比这两种，其中一种必然失真。

        rerank 未配置或熔断时会静默回落到 RRF（见 ``_rerank``），所以这条兜底路径不是
        边缘情况——恰恰是没有 rerank key 的部署里的常态。
        """
        return bool(self._filter_evidence_docs(docs))

    def _filter_evidence_docs(
        self,
        docs: list[dict],
        *,
        preferred_fact_ids: tuple[str, ...] | list[str] = (),
    ) -> list[dict]:
        """Keep only candidates whose own score clears its scoring-scale gate."""
        settings = get_settings()
        policy = runtime_rag_policy()
        evaluation = _RERANK_EVALUATION_STATS.get()
        evidence_threshold = (
            evaluation.evidence_threshold
            if evaluation is not None and evaluation.evidence_threshold is not None
            else policy.evidence_threshold
        )
        rrf_floor = rrf_score_at_rank(settings.rag_evidence_min_rrf_rank)
        rerank_scores = [
            float(doc.get("score") or 0)
            for doc in docs
            if doc.get("source") == "rerank"
        ]
        rerank_top = max(rerank_scores, default=0.0)
        margin = (
            evaluation.top_score_margin
            if evaluation is not None and evaluation.top_score_margin is not None
            else policy.top_score_margin
        )
        preferred = {str(value) for value in preferred_fact_ids if str(value)}
        preferred_accepted: list[dict] = []
        accepted: list[dict] = []
        for doc in docs:
            score = float(doc.get("score") or 0)
            source = doc.get("source")
            hinted = bool(preferred.intersection(_canonical_fact_ids_for_doc(doc)))
            if source == "rrf":
                enough = score >= rrf_floor
            else:
                # rerank and exact/non-fused results both use a 0..1 score.
                floor = (
                    min(evidence_threshold, policy.canonical_hint_floor)
                    if source == "rerank" and hinted
                    else evidence_threshold
                )
                enough = score >= floor
                if source == "rerank" and margin is not None and not hinted:
                    enough = enough and score >= rerank_top - float(margin)
            if enough:
                (preferred_accepted if hinted else accepted).append(doc)
        return [*preferred_accepted, *accepted]

    def _filter_expired(self, docs: list[dict]) -> list[dict]:
        """过滤 FAQ 时效窗口之外的文档。

        只有 ``dataType == "faq"`` 的文档携带时效约束；knowledge / product chunk
        原样透传。时间戳由 Java 侧入库时写入（epoch ms，B-1 fix）。缺少某个边界视为
        永久有效（两端都不填 = 常驻，只填 effectiveStart = 生效后永远有效，以此类推）。
        """
        now_ms = int(time.time() * 1000)
        result = []
        for doc in docs:
            metadata = doc.get("metadata") or {}
            if metadata.get("dataType") == "faq":
                eff_end = metadata.get("effectiveEnd")
                if eff_end is not None and int(eff_end) < now_ms:
                    logger.debug("faq_doc_expired_filtered", doc_id=doc.get("id"))
                    continue
                eff_start = metadata.get("effectiveStart")
                if eff_start is not None and int(eff_start) > now_ms:
                    logger.debug("faq_doc_not_yet_active_filtered", doc_id=doc.get("id"))
                    continue
            result.append(doc)
        return result

    def _rewrite_query(self, query: str) -> str:
        # Preserve the user's complete question. Pronouns and sentence-final
        # particles can carry meaning, and removing them silently changed the
        # retrieval target. Multi-turn resolution is handled separately and only
        # when conversation context exists.
        return " ".join(str(query or "").strip().split())

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
        # str.isalnum() is Unicode-aware: Chinese characters (CJK Unified Ideographs)
        # are classified as Letter+Other (Lo) and return True, so this filter strips
        # only whitespace and punctuation while preserving Chinese query strings intact.
        return "".join(ch for ch in (query or "").lower() if ch.isalnum())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


rag_retriever = RagRetriever()
