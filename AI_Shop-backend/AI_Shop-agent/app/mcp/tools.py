"""LangChain tool schemas for LLM bind_tools; execution goes through MCP Streamable HTTP."""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.services.mcp_streamable_client import mcp_streamable_client


class SearchProductsArgs(BaseModel):
    userId: str = Field(description="用户Id")
    keyword: str = Field(description="搜索关键词（品类/品牌/特征，非用户原话）")
    excludeProductId: str | None = Field(None, description="排除的商品Id，如当前咨询商品")


class QueryOrdersArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderId: str | None = Field(None, description="订单号，空则查最近订单")


class ProductDetailArgs(BaseModel):
    userId: str = Field(description="用户Id")
    productId: str = Field(description="商品Id")


class UserIdOrderArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderId: str = Field(description="订单Id")


class UserIdOrderItemArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderItemId: str = Field(description="订单项Id")


class ReviewArgs(BaseModel):
    userId: str
    orderId: str
    commentContent: str
    star: int = Field(ge=1, le=5)


class RecommentArgs(BaseModel):
    userId: str
    orderId: str
    reCommentContent: str


class CouponArgs(BaseModel):
    userId: str
    status: int | None = Field(None, description="0未使用 1已使用 2已过期")


class SearchKnowledgeArgs(BaseModel):
    userId: str = Field(description="用户Id")
    query: str = Field(description="检索关键词（独立、完整，不依赖上下文即可理解）")


async def _call(name: str, **kwargs) -> str:
    args = {k: v for k, v in kwargs.items() if v is not None}
    return (await mcp_streamable_client.call_tool(name, args)).to_tool_message()


def build_mcp_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=lambda userId, keyword, excludeProductId=None: _call(
                "SEARCH_PRODUCTS",
                userId=userId,
                keyword=keyword,
                excludeProductId=excludeProductId,
            ),
            name="SEARCH_PRODUCTS",
            description="[READ] 搜索/推荐商品",
            args_schema=SearchProductsArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId=None: _call(
                "QUERY_ORDERS", userId=userId, orderId=orderId
            ),
            name="QUERY_ORDERS",
            description="[READ] 仅查询订单列表或订单状态；用户要评价/退款/确认收货时不要用本工具",
            args_schema=QueryOrdersArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, productId: _call(
                "GET_PRODUCT_DETAIL", userId=userId, productId=productId
            ),
            name="GET_PRODUCT_DETAIL",
            description="[READ] 查询商品详情",
            args_schema=ProductDetailArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: _call(
                "QUERY_LOGISTICS", userId=userId, orderId=orderId
            ),
            name="QUERY_LOGISTICS",
            description="[READ] 查询订单物流轨迹（不是查订单列表）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: _call(
                "QUERY_COMMENT", userId=userId, orderId=orderId
            ),
            name="QUERY_COMMENT",
            description="[READ] 查看订单已提交的评价内容（不是写评价）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, status=None: _call(
                "QUERY_USER_COUPONS", userId=userId, status=status
            ),
            name="QUERY_USER_COUPONS",
            description="[READ] 查询用户优惠券",
            args_schema=CouponArgs,
        ),
        # P3-1 Agentic RAG: in-process tool; route goes through mcp_tool_router,
        # not through the MCP Streamable HTTP server.
        StructuredTool.from_function(
            coroutine=lambda userId, query: _call(
                "SEARCH_KNOWLEDGE", userId=userId, query=query
            ),
            name="SEARCH_KNOWLEDGE",
            description=(
                "[READ] 检索知识库/FAQ；商品政策、售后规则、使用方法等知识类问题，"
                "先调本工具获取依据再回答；系统已注入 RAG 上下文时，对不确定的问题可迭代调用"
            ),
            args_schema=SearchKnowledgeArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: _call(
                "PROPOSE_CONFIRM_RECEIPT", userId=userId, orderId=orderId
            ),
            name="PROPOSE_CONFIRM_RECEIPT",
            description="[WRITE] 确认收货提案；用户说确认收货时直接调用，不要先 QUERY_ORDERS",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderItemId: _call(
                "PROPOSE_REFUND", userId=userId, orderItemId=orderItemId
            ),
            name="PROPOSE_REFUND",
            description="[WRITE] 退款提案；用户要退款时直接调用，不要先 QUERY_ORDERS",
            args_schema=UserIdOrderItemArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, commentContent, star: _call(
                "PROPOSE_PRODUCT_REVIEW",
                userId=userId,
                orderId=orderId,
                commentContent=commentContent,
                star=star,
            ),
            name="PROPOSE_PRODUCT_REVIEW",
            description="[WRITE] 提交评价提案；用户要写评价/打分时用；缺星级或内容时先追问用户",
            args_schema=ReviewArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, reCommentContent: _call(
                "PROPOSE_RECOMMENT",
                userId=userId,
                orderId=orderId,
                reCommentContent=reCommentContent,
            ),
            name="PROPOSE_RECOMMENT",
            description="[WRITE] 提交追评提案；不是查评价",
            args_schema=RecommentArgs,
        ),
    ]
