from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.recommendation.contracts import (
    AuthoritativeOffer,
    RecommendationCard,
    RecommendationEvent,
    RecommendationRequest,
    RecommendationResponse,
)
from app.domain.support.contracts import (
    ActionProposal,
    ConfirmAction,
    SupportTask,
    SupportTaskRequest,
)


def test_recommendation_request_supports_text_image_and_mixed_modes():
    text = RecommendationRequest(mode="TEXT", query="轻薄办公本")
    image = RecommendationRequest(mode="IMAGE", imageAssetId="img_1")
    mixed = RecommendationRequest(mode="MIXED", query="黑色", imageAssetId="img_1")

    assert text.idempotency_key == text.request_id
    assert image.image_asset_id == "img_1"
    assert mixed.mode == "MIXED"


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "TEXT"},
        {"mode": "IMAGE"},
        {"mode": "MIXED"},
        {"mode": "TEXT", "query": "a", "selectedSubjectId": "s1"},
    ],
)
def test_recommendation_request_rejects_incomplete_input(payload):
    with pytest.raises(ValidationError):
        RecommendationRequest.model_validate(payload)


def test_recommendation_response_requires_authoritative_offer_for_completed_item():
    offer = AuthoritativeOffer(
        productId="p1",
        price=99,
        stock=3,
        inStock=True,
        purchasable=True,
        checkedAt=datetime.now(timezone.utc),
    )
    card = RecommendationCard(productId="p1", position=1, offer=offer)
    response = RecommendationResponse(
        requestId="req-1",
        runId="run-1",
        mode="TEXT",
        status="COMPLETED",
        items=[card],
    )

    assert response.model_dump(by_alias=True)["items"][0]["offer"]["purchasable"] is True


def test_recommendation_event_carries_full_attribution_identity():
    event = RecommendationEvent(
        eventType="ADD_TO_CART",
        idempotencyKey="idem-1",
        requestId="req-1",
        runId="run-1",
        productId="p1",
        position=2,
        modelVersion="shopping-user-utility-v2",
    )

    assert event.model_dump(by_alias=True)["eventType"] == "ADD_TO_CART"
    assert event.position == 2


def test_support_request_and_confirmation_are_versioned():
    request = SupportTaskRequest(message="帮我查一下退款进度")
    action = ActionProposal(
        proposalToken="act_1",
        idempotencyKey="idem_1",
        actionType="REFUND",
        orderItemId="item_1",
        summary="为订单项申请退款",
    )
    confirmation = ConfirmAction(
        proposalToken=action.proposal_token,
        idempotencyKey=action.idempotency_key,
        requestId=request.request_id,
    )

    assert confirmation.proposal_token == "act_1"
    assert request.model_dump(by_alias=True)["idempotencyKey"] == request.request_id


def test_support_terminal_state_requires_proposal_or_reason():
    with pytest.raises(ValidationError):
        SupportTask(
            taskId="task-1",
            requestId="req-1",
            runId="run-1",
            userId="u1",
            state="CONFIRM_REQUIRED",
            idempotencyKey="idem-1",
        )
    with pytest.raises(ValidationError):
        SupportTask(
            taskId="task-1",
            requestId="req-1",
            runId="run-1",
            userId="u1",
            state="MANUAL_REVIEW",
            idempotencyKey="idem-1",
        )
