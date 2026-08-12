from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.services.badcase_service import badcase_service
from app.services.regression_replay_service import regression_replay_service
from tests.admin_assertion_helpers import signed_admin_request


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")
    return app


def test_badcase_admin_routes_enforce_internal_auth(monkeypatch):
    candidates = AsyncMock(return_value={"list": [], "totalCount": 0})
    monkeypatch.setattr(badcase_service, "list_candidates", candidates)

    with TestClient(_app()) as client:
        response = client.post("/api/agent/admin/badcases", json={})

    assert response.status_code == 401
    candidates.assert_not_awaited()


def test_badcase_review_accepts_lifecycle_metadata_and_regression(monkeypatch):
    review = AsyncMock(return_value={"candidateId": 7, "status": "REGRESSION_ADDED"})
    monkeypatch.setattr(badcase_service, "review", review)
    regression = {
        "name": "退款状态需要工具依据",
        "input": {"userMessage": "退款到哪了"},
        "expected": {"requiredTools": ["QUERY_REFUND_STATUS"]},
    }

    with TestClient(_app()) as client:
        body, headers = signed_admin_request(
            "/api/agent/admin/reviewBadcase",
            {
                "candidateId": 7,
                "status": "REGRESSION_ADDED",
                "reviewer": "attacker-controlled",
                "remark": "已补回归",
                "labels": ["grounding"],
                "owner": "agent-team",
                "fixVersion": "v2",
                "regression": regression,
            },
        )
        response = client.post(
            "/api/agent/admin/reviewBadcase",
            headers=headers,
            content=body,
        )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "REGRESSION_ADDED"
    review.assert_awaited_once_with(
        7,
        "REGRESSION_ADDED",
        "admin-session",
        remark="已补回归",
        labels=["grounding"],
        owner="agent-team",
        fix_version="v2",
        regression=regression,
    )


def test_regression_case_list_route(monkeypatch):
    cases = AsyncMock(return_value={"list": [{"caseId": 9}], "totalCount": 1})
    monkeypatch.setattr(badcase_service, "list_regression_cases", cases)
    with TestClient(_app()) as client:
        body, headers = signed_admin_request(
            "/api/agent/admin/regressionCases",
            {"pageNo": 2, "pageSize": 20, "status": "ACTIVE"},
        )
        response = client.post(
            "/api/agent/admin/regressionCases",
            headers=headers,
            content=body,
        )

    assert response.status_code == 200
    assert response.json()["data"]["list"][0]["caseId"] == 9
    cases.assert_awaited_once_with(2, 20, "ACTIVE")


def test_regression_replay_route_runs_one_active_case(monkeypatch):
    replay = AsyncMock(
        return_value={"total": 1, "passed": 1, "failed": 0, "errors": 0}
    )
    monkeypatch.setattr(regression_replay_service, "run_active", replay)
    with TestClient(_app()) as client:
        body, headers = signed_admin_request(
            "/api/agent/admin/runRegressionCases", {"caseId": 9}
        )
        response = client.post(
            "/api/agent/admin/runRegressionCases",
            headers=headers,
            content=body,
        )

    assert response.status_code == 200
    assert response.json()["data"]["passed"] == 1
    replay.assert_awaited_once_with(9)
