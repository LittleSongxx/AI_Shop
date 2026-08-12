from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import agent
from app.services.data_analyst_service import data_analyst_service
from app.services.episode_query_service import episode_query_service
from app.services.episode_review_service import episode_review_service
from app.services.inventory_ops_service import inventory_ops_service
from tests.admin_assertion_helpers import signed_admin_request


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
    with TestClient(_app()) as client:
        runs_body, runs_headers = signed_admin_request(
            "/api/agent/admin/traceRuns", {"pageNo": 2, "status": "FAILED"}
        )
        runs = client.post(
            "/api/agent/admin/traceRuns",
            headers=runs_headers,
            content=runs_body,
        )
        trace_body, trace_headers = signed_admin_request(
            "/api/agent/admin/traceDetail", {"runId": "run-1"}
        )
        trace = client.post(
            "/api/agent/admin/traceDetail",
            headers=trace_headers,
            content=trace_body,
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
        agent_id=None,
        run_scope="ROOT",
    )
    detail.assert_awaited_once_with("run-1")


def test_episode_review_admin_api_forwards_internal_reviewer(monkeypatch):
    review = AsyncMock(
        return_value={"runId": "run-1", "datasetEligible": "APPROVED"}
    )
    monkeypatch.setattr(episode_review_service, "review", review)
    with TestClient(_app()) as client:
        body, headers = signed_admin_request(
            "/api/agent/admin/reviewEpisode",
            {
                "runId": "run-1",
                "datasetEligible": "APPROVED",
                "reviewer": "attacker-controlled",
                "note": "完整售后终态",
            },
        )
        response = client.post(
            "/api/agent/admin/reviewEpisode",
            headers=headers,
            content=body,
        )

    assert response.status_code == 200
    assert response.json()["data"]["datasetEligible"] == "APPROVED"
    review.assert_awaited_once_with(
        "run-1",
        "APPROVED",
        "admin-session",
        note="完整售后终态",
    )


def test_admin_agents_require_and_forward_java_session_identity(monkeypatch):
    ask = AsyncMock(return_value={"status": "SUCCEEDED", "runId": "analysis-1"})
    suggestions = AsyncMock(return_value={"status": "SUCCEEDED", "runId": "inventory-1"})
    monkeypatch.setattr(data_analyst_service, "ask", ask)
    monkeypatch.setattr(inventory_ops_service, "suggestions", suggestions)
    with TestClient(_app()) as client:
        analysis_body, analysis_headers = signed_admin_request(
            "/api/agent/admin/dataAnalyst/ask",
            {"question": "最近七天销售额", "adminId": "attacker"},
        )
        analysis = client.post(
            "/api/agent/admin/dataAnalyst/ask",
            headers=analysis_headers,
            content=analysis_body,
        )
        inventory_body, inventory_headers = signed_admin_request(
            "/api/agent/admin/inventoryOps/suggestions",
            {"adminId": "attacker", "lookbackDays": 30, "limit": 20},
        )
        inventory = client.post(
            "/api/agent/admin/inventoryOps/suggestions",
            headers=inventory_headers,
            content=inventory_body,
        )

    assert analysis.json()["data"]["runId"] == "analysis-1"
    assert inventory.json()["data"]["runId"] == "inventory-1"
    ask.assert_awaited_once_with("最近七天销售额", admin_id="admin-session")
    suggestions.assert_awaited_once_with(
        admin_id="admin-session",
        lookback_days=30,
        limit=20,
    )
