from __future__ import annotations

import structlog

from app.harness.guardrails.tool_guard import ToolGuardrail
from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.tool_invoke_result import ToolInvokeResult, parse_tool_wire

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
            text = await mcp_streamable_client.call_tool(tool_name, mcp_args)
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="success").inc()
            result = parse_tool_wire(text)
            return await self._backfill_structured(tool_name, mcp_args, result)
        except TypeError as e:
            logger.warning("mcp_tool_bad_args", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="bad_args").inc()
            return ToolInvokeResult(content="【操作失败】参数不完整")
        except Exception as e:
            logger.exception("mcp_tool_failed", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="error").inc()
            return ToolInvokeResult(content="【操作失败】系统处理异常，请稍后重试")

    async def _backfill_structured(
        self,
        tool_name: str,
        mcp_args: dict,
        result: ToolInvokeResult,
    ) -> ToolInvokeResult:
        """Prefer in-process tool logic so MCP process drift cannot serve stale search/order cards."""
        if tool_name == "SEARCH_PRODUCTS":
            from app.services.mcp_tools_service import tool_search_products

            local = await tool_search_products(
                str(mcp_args.get("userId") or ""),
                str(mcp_args.get("keyword") or ""),
                mcp_args.get("excludeProductId"),
            )
            if local.assistant_cards or local.content:
                if (result.content or "") != (local.content or ""):
                    logger.info(
                        "search_products_local_override",
                        mcp_preview=(result.content or "")[:80],
                        local_preview=(local.content or "")[:80],
                    )
                return local
            return result

        if result.assistant_cards or result.biz_type:
            return result
        if tool_name != "QUERY_ORDERS":
            return result
        from app.services.mcp_tools_service import tool_query_orders

        local = await tool_query_orders(
            str(mcp_args.get("userId") or ""),
            mcp_args.get("orderId"),
        )
        if local.assistant_cards or local.biz_type:
            logger.info("query_orders_cards_backfilled")
            return local
        return result

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
