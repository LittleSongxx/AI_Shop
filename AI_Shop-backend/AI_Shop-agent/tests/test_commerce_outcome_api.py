from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import commerce_outcomes
from app.config.settings import get_settings
from app.services.commerce_outcome_ledger_service import (
    commerce_outcome_ledger_service,
)


def _body() -> dict:
    return {
        "events": [
            {
                "eventId": "evt-1",
                "source": "CART",
                "idempotencyKey": "cart:add:1",
                "eventType": "ADD_TO_CART",
                "userId": "u1",
                "productId": "p1",
                "payload": {"quantity": 1},
                "occurredAt": "2026-08-10T10:00:00Z",
            }
        ]
    }


def test_internal_outcome_ingest_requires_shared_token(monkeypatch):
    ingest = AsyncMock(return_value=[])
    monkeypatch.setattr(commerce_outcome_ledger_service, "record_batch", ingest)
    app = FastAPI()
    app.include_router(commerce_outcomes.router)

    with TestClient(app) as client:
        missing = client.post("/internal/commerce-outcomes/ingestBatch", json=_body())
        wrong = client.post(
            "/internal/commerce-outcomes/ingestBatch",
            headers={"X-Internal-Token": "wrong"},
            json=_body(),
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    ingest.assert_not_awaited()


def test_internal_outcome_ingest_returns_per_event_status(monkeypatch):
    expected = [{"eventId": "evt-1", "accepted": True, "status": "RECORDED"}]
    ingest = AsyncMock(return_value=expected)
    monkeypatch.setattr(commerce_outcome_ledger_service, "record_batch", ingest)
    app = FastAPI()
    app.include_router(commerce_outcomes.router)

    with TestClient(app) as client:
        response = client.post(
            "/internal/commerce-outcomes/ingestBatch",
            headers={"X-Internal-Token": get_settings().internal_token},
            json=_body(),
        )

    assert response.status_code == 200
    assert response.json()["data"] == expected
    events = ingest.await_args.args[0]
    assert events[0].eventType == "ADD_TO_CART"
    assert events[0].source == "CART"
