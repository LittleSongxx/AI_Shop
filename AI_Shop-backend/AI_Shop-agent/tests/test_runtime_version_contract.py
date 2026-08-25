from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import health_service as health_module
from app.services.health_service import HealthService
from app.services.redis_service import redis_service
from app.services.runtime_identity import source_fingerprint
from evaluation.core.preflight import _probe_agent_readiness


def _identity(role: str, sha: str = "a" * 64) -> dict:
    return {
        "schemaVersion": "aishop-runtime-identity/v1",
        "processRole": role,
        "startedAt": "2026-08-24T00:00:00.000Z",
        "pid": 123,
        "source": {
            "scope": "agent-app-source-and-runtime-dependencies/v1",
            "sha256": sha,
            "fileCount": 12,
        },
    }


def test_runtime_source_fingerprint_is_safe_and_content_addressed():
    value = source_fingerprint()

    assert value["scope"] == "agent-app-source-and-runtime-dependencies/v1"
    assert len(value["sha256"]) == 64
    assert value["fileCount"] > 0
    assert "endpoint" not in str(value).lower()
    assert "token" not in str(value).lower()


@pytest.mark.asyncio
async def test_worker_health_rejects_live_but_stale_source(monkeypatch: pytest.MonkeyPatch):
    service = HealthService()
    monkeypatch.setattr(
        health_module, "current_runtime_identity", lambda: _identity("api", "a" * 64)
    )
    monkeypatch.setattr(redis_service, "worker_is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        redis_service,
        "worker_heartbeat_metadata",
        AsyncMock(return_value={**_identity("worker", "b" * 64), "workerId": "worker-1"}),
    )

    result = await service._check_worker()

    assert result["alive"] is True
    assert result["sourceFingerprintMatch"] is False
    assert result["ok"] is False
    assert result["reason"] == "WORKER_HEARTBEAT_METADATA_MISSING_OR_SOURCE_MISMATCH"


@pytest.mark.asyncio
async def test_worker_health_accepts_matching_attested_source(monkeypatch: pytest.MonkeyPatch):
    service = HealthService()
    api = _identity("api")
    worker = {**_identity("worker"), "workerId": "worker-1"}
    monkeypatch.setattr(health_module, "current_runtime_identity", lambda: api)
    monkeypatch.setattr(redis_service, "worker_is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(
        redis_service, "worker_heartbeat_metadata", AsyncMock(return_value=worker)
    )

    result = await service._check_worker()

    assert result["ok"] is True
    assert result["sourceFingerprintMatch"] is True
    assert result["workerRuntimeIdentity"]["workerId"] == "worker-1"


@pytest.mark.asyncio
async def test_mcp_health_rejects_reachable_but_stale_source(monkeypatch: pytest.MonkeyPatch):
    service = HealthService()
    monkeypatch.setattr(
        health_module, "current_runtime_identity", lambda: _identity("api", "a" * 64)
    )
    monkeypatch.setattr(
        health_module.mcp_streamable_client,
        "runtime_identity",
        AsyncMock(return_value=_identity("mcp", "b" * 64)),
    )

    result = await service._check_mcp_runtime()

    assert result["ok"] is False
    assert result["sourceFingerprintMatch"] is False
    assert result["reason"] == "MCP_RUNTIME_IDENTITY_MISSING_OR_SOURCE_MISMATCH"


@pytest.mark.asyncio
async def test_mcp_health_accepts_matching_attested_source(monkeypatch: pytest.MonkeyPatch):
    service = HealthService()
    api = _identity("api")
    mcp = _identity("mcp")
    monkeypatch.setattr(health_module, "current_runtime_identity", lambda: api)
    monkeypatch.setattr(
        health_module.mcp_streamable_client,
        "runtime_identity",
        AsyncMock(return_value=mcp),
    )

    result = await service._check_mcp_runtime()

    assert result["ok"] is True
    assert result["mcpRuntimeIdentity"]["processRole"] == "mcp"


class _Response:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, payload: dict):
        self._payload = payload

    async def get(self, _url: str, timeout: int):
        assert timeout == 10
        return _Response(self._payload)


@pytest.mark.asyncio
async def test_agent_preflight_requires_readiness_source_match():
    api = _identity("api")
    worker = {**_identity("worker"), "workerId": "worker-1"}
    mcp = _identity("mcp")
    payload = {
        "runtimeIdentity": api,
        "checks": {
            "mcp": True,
            "worker": {
                "ok": True,
                "sourceFingerprintMatch": True,
                "workerRuntimeIdentity": worker,
            },
            "mcpRuntime": {
                "ok": True,
                "sourceFingerprintMatch": True,
                "mcpRuntimeIdentity": mcp,
            },
        },
    }

    result = await _probe_agent_readiness(_Client(payload), "http://agent.test/health/ready")

    assert result["ok"] is True
    assert result["facts"]["apiRuntimeIdentity"]["source"]["sha256"] == "a" * 64
    assert result["facts"]["mcpRuntimeIdentity"]["processRole"] == "mcp"

    stale_workspace = await _probe_agent_readiness(
        _Client(payload),
        "http://agent.test/health/ready",
        expected_source={
            "scope": "agent-app-source-and-runtime-dependencies/v1",
            "sha256": "b" * 64,
            "fileCount": 12,
        },
    )

    assert stale_workspace["ok"] is False
    assert stale_workspace["facts"]["processSourceAgreement"] is True
    assert stale_workspace["facts"]["runtimeMatchesExpectedSource"] is False

    payload["checks"]["worker"]["workerRuntimeIdentity"] = {
        **_identity("worker", "b" * 64),
        "workerId": "worker-1",
    }
    mismatch = await _probe_agent_readiness(
        _Client(payload), "http://agent.test/health/ready"
    )

    assert mismatch["ok"] is False

    payload["checks"]["worker"]["workerRuntimeIdentity"] = worker
    payload["checks"]["mcpRuntime"]["mcpRuntimeIdentity"] = _identity("mcp", "b" * 64)
    mcp_mismatch = await _probe_agent_readiness(
        _Client(payload), "http://agent.test/health/ready"
    )

    assert mcp_mismatch["ok"] is False
