from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.services import mcp_tools_service as tools

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

async def _wrap_search(userId, keyword, excludeProductId=None):
    result = await tools.tool_search_products(userId, keyword, excludeProductId)
    return result.to_tool_message()

async def _wrap_query_orders(userId, orderId=None):
    result = await tools.tool_query_orders(userId, orderId)
    return result.to_tool_message()

def build_mcp_tools() -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            coroutine=_wrap_search,
            name="SEARCH_PRODUCTS",
            description="[READ] 搜索/推荐商品",
            args_schema=SearchProductsArgs,
        ),
        StructuredTool.from_function(
            coroutine=_wrap_query_orders,
            name="QUERY_ORDERS",
            description="[READ] 仅查询订单列表或订单状态；用户要评价/退款/确认收货时不要用本工具",
            args_schema=QueryOrdersArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, productId: tools.tool_get_product_detail(userId, productId),
            name="GET_PRODUCT_DETAIL",
            description="[READ] 查询商品详情",
            args_schema=ProductDetailArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: tools.query_logistics(userId, orderId),
            name="QUERY_LOGISTICS",
            description="[READ] 查询订单物流轨迹（不是查订单列表）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: tools.query_comment(userId, orderId),
            name="QUERY_COMMENT",
            description="[READ] 查看订单已提交的评价内容（不是写评价）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, status=None: tools.query_user_coupons(userId, status),
            name="QUERY_USER_COUPONS",
            description="[READ] 查询用户优惠券",
            args_schema=CouponArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId: tools.propose_confirm_receipt(userId, orderId),
            name="PROPOSE_CONFIRM_RECEIPT",
            description="[WRITE] 确认收货提案；用户说确认收货时直接调用，不要先 QUERY_ORDERS",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderItemId: tools.propose_refund(userId, orderItemId),
            name="PROPOSE_REFUND",
            description="[WRITE] 退款提案；用户要退款时直接调用，不要先 QUERY_ORDERS",
            args_schema=UserIdOrderItemArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, commentContent, star: tools.propose_product_review(
                userId, orderId, commentContent, star
            ),
            name="PROPOSE_PRODUCT_REVIEW",
            description="[WRITE] 提交评价提案；用户要写评价/打分时用；缺星级或内容时先追问用户",
            args_schema=ReviewArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, reCommentContent: tools.propose_recomment(
                userId, orderId, reCommentContent
            ),
            name="PROPOSE_RECOMMENT",
            description="[WRITE] 提交追评提案；不是查评价",
            args_schema=RecommentArgs,
        ),
    ]
