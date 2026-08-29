import json
import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import TokenUserInfo, get_request_token, require_login
from app.auth.admin_assertion import AdminAssertion, require_admin_assertion
from app.config.settings import get_settings
from app.constants import IMPRESSION_LOG_MAX_PRODUCTS
from app.exceptions import PendingActionExpired
from app.harness.metrics.runtime_sensors import (
    ORDER_SELECTION_TOTAL,
    VISUAL_SELECTION_TOTAL,
)
from app.models.response import ResponseVO, error, success
from app.models.shopping_profile import (
    ShoppingPersonalizationRequest,
    ShoppingProfileClearRequest,
    ShoppingProfileSignalRequest,
    ShoppingProfileUpdateRequest,
)
from app.observability.telemetry import get_tracer
from app.services.action_execute_service import action_execute_service
from app.services.agent_service import agent_orchestrator
from app.services.analytics_clarification_service import analytics_clarification_service
from app.services.analytics_export_service import analytics_export_service
from app.services.analytics_result_service import AnalyticsResultError, analytics_result_service
from app.services.badcase_service import badcase_service
from app.services.data_analyst_service import analytics_no_query_contract, data_analyst_service
from app.services.episode_query_service import episode_query_service
from app.services.episode_review_service import episode_review_service
from app.services.evaluation_fault_service import (
    FaultCapabilityRejected,
    consume_api_fault_capability,
)
from app.services.inventory_ops_service import inventory_ops_service
from app.services.message_service import agent_message_service
from app.services.order_selection_store import (
    OrderSelectionConflict,
    OrderSelectionExpired,
)
from app.services.pending_action_service import pending_action_service
from app.services.pilot_batch_service import pilot_batch_service
from app.services.pilot_metrics_service import pilot_metrics_service
from app.services.rate_limit_service import rate_limit_service
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)
from app.services.redis_service import redis_service
from app.services.regression_replay_service import regression_replay_service
from app.services.request_idempotency_service import (
    AgentRequestIdempotencyConflict,
    IdempotencyReservation,
    agent_request_idempotency_service,
)
from app.services.shopping_profile_service import (
    ProfileRevisionConflict,
    shopping_profile_service,
)
from app.services.support_case_service import support_case_service
from app.services.support_service import support_service
from app.services.visual_selection_store import (
    VisualSelectionConflict,
    VisualSelectionExpired,
)

router = APIRouter(prefix="/agent", tags=["agent"])
tracer = get_tracer()
logger = structlog.get_logger()

# 限流统一走 rate_limit_service（Redis 固定窗口，跨进程共享配额）。
# 这里曾经叠了一层 slowapi @limiter.limit：它默认存在进程内存里，多 uvicorn worker
# 时每个进程各算一份配额，"1/second" 实际是 "N/second"；而且下面四个接口本来就各有
# 一次等价的 Redis 校验，留着它只是让人误以为已经限流了。


def _form_bool(value: str | bool | None) -> bool:

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def _form_string_list(value: str | None) -> list[str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",")]
    if not isinstance(parsed, list):
        raise ValueError("comparisonProductIds 必须是数组")
    return [str(item).strip() for item in parsed if str(item or "").strip()]


def _require_internal_token(
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
) -> str:

    expected = get_settings().internal_token
    if not x_internal_token or x_internal_token != expected:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="invalid internal token")
    return x_internal_token


async def _require_admin(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> AdminAssertion:
    return await require_admin_assertion(request)


async def _read_admin_body(request: Request) -> dict:

    ct = (request.headers.get("content-type") or "").lower()
    if "application/json" in ct:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {k: form.get(k) for k in form.keys()}


def _as_int(value, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("时间参数必须是 ISO-8601 格式") from exc


def _canonical_response(response: ResponseVO) -> ResponseVO:
    payload = json.dumps(
        response.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return ResponseVO.model_validate_json(payload)


def _analytics_request_id(request: Request) -> str:
    return str(request.headers.get("X-Request-ID") or "").strip() or uuid.uuid4().hex


def _analytics_response(
    result: dict,
    *,
    request_id: str,
) -> JSONResponse:
    payload = dict(result)
    http_status = int(payload.pop("_httpStatus", 200) or 200)
    payload.setdefault("requestId", request_id)
    if http_status == 200:
        response = success(payload)
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))
    response = error(http_status, str(payload.get("answer") or payload.get("status") or "请求失败"))
    response = response.model_copy(update={"data": payload})
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))


def _analytics_exception_response(
    exc: AnalyticsResultError,
    *,
    request_id: str,
) -> JSONResponse:
    denied = exc.http_status == 403
    payload = {
        "outcome": "DENY" if denied else None,
        "completion": "NOT_APPLICABLE" if denied else "FAILED",
        "status": exc.code,
        "reasonCode": exc.code,
        "answer": str(exc),
        "requestId": request_id,
    }
    if denied:
        payload.update(
            analytics_no_query_contract(
                "HTTP 403",
                "结构化 reasonCode",
                "关联 run/request ID",
            )
        )
    response = error(exc.http_status, str(exc)).model_copy(update={"data": payload})
    return JSONResponse(
        status_code=exc.http_status,
        content=response.model_dump(mode="json"),
    )


