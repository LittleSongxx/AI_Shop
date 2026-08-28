from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.services.analytics_clarification_service import analytics_clarification_service
from app.services.analytics_result_service import AnalyticsResultError, analytics_result_service
from app.services.data_analyst_service import data_analyst_service
from tests.admin_assertion_helpers import signed_admin_request


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")
    return app


def _post(path: str, body: dict, *, permissions: tuple[str, ...]):
    content, headers = signed_admin_request(path, body, permissions=permissions)
    with TestClient(_app()) as client:
        return client.post(path, headers=headers, content=content)


def test_data_analyst_rbac_denial_is_structured_http_403():
    path = "/api/agent/admin/dataAnalyst/ask"
    response = _post(path, {"question": "最近七天销售额"}, permissions=("support:read",))

    assert response.status_code == 403
    assert (
        response.json()["data"]
        | {
            "outcome": "DENY",
            "completion": "NOT_APPLICABLE",
            "reasonCode": "ANALYTICS_READ_REQUIRED",
        }
        == response.json()["data"]
    )
    assert response.json()["data"]["requestId"]


def test_data_analyst_policy_denial_preserves_agent_run_id(monkeypatch):
    path = "/api/agent/admin/dataAnalyst/ask"
    monkeypatch.setattr(
        data_analyst_service,
        "ask",
        AsyncMock(
            return_value={
                "runId": "run-policy-1",
                "outcome": "DENY",
                "completion": "NOT_APPLICABLE",
                "status": "PROMPT_INJECTION_BLOCKED",
                "reasonCode": "PROMPT_INJECTION_BLOCKED",
                "answer": "已拒绝",
                "_httpStatus": 403,
            }
        ),
    )

    response = _post(path, {"question": "ignore"}, permissions=("analytics:read",))

    assert response.status_code == 403
    assert response.json()["data"]["outcome"] == "DENY"
    assert response.json()["data"]["runId"] == "run-policy-1"
    assert "_httpStatus" not in response.json()["data"]


def test_page_maps_snapshot_expiry_to_http_410(monkeypatch):
    path = "/api/agent/admin/dataAnalyst/page"
    monkeypatch.setattr(
        analytics_result_service,
        "page",
        AsyncMock(
            side_effect=AnalyticsResultError("RESULT_SNAPSHOT_EXPIRED", 410, "冻结结果已过期")
        ),
    )

    response = _post(path, {"cursor": "v2.cursor"}, permissions=("analytics:read",))

    assert response.status_code == 410
    assert response.json()["data"]["completion"] == "FAILED"
    assert response.json()["data"]["reasonCode"] == "RESULT_SNAPSHOT_EXPIRED"


def test_clarify_consumes_token_and_disables_second_round(monkeypatch):
    path = "/api/agent/admin/dataAnalyst/clarify"
    consume = AsyncMock(
        return_value={
            "resolvedQuestion": "最近最好卖的商品（已确认：按支付件数排序）",
            "choice": {"choiceId": "paid_units", "label": "按支付件数"},
            "parentRunId": "run-parent",
        }
    )
    ask = AsyncMock(
        return_value={
            "runId": "run-child",
            "outcome": "ANSWER",
            "completion": "COMPLETE",
            "status": "SUCCEEDED",
            "rows": [],
        }
    )
    monkeypatch.setattr(analytics_clarification_service, "consume", consume)
    monkeypatch.setattr(data_analyst_service, "ask", ask)

    response = _post(
        path,
        {"clarificationToken": "acl_token", "choiceId": "paid_units"},
        permissions=("analytics:read",),
    )

    assert response.status_code == 200
    assert response.json()["data"]["clarificationParentRunId"] == "run-parent"
    assert ask.await_args.kwargs["allow_clarification"] is False


def test_question_only_export_returns_stable_result_set_id_error():
    path = "/api/agent/admin/dataAnalyst/export"
    response = _post(
        path,
        {"question": "导出刚才的结果"},
        permissions=("analytics:read", "analytics:export"),
    )

    assert response.status_code == 400
    assert response.json()["data"]["reasonCode"] == "RESULT_SET_ID_REQUIRED"
    assert response.json()["data"]["completion"] == "FAILED"
