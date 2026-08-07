from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import TokenUserInfo, require_login
from app.api.routes import agent
from app.services.support_case_service import support_case_service


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")

    async def logged_in() -> TokenUserInfo:
        return TokenUserInfo(user_id="authenticated-user")

    app.dependency_overrides[require_login] = logged_in
    return app


def test_support_case_list_and_detail_always_use_authenticated_user(monkeypatch):
    rows = [{"caseId": 1, "caseNo": "SC20260807ABC123", "status": "OPEN"}]
    list_for_user = AsyncMock(side_effect=[rows, rows])
    monkeypatch.setattr(support_case_service, "list_for_user", list_for_user)

    with TestClient(_app()) as client:
        listing = client.get("/api/agent/supportCases", params={"limit": 99})
        detail = client.get(
            "/api/agent/supportCaseDetail",
            params={"caseId": "SC20260807ABC123"},
        )

    assert listing.json()["data"] == rows
    assert detail.json()["data"] == rows[0]
    assert list_for_user.await_args_list[0].args == ("authenticated-user",)
    assert list_for_user.await_args_list[0].kwargs == {"limit": 99}
    assert list_for_user.await_args_list[1].args == (
        "authenticated-user",
        "SC20260807ABC123",
    )
    assert list_for_user.await_args_list[1].kwargs == {"limit": 1}


def test_support_case_detail_hides_cross_user_lookup(monkeypatch):
    monkeypatch.setattr(support_case_service, "list_for_user", AsyncMock(return_value=[]))

    with TestClient(_app()) as client:
        response = client.get(
            "/api/agent/supportCaseDetail", params={"caseId": "41"}
        )

    assert response.json()["status"] == "error"
    assert response.json()["code"] == 404
