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
    success: bool = True
    error_code: str | None = None
    biz_type: str | None = None
    biz_data: str | None = None
    assistant_cards: str | None = None
    product_ids: list[str] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)
    source_refs: list[dict] = field(default_factory=list)
    retrieval_trace: dict | None = None
    grounding: dict | None = None
    contract_data: dict | None = None
    protocol_version: str = MCP_PROTOCOL
    contract_version: str = MCP_TOOL_CONTRACT

    def to_tool_message(self) -> str:
        return self.content

    def to_biz_dict(self) -> dict | None:
        if not (
            self.product_ids
            or self.product_names
            or self.order_ids
            or self.contract_data
        ):
            return None
        payload = {
            "productIds": self.product_ids,
            "productNames": self.product_names,
            "orderIds": self.order_ids,
        }
        if self.source_refs:
            payload["sourceRefs"] = self.source_refs
        if self.retrieval_trace:
            payload["retrievalTrace"] = self.retrieval_trace
        if self.grounding:
            payload["grounding"] = self.grounding
        if self.contract_data:
            payload["contractData"] = self.contract_data
        return payload

    def to_wire(self) -> str:
        """Serialize all results, including read-only text, with the contract."""
        payload = {
            "protocolVersion": self.protocol_version,
            "contractVersion": self.contract_version,
            "content": self.content or "",
            "success": self.success,
            "errorCode": self.error_code,
            "bizType": self.biz_type,
            "bizData": self.biz_data,
            "assistantCards": self.assistant_cards,
            "productIds": self.product_ids,
            "productNames": self.product_names,
            "orderIds": self.order_ids,
            "sourceRefs": self.source_refs,
            "retrievalTrace": self.retrieval_trace,
            "grounding": self.grounding,
            "contractData": self.contract_data,
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
        success=obj.get("success") is not False,
        error_code=obj.get("errorCode"),
        biz_type=obj.get("bizType"),
        biz_data=obj.get("bizData"),
        assistant_cards=obj.get("assistantCards"),
        product_ids=list(obj.get("productIds") or []),
        product_names=list(obj.get("productNames") or []),
        order_ids=list(obj.get("orderIds") or []),
        source_refs=list(obj.get("sourceRefs") or []),
        retrieval_trace=(
            obj.get("retrievalTrace")
            if isinstance(obj.get("retrievalTrace"), dict)
            else None
        ),
        grounding=(obj.get("grounding") if isinstance(obj.get("grounding"), dict) else None),
        contract_data=(
            obj.get("contractData")
            if isinstance(obj.get("contractData"), dict)
            else None
        ),
        protocol_version=str(obj.get("protocolVersion") or ""),
        contract_version=str(obj.get("contractVersion") or ""),
    )