def _analytics_permission_denied(
    request: Request,
    permission: str,
    reason_code: str,
) -> JSONResponse:
    return _analytics_exception_response(
        AnalyticsResultError(
            reason_code,
            403,
            f"缺少 {permission} 权限",
        ),
        request_id=_analytics_request_id(request),
    )


def _inconclusive_response(
    reservation: IdempotencyReservation,
    message_id: int | None = None,
) -> ResponseVO:
    return _canonical_response(
        error(503, "请求结果未知，请人工核验").model_copy(
            update={
                "data": {
                    "terminalState": "INCONCLUSIVE",
                    "deliveryState": "MANUAL_REVIEW",
                    "manualReview": True,
                    "runId": reservation.run_id,
                    "messageId": message_id,
                }
            }
        )
    )


async def _record_idempotent_failure(
    reservation: IdempotencyReservation, response: ResponseVO
) -> bool:
    """Persist a replayable failure, reporting false when its ledger is unknown."""
    try:
        await agent_request_idempotency_service.fail(reservation, response.model_dump(mode="json"))
    except Exception as exc:  # pragma: no cover - exercised with a live DB fault
        logger.error(
            "agent_idempotency_failure_ledger_unknown",
            user_id=reservation.user_id,
            run_id=reservation.run_id,
            error=type(exc).__name__,
        )
        return False
    return True


async def _record_idempotent_inconclusive(
    reservation: IdempotencyReservation,
    response: ResponseVO,
    message_id: int | None,
) -> bool:
    try:
        await agent_request_idempotency_service.inconclusive(
            reservation,
            response.model_dump(mode="json"),
            message_id=message_id,
        )
    except Exception as exc:  # pragma: no cover - exercised with a live DB fault
        logger.error(
            "agent_idempotency_inconclusive_ledger_unknown",
            user_id=reservation.user_id,
            run_id=reservation.run_id,
            message_id=message_id,
            error=type(exc).__name__,
        )
        return False
    return True


async def _idempotent_failure_or_unknown(
    reservation: IdempotencyReservation,
    failure: ResponseVO,
    *,
    known_message_id: int | None = None,
) -> ResponseVO:
    """Fail only when the deterministic run is authoritatively absent."""
    lookup_unknown = False
    message_id = known_message_id
    if message_id is None:
        try:
            message = await agent_message_service.get_by_run_id(
                reservation.user_id, reservation.run_id
            )
        except Exception as exc:
            lookup_unknown = True
            logger.error(
                "agent_idempotency_business_state_unknown",
                user_id=reservation.user_id,
                run_id=reservation.run_id,
                error=type(exc).__name__,
            )
        else:
            if message is not None:
                message_id = int(message["messageId"])

    if message_id is not None or lookup_unknown:
        response = _inconclusive_response(reservation, message_id)
        await _record_idempotent_inconclusive(reservation, response, message_id)
        return response

    canonical_failure = _canonical_response(failure)
    if await _record_idempotent_failure(reservation, canonical_failure):
        return canonical_failure

    response = _inconclusive_response(reservation)
    await _record_idempotent_inconclusive(reservation, response, None)
    return response


