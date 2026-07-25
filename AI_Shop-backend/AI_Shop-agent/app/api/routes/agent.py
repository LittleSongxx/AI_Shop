from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.deps import TokenUserInfo, get_request_token, require_login
from app.config.settings import get_settings
from app.exceptions import PendingActionExpired
from app.models.response import ResponseVO, error, success
from app.observability.telemetry import get_tracer
from app.services.action_execute_service import action_execute_service
from app.services.agent_service import agent_orchestrator
from app.services.message_service import agent_message_service
from app.services.pending_action_service import pending_action_service
from app.services.rate_limit_service import rate_limit_service
from app.services.redis_service import redis_service
from app.services.support_service import support_service

router = APIRouter(prefix="/agent", tags=["agent"])
tracer = get_tracer()

limiter = Limiter(key_func=get_remote_address)

def _user_key(request: Request) -> str:

    token = get_request_token(request)
    return token or get_remote_address(request)

def _form_bool(value: str | bool | None) -> bool:

    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")

def _require_internal_token(x_internal_token: str | None = Header(None, alias="X-Internal-Token")) -> str:

    expected = get_settings().internal_token
    if not x_internal_token or x_internal_token != expected:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="invalid internal token")
    return x_internal_token

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

@router.post("/loadHistoryMessage")
async def load_history_message(
    pageNo: int = Form(1),
    maxMessageId: int | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_message_service.load_history(user.user_id, pageNo, maxMessageId)
    return success(data)

@router.post("/sendMessage")
@limiter.limit("1/second", key_func=_user_key)
async def send_message(
    request: Request,
    message: str = Form(...),
    fromProduct: str | None = Form(None),
    consultProductId: str | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    try:
        with tracer.start_as_current_span("agent.send_message") as span:
            span.set_attribute("agent.user_id", user.user_id)
            span.set_attribute("agent.from_product", _form_bool(fromProduct))
            data = await agent_orchestrator.send_message(
                user.user_id,
                message,
                _form_bool(fromProduct),
                consultProductId,
            )
        return success(data)
    except PendingActionExpired as e:
        raise HTTPException(status_code=410, detail=str(e)) from e
    except ValueError as e:

        return error(600, str(e))

@router.post("/cancelMessage")
@limiter.limit("1/second", key_func=_user_key)
async def cancel_message(
    request: Request,
    messageId: int = Form(...),
    assistantMessage: str | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    await agent_orchestrator.cancel_message(user.user_id, messageId, assistantMessage)
    return success(None)

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


@router.post("/requestHuman")
async def request_human(
    reason: str | None = Form(None),
    sourceMessageId: int | None = Form(None),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    data = await agent_orchestrator.request_human(
        user.user_id, reason, sourceMessageId
    )
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
        await support_service.save_feedback(
            user.user_id, messageId, rating, reason, detail
        )
        return success(None)
    except ValueError as exc:
        return error(600, str(exc))

@router.post("/confirmAction")
@limiter.limit("3/second", key_func=_user_key)
async def confirm_action(
    request: Request,
    actionToken: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:

    if not await rate_limit_service.allow(user.user_id, "confirmAction", 1, 3):
        return success({
            "actionType": None,
            "success": False,
            "resultMessage": "操作过于频繁，请稍后再试",
        })
    token = get_request_token(request) or user.token or ""

    async def executor(pending: dict) -> str:
        return await action_execute_service.execute(pending, token)

    try:
        action_type, ok, msg = await pending_action_service.confirm(
            user.user_id, actionToken, executor
        )
        return success({
            "actionType": action_type,
            "success": ok,
            "resultMessage": msg,
        })
    except PendingActionExpired as e:
        raise HTTPException(status_code=410, detail=str(e)) from e
    except ValueError as e:
        return success({
            "actionType": None,
            "success": False,
            "resultMessage": str(e),
        })

@router.post("/cancelAction")
@limiter.limit("3/second", key_func=_user_key)
async def cancel_action(
    request: Request,
    actionToken: str = Form(...),
    user: TokenUserInfo = Depends(require_login),
) -> ResponseVO:
    if not await rate_limit_service.allow(user.user_id, "cancelAction", 1, 3):
        return success({
            "actionType": None,
            "success": False,
            "resultMessage": "操作过于频繁，请稍后再试",
        })
    try:
        await pending_action_service.cancel(user.user_id, actionToken)
        return success(None)
    except ValueError as e:
        return success({
            "actionType": None,
            "success": False,
            "resultMessage": str(e),
        })

@router.post("/admin/loadMessages")
async def admin_load_messages(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    page_no = _as_int(body.get("pageNo"), 1) or 1
    page_size = _as_int(body.get("pageSize"), 15) or 15
    user_id = body.get("userId") or None
    if user_id is not None:
        user_id = str(user_id).strip() or None
    biz_type = body.get("bizType") or None
    if biz_type is not None:
        biz_type = str(biz_type).strip() or None
    data = await agent_message_service.admin_load_messages(
        page_no, page_size, user_id, biz_type
    )
    return success(data)

@router.post("/admin/getMessage")
async def admin_get_message(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    message_id = _as_int(body.get("messageId"))
    if not message_id:
        return error(600, "messageId 不能为空")
    data = await agent_message_service.admin_get_message(message_id)
    return success(data)

@router.post("/admin/deleteMessage")
async def admin_delete_message(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    message_id = _as_int(body.get("messageId"))
    if not message_id:
        return error(600, "messageId 不能为空")
    ok = await agent_message_service.admin_delete_message(message_id)
    return success({"deleted": ok})


@router.post("/admin/supportQueue")
async def admin_support_queue(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    data = await support_service.list_queue(
        _as_int(body.get("pageNo"), 1) or 1,
        _as_int(body.get("pageSize"), 30) or 30,
    )
    return success(data)


@router.post("/admin/supportSessions")
async def admin_support_sessions(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
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
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
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
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    try:
        data = await support_service.claim(
            _required_text(body, "sessionId"), _required_text(body, "adminId")
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportActivate")
async def admin_support_activate(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    try:
        data = await support_service.activate(
            _required_text(body, "sessionId"), _required_text(body, "adminId")
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportReply")
async def admin_support_reply(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    try:
        data = await support_service.reply(
            _required_text(body, "sessionId"),
            _required_text(body, "adminId"),
            _required_text(body, "content"),
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportResolve")
async def admin_support_resolve(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    try:
        data = await support_service.resolve(
            _required_text(body, "sessionId"),
            _required_text(body, "adminId"),
            str(body.get("remark") or "").strip() or None,
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportReturnAi")
async def admin_support_return_ai(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    try:
        data = await support_service.return_to_ai(
            _required_text(body, "sessionId"), _required_text(body, "adminId")
        )
        return success(support_service.public_session(data))
    except ValueError as exc:
        return error(600, str(exc))


@router.post("/admin/supportHistory")
async def admin_support_history(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
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
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    data = await support_service.list_badcases(
        _as_int(body.get("pageNo"), 1) or 1,
        _as_int(body.get("pageSize"), 30) or 30,
        str(body.get("status") or "").strip() or None,
    )
    return success(data)


@router.post("/admin/reviewBadcase")
async def admin_review_badcase(
    request: Request,
    _token: str = Depends(_require_internal_token),
) -> ResponseVO:
    body = await _read_admin_body(request)
    candidate_id = _as_int(body.get("candidateId"))
    if not candidate_id:
        return error(600, "candidateId 不能为空")
    try:
        data = await support_service.review_badcase(
            candidate_id,
            _required_text(body, "status"),
            _required_text(body, "reviewer"),
            str(body.get("remark") or "").strip() or None,
            str(body.get("faqAnswer") or "").strip() or None,
        )
        return success(data)
    except ValueError as exc:
        return error(600, str(exc))


def _required_text(body: dict, key: str) -> str:
    value = str(body.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} 不能为空")
    return value
