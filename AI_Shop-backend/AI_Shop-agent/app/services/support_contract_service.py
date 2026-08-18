from __future__ import annotations

import json
from typing import Any

from app.domain.support.contracts import (
    ActionProposal,
    ConfirmAction,
    OrderCandidate,
    OrderFact,
    PolicyEvidence,
    SupportTask,
    SupportTaskRequest,
)
from app.observability.telemetry import current_traceparent
from app.services.action_execute_service import action_execute_service
from app.services.agent_service import agent_orchestrator
from app.services.java_internal_client import java_internal_client
from app.services.pending_action_service import pending_action_service


class SupportContractService:
    async def dispatch(self, user_id: str, request: SupportTaskRequest) -> SupportTask:
        selected_reference, selected_order = await self._resolve_requested_order(
            user_id, request
        )
        result = await agent_orchestrator.send_message(
            user_id=user_id,
            message=request.message,
            request_id=request.request_id,
            run_id=request.run_id,
            episode_id=request.episode_id,
            traceparent=request.traceparent,
            selected_order_reference=selected_reference,
        )
        policy_evidence = self._policy_evidence(result)
        token = self._action_token(result)
        if token:
            pending = await pending_action_service.get_by_token(token)
            if pending and pending.get("userId") == user_id:
                return self._task_from_pending(
                    pending,
                    request_id=request.request_id,
                    episode_id=request.episode_id,
                    traceparent=self._traceparent(request.traceparent, result),
                    selected_order=selected_order,
                    policy_evidence=policy_evidence,
                )
        return self._task_from_agent_message(
            user_id,
            request,
            result,
            selected_order=selected_order,
            policy_evidence=policy_evidence,
        )

    async def get_action(self, user_id: str, token: str) -> SupportTask:
        pending = await pending_action_service.load_owned(user_id, token)
        return self._task_from_pending(pending)

    async def confirm(
        self, user_id: str, action: ConfirmAction, auth_token: str
    ) -> SupportTask:
        pending = await pending_action_service.load_owned(
            user_id, action.proposal_token
        )
        if not self._idempotency_matches(action, pending):
            raise ValueError("确认请求的幂等键与提案不一致")

        async def executor(current: dict) -> str:
            return await action_execute_service.execute(current, auth_token)

        try:
            await pending_action_service.confirm(
                user_id, action.proposal_token, executor
            )
        except ValueError:
            refreshed = await pending_action_service.get_by_token(
                action.proposal_token
            )
            if refreshed and str(refreshed.get("statusName") or "").upper() in {
                "EXECUTING",
                "INCONCLUSIVE",
                "MANUAL_REVIEW",
                "CONFIRMED",
                "FAILED",
                "CANCELLED",
                "EXPIRED",
            }:
                return self._task_from_pending(
                    refreshed,
                    request_id=action.request_id,
                    episode_id=action.episode_id,
                    traceparent=action.traceparent,
                )
            raise
        refreshed = await pending_action_service.get_by_token(action.proposal_token)
        if not refreshed:
            raise ValueError("提案状态读取失败，请通过工单查询")
        return self._task_from_pending(
            refreshed,
            request_id=action.request_id,
            episode_id=action.episode_id,
            traceparent=action.traceparent,
        )

    @staticmethod
    def _idempotency_matches(action: ConfirmAction, pending: dict) -> bool:
        key = action.idempotency_key
        return key in {
            str(pending.get("token") or ""),
            str(pending.get("businessKey") or ""),
        }

    @staticmethod
    def _action_token(result: dict) -> str | None:
        direct = str(result.get("actionToken") or result.get("token") or "").strip()
        if direct:
            return direct
        for field in ("assistantMessage", "assistantCards", "bizData"):
            value = SupportContractService._json_value(result.get(field))
            candidates = value if isinstance(value, list) else [value]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "").upper() == "ACTION_CONFIRM":
                    token = str(item.get("token") or "").strip()
                    if token:
                        return token
                if item.get("token"):
                    return str(item["token"]).strip()
        return None

    def _task_from_agent_message(
        self,
        user_id: str,
        request: SupportTaskRequest,
        result: dict,
        *,
        selected_order: OrderFact | None,
        policy_evidence: list[PolicyEvidence],
    ) -> SupportTask:
        delivery_state = str(result.get("deliveryState") or "").upper()
        biz_type = str(result.get("bizType") or "").lower()
        assistant = str(
            result.get("assistantMessage") or result.get("content") or ""
        ).strip()
        candidates = self._order_candidates(result)
        state = (
            "CLARIFICATION_REQUIRED"
            if candidates and self._is_order_selection(result, biz_type)
            else "ANSWERED"
        )
        lifecycle = (
            "QUEUED"
            if delivery_state in {"QUEUED", "PENDING_RECOVERY"}
            else "IN_PROGRESS"
            if delivery_state in {"PROCESSING", "RUNNING"}
            else "FINAL"
        )
        minimal_refs = self._minimal_source_refs(result)
        return SupportTask(
            taskId=str(result.get("messageId") or f"task_{request.request_id}"),
            requestId=request.request_id,
            runId=str(
                result.get("runId")
                or request.run_id
                or f"run_{request.request_id}"
            )[:64],
            episodeId=(
                request.episode_id
                or str(result.get("episodeId") or result.get("runId") or "")[:64]
                or None
            ),
            userId=user_id,
            state=state,
            lifecycle=lifecycle,
            orderCandidates=candidates,
            selectedOrder=selected_order,
            policyEvidence=policy_evidence,
            idempotencyKey=request.idempotency_key or request.request_id,
            message=assistant
            or (
                "任务已进入 Agent 工作流，请通过消息流读取最终结果。"
                if delivery_state in {"QUEUED", "PENDING_RECOVERY"}
                else None
            ),
            evidence={
                "deliveryState": delivery_state,
                "bizType": biz_type or None,
                "messageId": result.get("messageId"),
                "orderOwnershipVerified": bool(
                    selected_order and selected_order.ownership_verified
                ),
                "sourceRefs": minimal_refs,
                "policyReleases": sorted(
                    {item.release for item in policy_evidence}
                ),
            },
            traceparent=self._traceparent(request.traceparent, result),
        )

    @staticmethod
    def _task_from_pending(
        pending: dict,
        *,
        request_id: str | None = None,
        episode_id: str | None = None,
        traceparent: str | None = None,
        selected_order: OrderFact | None = None,
        policy_evidence: list[PolicyEvidence] | None = None,
    ) -> SupportTask:
        status_name = str(pending.get("statusName") or "PENDING").upper()
        params = SupportContractService._json_value(pending.get("paramsJson"))
        if not isinstance(params, dict):
            params = {}
        policy_evidence = policy_evidence or SupportContractService._policy_evidence(
            params
        )
        selected_order = selected_order or SupportContractService._order_fact_from_pending(
            params
        )
        proposal_status = {
            "PENDING": "CONFIRM_REQUIRED",
            "EXECUTING": "EXECUTING",
            "INCONCLUSIVE": "INCONCLUSIVE",
            "MANUAL_REVIEW": "MANUAL_REVIEW",
            "CONFIRMED": "SUCCEEDED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
            "EXPIRED": "EXPIRED",
        }.get(status_name, "PROPOSED")
        action_token = str(pending.get("token") or "")
        proposal = ActionProposal(
            proposalToken=action_token,
            idempotencyKey=action_token,
            actionType=str(pending.get("actionType") or "CREATE_SUPPORT_CASE"),
            summary=str(pending.get("summary") or "请确认该售后操作"),
            orderId=params.get("orderId"),
            orderItemId=params.get("orderItemId"),
            policyEvidence=policy_evidence,
            requiresConfirmation=status_name == "PENDING",
            expiresAt=pending.get("expiresAt"),
            status=proposal_status,
        )
        state = {
            "PENDING": "CONFIRM_REQUIRED",
            "EXECUTING": "PROPOSED",
            "INCONCLUSIVE": "INCONCLUSIVE",
            "MANUAL_REVIEW": "MANUAL_REVIEW",
            "CONFIRMED": "SUCCEEDED",
        }.get(status_name, "ANSWERED")
        reason = (
            str(
                pending.get("reviewReason")
                or pending.get("errorMessage")
                or "执行结果未知，禁止重复写入"
            )[:500]
            if state in {"INCONCLUSIVE", "MANUAL_REVIEW"}
            else None
        )
        lifecycle = (
            "WAITING_USER"
            if status_name == "PENDING"
            else "IN_PROGRESS"
            if status_name == "EXECUTING"
            else "FINAL"
        )
        return SupportTask(
            taskId=action_token or "task_unknown",
            requestId=str(request_id or action_token or "req_unknown")[:128],
            runId=str(
                pending.get("runId") or f"run_{action_token or 'unknown'}"
            )[:64],
            episodeId=str(episode_id or pending.get("runId") or "")[:64] or None,
            userId=str(pending.get("userId") or ""),
            state=state,
            lifecycle=lifecycle,
            selectedOrder=selected_order,
            policyEvidence=policy_evidence,
            actionProposal=proposal,
            idempotencyKey=action_token,
            message=str(
                pending.get("resultMessage")
                or pending.get("errorMessage")
                or pending.get("summary")
                or ""
            )[:2_000]
            or None,
            manualReviewReason=reason,
            evidence={
                "pendingStatus": status_name,
                "reconcileAttempts": pending.get("reconcileAttempts", 0),
                "reconcileDeadline": pending.get("reconcileDeadline"),
                "orderOwnershipVerified": bool(
                    selected_order and selected_order.ownership_verified
                ),
                "policyReleases": sorted(
                    {item.release for item in policy_evidence}
                ),
            },
            traceparent=traceparent or current_traceparent(),
        )

    async def _resolve_requested_order(
        self,
        user_id: str,
        request: SupportTaskRequest,
    ) -> tuple[dict | None, OrderFact | None]:
        if not request.order_id and not request.order_item_id:
            return None, None
        try:
            item = None
            order_id = str(request.order_id or "").strip()
            if request.order_item_id:
                item = await java_internal_client.get_order_item(
                    request.order_item_id
                )
                if not item:
                    raise ValueError("指定订单不存在或不属于当前用户")
                item_order_id = str(
                    item.get("order_id") or item.get("orderId") or ""
                ).strip()
                if order_id and item_order_id and order_id != item_order_id:
                    raise ValueError("订单与订单项不匹配")
                order_id = order_id or item_order_id
            if not order_id:
                raise ValueError("无法从订单项解析所属订单")
            orders = await java_internal_client.list_orders(
                user_id=user_id,
                order_id=order_id,
                limit=2,
            )
            order = next(
                (
                    row
                    for row in orders
                    if str(row.get("order_id") or row.get("orderId") or "")
                    == order_id
                ),
                None,
            )
            if not order:
                raise ValueError("指定订单不存在或不属于当前用户")
            items = list(
                order.get("items") or order.get("order_item_list") or []
            )
            if not items:
                items = await java_internal_client.list_order_items(order_id)
            if item is not None:
                item_id = str(
                    item.get("order_item_id") or item.get("orderItemId") or ""
                )
                if item_id != request.order_item_id:
                    raise ValueError("指定订单项无效")
                if not any(
                    str(
                        row.get("order_item_id") or row.get("orderItemId") or ""
                    )
                    == item_id
                    for row in items
                ):
                    items.append(item)
            fact = self._order_fact(order, items)
            target_item = item if request.order_item_id else None
            reference = self._order_reference(order, target_item)
            return reference, fact
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "Java 订单权威事实暂不可用，请稍后重试或转人工"
            ) from exc

    @staticmethod
    def _order_reference(order: dict, item: dict | None) -> dict:
        order_id = str(order.get("order_id") or order.get("orderId") or "")
        if item:
            item_id = str(
                item.get("order_item_id") or item.get("orderItemId") or ""
            )
            return {
                "targetType": "ORDER_ITEM",
                "targetId": item_id,
                "orderId": order_id,
                "orderItemId": item_id,
                "productId": SupportContractService._first_present(
                    item, "product_id", "productId"
                ),
                "productName": SupportContractService._first_present(
                    item, "product_name", "productName"
                ),
                "propertyInfo": SupportContractService._first_present(
                    item, "property_info", "propertyInfo"
                ),
                "amount": SupportContractService._first_present(
                    item, "item_amount", "itemAmount"
                ),
                "orderStatus": SupportContractService._first_present(
                    order, "order_status", "orderStatus"
                ),
                "orderStatusName": SupportContractService._first_present(
                    order, "order_status_name", "orderStatusName"
                ),
                "orderTime": SupportContractService._first_present(
                    order, "create_time", "createdAt"
                ),
            }
        return {
            "targetType": "ORDER",
            "targetId": order_id,
            "orderId": order_id,
            "amount": SupportContractService._first_present(
                order, "amount", "orderTotal"
            ),
            "orderStatus": SupportContractService._first_present(
                order, "order_status", "orderStatus"
            ),
            "orderStatusName": SupportContractService._first_present(
                order, "order_status_name", "orderStatusName"
            ),
            "orderTime": SupportContractService._first_present(
                order, "create_time", "createdAt"
            ),
        }

    @staticmethod
    def _order_fact(order: dict, items: list[dict]) -> OrderFact:
        order_id = str(order.get("order_id") or order.get("orderId") or "")
        amount = SupportContractService._float_or_none(
            SupportContractService._first_present(order, "amount", "orderTotal")
        )
        status = order.get("order_status")
        if status is None:
            status = order.get("orderStatus")
        return OrderFact(
            orderId=order_id,
            orderStatus=str(status) if status is not None else None,
            orderTotal=amount,
            items=[
                SupportContractService._public_order_item(item)
                for item in items[:50]
                if isinstance(item, dict)
            ],
            source="JAVA_GATEWAY",
            ownershipVerified=True,
        )

    @staticmethod
    def _order_fact_from_pending(params: dict) -> OrderFact | None:
        order_id = str(params.get("orderId") or "").strip()
        if not order_id:
            return None
        status = params.get("orderStatusBefore")
        return OrderFact(
            orderId=order_id,
            orderStatus=str(status) if status is not None else None,
            orderTotal=SupportContractService._float_or_none(
                params.get("orderAmount")
            ),
            items=[
                SupportContractService._public_order_item(item)
                for item in list(params.get("orderItems") or [])[:50]
                if isinstance(item, dict)
            ],
            source="JAVA_GATEWAY",
            ownershipVerified=True,
        )

    @staticmethod
    def _public_order_item(item: dict) -> dict[str, Any]:
        fields = {
            "orderItemId": SupportContractService._first_present(
                item, "order_item_id", "orderItemId"
            ),
            "productId": SupportContractService._first_present(
                item, "product_id", "productId"
            ),
            "productName": SupportContractService._first_present(
                item, "product_name", "productName"
            ),
            "skuKey": SupportContractService._first_present(
                item, "sku_key", "skuKey"
            ),
            "propertyInfo": SupportContractService._first_present(
                item, "property_info", "propertyInfo"
            ),
            "quantity": SupportContractService._first_present(
                item, "buy_count", "buyCount", "quantity"
            ),
            "amount": SupportContractService._first_present(
                item, "item_amount", "itemAmount"
            ),
            "status": SupportContractService._first_present(
                item, "order_item_status", "orderItemStatus"
            ),
        }
        return {key: value for key, value in fields.items() if value is not None}

    @staticmethod
    def _first_present(values: dict, *keys: str) -> Any:
        for key in keys:
            if key in values and values[key] is not None:
                return values[key]
        return None

    @staticmethod
    def _order_candidates(result: dict) -> list[OrderCandidate]:
        raw_candidates: list[Any] = []
        direct = result.get("orderCandidates") or result.get("orders")
        if isinstance(direct, list):
            raw_candidates.extend(direct)
        for field in ("assistantCards", "bizData"):
            parsed = SupportContractService._json_value(result.get(field))
            if isinstance(parsed, dict):
                values = parsed.get("candidates") or parsed.get("orders")
                if isinstance(values, list):
                    raw_candidates.extend(values)
            elif isinstance(parsed, list):
                raw_candidates.extend(parsed)
        candidates: list[OrderCandidate] = []
        seen: set[str] = set()
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            order_id = item.get("orderId") or item.get("order_id")
            if not order_id or str(order_id) in seen:
                continue
            seen.add(str(order_id))
            candidates.append(
                OrderCandidate(
                    orderId=str(order_id),
                    orderNo=item.get("orderNo") or item.get("order_no"),
                    status=item.get("orderStatus") or item.get("order_status"),
                    createdAt=item.get("orderTime") or item.get("createdAt"),
                    matchReason=item.get("matchReason") or "Java 订单候选",
                    ownershipVerified=bool(
                        item.get("ownershipVerified")
                        or item.get("targetType") in {"ORDER", "ORDER_ITEM"}
                    ),
                )
            )
            if len(candidates) >= 20:
                break
        return candidates

    @staticmethod
    def _is_order_selection(result: dict, biz_type: str) -> bool:
        if "order_selection" in biz_type:
            return True
        parsed = SupportContractService._json_value(result.get("assistantCards"))
        return isinstance(parsed, dict) and str(parsed.get("type") or "").upper() == "ORDER_SELECTION"

    @staticmethod
    def _policy_evidence(payload: dict) -> list[PolicyEvidence]:
        raw_refs: list[Any] = []
        for key in ("policyEvidence", "sourceRefs", "source_refs"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_refs.extend(value)
        evidence = payload.get("evidence")
        if isinstance(evidence, dict):
            for key in ("policyEvidence", "sourceRefs"):
                value = evidence.get(key)
                if isinstance(value, list):
                    raw_refs.extend(value)
        result: list[PolicyEvidence] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_refs:
            if not isinstance(item, dict):
                continue
            release = str(
                item.get("release")
                or item.get("knowledgeVersion")
                or item.get("version")
                or ""
            ).strip()
            document_id = str(
                item.get("documentId")
                or item.get("policyId")
                or item.get("id")
                or item.get("chunkId")
                or item.get("questionId")
                or ""
            ).strip()
            quote = str(
                item.get("quote")
                or item.get("snippet")
                or item.get("source")
                or ""
            ).strip()
            if not release or not document_id or not quote:
                continue
            identity = (release, document_id)
            if identity in seen:
                continue
            seen.add(identity)
            score = SupportContractService._float_or_none(item.get("score"))
            result.append(
                PolicyEvidence(
                    release=release[:128],
                    documentId=document_id[:128],
                    chunkId=str(item.get("chunkId") or "")[:128] or None,
                    quote=quote[:1_000],
                    supportsClaim=item.get("supportsClaim") is not False,
                    score=(max(0.0, min(score, 1.0)) if score is not None else None),
                )
            )
            if len(result) >= 20:
                break
        return result

    @staticmethod
    def _minimal_source_refs(result: dict) -> list[dict[str, Any]]:
        refs = result.get("sourceRefs") or result.get("source_refs") or []
        if not isinstance(refs, list):
            return []
        allowlist = (
            "type",
            "id",
            "documentId",
            "chunkId",
            "questionId",
            "policyId",
            "knowledgeVersion",
            "version",
            "score",
        )
        return [
            {
                key: item.get(key)
                for key in allowlist
                if item.get(key) is not None
            }
            for item in refs[:20]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _traceparent(explicit: str | None, result: dict) -> str | None:
        return (
            str(explicit or result.get("traceparent") or current_traceparent() or "").strip()
            or None
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


support_contract_service = SupportContractService()
