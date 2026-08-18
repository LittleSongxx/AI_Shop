from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import TokenUserInfo, get_request_token, require_login
from app.domain.recommendation.contracts import (
    RecommendationEvent,
    RecommendationRequest,
    RecommendationResponse,
)
from app.domain.support.contracts import ConfirmAction, SupportTask, SupportTaskRequest
from app.exceptions import PendingActionExpired
from app.models.response import ResponseVO, success
from app.services.recommendation_attribution_service import recommendation_attribution_service
from app.services.recommendation_event_store import RecommendationEventConflict
from app.services.recommendation_facade import recommendation_facade
from app.services.support_contract_service import support_contract_service

router = APIRouter(prefix="/agent/v1", tags=["agent-v1"])


@router.post("/recommendations", response_model=RecommendationResponse)
async def recommend(
    body: RecommendationRequest,
    user: TokenUserInfo = Depends(require_login),
) -> RecommendationResponse:
    try:
        return await recommendation_facade.recommend(user.user_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/recommendations/events/click")
async def recommendation_click(
    body: RecommendationEvent,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    if body.event_type != "CLICK":
        raise HTTPException(
            status_code=403,
            detail="客户端只能上报 CLICK，交易事件由 Java 领域服务产生",
        )
    attribution = await recommendation_attribution_service.record_click(
        user.user_id,
        body.request_id,
        body.product_id,
        body.position,
    )
    if attribution is None:
        raise HTTPException(status_code=409, detail="点击归因无效或已过期")
    return success(
        {
            "eventId": body.event_id,
            "idempotencyKey": body.idempotency_key,
            "attribution": attribution,
        }
    )


@router.post("/recommendations/events")
async def recommendation_event(
    body: RecommendationEvent,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    if body.event_type in {"PAYMENT", "REPEAT_PURCHASE"}:
        raise HTTPException(
            status_code=403,
            detail="支付和复购必须由 Java 领域服务通过内部事件入口上报",
        )
    if body.event_type not in {"IMPRESSION", "CLICK", "ADD_TO_CART"}:
        raise HTTPException(status_code=403, detail="不允许由客户端上报该推荐事件")
    try:
        canonical = await recommendation_attribution_service.record_event(
            user.user_id, body
        )
    except RecommendationEventConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if canonical is None:
        raise HTTPException(status_code=409, detail="推荐事件无有效曝光归因或已冲突")
    return success(canonical)


@router.post("/support/tasks", response_model=SupportTask)
async def create_support_task(
    body: SupportTaskRequest,
    user: TokenUserInfo = Depends(require_login),
) -> SupportTask:
    try:
        return await support_contract_service.dispatch(user.user_id, body)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/support/actions/{action_token}", response_model=SupportTask)
async def get_support_action(
    action_token: str,
    user: TokenUserInfo = Depends(require_login),
) -> SupportTask:
    try:
        return await support_contract_service.get_action(user.user_id, action_token)
    except PendingActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/support/actions/confirm", response_model=SupportTask)
async def confirm_support_action(
    body: ConfirmAction,
    request: Request,
    user: TokenUserInfo = Depends(require_login),
) -> SupportTask:
    token = get_request_token(request) or user.token or ""
    try:
        return await support_contract_service.confirm(user.user_id, body, token)
    except PendingActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
