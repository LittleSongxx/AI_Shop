from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.health_service import HealthService


@pytest.mark.asyncio
async def test_readiness_requires_all_core_dependencies(monkeypatch):
    service = HealthService()
    monkeypatch.setattr(service, "_check_mysql", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_redis", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_rabbitmq", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_worker", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_java_gateway", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_mcp", AsyncMock(return_value=True))
    monkeypatch.setattr(
        service,
        "_check_mcp_runtime",
        AsyncMock(return_value={"ok": True, "sourceFingerprintMatch": True}),
    )
    monkeypatch.setattr(
        service,
        "_check_es_mapping",
        AsyncMock(return_value={"ok": True, "field": "embedding", "expectedDimensions": 1024}),
    )

    ready = await service.check_readiness()

    assert ready["ready"] is True
    assert ready["status"] == "ready"
    assert ready["checks"]["elasticsearchMapping"]["ok"] is True

    service._check_worker.return_value = False
    degraded = await service.check_readiness()
    assert degraded["ready"] is False
    assert degraded["status"] == "not_ready"


@pytest.mark.asyncio
async def test_model_provider_health_is_diagnostic_only(monkeypatch):
    service = HealthService()
    settings = SimpleNamespace(
        llm_api_key="",
        embedding_api_key="",
        embedding_provider="openai",
        rerank_api_key="",
    )
    monkeypatch.setattr("app.services.health_service.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_check_es_mapping", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(service, "_check_java_gateway", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_mcp", AsyncMock(return_value=True))

    dependencies = await service.check_dependencies()

    assert dependencies["llm"] is False
    assert dependencies["embedding"] is False
    assert dependencies["rerank"] is False
    assert dependencies["elasticsearch"]["ok"] is True
    assert dependencies["visualSearch"] == {"state": "DISABLED"}
    assert dependencies["javaGateway"] is True
    assert dependencies["mcp"] is True
    assert dependencies["toolManifest"]["health"] == "READY"
    assert dependencies["toolManifest"]["timeoutSeconds"] == 20.0


@pytest.mark.asyncio
async def test_local_embedding_is_available_but_not_production_ready(monkeypatch):
    service = HealthService()
    settings = SimpleNamespace(
        llm_api_key="",
        embedding_api_key="",
        embedding_provider="local",
        rerank_api_key="",
    )
    monkeypatch.setattr("app.services.health_service.get_settings", lambda: settings)
    monkeypatch.setattr(service, "_check_es_mapping", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(service, "_check_java_gateway", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_check_mcp", AsyncMock(return_value=True))

    dependencies = await service.check_dependencies()

    assert dependencies["embedding"] is True
    assert dependencies["embeddingProvider"] == "local"
    assert dependencies["embeddingProductionReady"] is False
