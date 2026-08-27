"""Conservative, snippet-bound refund policy helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_CONDITION_MARKERS = ("条件", "要求", "需要什么", "需满足", "哪些情况")
_REQUIRED_SNIPPET_CLAIMS = (
    "订单详情中发起售后申请",
    "商品、附件和包装完整",
    "商品类型、订单状态和实际情况审核",
)


def asks_refund_conditions(user_text: str | None) -> bool:
    text = str(user_text or "").strip()
    return any(term in text for term in ("退款", "退货", "售后")) and any(
        marker in text for marker in _CONDITION_MARKERS
    )


def cited_refund_conditions(
    source_refs: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Use only a visible snippet that contains every sentence we expose."""

    for citation, ref in enumerate(source_refs or (), start=1):
        snippet = "".join(
            str(ref.get(key) or "") for key in ("heading", "snippet", "text")
        ).replace(" ", "")
        if not all(claim.replace(" ", "") in snippet for claim in _REQUIRED_SNIPPET_CLAIMS):
            continue
        return {
            "answer": (
                "需要从订单详情发起售后申请，并保持商品、附件和包装完整。"
                f"[{citation}] 平台会根据商品类型、订单状态和实际情况审核。"
                f"[{citation}]"
            ),
            "citation": citation,
            "sourceId": ref.get("id"),
            "factId": "aftersales.request_and_refund_boundary",
        }
    return None
