from __future__ import annotations

import json
from dataclasses import dataclass, field

WIRE_PREFIX = "SIMLECT_TOOL_RESULT:"


@dataclass
class ToolInvokeResult:
    content: str
    biz_type: str | None = None
    biz_data: str | None = None
    assistant_cards: str | None = None
    product_ids: list[str] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)

    def to_tool_message(self) -> str:
        return self.content

    def to_biz_dict(self) -> dict | None:
        if not (self.product_ids or self.product_names or self.order_ids):
            return None
        return {
            "productIds": self.product_ids,
            "productNames": self.product_names,
            "orderIds": self.order_ids,
        }

    def to_wire(self) -> str:
        """Serialize for MCP transport so Agent can restore cards/biz fields."""
        if not (self.assistant_cards or self.biz_type or self.biz_data or self.product_ids or self.order_ids):
            return self.content or ""
        payload = {
            "content": self.content or "",
            "bizType": self.biz_type,
            "bizData": self.biz_data,
            "assistantCards": self.assistant_cards,
            "productIds": self.product_ids,
            "productNames": self.product_names,
            "orderIds": self.order_ids,
        }
        return WIRE_PREFIX + json.dumps(payload, ensure_ascii=False)


def parse_tool_wire(text: str | None) -> ToolInvokeResult:
    """Parse MCP tool text back into ToolInvokeResult (plain text or wire envelope)."""
    raw = text or ""
    if not raw.startswith(WIRE_PREFIX):
        return ToolInvokeResult(content=raw)
    try:
        obj = json.loads(raw[len(WIRE_PREFIX) :])
    except json.JSONDecodeError:
        return ToolInvokeResult(content=raw)
    if not isinstance(obj, dict):
        return ToolInvokeResult(content=raw)
    return ToolInvokeResult(
        content=str(obj.get("content") or ""),
        biz_type=obj.get("bizType") or obj.get("biz_type"),
        biz_data=obj.get("bizData") or obj.get("biz_data"),
        assistant_cards=obj.get("assistantCards") or obj.get("assistant_cards"),
        product_ids=list(obj.get("productIds") or obj.get("product_ids") or []),
        product_names=list(obj.get("productNames") or obj.get("product_names") or []),
        order_ids=list(obj.get("orderIds") or obj.get("order_ids") or []),
    )
