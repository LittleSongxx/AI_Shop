from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from starlette.responses import JSONResponse

from app.config.settings import get_settings
from app.db.migrations import run_migrations
from app.db.pool import close_pool, init_pool
from app.observability.logging import configure_structured_logging
from app.services import mcp_tools_service as tools
from app.services.episode_service import episode_service
from app.services.java_internal_client import delegated_user_scope
from app.services.redis_service import redis_service
from app.services.shopping_mission_service import initialize_category_need_schemas
from app.services.tool_invoke_result import ToolInvokeResult

_MCP_HOST = os.getenv("FASTMCP_HOST", "127.0.0.1")
_MCP_PORT = int(os.getenv("FASTMCP_PORT", "7060"))
configure_structured_logging()

# mcp 1.28.1 defines Settings before FastMCP and leaves its lifespan forward
# reference unresolved under pydantic-settings 2.15.
FastMCPSettings.model_rebuild()


@asynccontextmanager
async def _mcp_lifespan(_server: FastMCP) -> AsyncIterator[dict]:
    settings = get_settings()
    await redis_service.ensure_connected()
    await init_pool()
    if settings.agent_auto_migrate:
        await asyncio.to_thread(run_migrations)
    await initialize_category_need_schemas()
    await episode_service.start()
    try:
        yield {}
    finally:
        await episode_service.close()
        await close_pool()
        await redis_service.close()


mcp = FastMCP(
    "aishop-tools",
    instructions=(
        "AI_Shop mall agent tools. Read tools return text; write tools create confirm cards. "
        "Tool contract: aishop-tools/current."
    ),
    host=_MCP_HOST,
    port=_MCP_PORT,
    lifespan=_mcp_lifespan,
)


def _text(result) -> str:
    if isinstance(result, ToolInvokeResult):
        return result.to_wire()
    content = str(result)
    failed = content.lstrip().startswith("【") and "失败】" in content[:40]
    return ToolInvokeResult(
        content=content,
        success=not failed,
        error_code="BUSINESS_REJECTED" if failed else None,
    ).to_wire()


async def _run_as_delegated_user(
    user_id: str,
    operation: Awaitable[Any],
) -> Any:
    # The Agent router replaces model-supplied userId with the authenticated
    # message owner before crossing MCP. Rebind it in this process so calls to
    # Java carry the required X-Agent-User-Id header.
    with delegated_user_scope(user_id):
        return await operation


@mcp.tool(name="MCP_CONTRACT", description="[SYSTEM] report the AI_Shop tool contract version")
async def mcp_contract() -> str:
    return _text(ToolInvokeResult(content="ok"))


@mcp.tool(name="SEARCH_PRODUCTS", description="[READ] 搜索/推荐商品")
async def search_products(
    userId: str,
    keyword: str,
    excludeProductId: str | None = None,
    requestId: str | None = None,
    runId: str | None = None,
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.tool_search_products(
                userId,
                keyword,
                excludeProductId,
                request_id=requestId,
                run_id=runId,
            ),
        )
    )


@mcp.tool(
    name="QUERY_ORDERS",
    description="[READ] 查询当前用户的订单列表或订单状态；自然语言目标由系统解析器先定位",
)
async def query_orders(userId: str, orderId: str | None = None) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.tool_query_orders(userId, orderId),
        )
    )


@mcp.tool(name="GET_PRODUCT_DETAIL", description="[READ] 查询商品详情")
async def get_product_detail(userId: str, productId: str) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.tool_get_product_detail(userId, productId),
        )
    )


@mcp.tool(
    name="COMPARE_PRODUCTS",
    description="[READ] 使用实时商品快照比较 2 到 4 个当前或近期推荐候选",
)
async def compare_products(userId: str, productIds: list[str]) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.tool_compare_products(userId, productIds),
        )
    )


@mcp.tool(name="QUERY_LOGISTICS", description="[READ] 查询订单物流轨迹（不是查订单列表）")
async def query_logistics(userId: str, orderId: str, runId: str | None = None) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.query_logistics(userId, orderId),
        )
    )


@mcp.tool(name="QUERY_COMMENT", description="[READ] 查看订单已提交的评价内容（不是写评价）")
async def query_comment(userId: str, orderId: str, runId: str | None = None) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.query_comment(userId, orderId),
        )
    )


