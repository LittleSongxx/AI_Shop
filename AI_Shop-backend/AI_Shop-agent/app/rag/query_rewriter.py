"""Multi-turn RAG query rewriter.

Resolves coreferences ("这款", "它", "那个" …) in the user's current question
by injecting session context, then calling the main chat LLM to produce a
self-contained retrieval query.

Falls back to the original user text silently on:
- first turn (no context yet)
- empty narrative / no known consult product
- circuit-breaker open
- any LLM / network error
- empty rewrite result

Callers always receive a usable string.
"""
from __future__ import annotations

import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.memory.models import SessionMemory
from app.resilience.circuit_breaker import circuit_registry

logger = structlog.get_logger()

_POLICY_QUERY_TOPICS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("退款", "退货", "退换", "能退", "无理由"), "退货与退款"),
    (("取消订单", "订单取消"), "订单取消规则"),
    (("物流", "快递", "配送", "发货"), "订单配送与物流"),
    (("优惠券", "优惠卷", "用券"), "优惠券使用规则"),
    (("发票", "开票"), "发票规则"),
    (("破损", "损坏", "坏了", "错发", "漏发", "保修"), "商品售后服务"),
    (("售后", "投诉"), "售后服务规则"),
)
_POLICY_DETAIL_TERMS = (
    "拆封",
    "已使用",
    "七天",
    "无理由",
    "运费",
    "时效",
    "多久",
    "条件",
    "材料",
    "凭证",
    "质量问题",
)


def normalize_policy_query(query: str) -> str:
    """Turn unstable conversational policy wording into a retrieval topic."""

    text = str(query or "").strip()
    if not text:
        return text
    for markers, topic in _POLICY_QUERY_TOPICS:
        if any(marker in text for marker in markers):
            details = [term for term in _POLICY_DETAIL_TERMS if term in text]
            suffix = " ".join(dict.fromkeys(details))
            return f"{topic} 政策 条件 流程 {suffix}".strip()
    return text

_SYSTEM_PROMPT = (
    "你是一个查询改写助手，服务于电商客服 RAG 检索。"
    "给定对话上下文和用户当前问题，输出一个独立、完整的检索查询：\n"
    "- 把「这款」「它」「这个」「那个」等指代词替换为具体商品名或品牌；\n"
    "- 若原问题已足够清晰或无上下文可利用，原样返回；\n"
    "- 只输出改写后的查询本身，不加任何解释或前缀。"
)


def _build_context(memory: SessionMemory) -> str:
    """从 SessionMemory 提取供改写用的精简上下文。"""
    parts: list[str] = []
    narrative = (memory.summary.get("narrative") or "").strip()
    if narrative:
        parts.append(f"对话摘要：{narrative}")
    consult = (memory.state.get("consultProduct") or {})
    if consult.get("productName"):
        parts.append(f"当前咨询商品：{consult['productName']}")
    last_results = memory.state.get("lastToolResults") or {}
    names = last_results.get("searchedProductNames") or []
    if names:
        parts.append(f"近期搜索商品：{', '.join(str(n) for n in names[:4])}")
    return "\n".join(parts)


async def rewrite_for_rag(user_text: str, memory: SessionMemory) -> str:
    """返回消解了指代词的独立检索查询。

    调用方始终得到可用字符串（最差情况回落为原始 ``user_text``）。
    """
    normalized = normalize_policy_query(user_text)
    if normalized != str(user_text or "").strip():
        return normalized

    context = _build_context(memory)
    if not context:
        return user_text  # 第一轮，没有可利用的上下文

    settings = get_settings()
    breaker = circuit_registry.get_or_create(
        "llm_rewriter", failure_threshold=3, recovery_timeout=60
    )
    if not breaker.allow_request():
        return user_text

    try:
        client = await get_client("llm_rewriter", timeout=8)
        resp = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"对话上下文：\n{context}\n\n"
                            f"用户当前问题：{user_text}"
                        ),
                    },
                ],
                "max_tokens": 80,
                "temperature": 0,
            },
            timeout=8,
        )
        resp.raise_for_status()
        rewritten = (
            ((resp.json().get("choices") or [{}])[0])
            .get("message", {})
            .get("content", "")
            or ""
        ).strip()
        breaker.record_success()
        if not rewritten:
            return user_text
        logger.debug(
            "rag_query_rewritten",
            original=user_text[:120],
            rewritten=rewritten[:120],
        )
        return normalize_policy_query(rewritten)
    except Exception as exc:
        breaker.record_failure()
        logger.warning("rag_query_rewrite_failed", error=str(exc))
        return user_text
