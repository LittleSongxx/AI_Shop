from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import TokenUserInfo, require_login
from app.api.routes import agent
from app.services.shopping_profile_service import (
    ProfileRevisionConflict,
    empty_profile,
    shopping_profile_service,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")

    async def logged_in() -> TokenUserInfo:
        return TokenUserInfo(user_id="u1")

    app.dependency_overrides[require_login] = logged_in
    return app


def test_profile_get_and_manual_update_use_authenticated_identity(monkeypatch):
    current = {**empty_profile(), "revision": 3, "category": "手机"}
    updated = {**current, "revision": 4, "budgetMax": 5000}
    get_profile = AsyncMock(return_value=current)
    manual_update = AsyncMock(return_value=updated)
    monkeypatch.setattr(shopping_profile_service, "get_profile", get_profile)
    monkeypatch.setattr(shopping_profile_service, "manual_update", manual_update)

    with TestClient(_app()) as client:
        loaded = client.get("/api/agent/shoppingProfile")
        changed = client.post(
            "/api/agent/shoppingProfile/update",
            json={
                "expectedRevision": 3,
                "profile": {"budgetMax": 5000},
            },
        )

    assert loaded.json()["data"]["revision"] == 3
    assert changed.json()["data"]["revision"] == 4
    get_profile.assert_awaited_once_with("u1")
    manual_update.assert_awaited_once_with("u1", {"budgetMax": 5000.0}, 3)


def test_profile_cas_conflict_returns_current_revision(monkeypatch):
    current = {**empty_profile(), "revision": 8, "brands": ["华为"]}
    monkeypatch.setattr(
        shopping_profile_service,
        "manual_update",
        AsyncMock(side_effect=ProfileRevisionConflict(current)),
    )

    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/shoppingProfile/update",
            json={
                "expectedRevision": 7,
                "profile": {"brands": ["苹果"]},
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "error"
    assert body["code"] == 409
    assert body["data"]["revision"] == 8
    assert body["data"]["brands"] == ["华为"]


def test_profile_clear_requires_expected_revision(monkeypatch):
    cleared = {**empty_profile(), "revision": 2}
    clear_profile = AsyncMock(return_value=cleared)
    monkeypatch.setattr(shopping_profile_service, "clear_profile", clear_profile)

    with TestClient(_app()) as client:
        invalid = client.post("/api/agent/shoppingProfile/clear", json={})
        valid = client.post(
            "/api/agent/shoppingProfile/clear",
            json={"expectedRevision": 1},
        )

    assert invalid.status_code == 422
    assert valid.json()["data"]["revision"] == 2
    clear_profile.assert_awaited_once_with("u1", 1)