@mcp.tool(name="QUERY_REFUND_STATUS", description="[READ] 查询当前用户订单或订单项的退款进度")
async def query_refund_status(
    userId: str,
    orderId: str | None = None,
    orderItemId: str | None = None,
    runId: str | None = None,
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.query_refund_status(userId, orderId, orderItemId),
        )
    )


@mcp.tool(name="QUERY_USER_COUPONS", description="[READ] 查询用户优惠券")
async def query_user_coupons(userId: str, status: int | None = None) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.query_user_coupons(userId, status),
        )
    )


@mcp.tool(
    name="PROPOSE_CONFIRM_RECEIPT",
    description="[WRITE] 为系统已验证归属和状态的订单生成确认收货提案",
)
async def propose_confirm_receipt(
    userId: str, orderId: str, runId: str | None = None
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_confirm_receipt(userId, orderId, runId),
        )
    )


@mcp.tool(
    name="PROPOSE_CANCEL_ORDER",
    description="[WRITE] 为系统已验证归属且待付款的订单生成取消提案",
)
async def propose_cancel_order(
    userId: str, orderId: str, runId: str | None = None
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_cancel_order(userId, orderId, runId),
        )
    )


@mcp.tool(
    name="PROPOSE_CREATE_SUPPORT_CASE",
    description="[WRITE] 创建售后工单提案；地址修改和发票也只能走工单",
)
async def propose_create_support_case(
    userId: str,
    category: str,
    description: str,
    orderId: str | None = None,
    orderItemId: str | None = None,
    imageAssetId: str | None = None,
    imageUnderstanding: str | None = None,
    imageUnderstandingStatus: str | None = None,
    runId: str | None = None,
    sourceMessageId: int | None = None,
    forcedHandoff: bool = False,
    priority: str = "NORMAL",
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_create_support_case(
                userId,
                category,
                description,
                orderId,
                orderItemId,
                imageAssetId,
                imageUnderstanding,
                imageUnderstandingStatus,
                runId,
                sourceMessageId,
                forcedHandoff,
                priority,
            ),
        )
    )


@mcp.tool(
    name="QUERY_SUPPORT_CASES",
    description="[READ] 查询当前用户本人近期售后工单或指定工单详情",
)
async def query_support_cases(
    userId: str, caseId: str | None = None, runId: str | None = None
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.query_support_cases(userId, caseId),
        )
    )


@mcp.tool(
    name="PROPOSE_REFUND",
    description="[WRITE] 为系统已验证归属和状态的订单项生成退款提案",
)
async def propose_refund(
    userId: str, orderItemId: str, runId: str | None = None
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_refund(userId, orderItemId, runId),
        )
    )


@mcp.tool(
    name="PROPOSE_PRODUCT_REVIEW",
    description="[WRITE] 提交评价提案；用户要写评价/打分时用；缺星级或内容时先追问用户",
)
async def propose_product_review(
    userId: str,
    orderId: str,
    commentContent: str,
    star: int,
    runId: str | None = None,
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_product_review(
                userId, orderId, commentContent, star, runId
            ),
        )
    )


@mcp.tool(name="PROPOSE_RECOMMENT", description="[WRITE] 提交追评提案；不是查评价")
async def propose_recomment(
    userId: str,
    orderId: str,
    reCommentContent: str,
    runId: str | None = None,
) -> str:
    return _text(
        await _run_as_delegated_user(
            userId,
            tools.propose_recomment(userId, orderId, reCommentContent, runId),
        )
    )


def main() -> None:
    import uvicorn

    settings = get_settings()
    settings.validate_runtime()
    app = InternalTokenMiddleware(mcp.streamable_http_app(), settings.internal_token)
    uvicorn.run(app, host=_MCP_HOST, port=_MCP_PORT)


class InternalTokenMiddleware:

    def __init__(self, app, expected_token: str):
        self.app = app
        self.expected_token = expected_token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            supplied = headers.get("x-internal-token", "")
            if not supplied or not secrets.compare_digest(supplied, self.expected_token):
                response = JSONResponse({"detail": "invalid internal token"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    main()
