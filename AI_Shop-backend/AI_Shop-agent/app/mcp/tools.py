"""LangChain tool schemas for LLM bind_tools; execution goes through MCP Streamable HTTP."""

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.harness.observation import build_tool_result_observation
from app.services.mcp_streamable_client import mcp_streamable_client


class SearchProductsArgs(BaseModel):
    userId: str = Field(description="用户Id")
    keyword: str = Field(description="搜索关键词（品类/品牌/特征，非用户原话）")
    excludeProductId: str | None = Field(None, description="排除的商品Id，如当前咨询商品")


class SearchProductsByImageArgs(BaseModel):
    userId: str = Field(description="用户Id")
    imageAssetId: str | None = Field(
        None,
        description="服务端已审核的图片资产ID；系统会以当前任务中的可信资产为准",
    )
    queryText: str | None = Field(
        None,
        description="图片搜索的文字约束，如预算、品牌、颜色或品类",
    )
    selectedSubjectId: str | None = Field(
        None,
        description="已由服务端主体选择流程确认的主体ID",
    )


class QueryOrdersArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderId: str | None = Field(None, description="订单号，空则查最近订单")


class ProductDetailArgs(BaseModel):
    userId: str = Field(description="用户Id")
    productId: str = Field(description="商品Id")


class CompareProductsArgs(BaseModel):
    userId: str = Field(description="用户Id")
    productIds: list[str] = Field(
        min_length=2,
        max_length=4,
        description="当前或近期推荐候选中的 2 到 4 个商品Id",
    )


class UserIdOrderArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderId: str = Field(description="订单Id")
    runId: str | None = Field(None, description="当前 Agent Episode runId（服务端关联用）")


class CancelOrderArgs(UserIdOrderArgs):
    runId: str | None = Field(None, description="当前 Agent Episode runId（服务端关联用）")


class UserIdOrderItemArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderItemId: str = Field(description="订单项Id")
    runId: str | None = Field(None, description="当前 Agent Episode runId（服务端关联用）")


class RefundStatusArgs(BaseModel):
    userId: str = Field(description="用户Id")
    orderId: str | None = Field(None, description="订单Id")
    orderItemId: str | None = Field(None, description="订单项Id")
    runId: str | None = Field(None, description="当前 Agent Episode runId（观测关联用）")


class AfterSalesEligibilityArgs(BaseModel):
    userId: str = Field(description="用户Id")
    action: str = Field(description="售后动作，例如 REFUND 或 RETURN")
    orderId: str | None = Field(None, description="订单Id")
    orderItemId: str | None = Field(None, description="订单项Id")
    evidence: list[str] = Field(default_factory=list, description="已提供的凭证类型")
    runId: str | None = Field(None, description="当前 Agent Episode runId")


class ReviewArgs(BaseModel):
    userId: str
    orderId: str
    commentContent: str
    star: int = Field(ge=1, le=5)
    runId: str | None = Field(None, description="当前 Agent Episode runId（服务端关联用）")


class RecommentArgs(BaseModel):
    userId: str
    orderId: str
    reCommentContent: str
    runId: str | None = Field(None, description="当前 Agent Episode runId（服务端关联用）")


class SupportCaseArgs(BaseModel):
    userId: str = Field(description="用户Id")
    category: str = Field(description="工单类别")
    description: str = Field(description="问题描述")
    orderId: str | None = Field(None, description="关联订单号")
    orderItemId: str | None = Field(None, description="关联订单项")
    imageAssetId: str | None = Field(None, description="已通过服务端审核的图片资产ID")
    imageUnderstanding: str | None = Field(None, description="图片内容的受限辅助描述")
    imageUnderstandingStatus: str | None = Field(None, description="图片理解状态")
    runId: str | None = Field(None, description="当前 Agent Episode runId")


class SupportCaseQueryArgs(BaseModel):
    userId: str = Field(description="用户Id")
    caseId: str | None = Field(None, description="工单ID或工单号")


class CouponArgs(BaseModel):
    userId: str
    status: int | None = Field(None, description="0未使用 1已使用 2已过期")


class SearchKnowledgeArgs(BaseModel):
    userId: str = Field(description="用户Id")
    query: str = Field(description="检索关键词（独立、完整，不依赖上下文即可理解）")


async def _call(name: str, **kwargs) -> str:
    args = {k: v for k, v in kwargs.items() if v is not None}
    result = await mcp_streamable_client.call_tool(name, args)
    return build_tool_result_observation(result).text


