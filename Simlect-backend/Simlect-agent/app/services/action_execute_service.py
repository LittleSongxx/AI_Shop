import httpx
import structlog

from app.config.settings import get_settings

logger = structlog.get_logger()

class JavaBridge:

    async def _post_form(self, token: str, path: str, data: dict) -> dict:

        settings = get_settings()
        url = f"{settings.java_web_url.rstrip('/')}/api{path}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                data=data,
                headers={"token": token},
                cookies={"token": token},
            )
            resp.raise_for_status()
            return resp.json()

    async def refund_order(self, token: str, order_item_id: str) -> str:

        result = await self._post_form(token, "/order/refundOrder", {"orderItemId": order_item_id})
        if result.get("status") == "success":
            return f"订单项 {order_item_id} 退款已处理完成"
        raise ValueError(result.get("info") or "退款失败")

    async def confirm_order(self, token: str, order_id: str) -> str:

        result = await self._post_form(token, "/order/confirmOrder", {"orderId": order_id})
        if result.get("status") == "success":
            return f"订单 {order_id} 已确认收货"
        raise ValueError(result.get("info") or "确认收货失败")

    async def post_comment(
        self, token: str, order_id: str, content: str, star: int
    ) -> str:

        result = await self._post_form(
            token,
            "/order/comment/postComment",
            {"orderId": order_id, "commentContent": content, "star": star},
        )
        if result.get("status") == "success":
            return f"订单 {order_id} 评价成功"
        raise ValueError(result.get("info") or "评价失败")

    async def post_recomment(self, token: str, order_id: str, content: str) -> str:

        result = await self._post_form(
            token,
            "/order/comment/postReComment",
            {"orderId": order_id, "reCommentContent": content},
        )
        if result.get("status") == "success":
            return f"订单 {order_id} 追评成功"
        raise ValueError(result.get("info") or "追评失败")

java_bridge = JavaBridge()

class ActionExecuteService:

    def __init__(self):
        self._bridge = java_bridge

    async def execute(self, pending: dict, token: str) -> str:

        action_type = pending.get("actionType")
        import json
        params = json.loads(pending.get("paramsJson") or "{}")
        if action_type == "REFUND":
            return await self._bridge.refund_order(token, params["orderItemId"])
        if action_type == "CONFIRM_RECEIPT":
            return await self._bridge.confirm_order(token, params["orderId"])
        if action_type == "PRODUCT_REVIEW":
            return await self._bridge.post_comment(
                token, params["orderId"], params["commentContent"], int(params["star"])
            )
        if action_type == "RECOMMENT":
            return await self._bridge.post_recomment(token, params["orderId"], params["reCommentContent"])
        raise ValueError("该操作不支持执行")

action_execute_service = ActionExecuteService()
