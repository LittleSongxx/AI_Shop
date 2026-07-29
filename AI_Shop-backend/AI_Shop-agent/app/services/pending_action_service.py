from __future__ import annotations

import json
import time
import uuid

from app.exceptions import PendingActionExpired
from app.services.pending_action_store import pending_action_store
from app.services.redis_service import redis_service
from app.utils.biz_payload import ACTION_LABELS


class PendingActionService:

    STATUS_PENDING = 0
    STATUS_CONFIRMED = 1
    STATUS_CANCELLED = 2
    STATUS_EXECUTING = 3
    STATUS_FAILED = 4
    STATUS_EXPIRED = 5

    async def create_pending(
        self,
        action_type: str,
        user_id: str,
        params: dict,
        summary: str,
    ) -> dict:
        await redis_service.ensure_connected()
        token = f"act_{uuid.uuid4().hex}"
        _label, confirm_text, risk_tip = ACTION_LABELS.get(
            action_type, (action_type, "确认", "")
        )
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
        await pending_action_store.create(pending)
        await redis_service.save_pending_action(token, pending)
        return pending

    async def get_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        pending = await pending_action_store.get(token)
        if pending is not None:
            await redis_service.ensure_connected()
            await redis_service.save_pending_action(token, pending)
        return pending

    async def load_owned(self, user_id: str, token: str) -> dict:
        pending = await self.get_by_token(token)
        if not pending:
            raise PendingActionExpired("操作已过期或不存在")
        if pending.get("userId") != user_id:
            raise ValueError("无权操作该请求")
        if pending.get("status") == self.STATUS_EXPIRED:
            raise PendingActionExpired("操作已过期")
        return pending

    async def confirm(self, user_id: str, token: str, executor) -> tuple[str, bool, str]:
        await redis_service.ensure_connected()
        owner = uuid.uuid4().hex
        if not await redis_service.try_lock_pending_action(token, owner, ttl_seconds=120):
            raise ValueError("操作处理中，请勿重复点击")
        try:
            claimed, pending = await pending_action_store.claim(user_id, token)

            if not pending:
                raise PendingActionExpired("操作已过期或不存在")
            if pending.get("userId") != user_id:
                raise ValueError("无权操作该请求")
            status = pending.get("status")
            if status == self.STATUS_EXPIRED:
                raise PendingActionExpired("操作已过期")
            if status in (self.STATUS_CONFIRMED, self.STATUS_FAILED, self.STATUS_CANCELLED):
                return (
                    pending.get("actionType"),
                    status == self.STATUS_CONFIRMED,
                    pending.get("resultMessage")
                    or pending.get("errorMessage")
                    or "该操作已处理",
                )
            # not claimed → 另一请求正持有 EXECUTING slot，幂等拒绝
            # claimed=True → claim() 已将 status 写为 EXECUTING，正常向下执行
            if not claimed:
                raise ValueError("操作处理中，请勿重复点击")

            try:
                result_message = await executor(pending)
            except ValueError as exc:
                result_message = str(exc)
                return await self._complete(
                    pending,
                    pending_action_store.FAILED,
                    error_message=result_message,
                )
            except Exception:
                result_message = "系统处理异常，请稍后重试"
                return await self._complete(
                    pending,
                    pending_action_store.FAILED,
                    error_message=result_message,
                )

            return await self._complete(
                pending,
                pending_action_store.CONFIRMED,
                result_message=result_message,
            )
        finally:
            await redis_service.unlock_pending_action(token, owner)

    async def _complete(
        self,
        pending: dict,
        status: str,
        *,
        result_message: str | None = None,
        error_message: str | None = None,
    ) -> tuple[str, bool, str]:
        token = pending["token"]
        final = await pending_action_store.complete(
            token,
            status,
            result_message=result_message,
            error_message=error_message,
        )
        if final is None:
            final = await pending_action_store.get(token)
        if final is None:
            return pending.get("actionType"), False, "系统处理异常，请稍后重试"

        await redis_service.save_pending_action(token, final)
        final_status = final.get("status")
        message = (
            final.get("resultMessage")
            or final.get("errorMessage")
            or (
                "操作处理中，请勿重复点击"
                if final_status == self.STATUS_EXECUTING
                else "该操作已处理"
            )
        )
        return (
            final.get("actionType") or pending.get("actionType"),
            final_status == self.STATUS_CONFIRMED,
            message,
        )

    async def cancel(self, user_id: str, token: str) -> None:
        await redis_service.ensure_connected()
        owner = uuid.uuid4().hex
        if not await redis_service.try_lock_pending_action(token, owner, ttl_seconds=30):
            raise ValueError("操作处理中，请稍后再试")
        try:
            pending = await pending_action_store.cancel(user_id, token)
            if not pending:
                raise PendingActionExpired("操作已过期或不存在")
            if pending.get("userId") != user_id:
                raise ValueError("无权操作该请求")
            if pending.get("status") == self.STATUS_EXPIRED:
                raise PendingActionExpired("操作已过期")
            if pending.get("status") != self.STATUS_CANCELLED:
                raise ValueError("该操作已处理或已过期")
            await redis_service.save_pending_action(token, pending)
        finally:
            await redis_service.unlock_pending_action(token, owner)


pending_action_service = PendingActionService()
