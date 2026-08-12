from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.config.settings import get_settings
from app.services.badcase_service import badcase_service
from app.services.pilot_batch_service import pilot_batch_service
from app.services.pilot_metrics_service import pilot_metrics_service
from app.services.redis_service import redis_service
from tests.admin_assertion_helpers import signed_admin_request


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")
    return app


def test_admin_assertion_rejects_expired_signature(monkeypatch):
    monkeypatch.setattr(
        badcase_service,
        "list_candidates",
        lambda *_args, **_kwargs: None,
    )
    body, headers = signed_admin_request(
        "/api/agent/admin/badcases",
        {},
        timestamp=int(time.time()) - 301,
    )
    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/badcases", headers=headers, content=body
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "expired admin assertion"


def test_admin_assertion_rejects_body_tampering():
    _body, headers = signed_admin_request("/api/agent/admin/badcases", {})
    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/badcases", headers=headers, content=b'{"status":"NEW"}'
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "admin assertion body mismatch"


def test_admin_assertion_rejects_nonce_replay(monkeypatch):
    claimed: set[str] = set()

    async def claim_once(nonce_hash: str, _ttl_seconds: int) -> bool:
        if nonce_hash in claimed:
            return False
        claimed.add(nonce_hash)
        return True

    async def list_candidates(*_args, **_kwargs):
        return {"list": [], "totalCount": 0}

    monkeypatch.setattr(redis_service, "claim_admin_assertion_nonce", claim_once)
    monkeypatch.setattr(badcase_service, "list_candidates", list_candidates)
    body, headers = signed_admin_request(
        "/api/agent/admin/badcases", {}, nonce="fixed-nonce"
    )
    with TestClient(_app()) as client:
        first = client.post("/api/agent/admin/badcases", headers=headers, content=body)
        replay = client.post("/api/agent/admin/badcases", headers=headers, content=body)
    assert first.status_code == 200
    assert replay.status_code == 401
    assert replay.json()["detail"] == "replayed admin assertion"


def test_admin_assertion_accepts_previous_rotation_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_assertion_previous_secret", "previous-secret")
    monkeypatch.setattr(settings, "admin_assertion_previous_key_id", "previous-v1")

    async def list_candidates(*_args, **_kwargs):
        return {"list": [], "totalCount": 0}

    monkeypatch.setattr(badcase_service, "list_candidates", list_candidates)
    body, headers = signed_admin_request(
        "/api/agent/admin/badcases",
        {},
        secret="previous-secret",
        key_id="previous-v1",
    )
    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/badcases", headers=headers, content=body
        )
    assert response.status_code == 200


def test_admin_assertion_enforces_route_permission():
    body, headers = signed_admin_request(
        "/api/agent/admin/badcases", {}, permissions=("support:read",)
    )
    with TestClient(_app()) as client:
        response = client.post(
            "/api/agent/admin/badcases", headers=headers, content=body
        )
    assert response.status_code == 403


def test_ai_operator_can_create_pilot_but_analyst_cannot(monkeypatch):
    async def create(**kwargs):
        return {"batchId": "pilot-1", "createdBy": kwargs["created_by"]}

    monkeypatch.setattr(pilot_batch_service, "create", create)
    path = "/api/agent/admin/createPilotBatch"
    request = {
        "name": "本地试用",
        "evidenceSource": "LOCAL_PILOT",
        "consentTextVersion": "v1",
    }
    allowed_body, allowed_headers = signed_admin_request(
        path, request, permissions=("ai:pilot",)
    )
    denied_body, denied_headers = signed_admin_request(
        path, request, permissions=("analytics:read",)
    )

    with TestClient(_app()) as client:
        allowed = client.post(path, headers=allowed_headers, content=allowed_body)
        denied = client.post(path, headers=denied_headers, content=denied_body)

    assert allowed.status_code == 200
    assert allowed.json()["data"]["batchId"] == "pilot-1"
    assert denied.status_code == 403


def test_data_analyst_can_read_metrics_but_cannot_export(monkeypatch):
    async def overview(**_kwargs):
        return {"realUserStatus": "未采集"}

    monkeypatch.setattr(pilot_metrics_service, "overview", overview)
    overview_path = "/api/agent/admin/metricsOverview"
    overview_body, overview_headers = signed_admin_request(
        overview_path, {}, permissions=("analytics:read",)
    )
    report_path = "/api/agent/admin/pilotReport"
    report_body, report_headers = signed_admin_request(
        report_path,
        {"batchId": "pilot-1", "format": "json"},
        permissions=("analytics:read",),
    )

    with TestClient(_app()) as client:
        overview_response = client.post(
            overview_path, headers=overview_headers, content=overview_body
        )
        report_response = client.post(
            report_path, headers=report_headers, content=report_body
        )

    assert overview_response.status_code == 200
    assert overview_response.json()["data"]["realUserStatus"] == "未采集"
    assert report_response.status_code == 403
