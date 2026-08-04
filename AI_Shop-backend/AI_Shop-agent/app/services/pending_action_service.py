from __future__ import annotations

import json
import time
import uuid

import structlog

from app.exceptions import PendingActionExpired, RemoteActionRejected
from app.services.java_internal_client import java_internal_client
from app.services.pending_action_store import pending_action_store
from app.services.redis_service import redis_service
from app.utils.biz_payload import ACTION_LABELS

logger = structlog.get_logger()


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

            # MySQL 已经进入 EXECUTING，Redis 只同步展示态。此后若 HTTP
            # 响应丢失，状态会由 Java 幂等账本/领域数据对账，而不是猜测失败。
            await redis_service.save_pending_action(token, pending)

            try:
                result_message = await executor(pending)
            except RemoteActionRejected as exc:
                reconciled = await self._reconcile_rejected(pending, exc)
                if reconciled is not None:
                    return reconciled
                logger.warning(
                    "pending_action_rejection_outcome_uncertain",
                    token=pending.get("token"),
                    action_type=pending.get("actionType"),
                    error=str(exc),
                )
                return (
                    pending.get("actionType"),
                    False,
                    "操作请求已返回，正在核对执行结果，请勿重复操作",
                )
            except ValueError as exc:
                result_message = str(exc)
                return await self._complete(
                    pending,
                    pending_action_store.FAILED,
                    error_message=result_message,
                )
            except Exception as exc:
                # 网络超时、连接中断或进程取消都无法证明 Java 写操作失败：
                # 请求可能已经提交，只是响应没回来。保留 EXECUTING，交给
                # reconciler 查询幂等账本和订单领域状态，避免重复退款/评价。
                logger.warning(
                    "pending_action_execution_outcome_uncertain",
                    token=token,
                    action_type=pending.get("actionType"),
                    error=str(exc),
                )
                return (
                    pending.get("actionType"),
                    False,
                    "操作已提交，正在核对执行结果，请勿重复操作",
                )

            return await self._complete(
                pending,
                pending_action_store.CONFIRMED,
                result_message=result_message,
            )
        finally:
            await redis_service.unlock_pending_action(token, owner)

    async def _reconcile_rejected(
        self, pending: dict, error: RemoteActionRejected
    ) -> tuple[str, bool, str] | None:
        """Resolve a structured Java rejection without guessing its side effects."""
        token = str(pending.get("token") or "")
        try:
            params = json.loads(pending.get("paramsJson") or "{}")
        except (TypeError, json.JSONDecodeError):
            params = {}
        if not isinstance(params, dict) or not token:
            return None
        try:
            remote = await java_internal_client.get_agent_action_status(
                str(pending.get("userId") or ""),
                str(pending.get("actionType") or ""),
                token,
                params,
            )
        except Exception as exc:
            logger.warning(
                "pending_action_rejection_status_failed",
                token=token,
                error=str(exc),
            )
            return None

        remote_status = str(remote.get("status") or "UNKNOWN").upper()
        result_message = str(remote.get("result_message") or "").strip()
        if remote_status == "SUCCESS":
            return await self._complete(
                pending,
                pending_action_store.CONFIRMED,
                result_message=result_message or "操作已完成",
            )
        if remote_status == "FAILED":
            return await self._complete(
                pending,
                pending_action_store.FAILED,
                error_message=result_message or str(error),
            )
        # PROCESSING/UNKNOWN are deliberately left EXECUTING. A transient
        # status read must never authorize a second refund or review.
        return None

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

    async def reconcile_stale_executing(self, stale_seconds: int = 600) -> int:
        """用 Java 幂等账本与领域状态核对悬挂的 EXECUTING 动作。

        只有 Java 明确返回 SUCCESS/FAILED 时才写终态。PROCESSING、UNKNOWN
        或查询失败都保持 EXECUTING 并告警，禁止自动重试；把不确定结果写成
        FAILED 会允许用户换 token 再执行一次，可能造成重复退款或重复评价。
        """
        await redis_service.ensure_connected()
        stale = await pending_action_store.list_stale_executing(stale_seconds)
        reconciled = 0
        for pending in stale:
            token = pending.get("token") or pending.get("actionToken")
            if not token:
                continue
            owner = uuid.uuid4().hex
            if not await redis_service.try_lock_pending_action(token, owner, ttl_seconds=60):
                continue
            try:
                try:
                    params = json.loads(pending.get("paramsJson") or "{}")
                except (TypeError, json.JSONDecodeError):
                    params = {}
                if not isinstance(params, dict):
                    params = {}
                remote = await java_internal_client.get_agent_action_status(
                    str(pending.get("userId") or ""),
                    str(pending.get("actionType") or ""),
                    str(token),
                    params,
                )
                remote_status = str(remote.get("status") or "UNKNOWN").upper()
                result_message = str(remote.get("result_message") or "").strip()
                if remote_status == "SUCCESS":
                    final = await pending_action_store.complete(
                        token,
                        pending_action_store.CONFIRMED,
                        result_message=result_message or "操作已完成",
                    )
                    expected_status = pending_action_store.CONFIRMED
                elif remote_status == "FAILED":
                    final = await pending_action_store.complete(
                        token,
                        pending_action_store.FAILED,
                        error_message=result_message or "操作执行失败",
                    )
                    expected_status = pending_action_store.FAILED
                else:
                    logger.warning(
                        "pending_action_reconcile_inconclusive",
                        token=token,
                        action_type=pending.get("actionType"),
                        remote_status=remote_status,
                    )
                    continue

                if final:
                    await redis_service.save_pending_action(token, final)
                    # complete 带 status='EXECUTING' CAS；并发执行方若先落终态，
                    # 读回状态可能不同，不能把它计作本轮对账完成。
                    if final.get("statusName") == expected_status:
                        reconciled += 1
                        logger.info(
                            "pending_action_reconciled",
                            token=token,
                            action_type=pending.get("actionType"),
                            status=expected_status,
                        )
            except Exception as exc:
                logger.warning("pending_action_reconcile_failed", token=token, error=str(exc))
            finally:
                await redis_service.unlock_pending_action(token, owner)
        return reconciled

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
