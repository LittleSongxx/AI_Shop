from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.agent import select_order_candidate
from app.auth.token_service import TokenUserInfo
from app.services.order_selection_store import OrderSelectionExpired


@pytest.mark.asyncio
async def test_select_order_candidate_restores_original_intent_and_completes():
    with patch(
        "app.api.routes.agent.agent_orchestrator.send_selected_order_candidate",
        AsyncMock(
            return_value={
                "messageId": 32,
                "status": 1,
                "intent": "REFUND",
                "selectionId": "sel_1",
            }
        ),
    ) as send:
        response = await select_order_candidate(
            selectionId="sel_1",
            targetType="ORDER_ITEM",
            targetId="SMITEM202608050002",
            user=TokenUserInfo(user_id="u1"),
        )

    assert response.data["messageId"] == 32
    send.assert_awaited_once_with(
        "u1", "sel_1", "ORDER_ITEM", "SMITEM202608050002"
    )


@pytest.mark.asyncio
async def test_select_order_candidate_returns_the_existing_message_on_repeat():
    with patch(
        "app.api.routes.agent.agent_orchestrator.send_selected_order_candidate",
        AsyncMock(return_value={"messageId": 32, "status": 2, "intent": "REFUND"}),
    ) as send:
        response = await select_order_candidate(
            selectionId="sel_1",
            targetType="ORDER_ITEM",
            targetId="SMITEM202608050002",
            user=TokenUserInfo(user_id="u1"),
        )

    assert response.data["messageId"] == 32
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_or_foreign_selection_does_not_leak_candidates():
    with patch(
        "app.api.routes.agent.agent_orchestrator.send_selected_order_candidate",
        AsyncMock(side_effect=OrderSelectionExpired("订单候选已失效，请重新描述要办理的订单")),
    ):
        with pytest.raises(HTTPException) as exc:
            await select_order_candidate(
                selectionId="sel_foreign",
                targetType="ORDER",
                targetId="someone-elses-order",
                user=TokenUserInfo(user_id="u1"),
            )

    assert exc.value.status_code == 410
    assert "订单候选" in str(exc.value.detail)
    assert "someone-elses-order" not in str(exc.value.detail)
