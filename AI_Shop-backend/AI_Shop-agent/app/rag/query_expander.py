"""P2-1 Query Expansion: LLM generates synonym / alias variants for better recall.

Given a single retrieval query the function returns a deduplicated list
``[original_query, variant_1, ..., variant_N]`` (at most 3 entries total).
Callers always receive a usable list: any failure silently returns
``[original_query]`` so the normal single-query path stays intact.
"""
from __future__ import annotations

import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import circuit_registry

logger = structlog.get_logger()

# Max total queries (original + variants).  3 keeps latency reasonable
# while still covering the most common synonym / abbreviation mismatches.
_MAX_TOTAL = 3

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

    settings = get_settings()
    breaker = circuit_registry.get_or_create(
        "llm_query_expander", failure_threshold=3, recovery_timeout=60
    )
    if not breaker.allow_request():
        return [query]

    try:
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
                "temperature": 0.3,
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
        if not text:
            return [query]

        # Parse newline-separated variants; strip blank lines and leading bullets.
        raw_variants = [
            line.lstrip("-•·0123456789.) \t").strip()
            for line in text.splitlines()
            if line.strip()
        ]
        seen: set[str] = {query}
        result: list[str] = [query]
        for v in raw_variants:
            if v and v not in seen and len(result) < _MAX_TOTAL:
                seen.add(v)
                result.append(v)

        logger.debug(
            "rag_query_expanded",
            original=query[:80],
            variants=[v[:80] for v in result[1:]],
        )
        return result
    except Exception as exc:
        breaker.record_failure()
        logger.warning("rag_query_expand_failed", error=str(exc))
        return [query]
