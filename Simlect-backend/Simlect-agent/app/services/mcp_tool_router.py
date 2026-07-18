from __future__ import annotations

import structlog

from app.harness.guardrails.tool_guard import ToolGuardrail
from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL
from app.services import mcp_tools_service as tools
from app.services.tool_invoke_result import ToolInvokeResult

logger = structlog.get_logger()
tool_guard = ToolGuardrail()

class McpToolRouter:

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
            normalized = self._normalize_args(tool_name, raw)
            result = await self._dispatch(tool_name, normalized)
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="success").inc()

            if isinstance(result, ToolInvokeResult):
                return result
            return ToolInvokeResult(content=str(result))
        except TypeError as e:

            logger.warning("mcp_tool_bad_args", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="bad_args").inc()
            return ToolInvokeResult(content="【操作失败】参数不完整")
        except Exception as e:

            logger.exception("mcp_tool_failed", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="error").inc()
            return ToolInvokeResult(content="【操作失败】系统处理异常，请稍后重试")

    async def _dispatch(self, tool_name: str, args: dict):

        if tool_name == "SEARCH_PRODUCTS":
            return await tools.tool_search_products(
                args["user_id"], args["keyword"], args.get("exclude_product_id")
            )
        if tool_name == "QUERY_ORDERS":
            return await tools.tool_query_orders(args["user_id"], args.get("order_id"))
        if tool_name == "GET_PRODUCT_DETAIL":
            return await tools.tool_get_product_detail(args["user_id"], args["product_id"])
        if tool_name == "QUERY_LOGISTICS":
            return await tools.query_logistics(args["user_id"], args["order_id"])
        if tool_name == "QUERY_COMMENT":
            return await tools.query_comment(args["user_id"], args["order_id"])
        if tool_name == "QUERY_USER_COUPONS":
            return await tools.query_user_coupons(args["user_id"], args.get("status"))
        if tool_name == "PROPOSE_CONFIRM_RECEIPT":
            return await tools.propose_confirm_receipt(args["user_id"], args["order_id"])
        if tool_name == "PROPOSE_REFUND":
            return await tools.propose_refund(args["user_id"], args["order_item_id"])
        if tool_name == "PROPOSE_PRODUCT_REVIEW":
            return await tools.propose_product_review(
                args["user_id"], args["order_id"], args["comment_content"], int(args["star"])
            )
        if tool_name == "PROPOSE_RECOMMENT":
            return await tools.propose_recomment(
                args["user_id"], args["order_id"], args["re_comment_content"]
            )

        return ToolInvokeResult(content=f"未知工具: {tool_name}")

    def _normalize_args(self, tool_name: str, args: dict) -> dict:

        g = lambda *keys: next((args[k] for k in keys if k in args and args[k] is not None), None)
        uid = g("userId", "user_id")
        if tool_name == "SEARCH_PRODUCTS":
            return {
                "user_id": uid,
                "keyword": g("keyword") or "",
                "exclude_product_id": g("excludeProductId", "exclude_product_id"),
            }
        if tool_name == "QUERY_ORDERS":
            return {"user_id": uid, "order_id": g("orderId", "order_id")}
        if tool_name == "GET_PRODUCT_DETAIL":
            return {"user_id": uid, "product_id": g("productId", "product_id")}

        if tool_name in ("QUERY_LOGISTICS", "QUERY_COMMENT", "PROPOSE_CONFIRM_RECEIPT"):
            return {"user_id": uid, "order_id": g("orderId", "order_id")}
        if tool_name == "QUERY_USER_COUPONS":
            return {"user_id": uid, "status": g("status")}
        if tool_name == "PROPOSE_REFUND":
            return {"user_id": uid, "order_item_id": g("orderItemId", "order_item_id")}
        if tool_name == "PROPOSE_PRODUCT_REVIEW":
            return {
                "user_id": uid,
                "order_id": g("orderId", "order_id"),
                "comment_content": g("commentContent", "comment_content"),
                "star": g("star"),
            }
        if tool_name == "PROPOSE_RECOMMENT":
            return {
                "user_id": uid,
                "order_id": g("orderId", "order_id"),
                "re_comment_content": g("reCommentContent", "re_comment_content"),
            }

        return {"user_id": uid}

mcp_tool_router = McpToolRouter()
