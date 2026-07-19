from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from app.services import mcp_tools_service as tools
from app.services.redis_service import redis_service
from app.services.tool_invoke_result import ToolInvokeResult

_MCP_HOST = os.getenv("FASTMCP_HOST", "0.0.0.0")
_MCP_PORT = int(os.getenv("FASTMCP_PORT", "7060"))


@asynccontextmanager
async def _mcp_lifespan(_server: FastMCP) -> AsyncIterator[dict]:
    # MCP is a separate process from Agent; must connect Redis for pending-action cards.
    await redis_service.ensure_connected()
    try:
        yield {}
    finally:
        # Keep connection for process lifetime; sessions reconnect frequently.
        pass


mcp = FastMCP(
    "simlect-tools",
    instructions="Simlect mall agent tools. Read tools return text; write tools create confirm cards.",
    host=_MCP_HOST,
    port=_MCP_PORT,
    lifespan=_mcp_lifespan,
)


def _text(result) -> str:
    if isinstance(result, ToolInvokeResult):
        return result.to_wire()
    return str(result)




@mcp.tool(name="SEARCH_PRODUCTS", description="[READ] 搜索/推荐商品")
async def search_products(
    userId: str,
    keyword: str,
    excludeProductId: str | None = None,
) -> str:
    return _text(await tools.tool_search_products(userId, keyword, excludeProductId))


@mcp.tool(
    name="QUERY_ORDERS",
    description="[READ] 仅查询订单列表或订单状态；用户要评价/退款/确认收货时不要用本工具",
)
async def query_orders(userId: str, orderId: str | None = None) -> str:
    return _text(await tools.tool_query_orders(userId, orderId))


@mcp.tool(name="GET_PRODUCT_DETAIL", description="[READ] 查询商品详情")
async def get_product_detail(userId: str, productId: str) -> str:
    return _text(await tools.tool_get_product_detail(userId, productId))


@mcp.tool(name="QUERY_LOGISTICS", description="[READ] 查询订单物流轨迹（不是查订单列表）")
async def query_logistics(userId: str, orderId: str) -> str:
    return _text(await tools.query_logistics(userId, orderId))


@mcp.tool(name="QUERY_COMMENT", description="[READ] 查看订单已提交的评价内容（不是写评价）")
async def query_comment(userId: str, orderId: str) -> str:
    return _text(await tools.query_comment(userId, orderId))


@mcp.tool(name="QUERY_USER_COUPONS", description="[READ] 查询用户优惠券")
async def query_user_coupons(userId: str, status: int | None = None) -> str:
    return _text(await tools.query_user_coupons(userId, status))


@mcp.tool(
    name="PROPOSE_CONFIRM_RECEIPT",
    description="[WRITE] 确认收货提案；用户说确认收货时直接调用，不要先 QUERY_ORDERS",
)
async def propose_confirm_receipt(userId: str, orderId: str) -> str:
    return _text(await tools.propose_confirm_receipt(userId, orderId))


@mcp.tool(
    name="PROPOSE_REFUND",
    description="[WRITE] 退款提案；用户要退款时直接调用，不要先 QUERY_ORDERS",
)
async def propose_refund(userId: str, orderItemId: str) -> str:
    return _text(await tools.propose_refund(userId, orderItemId))


@mcp.tool(
    name="PROPOSE_PRODUCT_REVIEW",
    description="[WRITE] 提交评价提案；用户要写评价/打分时用；缺星级或内容时先追问用户",
)
async def propose_product_review(
    userId: str,
    orderId: str,
    commentContent: str,
    star: int,
) -> str:
    return _text(await tools.propose_product_review(userId, orderId, commentContent, star))


@mcp.tool(name="PROPOSE_RECOMMENT", description="[WRITE] 提交追评提案；不是查评价")
async def propose_recomment(userId: str, orderId: str, reCommentContent: str) -> str:
    return _text(await tools.propose_recomment(userId, orderId, reCommentContent))


def main() -> None:
    # http://{_MCP_HOST}:{_MCP_PORT}/mcp  (Agent expects :7060)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
