from __future__ import annotations

import structlog

from app.domain.tool_policy import policy_for
from app.harness.guardrails.tool_guard import ToolGuardrail
from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL, measure_agent_stage
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.tool_invoke_result import ToolInvokeResult

logger = structlog.get_logger()
tool_guard = ToolGuardrail()


class McpToolRouter:
    """Dispatches tools via Streamable HTTP MCP server (not in-process).

    Exception: SEARCH_KNOWLEDGE is handled in-process (P3-1 Agentic RAG) and
    is never forwarded to the MCP Streamable HTTP server.
    """

    async def invoke(self, tool_name: str, args: dict, user_id: str) -> ToolInvokeResult:
        with measure_agent_stage("tool"):
            return await self._invoke_unmeasured(tool_name, args, user_id)

    async def _invoke_unmeasured(
        self, tool_name: str, args: dict, user_id: str
    ) -> ToolInvokeResult:

        # 白名单即策略表：表里没有就是未知工具，不放行。
        policy = policy_for(tool_name)
        if policy is None:
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="denied").inc()
            return ToolInvokeResult(
                content=f"未知工具: {tool_name}", success=False, error_code="TOOL_DENIED"
            )

        raw = dict(args or {})

        # 归属校验必须在覆写之前：覆写之后再校验，看到的永远是自己刚写进去的值，
        # 这条分支永远返回 True——那是一条不会响的警报。
        if not tool_guard.validate_tool_args(tool_name, raw, user_id):
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="user_id_mismatch").inc()
            logger.warning(
                "tool_arg_user_id_mismatch",
                tool=tool_name,
                user_id=user_id,
                claimed=tool_guard.claimed_user_id(raw),
            )

        # 不论模型给了什么，身份一律取认证得到的 user_id——真正起作用的是这次覆写，
        # 不是上面那次校验。这里不拒绝调用：模型常会把工具结果里看到的 userId 原样带回来，
        # 拒绝并不比覆写更安全，只会把一次能正常完成的对话变成报错。
        # 两种写法都清掉，否则 _to_mcp_args 在 userId 为 None 时会退回去取 user_id。
        raw.pop("user_id", None)
        raw["userId"] = user_id

        # 写操作留一条审计线索：出问题时要能回答"谁在什么时候发起了哪个提案"。
        # 只读调用量大且没有追溯价值，不记。
        if policy.is_write:
            logger.info(
                "write_tool_invoked",
                tool=tool_name,
                risk=policy.risk.value,
                user_id=user_id,
            )

        # P3-1: in-process tools are handled locally, never forwarded to the
        # MCP Streamable HTTP server.
        if tool_name == "SEARCH_KNOWLEDGE":
            return await self._search_knowledge(raw.get("query") or "", user_id)

        try:
            mcp_args = self._to_mcp_args(tool_name, raw)
            result = await mcp_streamable_client.call_tool(tool_name, mcp_args)
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="success").inc()
            return result
        except TypeError as e:
            logger.warning("mcp_tool_bad_args", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="bad_args").inc()
            return ToolInvokeResult(
                content="【操作失败】参数不完整",
                success=False,
                error_code="BAD_ARGS",
            )
        except Exception as e:
            logger.exception("mcp_tool_failed", tool=tool_name, error=str(e))
            TOOL_CALL_TOTAL.labels(tool=tool_name, status="error").inc()
            return ToolInvokeResult(
                content="【操作失败】系统处理异常，请稍后重试",
                success=False,
                error_code="TOOL_ERROR",
            )

    # ------------------------------------------------------------------
    # In-process tool handlers
    # ------------------------------------------------------------------

    async def _search_knowledge(self, query: str, user_id: str) -> ToolInvokeResult:
        """P3-1 Agentic RAG: in-process knowledge/FAQ retrieval.

        Uses the same rag_retriever pipeline (query expansion + hybrid search
        + rerank) as the fixed build_context_node RAG path, so result quality
        is identical.  Import is deferred to avoid a circular-import cycle at
        module load time.
        """
        from app.rag.ab_test import get_bucket
        from app.rag.retriever import rag_retriever  # deferred: avoids circular import

        if not query:
            TOOL_CALL_TOTAL.labels(tool="SEARCH_KNOWLEDGE", status="bad_args").inc()
            return ToolInvokeResult(
                content="【知识检索失败】请提供检索关键词",
                success=False,
                error_code="BAD_ARGS",
            )
        try:
            # Agentic RAG 路径同样带上用户的 A/B 分桶，保证缓存键与预取路径一致。
            result = await rag_retriever.search_faq_with_trace(
                query, bucket=get_bucket(user_id)
            )
            text = str(result.get("text") or "")
            TOOL_CALL_TOTAL.labels(tool="SEARCH_KNOWLEDGE", status="success").inc()
            if not text:
                return ToolInvokeResult(content="【知识检索】未找到相关内容")
            return ToolInvokeResult(content=text)
        except Exception as e:
            logger.exception("search_knowledge_failed", query=query[:80], error=str(e))
            TOOL_CALL_TOTAL.labels(tool="SEARCH_KNOWLEDGE", status="error").inc()
            return ToolInvokeResult(
                content="【知识检索失败】系统处理异常，请稍后重试",
                success=False,
                error_code="TOOL_ERROR",
            )

    # ------------------------------------------------------------------
    # Argument normalisation (MCP server tools only)
    # ------------------------------------------------------------------

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
        if tool_name == "QUERY_REFUND_STATUS":
            return {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "orderItemId": g("orderItemId", "order_item_id"),
            }
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
