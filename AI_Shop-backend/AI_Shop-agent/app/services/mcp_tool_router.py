from __future__ import annotations

import hashlib
import json
import time

import structlog

from app.domain.tool_policy import policy_for
from app.harness.agents.contracts import VerifiedImageContext
from app.harness.guardrails.tool_guard import ToolGuardrail
from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL, measure_agent_stage
from app.observability.telemetry import get_tracer
from app.services.badcase_service import badcase_service
from app.services.episode_service import current_episode, episode_service
from app.services.mcp_streamable_client import mcp_streamable_client
from app.services.tool_invoke_result import ToolInvokeResult

logger = structlog.get_logger()
tool_guard = ToolGuardrail()


class McpToolRouter:
    """Dispatches tools via Streamable HTTP MCP server (not in-process).

    Exception: SEARCH_KNOWLEDGE is handled in-process (P3-1 Agentic RAG) and
    is never forwarded to the MCP Streamable HTTP server.
    """

    async def invoke(
        self,
        tool_name: str,
        args: dict,
        user_id: str,
        call_id: str | None = None,
        *,
        verified_image_context: VerifiedImageContext | dict | None = None,
        source_message_id: int | None = None,
    ) -> ToolInvokeResult:
        started = time.perf_counter()
        result: ToolInvokeResult | None = None
        error: Exception | None = None
        observable_args = self._observable_args(tool_name, args, user_id)
        with get_tracer().start_as_current_span("agent.tool.call") as span:
            span.set_attribute("agent.tool.name", tool_name)
            if call_id:
                span.set_attribute("agent.tool.call_id", call_id)
            try:
                with measure_agent_stage("tool"):
                    result = await self._invoke_unmeasured(
                        tool_name,
                        args,
                        user_id,
                        verified_image_context=verified_image_context,
                        source_message_id=source_message_id,
                    )
                span.set_attribute("agent.tool.success", bool(result.success))
                return result
            except Exception as exc:
                error = exc
                span.record_exception(exc)
                raise
            finally:
                elapsed_ms = round((time.perf_counter() - started) * 1_000)
                episode_service.record_step(
                    "TOOL_CALL",
                    node_name="tools",
                    status=(
                        "OK" if result and result.success else "ERROR"
                    ),
                    input_data={"args": observable_args},
                    output_data=(
                        {
                            "success": result.success,
                            "errorCode": result.error_code,
                            "bizType": result.biz_type,
                            "hasCards": bool(result.assistant_cards),
                            "sourceCount": len(result.source_refs),
                            "productIds": result.product_ids[:20],
                            "retrievalTrace": result.retrieval_trace,
                        }
                        if result
                        else None
                    ),
                    tool_name=tool_name,
                    call_id=call_id,
                    error_code=(
                        result.error_code
                        if result
                        else type(error).__name__ if error else "UNHANDLED_ERROR"
                    ),
                    error_message=str(error) if error else None,
                    latency_ms=elapsed_ms,
                )
                signal_key = hashlib.sha256(
                    f"{tool_name}:{call_id or time.time_ns()}".encode("utf-8")
                ).hexdigest()[:16]
                episode_service.update_run(
                    reward_signals={
                        "toolResults": {
                            signal_key: {
                                "toolName": tool_name,
                                "success": bool(result and result.success),
                                "errorCode": (
                                    result.error_code
                                    if result
                                    else type(error).__name__ if error else "UNHANDLED_ERROR"
                                ),
                                "bizType": result.biz_type if result else None,
                                "hasCards": bool(result and result.assistant_cards),
                                "hasSourceRefs": bool(result and result.source_refs),
                            }
                        }
                    }
                )
                if (result is not None and not result.success) or error is not None:
                    context = current_episode()
                    try:
                        await badcase_service.add_candidate(
                            context.message_id if context else None,
                            "TOOL_ERROR",
                            f"{tool_name} 调用失败",
                            run_id=context.run_id if context else None,
                            source="TOOL",
                            severity=(
                                "HIGH" if tool_name.startswith("PROPOSE_") else "MEDIUM"
                            ),
                            snapshot={
                                "toolName": tool_name,
                                "callId": call_id,
                                "errorCode": (
                                    result.error_code
                                    if result
                                    else type(error).__name__ if error else None
                                ),
                            },
                        )
                    except Exception as capture_error:
                        logger.warning(
                            "tool_badcase_capture_failed",
                            tool=tool_name,
                            error=type(capture_error).__name__,
                        )

    def _observable_args(self, tool_name: str, args: dict, user_id: str) -> dict:
        """Return the normalized shape without retaining a claimed identity."""
        raw = dict(args or {})
        raw.pop("user_id", None)
        raw["userId"] = user_id
        policy = policy_for(tool_name)
        if policy and policy.is_write:
            context = current_episode()
            if context and context.run_id:
                raw.setdefault("runId", context.run_id)
        if tool_name == "SEARCH_KNOWLEDGE":
            return {"userId": user_id, "query": raw.get("query") or ""}
        if tool_name == "SEARCH_PRODUCTS_BY_IMAGE":
            trusted = raw.get("imageAssetId") or raw.get("image_asset_id")
            return {
                "userId": user_id,
                "imageAssetId": str(trusted)[:64] if trusted else None,
                "queryText": str(raw.get("queryText") or raw.get("query_text") or "")[:500],
                "selectedSubjectId": str(
                    raw.get("selectedSubjectId") or raw.get("selected_subject_id") or ""
                )[:64]
                or None,
            }
        try:
            return self._to_mcp_args(tool_name, raw)
        except Exception:
            return {
                "userId": user_id,
                "argumentKeys": sorted(str(key) for key in raw if key != "userId"),
            }

    async def _invoke_unmeasured(
        self,
        tool_name: str,
        args: dict,
        user_id: str,
        *,
        verified_image_context: VerifiedImageContext | dict | None = None,
        source_message_id: int | None = None,
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
            context = current_episode()
            if context and context.run_id:
                raw.setdefault("runId", context.run_id)
            logger.info(
                "write_tool_invoked",
                tool=tool_name,
                risk=policy.risk.value,
                user_id=user_id,
            )

        # P3-1: in-process tools are handled locally, never forwarded to the
        # MCP Streamable HTTP server.
        if tool_name == "SEARCH_PRODUCTS_BY_IMAGE":
            return await self._search_products_by_image(
                raw,
                user_id,
                verified_image_context=verified_image_context,
                source_message_id=source_message_id,
            )
        if tool_name == "SEARCH_KNOWLEDGE":
            return await self._search_knowledge(
                raw.get("query") or "",
                user_id,
                category_filter=(
                    list(raw.get("_categoryFilter") or [])
                    if isinstance(raw.get("_categoryFilter"), list)
                    else None
                ),
            )
        if tool_name == "CHECK_AFTER_SALES_ELIGIBILITY":
            return await self._check_after_sales_eligibility(raw, user_id)

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

    async def _search_knowledge(
        self,
        query: str,
        user_id: str,
        category_filter: list[str] | None = None,
    ) -> ToolInvokeResult:
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
                query,
                category_filter=category_filter,
                bucket=get_bucket(user_id),
            )
            text = str(result.get("text") or "")
            source_refs = list(result.get("source_refs") or [])
            retrieval_trace = (
                result.get("trace") if isinstance(result.get("trace"), dict) else None
            )
            TOOL_CALL_TOTAL.labels(tool="SEARCH_KNOWLEDGE", status="success").inc()
            if not text:
                return ToolInvokeResult(
                    content="【知识检索】未找到通过证据门禁的相关内容",
                    source_refs=[],
                    retrieval_trace=retrieval_trace,
                )
            return ToolInvokeResult(
                content=text,
                source_refs=source_refs,
                retrieval_trace=retrieval_trace,
            )
        except Exception as e:
            logger.exception("search_knowledge_failed", query=query[:80], error=str(e))
            TOOL_CALL_TOTAL.labels(tool="SEARCH_KNOWLEDGE", status="error").inc()
            return ToolInvokeResult(
                content="【知识检索失败】系统处理异常，请稍后重试",
                success=False,
                error_code="TOOL_ERROR",
            )

    async def _check_after_sales_eligibility(
        self, args: dict, user_id: str
    ) -> ToolInvokeResult:
        from app.services.after_sales_policy_service import (
            POLICY_UNAVAILABLE,
            after_sales_policy_service,
        )

        action = str(args.get("action") or "").strip().upper()
        if not action:
            return ToolInvokeResult(
                content="【售后资格核验失败】缺少售后动作",
                success=False,
                error_code="BAD_ARGS",
            )
        try:
            result = await after_sales_policy_service.evaluate(
                user_id=user_id,
                action=action,
                order_id=args.get("orderId") or args.get("order_id"),
                order_item_id=args.get("orderItemId") or args.get("order_item_id"),
                evidence=list(args.get("evidence") or []),
            )
        except Exception as exc:
            logger.exception("after_sales_eligibility_tool_failed", error=type(exc).__name__)
            return ToolInvokeResult(
                content="【售后资格核验失败】权威订单或规则服务暂不可用，请转人工核验",
                success=False,
                error_code="POLICY_UNAVAILABLE",
            )
        decision = str(result.get("decision") or POLICY_UNAVAILABLE)
        if decision == "POLICY_UNAVAILABLE":
            return ToolInvokeResult(
                content="【售后资格核验】当前没有可用的已发布规则，请转人工核验",
                success=False,
                error_code=decision,
                biz_type="after_sales_eligibility",
            )
        refs = []
        if result.get("policyId") or result.get("decisionId"):
            refs.append(
                {
                    "type": "policy",
                    "policyId": result.get("policyId"),
                    "knowledgeVersion": result.get("policyVersion"),
                    "decisionId": result.get("decisionId"),
                }
            )
        return ToolInvokeResult(
            content=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            success=True,
            biz_type="after_sales_eligibility",
            biz_data=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            source_refs=refs,
            retrieval_trace={"decision": decision, "policyVersion": result.get("policyVersion")},
        )

    async def _search_products_by_image(
        self,
        args: dict,
        user_id: str,
        *,
        verified_image_context: VerifiedImageContext | dict | None,
        source_message_id: int | None,
    ) -> ToolInvokeResult:
        """Run visual retrieval only with server-owned image context.

        The LLM-visible arguments are intentionally advisory. The actual asset,
        moderation state and selected subject come from the graph task and are
        validated again here before any bytes are read.
        """
        if verified_image_context is None:
            TOOL_CALL_TOTAL.labels(tool="SEARCH_PRODUCTS_BY_IMAGE", status="missing_context").inc()
            return ToolInvokeResult(
                content="【识图找商品失败】当前消息没有可验证的图片资产，请重新上传图片。",
                success=False,
                error_code="VISUAL_CONTEXT_REQUIRED",
            )
        try:
            context = (
                verified_image_context
                if isinstance(verified_image_context, VerifiedImageContext)
                else VerifiedImageContext.model_validate(verified_image_context)
            )
            query_text = str(args.get("queryText") or args.get("query_text") or "").strip()
            from app.visual.search_service import visual_product_search_service

            result = await visual_product_search_service.search(
                user_id=user_id,
                image_context=context,
                query_text=query_text or "查找图中同款或相似商品",
                source_message_id=source_message_id,
            )
            TOOL_CALL_TOTAL.labels(
                tool="SEARCH_PRODUCTS_BY_IMAGE",
                status="success" if result.success else "business_rejected",
            ).inc()
            return result
        except ValueError:
            TOOL_CALL_TOTAL.labels(tool="SEARCH_PRODUCTS_BY_IMAGE", status="bad_context").inc()
            return ToolInvokeResult(
                content="【识图找商品失败】图片资产上下文无效，请重新上传图片。",
                success=False,
                error_code="VISUAL_CONTEXT_INVALID",
            )
        except Exception as exc:
            logger.exception("visual_product_search_failed", error=type(exc).__name__)
            TOOL_CALL_TOTAL.labels(tool="SEARCH_PRODUCTS_BY_IMAGE", status="error").inc()
            return ToolInvokeResult(
                content="【识图找商品失败】视觉检索暂时不可用，请稍后重试。",
                success=False,
                error_code="VISUAL_SEARCH_ERROR",
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
        if tool_name == "SEARCH_PRODUCTS_BY_IMAGE":
            out = {
                "userId": uid,
                "imageAssetId": g("imageAssetId", "image_asset_id"),
                "queryText": g("queryText", "query_text"),
                "selectedSubjectId": g("selectedSubjectId", "selected_subject_id"),
            }
            return {key: value for key, value in out.items() if value is not None}
        if tool_name == "QUERY_ORDERS":
            out = {"userId": uid}
            oid = g("orderId", "order_id")
            if oid is not None:
                out["orderId"] = oid
            return out
        if tool_name == "GET_PRODUCT_DETAIL":
            return {"userId": uid, "productId": g("productId", "product_id")}
        if tool_name == "COMPARE_PRODUCTS":
            return {"userId": uid, "productIds": list(g("productIds", "product_ids") or [])}
        if tool_name in (
            "QUERY_LOGISTICS",
            "QUERY_COMMENT",
            "PROPOSE_CONFIRM_RECEIPT",
            "PROPOSE_CANCEL_ORDER",
        ):
            out = {"userId": uid, "orderId": g("orderId", "order_id")}
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "QUERY_REFUND_STATUS":
            out = {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "orderItemId": g("orderItemId", "order_item_id"),
            }
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "CHECK_AFTER_SALES_ELIGIBILITY":
            out = {
                "userId": uid,
                "action": g("action"),
                "orderId": g("orderId", "order_id"),
                "orderItemId": g("orderItemId", "order_item_id"),
                "evidence": list(g("evidence") or []),
            }
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "QUERY_USER_COUPONS":
            out = {"userId": uid}
            st = g("status")
            if st is not None:
                out["status"] = st
            return out
        if tool_name == "PROPOSE_REFUND":
            out = {
                "userId": uid,
                "orderItemId": g("orderItemId", "order_item_id"),
            }
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "PROPOSE_PRODUCT_REVIEW":
            out = {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "commentContent": g("commentContent", "comment_content"),
                "star": g("star"),
            }
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "PROPOSE_RECOMMENT":
            out = {
                "userId": uid,
                "orderId": g("orderId", "order_id"),
                "reCommentContent": g("reCommentContent", "re_comment_content"),
            }
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        if tool_name == "PROPOSE_CREATE_SUPPORT_CASE":
            out = {
                "userId": uid,
                "category": g("category"),
                "description": g("description"),
            }
            optional = {
                "orderId": g("orderId", "order_id"),
                "orderItemId": g("orderItemId", "order_item_id"),
                "imageAssetId": g("imageAssetId", "image_asset_id"),
                "imageUnderstanding": g(
                    "imageUnderstanding", "image_understanding"
                ),
                "imageUnderstandingStatus": g(
                    "imageUnderstandingStatus", "image_understanding_status"
                ),
                "runId": g("runId", "run_id"),
                "sourceMessageId": g("sourceMessageId", "source_message_id"),
                "forcedHandoff": g("forcedHandoff", "forced_handoff"),
                "priority": g("priority"),
            }
            out.update({key: value for key, value in optional.items() if value is not None})
            return out
        if tool_name == "QUERY_SUPPORT_CASES":
            out = {"userId": uid}
            case_id = g("caseId", "case_id", "caseNo", "case_no")
            if case_id:
                out["caseId"] = case_id
            run_id = g("runId", "run_id")
            if run_id:
                out["runId"] = run_id
            return out
        return {"userId": uid}


mcp_tool_router = McpToolRouter()