@router.post("/loadHistoryMessage")
async def load_history_message(
    pageNo: int = Form(1),
    maxMessageId: int | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_message_service.load_history(user.user_id, pageNo, maxMessageId)
    return success(data)


@router.post("/clearHistoryMessage")
async def clear_history_message(
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        return success(await agent_message_service.clear_visible_history(user.user_id))
    except ValueError as exc:
        return error(409, str(exc))


@router.post("/sendMessage")
async def send_message(
    message: str = Form(""),
    fromProduct: str | None = Form(None),
    consultProductId: str | None = Form(None),
    comparisonProductIds: str | None = Form(None),
    imageAssetId: str | None = Form(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
    x_idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_evaluation_trial_id: str | None = Header(None, alias="X-Evaluation-Trial-ID"),
    x_evaluation_fault_capability: str | None = Header(None, alias="X-Evaluation-Fault-Capability"),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    reservation = None
    orchestration_data = None
    try:
        comparison_product_ids = _form_string_list(comparisonProductIds)
        normalized_idempotency_key = str(x_idempotency_key or "").strip()
        if normalized_idempotency_key:
            fingerprint = agent_request_idempotency_service.fingerprint(
                message=message,
                from_product=_form_bool(fromProduct),
                consult_product_id=consultProductId,
                comparison_product_ids=comparison_product_ids,
                image_asset_id=imageAssetId,
            )
            reservation = await agent_request_idempotency_service.reserve(
                user_id=user.user_id,
                key=normalized_idempotency_key,
                fingerprint=fingerprint,
            )
            if not reservation.owner:
                replay = await agent_request_idempotency_service.wait(reservation)
                if replay.response is not None:
                    return ResponseVO.model_validate(replay.response)
                # The first request has reserved the key but has not published
                # its response yet. Return the same deterministic run identity;
                # callers can poll history/episodes without creating a second
                # message or task.
                return success(
                    {
                        "runId": replay.run_id,
                        "messageId": replay.message_id,
                        "deliveryState": "IDEMPOTENCY_IN_PROGRESS",
                    }
                )
        evaluation_fault = None
        if x_evaluation_fault_capability:
            try:
                evaluation_fault = await consume_api_fault_capability(
                    x_evaluation_fault_capability,
                    user_id=user.user_id,
                    request_id=str(x_request_id or ""),
                    trial_id=str(x_evaluation_trial_id or ""),
                )
            except FaultCapabilityRejected as exc:
                raise HTTPException(
                    status_code=403,
                    detail="invalid evaluation fault capability",
                ) from exc
        with tracer.start_as_current_span("agent.send_message") as span:
            span.set_attribute("agent.user_id", user.user_id)
            span.set_attribute("agent.from_product", _form_bool(fromProduct))
            if x_request_id:
                span.set_attribute("agent.request_id", str(x_request_id)[:128])
            if reservation is not None:
                span.set_attribute("agent.run_id", reservation.run_id)
            orchestration_data = await agent_orchestrator.send_message(
                user.user_id,
                message,
                _form_bool(fromProduct),
                consultProductId,
                comparison_product_ids,
                imageAssetId,
                request_id=x_request_id,
                run_id=reservation.run_id if reservation is not None else None,
                evaluation_trial_id=x_evaluation_trial_id,
                evaluation_fault=evaluation_fault,
            )
            if isinstance(orchestration_data, dict):
                for key in ("episodeId", "episode_id"):
                    if orchestration_data.get(key):
                        span.set_attribute("agent.episode_id", str(orchestration_data[key]))
                        break
        response = _canonical_response(success(orchestration_data))
        if reservation is not None:
            await agent_request_idempotency_service.complete(
                reservation,
                response.model_dump(mode="json"),
                message_id=(
                    orchestration_data.get("messageId")
                    if isinstance(orchestration_data, dict)
                    else None
                ),
            )
        return response
    except PendingActionExpired as e:
        if reservation is not None:
            return await _idempotent_failure_or_unknown(reservation, error(410, str(e)))
        raise HTTPException(status_code=410, detail=str(e)) from e
    except AgentRequestIdempotencyConflict as e:
        return error(409, str(e))
    except HTTPException as e:
        if reservation is None:
            raise
        return await _idempotent_failure_or_unknown(
            reservation, error(e.status_code, str(e.detail))
        )
    except ValueError as e:
        response = error(600, str(e))
        if reservation is not None:
            return await _idempotent_failure_or_unknown(reservation, response)
        return response
    except Exception as exc:
        if reservation is not None:
            # A keyed request must return the same envelope that a later replay
            # reads. If the failure ledger itself is unavailable, the business
            # result is explicitly unknown; never report a false success/failure.
            failed = error(500, "AI 服务暂时不可用，请稍后重试")
            known_message_id = (
                orchestration_data.get("messageId")
                if isinstance(orchestration_data, dict)
                else None
            )
            return await _idempotent_failure_or_unknown(
                reservation,
                failed,
                known_message_id=known_message_id,
            )
        logger.exception(
            "agent_send_message_unhandled",
            error=type(exc).__name__,
        )
        raise


@router.get("/supportCases")
async def list_support_cases(
    limit: int = 20,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    return success(await support_case_service.list_for_user(user.user_id, limit=limit))


@router.get("/supportCaseDetail")
async def support_case_detail(
    caseId: str,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    rows = await support_case_service.list_for_user(user.user_id, caseId, limit=1)
    if not rows:
        return error(404, "工单不存在或无权查看")
    return success(rows[0])


@router.post("/selectOrderCandidate")
async def select_order_candidate(
    selectionId: str = Form(...),
    targetType: str = Form(...),
    targetId: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    """Continue a rendered order candidate without asking the user to type an ID."""
    selection_id = selectionId.strip()
    target_type = targetType.strip().upper()
    target_id = targetId.strip()
    if not selection_id or not target_id or target_type not in {"ORDER", "ORDER_ITEM"}:
        return error(600, "订单候选参数无效")
    metric_intent = "UNKNOWN"
    try:
        result = await agent_orchestrator.send_selected_order_candidate(
            user.user_id,
            selection_id,
            target_type,
            target_id,
        )
        metric_intent = str(result.get("intent") or "UNKNOWN")
        outcome = "selected" if result.get("selectionId") else "idempotent"
        ORDER_SELECTION_TOTAL.labels(intent=metric_intent, outcome=outcome).inc()
        return success(result)
    except OrderSelectionExpired as exc:
        ORDER_SELECTION_TOTAL.labels(intent=metric_intent, outcome="expired").inc()
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except OrderSelectionConflict as exc:
        ORDER_SELECTION_TOTAL.labels(intent=metric_intent, outcome="conflict").inc()
        return error(409, str(exc))
    except ValueError as exc:
        ORDER_SELECTION_TOTAL.labels(intent=metric_intent, outcome="invalid").inc()
        return error(600, str(exc))
    except Exception:
        ORDER_SELECTION_TOTAL.labels(intent=metric_intent, outcome="error").inc()
        raise


@router.post("/selectVisualSubject")
async def select_visual_subject(
    selectionId: str = Form(...),
    subjectId: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    """Continue a server-rendered visual selection without accepting a bbox."""
    selection_id = selectionId.strip()
    subject_id = subjectId.strip()
    if not selection_id or not subject_id:
        VISUAL_SELECTION_TOTAL.labels(outcome="invalid").inc()
        return error(600, "图片主体选择参数无效")
    try:
        result = await agent_orchestrator.send_selected_visual_subject(
            user.user_id,
            selection_id,
            subject_id,
        )
        VISUAL_SELECTION_TOTAL.labels(
            outcome="selected" if result.get("selectionId") else "idempotent"
        ).inc()
        return success(result)
    except VisualSelectionExpired as exc:
        VISUAL_SELECTION_TOTAL.labels(outcome="expired").inc()
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except VisualSelectionConflict as exc:
        VISUAL_SELECTION_TOTAL.labels(outcome="conflict").inc()
        return error(409, str(exc))
    except ValueError as exc:
        VISUAL_SELECTION_TOTAL.labels(outcome="invalid").inc()
        return error(600, str(exc))
    except Exception:
        VISUAL_SELECTION_TOTAL.labels(outcome="error").inc()
        raise


@router.post("/cancelMessage")
async def cancel_message(
    messageId: int = Form(...),
    assistantMessage: str | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_orchestrator.cancel_message(
        user.user_id, messageId, assistantMessage
    )
    return success(data)


@router.post("/reportClick")
async def report_click(
    productId: str = Form(...),
    requestId: str = Form(...),
    position: int = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    """P0-7：前端商品卡片点击上报（fire-and-forget）。

    requestId 是 serving 时随卡片下发的归因 token；点击日志与曝光日志
    靠它关联，离线分析才能算 CTR/转化而不是只有展示。
    """
    product_id = productId.strip()
    request_id = requestId.strip()
    if (
        not product_id
        or len(product_id) > 64
        or not request_id
        or len(request_id) > 128
        or position < 1
        or position > IMPRESSION_LOG_MAX_PRODUCTS
    ):
        return error(600, "点击归因参数无效")
    attribution = await recommendation_attribution_service.record_click(
        user.user_id,
        request_id,
        product_id,
        position,
    )
    if attribution is None:
        return error(600, "点击归因无效或已过期")
    return success(attribution)


@router.post("/clearProductConsult")
async def clear_product_consult(user: TokenUserInfo = Depends(require_login)) -> ResponseVO:

    await redis_service.clear_consult(user.user_id)
    return success(None)


@router.post("/pauseProductConsult")
async def pause_product_consult(user: TokenUserInfo = Depends(require_login)) -> ResponseVO:

    await redis_service.pause_consult(user.user_id)
    return success(None)


@router.post("/getProductConsultContext")
async def get_product_consult_context(
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    ctx = await agent_orchestrator.get_consult_context(user.user_id)
    return success(ctx)


@router.get("/shoppingProfile")
async def get_shopping_profile(
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    return success(await shopping_profile_service.get_profile(user.user_id))


@router.post("/shoppingProfile/update")
async def update_shopping_profile(
    payload: ShoppingProfileUpdateRequest,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        updated = await shopping_profile_service.manual_update(
            user.user_id,
            payload.profile.model_dump(by_alias=True, exclude_unset=True),
            payload.expectedRevision,
        )
        return success(updated)
    except ProfileRevisionConflict as exc:
        return ResponseVO(
            status="error",
            code=409,
            info="购物偏好已更新，请基于当前版本重试",
            data=exc.current,
        )
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/shoppingProfile/clear")
async def clear_shopping_profile(
    payload: ShoppingProfileClearRequest,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        updated = await shopping_profile_service.clear_profile(
            user.user_id, payload.expectedRevision
        )
        return success(updated)
    except ProfileRevisionConflict as exc:
        return ResponseVO(
            status="error",
            code=409,
            info="购物偏好已更新，请基于当前版本重试",
            data=exc.current,
        )


@router.post("/shoppingProfile/personalization")
async def set_shopping_personalization(
    payload: ShoppingPersonalizationRequest,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        updated = await shopping_profile_service.set_personalization(
            user.user_id, payload.enabled, payload.expectedRevision
        )
        return success(updated)
    except ProfileRevisionConflict as exc:
        return ResponseVO(
            status="error",
            code=409,
            info="购物偏好已更新，请基于当前版本重试",
            data=exc.current,
        )


@router.post("/shoppingProfile/signals/delete")
async def delete_shopping_signal(
    payload: ShoppingProfileSignalRequest,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        updated = await shopping_profile_service.delete_implicit_signal(
            user.user_id, payload.signalId, payload.expectedRevision
        )
        return success(updated)
    except ProfileRevisionConflict as exc:
        return ResponseVO(
            status="error",
            code=409,
            info="购物偏好已更新，请基于当前版本重试",
            data=exc.current,
        )


@router.post("/shoppingProfile/signals/clear")
async def clear_shopping_signals(
    payload: ShoppingProfileClearRequest,
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        updated = await shopping_profile_service.clear_implicit_signals(
            user.user_id, payload.expectedRevision
        )
        return success(updated)
    except ProfileRevisionConflict as exc:
        return ResponseVO(
            status="error",
            code=409,
            info="购物偏好已更新，请基于当前版本重试",
            data=exc.current,
        )


@router.post("/requestHuman")
async def request_human(
    reason: str | None = Form(None),
    sourceMessageId: int | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_orchestrator.request_human(user.user_id, reason, sourceMessageId)
    return success(data)


@router.post("/cancelHuman")
async def cancel_human(
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_orchestrator.cancel_human(user.user_id)
    return success(data)


@router.post("/humanStatus")
async def human_status(
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_orchestrator.human_status(user.user_id)
    return success(data)


@router.post("/feedback")
async def message_feedback(
    messageId: int = Form(...),
    rating: int = Form(...),
    reason: str | None = Form(None),
    detail: str | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    if rating not in (-1, 1):
        return error(600, "rating 仅支持 1 或 -1")
    try:
        await support_service.save_feedback(user.user_id, messageId, rating, reason, detail)
        return success(None)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/confirmAction")
async def confirm_action(
    request: Request,
    actionToken: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:

    if not await rate_limit_service.allow(user.user_id, "confirmAction", 1, 3):
        return success(
            {
                "actionType": None,
                "success": False,
                "resultMessage": "操作过于频繁，请稍后再试",
            }
        )
    token = get_request_token(request) or user.token or ""

    async def executor(pending: dict) -> str:
        return await action_execute_service.execute(pending, token)

    async def action_state() -> dict:
        try:
            pending = await pending_action_service.get_by_token(actionToken)
        except Exception:
            # The command result remains authoritative even if the follow-up
            # read used only to enrich the UI response is unavailable.
            return {}
        if not pending:
            return {}
        return {
            key: pending.get(key)
            for key in (
                "status",
                "statusName",
                "reconcileAttempts",
                "reconcileDeadline",
                "reviewReason",
                "resultMessage",
                "errorMessage",
            )
            if pending.get(key) is not None
        }

    try:
        action_type, ok, msg = await pending_action_service.confirm(
            user.user_id, actionToken, executor
        )
        state = await action_state()
        return success(
            {
                "actionType": action_type,
                "success": ok,
                "resultMessage": msg,
                **state,
            }
        )
    except PendingActionExpired as e:
        raise HTTPException(status_code=410, detail=str(e)) from e
    except ValueError as e:
        state = await action_state()
        return success(
            {
                "actionType": None,
                "success": False,
                "resultMessage": str(e),
                **state,
            }
        )


@router.post("/cancelAction")
async def cancel_action(
    actionToken: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    if not await rate_limit_service.allow(user.user_id, "cancelAction", 1, 3):
        return success(
            {
                "actionType": None,
                "success": False,
                "resultMessage": "操作过于频繁，请稍后再试",
            }
        )
    try:
        pending = await pending_action_service.cancel(user.user_id, actionToken) or {}
        return success(
            {
                "actionType": pending.get("actionType"),
                "success": True,
                "resultMessage": "已取消操作",
                "status": pending.get("status"),
                "statusName": pending.get("statusName"),
            }
        )
    except ValueError as e:
        try:
            pending = await pending_action_service.get_by_token(actionToken) or {}
        except Exception:
            pending = {}
        return success(
            {
                "actionType": None,
                "success": False,
                "resultMessage": str(e),
                "status": pending.get("status"),
                "statusName": pending.get("statusName"),
            }
        )


@router.post("/admin/loadMessages")
async def admin_load_messages(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    page_no = _as_int(body.get("pageNo"), 1) or 1
    page_size = _as_int(body.get("pageSize"), 15) or 15
    user_id = body.get("userId") or None
    if user_id is not None:
        user_id = str(user_id).strip() or None
    biz_type = body.get("bizType") or None
    if biz_type is not None:
        biz_type = str(biz_type).strip() or None
    data = await agent_message_service.admin_load_messages(page_no, page_size, user_id, biz_type)
    return success(data)


@router.post("/admin/traceRuns")
async def admin_trace_runs(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate", "analytics:read", "audit:read")
    body = await _read_admin_body(request)
    data = await episode_query_service.list_runs(
        page_no=_as_int(body.get("pageNo"), 1) or 1,
        page_size=_as_int(body.get("pageSize"), 30) or 30,
        status=str(body.get("status") or "").strip() or None,
        intent=str(body.get("intent") or "").strip() or None,
        user_id=str(body.get("userId") or "").strip() or None,
        outcome=str(body.get("outcome") or "").strip() or None,
        agent_id=str(body.get("agentId") or "").strip() or None,
        run_scope=str(body.get("runScope") or "ROOT").strip() or "ROOT",
    )
    return success(data)


@router.post("/admin/dataAnalyst/ask")
async def admin_data_analyst_ask(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:read" not in admin.permissions:
        return _analytics_permission_denied(request, "analytics:read", "ANALYTICS_READ_REQUIRED")
    body = await _read_admin_body(request)
    request_id = _analytics_request_id(request)
    try:
        result = await data_analyst_service.ask(
            str(body.get("question") or ""),
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(body.get("tenantId") or "").strip() or None,
            cursor=str(body.get("cursor") or "").strip() or None,
            page_size=_as_int(body.get("pageSize")),
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    return _analytics_response(result, request_id=request_id)


@router.post("/admin/dataAnalyst/clarify")
async def admin_data_analyst_clarify(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:read" not in admin.permissions:
        return _analytics_permission_denied(request, "analytics:read", "ANALYTICS_READ_REQUIRED")
    body = await _read_admin_body(request)
    request_id = _analytics_request_id(request)
    tenant_id = str(body.get("tenantId") or "").strip() or None
    try:
        clarification = await analytics_clarification_service.consume(
            str(body.get("clarificationToken") or ""),
            str(body.get("choiceId") or ""),
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=tenant_id,
        )
        result = await data_analyst_service.ask(
            str(clarification["resolvedQuestion"]),
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=tenant_id,
            page_size=_as_int(body.get("pageSize")),
            allow_clarification=False,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    result["clarificationParentRunId"] = clarification.get("parentRunId")
    result["clarificationChoice"] = clarification.get("choice")
    return _analytics_response(result, request_id=request_id)


@router.post("/admin/dataAnalyst/page")
async def admin_data_analyst_page(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:read" not in admin.permissions:
        return _analytics_permission_denied(request, "analytics:read", "ANALYTICS_READ_REQUIRED")
    body = await _read_admin_body(request)
    request_id = _analytics_request_id(request)
    try:
        result = await analytics_result_service.page(
            str(body.get("cursor") or ""),
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(body.get("tenantId") or "").strip() or None,
            page_size=_as_int(body.get("pageSize"), 50) or 50,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    return _analytics_response(result, request_id=request_id)


@router.post("/admin/dataAnalyst/export")
async def admin_data_analyst_export(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:export" not in admin.permissions:
        return _analytics_permission_denied(
            request, "analytics:export", "ANALYTICS_EXPORT_REQUIRED"
        )
    body = await _read_admin_body(request)
    request_id = _analytics_request_id(request)
    try:
        result = await analytics_export_service.request(
            str(body.get("resultSetId") or ""),
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(body.get("tenantId") or "").strip() or None,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    return _analytics_response(result, request_id=request_id)


@router.get("/admin/dataAnalyst/export/{job_id}")
async def admin_data_analyst_export_status(
    job_id: str,
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:export" not in admin.permissions:
        return _analytics_permission_denied(
            request, "analytics:export", "ANALYTICS_EXPORT_REQUIRED"
        )
    request_id = _analytics_request_id(request)
    try:
        result = await analytics_export_service.get(
            job_id,
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(request.query_params.get("tenantId") or "").strip() or None,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    return _analytics_response(result, request_id=request_id)


@router.post("/admin/dataAnalyst/export/status")
async def admin_data_analyst_export_status_post(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> JSONResponse:
    if "analytics:export" not in admin.permissions:
        return _analytics_permission_denied(
            request, "analytics:export", "ANALYTICS_EXPORT_REQUIRED"
        )
    body = await _read_admin_body(request)
    job_id = str(body.get("jobId") or "").strip()
    if not job_id:
        return _analytics_exception_response(
            AnalyticsResultError("EXPORT_JOB_ID_REQUIRED", 400, "jobId 不能为空"),
            request_id=_analytics_request_id(request),
        )
    request_id = _analytics_request_id(request)
    try:
        result = await analytics_export_service.get(
            job_id,
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(body.get("tenantId") or "").strip() or None,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=request_id)
    return _analytics_response(result, request_id=request_id)


@router.get("/admin/dataAnalyst/export/{job_id}/download")
async def admin_data_analyst_export_download(
    job_id: str,
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> Response:
    if "analytics:export" not in admin.permissions:
        return _analytics_permission_denied(
            request, "analytics:export", "ANALYTICS_EXPORT_REQUIRED"
        )
    try:
        content = await analytics_export_service.download(
            job_id,
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(request.query_params.get("tenantId") or "").strip() or None,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=_analytics_request_id(request))
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
    )


@router.post("/admin/dataAnalyst/export/download")
async def admin_data_analyst_export_download_post(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> Response:
    if "analytics:export" not in admin.permissions:
        return _analytics_permission_denied(
            request, "analytics:export", "ANALYTICS_EXPORT_REQUIRED"
        )
    body = await _read_admin_body(request)
    job_id = str(body.get("jobId") or "").strip()
    if not job_id:
        return _analytics_exception_response(
            AnalyticsResultError("EXPORT_JOB_ID_REQUIRED", 400, "jobId 不能为空"),
            request_id=_analytics_request_id(request),
        )
    try:
        content = await analytics_export_service.download(
            job_id,
            admin_id=admin.admin_id,
            permissions=admin.permissions,
            tenant_id=str(body.get("tenantId") or "").strip() or None,
        )
    except AnalyticsResultError as exc:
        return _analytics_exception_response(exc, request_id=_analytics_request_id(request))
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.json"'},
    )


@router.post("/admin/inventoryOps/suggestions")
async def admin_inventory_ops_suggestions(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:config")
    body = await _read_admin_body(request)
    result = await inventory_ops_service.suggestions(
        admin_id=admin.admin_id,
        lookback_days=_as_int(body.get("lookbackDays"), 30) or 30,
        limit=_as_int(body.get("limit"), 50) or 50,
    )
    return success(result)


@router.post("/admin/traceDetail")
async def admin_trace_detail(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate", "audit:read")
    body = await _read_admin_body(request)
    run_id = str(body.get("runId") or "").strip()
    if not run_id:
        return error(600, "runId 不能为空")
    data = await episode_query_service.detail(run_id)
    if data is None:
        return error(404, "Trace 不存在或已过保留期")
    return success(data)


@router.post("/admin/reviewEpisode")
async def admin_review_episode(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate")
    body = await _read_admin_body(request)
    try:
        data = await episode_review_service.review(
            _required_text(body, "runId"),
            _required_text(body, "datasetEligible"),
            admin.admin_id,
            note=str(body.get("note") or "").strip() or None,
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportCases")
async def admin_support_cases(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    try:
        data = await support_case_service.list_admin(
            page_no=_as_int(body.get("pageNo"), 1) or 1,
            page_size=_as_int(body.get("pageSize"), 30) or 30,
            status=str(body.get("status") or "").strip() or None,
            user_id=str(body.get("userId") or "").strip() or None,
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportCaseDetail")
async def admin_support_case_detail(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    case_id = str(body.get("caseId") or body.get("caseNo") or "").strip()
    if not case_id:
        return error(600, "caseId 不能为空")
    data = await support_case_service.get(case_id)
    if not data:
        return error(404, "工单不存在")
    return success(data)


@router.post("/admin/supportCaseClaim")
async def admin_support_case_claim(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_case_service.claim(_required_text(body, "caseId"), admin.admin_id)
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportCaseInProgress")
async def admin_support_case_in_progress(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_case_service.in_progress(
            _required_text(body, "caseId"), admin.admin_id
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportCaseResolve")
async def admin_support_case_resolve(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_case_service.resolve(
            _required_text(body, "caseId"),
            admin.admin_id,
            _required_text(body, "resolutionCode"),
            _required_text(body, "rootCause"),
            _required_text(body, "resolutionSummary"),
            support_session_id=str(body.get("supportSessionId") or "").strip() or None,
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/loadPendingActions")
async def admin_load_pending_actions(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    """Read-only lookup for uncertain writes and manual-review records."""
    admin.require_any("support:read", "ai:evaluate", "audit:read")
    body = await _read_admin_body(request)
    try:
        rows = await pending_action_service.list_for_review(
            status=str(body.get("status") or "MANUAL_REVIEW"),
            token=body.get("actionToken"),
            user_id=body.get("userId"),
            business_key=body.get("businessKey"),
            limit=_as_int(body.get("limit"), 100) or 100,
        )
        return success({"items": rows, "total": len(rows)})
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/getMessage")
async def admin_get_message(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    message_id = _as_int(body.get("messageId"))
    if not message_id:
        return error(600, "messageId 不能为空")
    data = await agent_message_service.admin_get_message(message_id)
    return success(data)


@router.post("/admin/deleteMessage")
async def admin_delete_message(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    message_id = _as_int(body.get("messageId"))
    if not message_id:
        return error(600, "messageId 不能为空")
    ok = await agent_message_service.admin_delete_message(message_id)
    return success({"deleted": ok})


@router.post("/admin/supportQueue")
async def admin_support_queue(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read")
    body = await _read_admin_body(request)
    data = await support_service.list_queue(
        _as_int(body.get("pageNo"), 1) or 1,
        _as_int(body.get("pageSize"), 30) or 30,
    )
    return success(data)


@router.post("/admin/supportSessions")
async def admin_support_sessions(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    data = await support_service.list_sessions(
        _as_int(body.get("pageNo"), 1) or 1,
        _as_int(body.get("pageSize"), 30) or 30,
        str(body.get("status") or "").strip() or None,
        str(body.get("userId") or "").strip() or None,
    )
    return success(data)


@router.post("/admin/supportStats")
async def admin_support_stats(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "analytics:read")
    body = await _read_admin_body(request)
    data = await support_service.sla_stats(
        _as_int(body.get("windowHours"), 24) or 24,
        _as_int(body.get("firstResponseSlaSeconds")),
        _as_int(body.get("queueAlertSeconds")),
    )
    return success(data)


@router.post("/admin/supportClaim")
async def admin_support_claim(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        admin_id = admin.admin_id
        session_id = _required_text(body, "sessionId")
        data = await support_service.claim(session_id, admin_id)
        _audit_admin_action("claim", admin_id, session_id)
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportActivate")
async def admin_support_activate(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_service.activate(_required_text(body, "sessionId"), admin.admin_id)
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportReply")
async def admin_support_reply(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        admin_id = admin.admin_id
        session_id = _required_text(body, "sessionId")
        content = _required_text(body, "content")
        data = await support_service.reply(session_id, admin_id, content)
        _audit_admin_action("reply", admin_id, session_id, {"contentLength": len(content)})
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportResolve")
async def admin_support_resolve(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_service.resolve(
            _required_text(body, "sessionId"),
            admin.admin_id,
            str(body.get("remark") or "").strip() or None,
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportReturnAi")
async def admin_support_return_ai(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:write")
    body = await _read_admin_body(request)
    try:
        data = await support_service.return_to_ai(_required_text(body, "sessionId"), admin.admin_id)
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportHistory")
async def admin_support_history(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("support:read", "audit:read")
    body = await _read_admin_body(request)
    try:
        data = await support_service.history(
            _required_text(body, "sessionId"),
            _as_int(body.get("limit"), 100) or 100,
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/badcases")
async def admin_badcases(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate", "audit:read")
    body = await _read_admin_body(request)
    try:
        data = await badcase_service.list_candidates(
            _as_int(body.get("pageNo"), 1) or 1,
            _as_int(body.get("pageSize"), 30) or 30,
            str(body.get("status") or "").strip() or "NEW",
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/reviewBadcase")
async def admin_review_badcase(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate")
    body = await _read_admin_body(request)
    candidate_id = _as_int(body.get("candidateId"))
    if not candidate_id:
        return error(600, "candidateId 不能为空")
    try:
        data = await badcase_service.review(
            candidate_id,
            _required_text(body, "status"),
            admin.admin_id,
            remark=str(body.get("remark") or "").strip() or None,
            labels=(body.get("labels") if isinstance(body.get("labels"), list) else []),
            owner=str(body.get("owner") or "").strip() or None,
            fix_version=str(body.get("fixVersion") or "").strip() or None,
            regression=(
                body.get("regression") if isinstance(body.get("regression"), dict) else None
            ),
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/regressionCases")
async def admin_regression_cases(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate", "audit:read")
    body = await _read_admin_body(request)
    data = await badcase_service.list_regression_cases(
        _as_int(body.get("pageNo"), 1) or 1,
        _as_int(body.get("pageSize"), 30) or 30,
        str(body.get("status") or "").strip() or None,
    )
    return success(data)


@router.post("/admin/runRegressionCases")
async def admin_run_regression_cases(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:evaluate")
    body = await _read_admin_body(request)
    try:
        return success(await regression_replay_service.run_active(_as_int(body.get("caseId"))))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/createPilotBatch")
async def admin_create_pilot_batch(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot")
    body = await _read_admin_body(request)
    try:
        return success(
            await pilot_batch_service.create(
                name=_required_text(body, "name"),
                description=str(body.get("description") or "").strip() or None,
                evidence_source=_required_text(body, "evidenceSource"),
                consent_text_version=_required_text(body, "consentTextVersion"),
                created_by=admin.admin_id,
            )
        )
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/pilotBatches")
async def admin_pilot_batches(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot", "analytics:read", "audit:read")
    body = await _read_admin_body(request)
    try:
        return success(
            await pilot_batch_service.list(
                status=str(body.get("status") or "").strip() or None,
                limit=_as_int(body.get("limit"), 50) or 50,
            )
        )
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/startPilotBatch")
async def admin_start_pilot_batch(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot")
    body = await _read_admin_body(request)
    try:
        return success(await pilot_batch_service.start(_required_text(body, "batchId")))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/closePilotBatch")
async def admin_close_pilot_batch(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot")
    body = await _read_admin_body(request)
    try:
        return success(await pilot_batch_service.close(_required_text(body, "batchId")))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/registerPilotParticipant")
async def admin_register_pilot_participant(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot")
    body = await _read_admin_body(request)
    try:
        return success(
            await pilot_batch_service.register_participant(
                batch_id=_required_text(body, "batchId"),
                user_id=_required_text(body, "userId"),
                pseudonym=str(body.get("pseudonym") or "").strip() or None,
                created_by=admin.admin_id,
            )
        )
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/withdrawPilotParticipant")
async def admin_withdraw_pilot_participant(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot")
    body = await _read_admin_body(request)
    try:
        return success(
            await pilot_batch_service.withdraw_participant(
                batch_id=_required_text(body, "batchId"),
                participant_id=_required_text(body, "participantId"),
            )
        )
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/pilotParticipants")
async def admin_pilot_participants(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("ai:pilot", "audit:read")
    body = await _read_admin_body(request)
    try:
        return success(await pilot_batch_service.list_participants(_required_text(body, "batchId")))
    except ValueError as exc:
        return error(600, str(exc))


def _pilot_metric_filters(body: dict) -> dict:
    start_at = _as_datetime(body.get("startAt"))
    end_at = _as_datetime(body.get("endAt"))
    if start_at and end_at and start_at >= end_at:
        raise ValueError("startAt 必须早于 endAt")
    return {
        "batch_id": str(body.get("batchId") or "").strip() or None,
        "evidence_source": str(body.get("evidenceSource") or "").strip() or None,
        "start_at": start_at,
        "end_at": end_at,
    }


@router.post("/admin/metricsOverview")
async def admin_metrics_overview(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("analytics:read", "ai:evaluate")
    body = await _read_admin_body(request)
    try:
        return success(await pilot_metrics_service.overview(**_pilot_metric_filters(body)))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/metricsPerformance")
async def admin_metrics_performance(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> ResponseVO:
    admin.require_any("analytics:read", "ai:evaluate")
    body = await _read_admin_body(request)
    try:
        return success(await pilot_metrics_service.performance(**_pilot_metric_filters(body)))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/pilotReport")
async def admin_pilot_report(
    request: Request,
    admin: AdminAssertion = Depends(_require_admin),
) -> Response:
    admin.require_any("analytics:export")
    body = await _read_admin_body(request)
    try:
        batch_id = _required_text(body, "batchId")
        output_format = str(body.get("format") or "json")
        content, content_type = await pilot_metrics_service.export_report(batch_id, output_format)
    except ValueError as exc:
        return Response(
            content=json.dumps({"code": 600, "message": str(exc)}, ensure_ascii=False),
            media_type="application/json",
            status_code=400,
        )
    suffix = {"json": "json", "csv": "csv", "markdown": "md"}.get(output_format.lower(), "bin")
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{batch_id}.{suffix}"'},
    )


def _required_text(body: dict, key: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} 不能为空")
    return value


def _audit_admin_action(
    action: str, admin_id: str, session_id: str | None, extra: dict | None = None
) -> None:
    """管理端写操作的结构化审计日志（P0-6 残留）。

    管理身份本身已由 Java 侧认证会话派生（/admin/agentMessage/* 的 currentAdmin），
    Python 只接收派生结果；这里把"谁在什么时间对哪个会话做了什么"落成审计线索，
    出问题时要能回答"哪条回复是谁发的"。
    """
    import structlog as _structlog

    _structlog.get_logger().info(
        "admin_support_action",
        action=action,
        admin_id=admin_id,
        session_id=session_id,
        extra=extra or {},
    )