def build_mcp_tools(allowed_tools: set[str] | frozenset[str] | None = None) -> list[StructuredTool]:
    """Build a tool set scoped to one AgentSpec.

    ``None`` deliberately keeps the legacy full set so feature-flagged rollout
    cannot change the existing single-agent behaviour.
    """
    tools = [
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
            coroutine=lambda userId, imageAssetId=None, queryText=None, selectedSubjectId=None: _call(
                "SEARCH_PRODUCTS_BY_IMAGE",
                userId=userId,
                imageAssetId=imageAssetId,
                queryText=queryText,
                selectedSubjectId=selectedSubjectId,
            ),
            name="SEARCH_PRODUCTS_BY_IMAGE",
            description=(
                "[READ] 基于当前已审核图片查找同图或视觉相似商品；"
                "图片资产和主体由服务端任务上下文校验，不能使用 URL、路径或模型自造框"
            ),
            args_schema=SearchProductsByImageArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId=None: _call(
                "QUERY_ORDERS", userId=userId, orderId=orderId
            ),
            name="QUERY_ORDERS",
            description="[READ] 查询当前用户的订单列表或订单状态；自然语言目标由系统解析器先定位",
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
            coroutine=lambda userId, productIds: _call(
                "COMPARE_PRODUCTS", userId=userId, productIds=productIds
            ),
            name="COMPARE_PRODUCTS",
            description="[READ] 使用实时价格、库存和属性比较 2 到 4 个近期推荐候选",
            args_schema=CompareProductsArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, runId=None: _call(
                "QUERY_LOGISTICS", userId=userId, orderId=orderId, runId=runId
            ),
            name="QUERY_LOGISTICS",
            description="[READ] 查询订单物流轨迹（不是查订单列表）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, runId=None: _call(
                "QUERY_COMMENT", userId=userId, orderId=orderId, runId=runId
            ),
            name="QUERY_COMMENT",
            description="[READ] 查看订单已提交的评价内容（不是写评价）",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId=None, orderItemId=None, runId=None: _call(
                "QUERY_REFUND_STATUS",
                userId=userId,
                orderId=orderId,
                orderItemId=orderItemId,
                runId=runId,
            ),
            name="QUERY_REFUND_STATUS",
            description="[READ] 查询当前用户订单或订单项的退款进度",
            args_schema=RefundStatusArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, action, orderId=None, orderItemId=None,
            evidence=None, runId=None: _call(
                "CHECK_AFTER_SALES_ELIGIBILITY",
                userId=userId,
                action=action,
                orderId=orderId,
                orderItemId=orderItemId,
                evidence=evidence or [],
                runId=runId,
            ),
            name="CHECK_AFTER_SALES_ELIGIBILITY",
            description="[READ] 使用已发布规则和权威订单事实核验退款/退货资格；不执行写操作",
            args_schema=AfterSalesEligibilityArgs,
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
            coroutine=lambda userId, orderId, runId=None: _call(
                "PROPOSE_CONFIRM_RECEIPT", userId=userId, orderId=orderId, runId=runId
            ),
            name="PROPOSE_CONFIRM_RECEIPT",
            description="[WRITE] 为系统已验证归属和状态的订单生成确认收货提案",
            args_schema=UserIdOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, runId=None: _call(
                "PROPOSE_CANCEL_ORDER",
                userId=userId,
                orderId=orderId,
                runId=runId,
            ),
            name="PROPOSE_CANCEL_ORDER",
            description="[WRITE] 为系统已验证归属且待付款的订单生成取消提案",
            args_schema=CancelOrderArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderItemId, runId=None: _call(
                "PROPOSE_REFUND", userId=userId, orderItemId=orderItemId, runId=runId
            ),
            name="PROPOSE_REFUND",
            description="[WRITE] 为系统已验证归属和状态的订单项生成退款提案",
            args_schema=UserIdOrderItemArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, commentContent, star, runId=None: _call(
                "PROPOSE_PRODUCT_REVIEW",
                userId=userId,
                orderId=orderId,
                commentContent=commentContent,
                star=star,
                runId=runId,
            ),
            name="PROPOSE_PRODUCT_REVIEW",
            description="[WRITE] 提交评价提案；用户要写评价/打分时用；缺星级或内容时先追问用户",
            args_schema=ReviewArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, orderId, reCommentContent, runId=None: _call(
                "PROPOSE_RECOMMENT",
                userId=userId,
                orderId=orderId,
                reCommentContent=reCommentContent,
                runId=runId,
            ),
            name="PROPOSE_RECOMMENT",
            description="[WRITE] 提交追评提案；不是查评价",
            args_schema=RecommentArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, category, description, orderId=None, orderItemId=None,
            imageAssetId=None, imageUnderstanding=None,
            imageUnderstandingStatus=None, runId=None: _call(
                "PROPOSE_CREATE_SUPPORT_CASE",
                userId=userId,
                category=category,
                description=description,
                orderId=orderId,
                orderItemId=orderItemId,
                imageAssetId=imageAssetId,
                imageUnderstanding=imageUnderstanding,
                imageUnderstandingStatus=imageUnderstandingStatus,
                runId=runId,
            ),
            name="PROPOSE_CREATE_SUPPORT_CASE",
            description="[WRITE] 创建售后工单提案；地址修改和发票也只能走工单",
            args_schema=SupportCaseArgs,
        ),
        StructuredTool.from_function(
            coroutine=lambda userId, caseId=None: _call(
                "QUERY_SUPPORT_CASES", userId=userId, caseId=caseId
            ),
            name="QUERY_SUPPORT_CASES",
            description="[READ] 查询当前用户本人近期售后工单或指定工单详情",
            args_schema=SupportCaseQueryArgs,
        ),
    ]
    if allowed_tools is None:
        return tools
    allowed = frozenset(allowed_tools)
    return [tool for tool in tools if tool.name in allowed]
