import json
from unittest.mock import AsyncMock

import pytest

from app.domain.support.contracts import ConfirmAction, SupportTaskRequest
from app.services.support_contract_service import SupportContractService


def _pending(status: str) -> dict:
    return {
        "token": "act_1",
        "userId": "u1",
        "actionType": "REFUND",
        "runId": "run-1",
        "paramsJson": json.dumps(
            {
                "orderId": "order-1",
                "orderItemId": "item-1",
                "orderItems": [
                    {
                        "orderItemId": "item-1",
                        "productId": "p1",
                        "productName": "耳机",
                    }
                ],
            }
        ),
        "businessKey": "u1:REFUND:item-1",
        "summary": "申请退款",
        "statusName": status,
        "status": {
            "PENDING": 0,
            "CONFIRMED": 1,
            "EXECUTING": 3,
            "INCONCLUSIVE": 6,
            "MANUAL_REVIEW": 7,
        }.get(status, 4),
        "reviewReason": "远端结果仍未知" if status != "PENDING" else None,
        "reconcileAttempts": 1,
    }


@pytest.mark.asyncio
async def test_dispatch_passes_verified_order_and_maps_policy_evidence(monkeypatch):
    service = SupportContractService()
    list_orders = AsyncMock(
        return_value=[
            {
                "order_id": "order-1",
                "order_status": 2,
                "amount": 399,
                "items": [
                    {
                        "order_item_id": "item-1",
                        "product_id": "p1",
                        "product_name": "耳机",
                        "buy_count": 1,
                    }
                ],
            }
        ]
    )
    send_message = AsyncMock(
        return_value={
            "messageId": 7,
            "runId": "run-1",
            "episodeId": "episode-1",
            "deliveryState": "COMPLETED",
            "bizType": "after_sales",
            "assistantMessage": "根据已发布政策，该订单可继续申请退款。",
            "sourceRefs": [
                {
                    "knowledgeVersion": "support-policy-2026-08",
                    "documentId": "refund-policy",
                    "chunkId": "refund-policy-1",
                    "snippet": "退款申请需先核验订单归属和当前状态。",
                    "score": 0.92,
                }
            ],
        }
    )
    monkeypatch.setattr(
        "app.services.support_contract_service.java_internal_client.list_orders",
        list_orders,
    )
    monkeypatch.setattr(
        "app.services.support_contract_service.agent_orchestrator.send_message",
        send_message,
    )

    task = await service.dispatch(
        "u1",
        SupportTaskRequest(
            requestId="req-1",
            runId="run-1",
            episodeId="episode-1",
            traceparent="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            message="帮我申请退款",
            orderId="order-1",
        ),
    )

    selected = send_message.await_args.kwargs["selected_order_reference"]
    assert selected["targetType"] == "ORDER"
    assert selected["orderId"] == "order-1"
    assert task.selected_order is not None
    assert task.selected_order.ownership_verified is True
    assert task.policy_evidence[0].release == "support-policy-2026-08"
    assert task.evidence["sourceRefs"] == [
        {
            "documentId": "refund-policy",
            "chunkId": "refund-policy-1",
            "knowledgeVersion": "support-policy-2026-08",
            "score": 0.92,
        }
    ]


@pytest.mark.parametrize(
    ("status", "state", "lifecycle", "proposal_status"),
    [
        ("PENDING", "CONFIRM_REQUIRED", "WAITING_USER", "CONFIRM_REQUIRED"),
        ("EXECUTING", "PROPOSED", "IN_PROGRESS", "EXECUTING"),
        ("INCONCLUSIVE", "INCONCLUSIVE", "FINAL", "INCONCLUSIVE"),
        ("MANUAL_REVIEW", "MANUAL_REVIEW", "FINAL", "MANUAL_REVIEW"),
        ("CONFIRMED", "SUCCEEDED", "FINAL", "SUCCEEDED"),
    ],
)
def test_pending_statuses_map_to_explicit_support_states(
    status, state, lifecycle, proposal_status
):
    task = SupportContractService._task_from_pending(_pending(status))

    assert task.state == state
    assert task.lifecycle == lifecycle
    assert task.action_proposal is not None
    assert task.action_proposal.status == proposal_status
    assert task.idempotency_key == "act_1"
    assert task.selected_order is not None
    if status in {"INCONCLUSIVE", "MANUAL_REVIEW"}:
        assert task.manual_review_reason == "远端结果仍未知"


def test_queued_and_processing_agent_messages_keep_nonfinal_lifecycle():
    service = SupportContractService()
    request = SupportTaskRequest(requestId="req-1", message="查退款进度")

    queued = service._task_from_agent_message(
        "u1",
        request,
        {"messageId": 1, "runId": "run-1", "deliveryState": "QUEUED"},
        selected_order=None,
        policy_evidence=[],
    )
    processing = service._task_from_agent_message(
        "u1",
        request,
        {"messageId": 1, "runId": "run-1", "deliveryState": "PROCESSING"},
        selected_order=None,
        policy_evidence=[],
    )

    assert queued.lifecycle == "QUEUED"
    assert processing.lifecycle == "IN_PROGRESS"


@pytest.mark.asyncio
async def test_duplicate_confirmation_returns_current_execution_state(monkeypatch):
    service = SupportContractService()
    monkeypatch.setattr(
        "app.services.support_contract_service.pending_action_service.load_owned",
        AsyncMock(return_value=_pending("PENDING")),
    )
    monkeypatch.setattr(
        "app.services.support_contract_service.pending_action_service.confirm",
        AsyncMock(side_effect=ValueError("操作处理中，请勿重复点击")),
    )
    monkeypatch.setattr(
        "app.services.support_contract_service.pending_action_service.get_by_token",
        AsyncMock(return_value=_pending("EXECUTING")),
    )

    task = await service.confirm(
        "u1",
        ConfirmAction(
            proposalToken="act_1",
            idempotencyKey="act_1",
            requestId="req-confirm-1",
        ),
        "user-token",
    )

    assert task.state == "PROPOSED"
    assert task.lifecycle == "IN_PROGRESS"
    assert task.action_proposal is not None
    assert task.action_proposal.status == "EXECUTING"
