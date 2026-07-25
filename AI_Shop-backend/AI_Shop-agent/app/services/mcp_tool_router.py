from __future__ import annotations

import structlog

from app.harness.guardrails.tool_guard import ToolGuardrail
from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.tool_invoke_result import ToolInvokeResult

logger = structlog.get_logger()
tool_guard = ToolGuardrail()


class McpToolRouter:
    """Dispatches tools via Streamable HTTP MCP server (not in-process)."""

    async def invoke(self, tool_name: str, args: dict, user_id: str) -> ToolInvokeResult:

        if not tool_guard.is_allowed(tool_name):
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="denied").inc()
            return ToolInvokeResult(content=f"未知工具: {tool_name}")

        raw = dict(args or {})
        raw["userId"] = user_id

        if not tool_guard.validate_tool_args(tool_name, raw, user_id):
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="invalid_args").inc()
            return ToolInvokeResult(content="【操作失败】用户身份校验未通过")

        try:
            mcp_args = self._to_mcp_args(tool_name, raw)
            result = await mcp_streamable_client.call_tool(tool_name, mcp_args)
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="success").inc()
            return result
        except TypeError as e:
            logger.warning("mcp_tool_bad_args", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="bad_args").inc()
            return ToolInvokeResult(content="【操作失败】参数不完整")
        except Exception as e:
            logger.exception("mcp_tool_failed", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="error").inc()
            return ToolInvokeResult(content="【操作失败】系统处理异常，请稍后重试")

    def _to_mcp_args(self, tool_name: str, args: dict) -> dict:
        """Normalize to camelCase keys expected by MCP tool schemas."""
        def g(*keys):
            return next(
                (args[key] for key in keys if key in args and args[key] is not None),
                None,
            )

        uid = g("userId", "user_id")
        if tool_name == "SEARCH_PRODUCTS":
            out = {"userId": uid, "keyword": g("keyword") or ""}
            ex = g("excludeProductId", "exclude_product_id")
            if ex is not None:
                out["excludeProductId"] = ex
            return out
        if tool_name == "QUERY_ORDERS":
            out = {"userId": uid}
            oid = g("orderId", "order_id")
            if oid is not None:
                out["orderId"] = oid
            return out
        if tool_name == "GET_PRODUCT_DETAIL":
            return {"userId": uid, "productId": g("productId", "product_id")}
        if tool_name in ("QUERY_LOGISTICS", "QUERY_COMMENT", "PROPOSE_CONFIRM_RECEIPT"):
            return {"userId": uid, "orderId": g("orderId", "order_id")}
        if tool_name == "QUERY_USER_COUPONS":
            out = {"userId": uid}
            st = g("status")
            if st is not None:
                out["status"] = st
            return out
        if tool_name == "PROPOSE_REFUND":
            return {"userId": uid, "orderItemId": g("orderItemId", "order_item_id")}
        if tool_name == "PROPOSE_PRODUCT_REVIEW":
            return {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "commentContent": g("commentContent", "comment_content"),
                "star": g("star"),
            }
        if tool_name == "PROPOSE_RECOMMENT":
            return {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "reCommentContent": g("reCommentContent", "re_comment_content"),
            }
        return {"userId": uid}


mcp_tool_router = McpToolRouter()
