from __future__ import annotations

from dataclasses import dataclass, field

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
