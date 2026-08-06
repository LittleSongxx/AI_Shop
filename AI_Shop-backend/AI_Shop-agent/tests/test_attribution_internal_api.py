from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import attribution
from app.config.settings import get_settings
from app.services.recommendation_attribution_service import (
    recommendation_attribution_service,
)


def test_internal_attribution_requires_shared_token(monkeypatch):
    validate = AsyncMock(return_value=[])
    monkeypatch.setattr(
        recommendation_attribution_service, "validate_batch", validate
    )
    app = FastAPI()
    app.include_router(attribution.router)

    with TestClient(app) as client:
        missing = client.post(
            "/internal/attribution/validateBatch",
            json={"userId": "u1", "items": []},
        )
        wrong = client.post(
            "/internal/attribution/validateBatch",
            headers={"X-Internal-Token": "wrong"},
            json={"userId": "u1", "items": []},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    validate.assert_not_awaited()


def test_internal_attribution_returns_only_store_validated_rows(monkeypatch):
    expected = [
        {
            "requestId": "request-1",
            "productId": "p1",
            "position": 1,
            "source": "hybrid",
            "occurredAt": "2026-08-06T09:00:00.000",
        }
    ]
    validate = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        recommendation_attribution_service, "validate_batch", validate
    )
    app = FastAPI()
    app.include_router(attribution.router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/attribution/validateBatch",
            headers={"X-Internal-Token": get_settings().internal_token},
            json={
                "userId": "u1",
                "items": [
                    {"requestId": "request-1", "productId": "p1", "position": 1},
                    {"requestId": "forged", "productId": "p2", "position": 2},
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == expected
    validate.assert_awaited_once_with(
        "u1",
        [
            {"requestId": "request-1", "productId": "p1", "position": 1},
            {"requestId": "forged", "productId": "p2", "position": 2},
        ],
    )
