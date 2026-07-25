from __future__ import annotations

import json
from dataclasses import dataclass, field

from mcp.types import LATEST_PROTOCOL_VERSION

WIRE_PREFIX = "AISHOP_TOOL_RESULT:"
MCP_PROTOCOL = LATEST_PROTOCOL_VERSION
MCP_TOOL_CONTRACT = "aishop-tools/current"


@dataclass
class ToolInvokeResult:
    content: str
    biz_type: str | None = None
    biz_data: str | None = None
    assistant_cards: str | None = None
    product_ids: list[str] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)
    protocol_version: str = MCP_PROTOCOL
    contract_version: str = MCP_TOOL_CONTRACT

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
        """Serialize all results, including read-only text, with the contract."""
        payload = {
            "protocolVersion": self.protocol_version,
            "contractVersion": self.contract_version,
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
    """Parse the single current MCP result contract."""
    raw = text or ""
    if not raw.startswith(WIRE_PREFIX):
        return ToolInvokeResult(content=raw, protocol_version="", contract_version="")
    try:
        obj = json.loads(raw[len(WIRE_PREFIX) :])
    except json.JSONDecodeError:
        return ToolInvokeResult(content=raw, protocol_version="", contract_version="")
    if not isinstance(obj, dict):
        return ToolInvokeResult(content=raw, protocol_version="", contract_version="")
    return ToolInvokeResult(
        content=str(obj.get("content") or ""),
        biz_type=obj.get("bizType"),
        biz_data=obj.get("bizData"),
        assistant_cards=obj.get("assistantCards"),
        product_ids=list(obj.get("productIds") or []),
        product_names=list(obj.get("productNames") or []),
        order_ids=list(obj.get("orderIds") or []),
        protocol_version=str(obj.get("protocolVersion") or ""),
        contract_version=str(obj.get("contractVersion") or ""),
    )
