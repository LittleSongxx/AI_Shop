"""P2-1 Query Expansion: LLM generates synonym / alias variants for better recall.

Given a single retrieval query the function returns a deduplicated list
``[original_query, variant_1, ..., variant_N]`` (at most 3 entries total).
Callers always receive a usable list: any failure silently returns
``[original_query]`` so the normal single-query path stays intact.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import structlog

from app.config.settings import get_settings
from app.harness.guardrails.channel_guard import scan_external_content
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import circuit_registry

logger = structlog.get_logger()


@dataclass
class QueryExpansionEvaluationStats:
    eligible_requests: int = 0
    provider_requests: int = 0
    provider_successes: int = 0
    provider_failures: int = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "eligibleRequests": self.eligible_requests,
            "providerRequests": self.provider_requests,
            "providerSuccesses": self.provider_successes,
            "providerFailures": self.provider_failures,
        }


_EVALUATION_STATS: contextvars.ContextVar[
    QueryExpansionEvaluationStats | None
] = contextvars.ContextVar("query_expansion_evaluation_stats", default=None)


@contextmanager
def query_expansion_evaluation_scope() -> Iterator[QueryExpansionEvaluationStats]:
    stats = QueryExpansionEvaluationStats()
    token = _EVALUATION_STATS.set(stats)
    try:
        yield stats
    finally:
        _EVALUATION_STATS.reset(token)

# Max total queries (original + variants).  3 keeps latency reasonable
# while still covering the most common synonym / abbreviation mismatches.
_MAX_TOTAL = 3
_MAX_QUERY_CHARS = 160

_DOMAIN_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("address", ("地址", "收货地", "默认地址", "改地址")),
    ("payment", ("支付", "付款", "支付宝", "支付渠道", "比特币", "数字货币")),
    ("refund", ("退款", "退钱", "退款进度", "退回款项")),
    ("logistics", ("物流", "快递", "配送", "包裹", "发货", "轨迹")),
    ("account", ("账户", "账号", "登录", "归属")),
    ("review", ("评价", "评论", "追评", "晒单")),
    ("member", ("会员", "成长值", "签到", "等级")),
    ("coupon", ("优惠券", "优惠卷", "券", "抢券", "用券")),
    ("privacy", ("隐私", "数据导出", "删除数据", "清空聊天", "记忆")),
    ("ai_boundary", ("AI助手", "AI 助手", "人工客服", "转人工", "写操作")),
    ("checkout", ("购物车", "结算", "下单", "库存", "价格快照")),
    ("after_sales", ("售后", "退货", "破损", "错发", "漏发", "凭证")),
)

_SYSTEM_PROMPT = (
    "你是一个电商客服检索查询扩展助手。"
    "给定一条检索查询，生成最多 2 个语义等价但表达不同的变体，帮助召回更多相关文档。\n"
    "要求：\n"
    "- 每行输出一个变体，只输出变体文本，不加编号、序号或说明；\n"
    "- 变体应覆盖同义词、缩写、别名（如「苹果手机」↔「iPhone」、"
    "「退款」↔「退钱」↔「申请退货」）；\n"
    "- 若原查询已非常精确且无常见同义词，输出 1 行即可；\n"
    "- 不要重复原始查询。"
)


async def expand_query(query: str) -> list[str]:
    """Return ``[original_query, *variants]``, deduplicated, at most ``_MAX_TOTAL`` entries.

    Always returns at least ``[original_query]``.
    """
    if not query.strip():
        return [query]

    result = deterministic_query_variants(query)
    settings = get_settings()
    if not settings.llm_api_key.strip():
        return result
    evaluation = _EVALUATION_STATS.get()
    if evaluation is not None:
        evaluation.eligible_requests += 1
    breaker = circuit_registry.get_or_create(
        "llm_query_expander", failure_threshold=3, recovery_timeout=60
    )
    if not breaker.allow_request():
        return result

    try:
        if evaluation is not None:
            evaluation.provider_requests += 1
        client = await get_client("llm_query_expander", timeout=6)
        resp = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"原始查询：{query}"},
                ],
                "max_tokens": 80,
                "temperature": 0,
            },
            timeout=6,
        )
        resp.raise_for_status()
        text = (
            ((resp.json().get("choices") or [{}])[0])
            .get("message", {})
            .get("content", "")
            or ""
        ).strip()
        breaker.record_success()
        if evaluation is not None:
            evaluation.provider_successes += 1
        if not text:
            return result

        # Parse newline-separated variants; strip blank lines and leading bullets.
        raw_variants = [
            line.lstrip("-•·0123456789.) \t").strip()
            for line in text.splitlines()
            if line.strip()
        ]
        seen: set[str] = {_query_key(value) for value in result}
        for v in raw_variants:
            key = _query_key(v)
            if _valid_variant(query, v) and key not in seen and len(result) < _MAX_TOTAL:
                seen.add(key)
                result.append(v)

        logger.debug(
            "rag_query_expanded",
            original=query[:80],
            variants=[v[:80] for v in result[1:]],
        )
        return result
    except Exception as exc:
        breaker.record_failure()
        if evaluation is not None:
            evaluation.provider_failures += 1
        logger.warning("rag_query_expand_failed", error=str(exc))
        return result


def deterministic_query_variants(query: str) -> list[str]:
    """Return the original query plus one deterministic business alias variant."""

    original = " ".join(str(query or "").strip().split())
    if not original:
        return [original]
    for _domain, aliases in _DOMAIN_ALIASES:
        hits = [alias for alias in aliases if alias.casefold() in original.casefold()]
        if not hits:
            continue
        additions = [alias for alias in aliases if alias not in hits][:3]
        if additions:
            variant = f"{original} {' '.join(additions)}"
            if len(variant) <= _MAX_QUERY_CHARS:
                return [original, variant]
    return [original]


def _domain_ids(value: str) -> set[str]:
    folded = value.casefold()
    return {
        domain
        for domain, aliases in _DOMAIN_ALIASES
        if any(alias.casefold() in folded for alias in aliases)
    }


def _query_key(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _valid_variant(original: str, variant: str) -> bool:
    value = " ".join(str(variant or "").strip().split())
    if len(value) < 2 or len(value) > _MAX_QUERY_CHARS or "\n" in value:
        return False
    if scan_external_content(value).contaminated:
        return False
    original_domains = _domain_ids(original)
    return not original_domains or bool(original_domains.intersection(_domain_ids(value)))
