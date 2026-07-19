import json
import time
import uuid

from app.services.redis_service import redis_service

from app.utils.biz_payload import ACTION_LABELS

class PendingActionService:

    STATUS_PENDING = 0
    STATUS_CONFIRMED = 1
    STATUS_CANCELLED = 2

    async def create_pending(
        self,
        action_type: str,
        user_id: str,
        params: dict,
        summary: str,
    ) -> dict:

        await redis_service.ensure_connected()
        token = f"act_{uuid.uuid4().hex}"

        label, confirm_text, risk_tip = ACTION_LABELS.get(action_type, (action_type, "确认", ""))
        pending = {
            "token": token,
            "userId": user_id,
            "messageId": await redis_service.get_bound_message_id(user_id),
            "actionType": action_type,
            "paramsJson": json.dumps(params, ensure_ascii=False),
            "summary": summary,
            "confirmText": confirm_text,
            "riskTip": risk_tip,
            "status": self.STATUS_PENDING,
            "createTime": int(time.time() * 1000),
        }
        await redis_service.save_pending_action(token, pending)
        return pending

    async def get_by_token(self, token: str) -> dict | None:

        await redis_service.ensure_connected()
        return await redis_service.get_pending_action(token)

    async def load_owned(self, user_id: str, token: str) -> dict:

        pending = await self.get_by_token(token)
        if not pending:
            raise ValueError("操作已过期或不存在")
        if pending.get("userId") != user_id:
            raise ValueError("无权操作该请求")
        return pending

    async def confirm(self, user_id: str, token: str, executor) -> tuple[str, bool, str]:

        await redis_service.ensure_connected()
        # Serialize double-clicks: only one request may hold the lock.
        if not await redis_service.try_lock_pending_action(token, ttl_seconds=120):
            raise ValueError("操作处理中，请勿重复点击")
        try:
            pending = await self.load_owned(user_id, token)
            if pending.get("status") != self.STATUS_PENDING:
                raise ValueError("该操作已处理或已过期")
            try:
                result_message = await executor(pending)
                pending["status"] = self.STATUS_CONFIRMED
                await redis_service.delete_pending_action(token)
                return pending.get("actionType"), True, result_message
            except ValueError as e:
                return pending.get("actionType"), False, str(e)
            except Exception:
                return pending.get("actionType"), False, "系统处理异常，请稍后重试"
        finally:
            await redis_service.unlock_pending_action(token)

    async def cancel(self, user_id: str, token: str) -> None:

        await redis_service.ensure_connected()
        if not await redis_service.try_lock_pending_action(token, ttl_seconds=30):
            raise ValueError("操作处理中，请稍后再试")
        try:
            pending = await self.load_owned(user_id, token)
            if pending.get("status") != self.STATUS_PENDING:
                raise ValueError("该操作已处理或已过期")
            pending["status"] = self.STATUS_CANCELLED
            await redis_service.delete_pending_action(token)
        finally:
            await redis_service.unlock_pending_action(token)

pending_action_service = PendingActionService()
