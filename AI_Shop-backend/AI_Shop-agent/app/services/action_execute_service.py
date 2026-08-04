import structlog

from app.config.settings import get_settings
from app.exceptions import RemoteActionOutcomeUnknown, RemoteActionRejected
from app.infra.http_client import get_client
from app.observability.telemetry import get_tracer

logger = structlog.get_logger()
tracer = get_tracer()

class JavaBridge:

    async def _post_form(
        self,
        token: str,
        path: str,
        data: dict,
        idempotency_key: str | None = None,
    ) -> dict:

        settings = get_settings()
        url = f"{settings.java_web_url.rstrip('/')}/api{path}"

        headers = {"token": token}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        with tracer.start_as_current_span("agent.java_action") as span:
            span.set_attribute("agent.java_path", path)
            span.set_attribute("agent.idempotent", bool(idempotency_key))
            client = await get_client("java_action", timeout=30)
            resp = await client.post(
                url,
                data=data,
                headers=headers,
                cookies={"token": token},
            )
            try:
                result = resp.json()
            except ValueError as exc:
                resp.raise_for_status()
                # A successful HTTP status with a truncated/non-JSON body does
                # not prove that the write failed. Keep the local action in
                # EXECUTING so the idempotency ledger/domain state can reconcile
                # it instead of authorizing the user to submit a second write.
                raise RemoteActionOutcomeUnknown("Java 返回了无效响应") from exc
            if not isinstance(result, dict):
                resp.raise_for_status()
                raise RemoteActionOutcomeUnknown("Java 返回了无效响应")
            if result.get("status") != "success":
                raise RemoteActionRejected(
                    result.get("info") or "远端业务操作被拒绝",
                    status_code=resp.status_code,
                )
            if resp.is_error:
                raise RemoteActionRejected(
                    result.get("info") or "远端业务操作被拒绝",
                    status_code=resp.status_code,
                )
            return result

    async def refund_order(self, token: str, order_item_id: str, idempotency_key: str) -> str:

        result = await self._post_form(
            token, "/order/refundOrder", {"orderItemId": order_item_id}, idempotency_key
        )
        if result.get("status") == "success":
            return f"订单项 {order_item_id} 退款已处理完成"
        raise ValueError(result.get("info") or "退款失败")

    async def confirm_order(self, token: str, order_id: str, idempotency_key: str) -> str:

        result = await self._post_form(
            token, "/order/confirmOrder", {"orderId": order_id}, idempotency_key
        )
        if result.get("status") == "success":
            return f"订单 {order_id} 已确认收货"
        raise ValueError(result.get("info") or "确认收货失败")

    async def post_comment(
        self, token: str, order_id: str, content: str, star: int, idempotency_key: str
    ) -> str:

        result = await self._post_form(
            token,
            "/order/comment/postComment",
            {"orderId": order_id, "commentContent": content, "star": star},
            idempotency_key,
        )
        if result.get("status") == "success":
            return f"订单 {order_id} 评价成功"
        raise ValueError(result.get("info") or "评价失败")

    async def post_recomment(
        self, token: str, order_id: str, content: str, idempotency_key: str
    ) -> str:

        result = await self._post_form(
            token,
            "/order/comment/postReComment",
            {"orderId": order_id, "reCommentContent": content},
            idempotency_key,
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
        idempotency_key = str(pending.get("token") or "")
        import json
        params = json.loads(pending.get("paramsJson") or "{}")
        if action_type == "REFUND":
            order_item_id = params.get("orderItemId")
            if not order_item_id:
                raise ValueError("退款参数缺失，请重新发起提案")
            return await self._bridge.refund_order(token, order_item_id, idempotency_key)
        if action_type == "CONFIRM_RECEIPT":
            order_id = params.get("orderId")
            if not order_id:
                raise ValueError("确认收货参数缺失，请重新发起提案")
            return await self._bridge.confirm_order(token, order_id, idempotency_key)
        if action_type == "PRODUCT_REVIEW":
            order_id = params.get("orderId")
            content = params.get("commentContent")
            star = params.get("star")
            if not order_id or not content or star is None:
                raise ValueError("评价参数缺失，请重新发起提案")
            return await self._bridge.post_comment(
                token, order_id, content, int(star), idempotency_key
            )
        if action_type == "RECOMMENT":
            order_id = params.get("orderId")
            content = params.get("reCommentContent")
            if not order_id or not content:
                raise ValueError("追评参数缺失，请重新发起提案")
            return await self._bridge.post_recomment(
                token, order_id, content, idempotency_key
            )
        raise ValueError("该操作不支持执行")

action_execute_service = ActionExecuteService()
