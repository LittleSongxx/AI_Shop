"""模型漏调工具时的确定性兜底。

ReAct 的前提是模型会在需要事实时自己调工具。实际上它会漏：明明在问订单，
却直接凭上下文编一段话。这类回答看起来通顺但可能完全错，比报错更糟。

所以第 0 轮结束后，如果意图明确需要工具而模型没调，就由代码直接调一次，
把结果并进这一轮的输出并直接进 finalize——不再给模型第二次机会，
因为再来一轮它大概率还是不调，只是多花一次 token。

四个分支都在做同一件事：调一个工具，然后把结果拼成"可以收尾"的状态增量。
原先它们内联在 ``agent_loop_node`` 里，各写一遍近似的 dict，
漏一个键就是卡片渲染不出来。这里收敛成一个 ``_finalize_with_tool``。
"""

from __future__ import annotations

import structlog
from langchain_core.messages import ToolMessage

from app.domain.intent.types import IntentKind
from app.domain.intent.write_args import required_tool_for_intent
from app.domain.tool_policy import fallback_biz_type
from app.services.mcp_tool_router import mcp_tool_router
from app.services.tool_invoke_result import ToolInvokeResult

logger = structlog.get_logger()

_TOOL_FAILURE_TEXT = "业务数据查询失败，请稍后重试；当前回复不会猜测订单或操作状态。"


def _biz_type_for(result: ToolInvokeResult, tool_name: str) -> str | None:
    # 工具自己给了就用工具的；否则查策略表兜底（PROPOSE_* 一律 action_confirm）。
    return result.biz_type or fallback_biz_type(tool_name)


def _finalize_with_tool(
    *,
    messages: list,
    tool_name: str,
    result: ToolInvokeResult,
    chunks: list[str],
    search_hint: str | None = None,
) -> dict:
    """把一次强制工具调用的结果拼成直接收尾的状态增量。"""
    return {
        "llm_messages": messages,
        "tools_called": [tool_name],
        "tool_biz": result.to_biz_dict() or None,
        "biz_type": _biz_type_for(result, tool_name),
        "biz_data": result.biz_data,
        "assistant_cards": result.assistant_cards,
        "search_tool_hint": search_hint,
        "search_fallback_done": True,
        "chunks": chunks,
        "pending_tool_calls": [],
        "route": "finalize",
    }


def _finalize_tool_failure(messages: list, error: Exception, *, intent: str | None) -> dict:
    logger.error(
        "forced_mcp_degraded",
        intent=intent,
        error_type=type(error).__name__,
        error=str(error)[:300],
    )
    return {
        "llm_messages": messages,
        "tools_called": [],
        "tool_biz": None,
        "biz_type": None,
        "biz_data": None,
        "assistant_cards": None,
        "search_tool_hint": None,
        "search_fallback_done": True,
        "chunks": [_TOOL_FAILURE_TEXT],
        "pending_tool_calls": [],
        "route": "finalize",
    }


def _has_cards(result: ToolInvokeResult) -> bool:
    return bool(result.assistant_cards and result.assistant_cards.strip() not in ("", "[]"))


def _failed_result(messages: list, result: ToolInvokeResult, *, intent: str | None) -> dict | None:
    if result.success:
        return None
    error = RuntimeError(result.error_code or result.content or "tool failed")
    return _finalize_tool_failure(messages, error, intent=intent)


async def forced_product_search(
    *,
    messages: list,
    user_id: str,
    keyword: str,
    llm_body: str,
    exclude_product_id: str | None,
    log_event: str,
) -> dict:
    """模型该搜商品却没搜时，直接搜一次。

    ``llm_body`` 是模型这一轮已经说出来的话，保留它，否则用户会看到回答凭空消失。
    """
    args: dict = {"keyword": keyword}
    if exclude_product_id:
        args["excludeProductId"] = str(exclude_product_id)
    try:
        result = await mcp_tool_router.invoke("SEARCH_PRODUCTS", args, user_id)
    except Exception as exc:
        return _finalize_tool_failure(messages, exc, intent=IntentKind.PRODUCT_SEARCH.value)
    if failed := _failed_result(messages, result, intent=IntentKind.PRODUCT_SEARCH.value):
        return failed
    logger.info(
        log_event,
        user_id=user_id,
        keyword=keyword,
        exclude_product_id=exclude_product_id,
        has_cards=bool(result.assistant_cards),
    )
    return _finalize_with_tool(
        messages=messages,
        tool_name="SEARCH_PRODUCTS",
        result=result,
        chunks=[llm_body] if llm_body else [],
        search_hint=result.to_tool_message(),
    )


async def forced_tool_for_intent(
    *,
    messages: list,
    user_id: str,
    intent: str | None,
    intent_data: str | None,
    user_text: str,
) -> dict | None:
    """意图要求工具但模型没调时，替它调。参数不全则返回 None 交回模型追问。"""
    try:
        forced = await required_tool_for_intent(
            intent,
            intent_data,
            user_text,
            user_id,
            after_sales_workflow=True,
        )
    except Exception as exc:
        return _finalize_tool_failure(messages, exc, intent=intent)
    if not forced:
        return None
    tool_name, tool_args = forced
    try:
        result = await mcp_tool_router.invoke(tool_name, tool_args, user_id)
    except Exception as exc:
        return _finalize_tool_failure(messages, exc, intent=intent)
    if failed := _failed_result(messages, result, intent=intent):
        return failed
    tool_text = result.to_tool_message() or ""
    messages.append(ToolMessage(content=tool_text or "未查询到相关记录。", tool_call_id="forced_mcp"))
    logger.warning(
        "forced_mcp_after_llm_skip",
        user_id=user_id,
        intent=intent,
        tool=tool_name,
        has_cards=bool(result.assistant_cards),
        has_act_token="act_" in tool_text.lower(),
    )

    # 有卡片时不再输出文本，避免卡片和纯文本重复描述同一批数据。
    chunks = [] if _has_cards(result) else [tool_text or "未查询到相关记录。"]
    return _finalize_with_tool(
        messages=messages,
        tool_name=tool_name,
        result=result,
        chunks=chunks,
        search_hint=tool_text if tool_name == "SEARCH_PRODUCTS" else None,
    )


async def forced_order_list(
    *,
    messages: list,
    user_id: str,
    intent: str | None,
    order_id: str | None,
) -> dict:
    """用户明确在要订单列表时强制查一次，保证前端拿到卡片而不是表格文本。"""
    try:
        result = await mcp_tool_router.invoke(
            "QUERY_ORDERS", {"orderId": order_id} if order_id else {}, user_id
        )
    except Exception as exc:
        return _finalize_tool_failure(messages, exc, intent=intent)
    if failed := _failed_result(messages, result, intent=intent):
        return failed
    tool_text = result.to_tool_message() or ""
    messages.append(
        ToolMessage(content=tool_text or "未查询到相关订单。", tool_call_id="forced_orders_ui")
    )
    logger.warning(
        "forced_query_orders_for_cards",
        user_id=user_id,
        intent=intent,
        has_cards=bool(result.assistant_cards),
    )
    return _finalize_with_tool(
        messages=messages,
        tool_name="QUERY_ORDERS",
        result=result,
        chunks=[],
    )
