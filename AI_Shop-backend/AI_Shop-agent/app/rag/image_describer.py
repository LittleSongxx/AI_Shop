"""P3-2 Multimodal RAG: describe an image URL via VLM and return plain text.

Pattern mirrors query_rewriter.py — same client factory, same circuit-breaker idiom.

When vlm_api_key is empty (the default) or the circuit breaker is open the call
returns None silently; callers always fall back to text-only retrieval.

Configuring the VLM (.env):
    VLM_API_KEY=sk-...          # or reuse DASHSCOPE_API_KEY
    VLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    VLM_MODEL=qwen-vl-plus      # any OpenAI-compatible vision model
    VLM_IMAGE_MAX_TOKENS=150
    VLM_TIMEOUT=15
"""
from __future__ import annotations

import structlog

from app.config.settings import get_settings
from app.infra.http_client import get_client
from app.resilience.circuit_breaker import circuit_registry

logger = structlog.get_logger()

_SYSTEM_PROMPT = (
    "你是电商客服 RAG 系统的图片分析助手。"
    "请用 1-3 句简洁中文描述图片的主要内容，"
    "重点说明商品外观、型号、包装及文字标识等与购物相关的可见信息。"
    "只输出描述本身，不含解释或前缀。"
)


async def describe_image(image_url: str) -> str | None:
    """Return a plain-text description of *image_url*, or None on failure / disabled.

    The returned string is for augmenting RAG queries and LLM context;
    it is never shown to the user directly.
    """
    settings = get_settings()
    if not settings.vlm_api_key:
        return None

    breaker = circuit_registry.get_or_create(
        "vlm_image_describer", failure_threshold=3, recovery_timeout=60
    )
    if not breaker.allow_request():
        logger.warning("vlm_image_describer_circuit_open", url=image_url[:80])
        return None

    try:
        client = await get_client("vlm_image_describer", timeout=settings.vlm_timeout)
        resp = await client.post(
            f"{settings.vlm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
            json={
                "model": settings.vlm_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url}}
                        ],
                    },
                ],
                "max_tokens": settings.vlm_image_max_tokens,
                "temperature": 0,
            },
            timeout=settings.vlm_timeout,
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
            return None
        logger.debug("vlm_image_described", url=image_url[:80], length=len(text))
        return text
    except Exception as exc:
        breaker.record_failure()
        logger.warning("vlm_image_describe_failed", url=image_url[:80], error=str(exc))
        return None
