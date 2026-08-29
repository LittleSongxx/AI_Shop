from __future__ import annotations

import hashlib
import json
import time
import uuid

import structlog

from app.config.settings import get_settings
from app.exceptions import PendingActionConflict, PendingActionExpired, RemoteActionRejected
from app.services.episode_service import current_episode, episode_service
from app.services.java_internal_client import delegated_user_scope, java_internal_client
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
    STATUS_INCONCLUSIVE = 6
    STATUS_MANUAL_REVIEW = 7

    _RESOURCE_FIELDS = {
        "REFUND": "orderItemId",
        "CANCEL_ORDER": "orderId",
        "CONFIRM_RECEIPT": "orderId",
        "PRODUCT_REVIEW": "orderId",
        "RECOMMENT": "orderId",
        "CREATE_SUPPORT_CASE": "caseDedupeKey",
    }

    async def create_pending(
        self,
        action_type: str,
        user_id: str,
        params: dict,
        summary: str,
        *,
        run_id: str | None = None,
    ) -> dict:
        await redis_service.ensure_connected()
        canonical_params = json.dumps(
            params,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        resource_field = self._RESOURCE_FIELDS.get(action_type)
        resource_id = str(params.get(resource_field) or "").strip() if resource_field else ""
        if not resource_id:
            raise ValueError(f"{action_type} 缺少可用于业务去重的资源 ID")
        business_key = f"{user_id}:{action_type}:{resource_id}"
        if len(business_key) > 255:
            raise ValueError("业务去重键超过数据库长度限制")
        args_fingerprint = hashlib.sha256(canonical_params.encode("utf-8")).hexdigest()
        token = f"act_{uuid.uuid4().hex}"
        _label, confirm_text, risk_tip = ACTION_LABELS.get(
            action_type, (action_type, "确认", "")
        )
        context = current_episode()
        resolved_run_id = str(run_id or (context.run_id if context else "")).strip() or None
        pending = {
            "token": token,
            "userId": user_id,
            "messageId": await redis_service.get_bound_message_id(user_id),
            "actionType": action_type,
            "runId": resolved_run_id,
            "paramsJson": canonical_params,
            "businessKey": business_key,
            "argsFingerprint": args_fingerprint,
            "summary": summary,
            "confirmText": confirm_text,
            "riskTip": risk_tip,
            "status": self.STATUS_PENDING,
            "createTime": int(time.time() * 1000),
        }
        stored, created = await pending_action_store.create(pending)
        if not created and stored.get("argsFingerprint") != args_fingerprint:
            logger.warning(
                "pending_action_business_conflict",
                business_key=business_key,
                existing_token=stored.get("token"),
                existing_status=stored.get("statusName"),
            )
            raise PendingActionConflict(
                "同一业务对象已有参数不同的活跃提案；请先取消旧提案。"
                "若旧提案正在核对或人工复核，请等待处理完成，不能重复提交。"
            )
        if not created:
            logger.info(
                "pending_action_reused",
                business_key=business_key,
                token=stored.get("token"),
                status=stored.get("statusName"),
            )
        await redis_service.save_pending_action(stored["token"], stored)
        if resolved_run_id:
            episode_service.update_run(
                run_id=resolved_run_id,
                scenario="ORDER_AFTERSALES",
                reward_signals={
                    "actionType": action_type,
                    "actionProposed": True,
                    "userConfirmed": False,
                },
            )
            episode_service.record_step(
                "ACTION_PROPOSED",
                run_id=resolved_run_id,
                node_name="pending_action",
                output_data={
                    "actionType": action_type,
                    "created": created,
                    "status": stored.get("statusName"),
                },
            )
        return stored

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

    async def list_for_review(
        self,
        *,
        status: str = "MANUAL_REVIEW",
        token: str | None = None,
        user_id: str | None = None,
        business_key: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        allowed = {
            pending_action_store.EXECUTING,
            pending_action_store.INCONCLUSIVE,
            pending_action_store.MANUAL_REVIEW,
        }
        normalized_status = str(status or pending_action_store.MANUAL_REVIEW).upper()
        if normalized_status not in allowed:
            raise ValueError("status 仅支持 EXECUTING、INCONCLUSIVE 或 MANUAL_REVIEW")
        return await pending_action_store.list_for_review(
            status=normalized_status,
            token=str(token).strip() if token else None,
            user_id=str(user_id).strip() if user_id else None,
            business_key=str(business_key).strip() if business_key else None,
            limit=limit,
        )

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
            if status == self.STATUS_INCONCLUSIVE:
                return (
                    pending.get("actionType"),
                    False,
                    "操作结果仍在核对中，请勿重复操作",
                )
            if status == self.STATUS_MANUAL_REVIEW:
                return (
                    pending.get("actionType"),
                    False,
                    "自动核对已到边界，操作已进入人工复核，请勿重复操作",
                )
            # not claimed → 另一请求正持有 EXECUTING slot，幂等拒绝
            # claimed=True → claim() 已将 status 写为 EXECUTING，正常向下执行
            if not claimed:
                raise ValueError("操作处理中，请勿重复点击")

            self._record_action_signal(
                pending,
                {
                    "actionType": pending.get("actionType"),
                    "actionProposed": True,
                    "userConfirmed": True,
                },
                event_type="ACTION_CONFIRMED_BY_USER",
            )

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
                await self._mark_inconclusive(
                    pending,
                    reason=f"remote_rejected:{type(exc).__name__}:{str(exc)[:300]}",
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
                # 请求可能已经提交，只是响应没回来。显式进入 INCONCLUSIVE，
                # 只查询幂等账本和订单领域状态，避免重复退款/评价。
                logger.warning(
                    "pending_action_execution_outcome_uncertain",
                    token=token,
                    action_type=pending.get("actionType"),
                    error=str(exc),
                )
                await self._mark_inconclusive(
                    pending,
                    reason=f"transport_unknown:{type(exc).__name__}:{str(exc)[:300]}",
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
        delegated_user_id = str(pending.get("userId") or "").strip()
        if not delegated_user_id:
            return None
        try:
            settings = get_settings()
            # The user ID is read from the persisted pending action, never from
            # model-controlled params. Bind it to the authenticated internal
            # request header so Java can enforce body/header consistency.
            with delegated_user_scope(delegated_user_id):
                remote = await java_internal_client.get_agent_action_status(
                    delegated_user_id,
                    str(pending.get("actionType") or ""),
                    token,
                    params,
                    max_attempts=settings.pending_action_reconcile_max_attempts,
                    reconcile_window_seconds=settings.pending_action_reconcile_deadline_seconds,
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
        if remote_status == "MANUAL_REVIEW":
            uncertain = await self._mark_inconclusive(
                pending,
                reason="remote_status=MANUAL_REVIEW",
            )
            if uncertain:
                manual = await pending_action_store.record_inconclusive_attempt(
                    token,
                    max_attempts=1,
                    deadline_seconds=settings.pending_action_reconcile_deadline_seconds,
                    reason="remote_status=MANUAL_REVIEW",
                )
                if manual:
                    await redis_service.save_pending_action(token, manual)
            return (
                pending.get("actionType"),
                False,
                "自动核对已到边界，操作已进入人工复核，请勿重复操作",
            )
        # PROCESSING/INCONCLUSIVE/UNKNOWN must never authorize a second write.
        return None

    async def _mark_inconclusive(self, pending: dict, *, reason: str) -> dict | None:
        settings = get_settings()
        token = str(pending.get("token") or "")
        if not token:
            return None
        uncertain = await pending_action_store.mark_inconclusive(
            token,
            settings.pending_action_reconcile_deadline_seconds,
            reason,
        )
        if uncertain:
            await redis_service.save_pending_action(token, uncertain)
        return uncertain

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
        self._record_action_signal(
            final,
            self._terminal_reward_signals(final),
            event_type="ACTION_TERMINAL",
            status=(
                "OK"
                if final_status == self.STATUS_CONFIRMED
                else "ERROR"
            ),
        )
        return (
            final.get("actionType") or pending.get("actionType"),
            final_status == self.STATUS_CONFIRMED,
            message,
        )

    @staticmethod
    def _terminal_reward_signals(pending: dict) -> dict:
        action_type = str(pending.get("actionType") or "")
        status_name = str(pending.get("statusName") or "UNKNOWN")
        params: dict = {}
        try:
            parsed = json.loads(pending.get("paramsJson") or "{}")
            if isinstance(parsed, dict):
                params = parsed
        except (TypeError, json.JSONDecodeError):
            pass
        signals: dict = {
            "actionType": action_type,
            "actionProposed": True,
            "userConfirmed": True,
            "remoteOutcomeKnown": status_name in {"CONFIRMED", "FAILED"},
            "outcome": status_name,
        }
        if action_type == "CANCEL_ORDER":
            signals.update(
                {
                    "orderStatusBefore": params.get("orderStatusBefore"),
                    "orderStatusAfter": 4 if status_name == "CONFIRMED" else None,
                }
            )
        elif action_type == "CREATE_SUPPORT_CASE":
            signals.update(
                {
                    "caseCreated": status_name == "CONFIRMED",
                    "caseCategory": params.get("category"),
                    "caseStatus": "OPEN" if status_name == "CONFIRMED" else None,
                    "forcedHandoff": bool(params.get("forcedHandoff")),
                }
            )
        return signals

    @staticmethod
    def _record_action_signal(
        pending: dict,
        signals: dict,
        *,
        event_type: str,
        status: str = "OK",
    ) -> None:
        run_id = str(pending.get("runId") or "").strip()
        if not run_id:
            return
        episode_service.update_run(
            run_id=run_id,
            scenario="ORDER_AFTERSALES",
            reward_signals=signals,
        )
        episode_service.record_step(
            event_type,
            run_id=run_id,
            node_name="pending_action",
            status=status,
            output_data=signals,
        )

    async def reconcile_stale_executing(self, stale_seconds: int = 600) -> int:
        """用 Java 幂等账本与领域状态核对不确定动作。

        只有 Java 明确返回 SUCCESS/FAILED 时才写终态。其他结果只增加查询次数，
        达到次数或时间边界后进入 MANUAL_REVIEW；此路径绝不调用写接口。
        """
        await redis_service.ensure_connected()
        settings = get_settings()
        stale = await pending_action_store.list_reconcilable(stale_seconds)
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
                try:
                    delegated_user_id = str(pending.get("userId") or "").strip()
                    if not delegated_user_id:
                        raise ValueError("pending action has no persisted user identity")
                    with delegated_user_scope(delegated_user_id):
                        remote = await java_internal_client.get_agent_action_status(
                            delegated_user_id,
                            str(pending.get("actionType") or ""),
                            str(token),
                            params,
                            max_attempts=settings.pending_action_reconcile_max_attempts,
                            reconcile_window_seconds=(
                                settings.pending_action_reconcile_deadline_seconds
                            ),
                        )
                    remote_status = str(remote.get("status") or "UNKNOWN").upper()
                    result_message = str(remote.get("result_message") or "").strip()
                except Exception as exc:
                    remote_status = "QUERY_ERROR"
                    result_message = f"{type(exc).__name__}:{str(exc)[:300]}"

                if remote_status == "SUCCESS":
                    final = await pending_action_store.complete_reconciled(
                        token,
                        pending_action_store.CONFIRMED,
                        result_message=result_message or "操作已完成",
                    )
                    expected_status = pending_action_store.CONFIRMED
                elif remote_status == "FAILED":
                    final = await pending_action_store.complete_reconciled(
                        token,
                        pending_action_store.FAILED,
                        error_message=result_message or "操作执行失败",
                    )
                    expected_status = pending_action_store.FAILED
                else:
                    max_attempts = (
                        1
                        if remote_status == "MANUAL_REVIEW"
                        else settings.pending_action_reconcile_max_attempts
                    )
                    final = await pending_action_store.record_inconclusive_attempt(
                        token,
                        max_attempts=max_attempts,
                        deadline_seconds=settings.pending_action_reconcile_deadline_seconds,
                        reason=f"remote_status={remote_status};{result_message}"[:512],
                    )
                    if final:
                        await redis_service.save_pending_action(token, final)
                    logger.warning(
                        "pending_action_reconcile_inconclusive",
                        token=token,
                        action_type=pending.get("actionType"),
                        remote_status=remote_status,
                        local_status=final.get("statusName") if final else None,
                        attempts=final.get("reconcileAttempts") if final else None,
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

    async def cancel(self, user_id: str, token: str) -> dict:
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
            return pending
        finally:
            await redis_service.unlock_pending_action(token, owner)


pending_action_service = PendingActionService()
