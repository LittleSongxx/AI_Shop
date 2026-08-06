from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.config.settings import get_settings
from app.services.episode_query_service import episode_query_service


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent.router, prefix="/api")
    return app


def test_trace_admin_api_requires_internal_token(monkeypatch):
    list_runs = AsyncMock(return_value={"list": [], "totalCount": 0})
    monkeypatch.setattr(episode_query_service, "list_runs", list_runs)

    with TestClient(_app()) as client:
        response = client.post("/api/agent/admin/traceRuns", json={})

    assert response.status_code == 401
    list_runs.assert_not_awaited()


def test_trace_admin_list_and_detail_return_sanitized_episode(monkeypatch):
    list_runs = AsyncMock(
        return_value={"list": [{"runId": "run-1"}], "totalCount": 1}
    )
    detail = AsyncMock(
        return_value={
            "runId": "run-1",
            "traceId": "1" * 32,
            "steps": [{"eventType": "TOOL_CALL", "input": {"orderId": "<ID>"}}],
        }
    )
    monkeypatch.setattr(episode_query_service, "list_runs", list_runs)
    monkeypatch.setattr(episode_query_service, "detail", detail)
    headers = {"X-Internal-Token": get_settings().internal_token}

    with TestClient(_app()) as client:
        runs = client.post(
            "/api/agent/admin/traceRuns",
            headers=headers,
            json={"pageNo": 2, "status": "FAILED"},
        )
        trace = client.post(
            "/api/agent/admin/traceDetail",
            headers=headers,
            json={"runId": "run-1"},
        )

    assert runs.status_code == 200
    assert runs.json()["data"]["list"][0]["runId"] == "run-1"
    assert trace.status_code == 200
    assert trace.json()["data"]["steps"][0]["eventType"] == "TOOL_CALL"
    list_runs.assert_awaited_once_with(
        page_no=2,
        page_size=30,
        status="FAILED",
        intent=None,
        user_id=None,
        outcome=None,
    )
    detail.assert_awaited_once_with("run-1")
