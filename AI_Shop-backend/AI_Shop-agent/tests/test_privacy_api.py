from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import privacy
from app.config.settings import get_settings
from app.services.privacy_job_service import PrivacyJobConflict, privacy_job_service


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(privacy.router)
    return app


def test_privacy_internal_api_requires_shared_token(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(privacy_job_service, "create", create)
    with TestClient(_app()) as client:
        response = client.post("/internal/privacy/jobs/create", json={})

    assert response.status_code == 401
    create.assert_not_awaited()


def test_privacy_create_maps_idempotency_conflict_to_http_409(monkeypatch):
    create = AsyncMock(side_effect=PrivacyJobConflict("conflict"))
    monkeypatch.setattr(privacy_job_service, "create", create)
    with TestClient(_app()) as client:
        response = client.post(
            "/internal/privacy/jobs/create",
            headers={"X-Internal-Token": get_settings().internal_token},
            json={
                "userId": "u1",
                "jobType": "DELETE",
                "idempotencyKey": "key-1",
                "requestFingerprint": "a" * 64,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "conflict"
