from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.config.settings import get_settings
from app.services.badcase_service import badcase_service


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
    headers = {"X-Internal-Token": get_settings().internal_token}
    regression = {
        "name": "退款状态需要工具依据",
        "input": {"userMessage": "退款到哪了"},
        "expected": {"requiredTools": ["QUERY_REFUND_STATUS"]},
    }

    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/reviewBadcase",
            headers=headers,
            json={
                "candidateId": 7,
                "status": "REGRESSION_ADDED",
                "reviewer": "admin-1",
                "remark": "已补回归",
                "labels": ["grounding"],
                "owner": "agent-team",
                "fixVersion": "v2",
                "regression": regression,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "REGRESSION_ADDED"
    review.assert_awaited_once_with(
        7,
        "REGRESSION_ADDED",
        "admin-1",
        remark="已补回归",
        labels=["grounding"],
        owner="agent-team",
        fix_version="v2",
        regression=regression,
    )


def test_regression_case_list_route(monkeypatch):
    cases = AsyncMock(return_value={"list": [{"caseId": 9}], "totalCount": 1})
    monkeypatch.setattr(badcase_service, "list_regression_cases", cases)
    headers = {"X-Internal-Token": get_settings().internal_token}

    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/regressionCases",
            headers=headers,
            json={"pageNo": 2, "pageSize": 20, "status": "ACTIVE"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["list"][0]["caseId"] == 9
    cases.assert_awaited_once_with(2, 20, "ACTIVE")
