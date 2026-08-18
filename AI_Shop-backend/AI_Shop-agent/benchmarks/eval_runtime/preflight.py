"""Non-destructive dependency checks run before any formal holdout claim."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .contracts import FailureClass


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    required: bool
    status: str
    failure_class: FailureClass = FailureClass.NONE
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "status": self.status,
            "failureClass": self.failure_class.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PreflightResult:
    suite: str
    run_id: str
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(item.status == "PASS" for item in self.checks if item.required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "suite": self.suite,
            "runId": self.run_id,
            "status": "READY" if self.passed else "BLOCKED",
            "checks": [item.to_dict() for item in self.checks],
        }


async def _call_check(
    name: str, check: Callable[[], Awaitable[Any] | Any], *, required: bool
) -> PreflightCheck:
    try:
        value = check()
        if asyncio.iscoroutine(value):
            value = await value
        if isinstance(value, tuple):
            ok, detail = bool(value[0]), str(value[1]) if len(value) > 1 else None
        else:
            ok, detail = bool(value), None
        return PreflightCheck(
            name,
            required,
            "PASS" if ok else "FAIL",
            FailureClass.NONE if ok else FailureClass.DEPENDENCY_ERROR,
            detail,
        )
    except Exception as exc:  # diagnostics must continue through every check
        return PreflightCheck(
            name,
            required,
            "FAIL",
            FailureClass.DEPENDENCY_ERROR,
            f"{type(exc).__name__}: {str(exc)[:240]}",
        )


async def run_checks(
    suite: str,
    run_id: str,
    checks: list[tuple[str, bool, Callable[[], Awaitable[Any] | Any]]],
) -> PreflightResult:
    results = await asyncio.gather(
        *(
            _call_check(name, check, required=required)
            for name, required, check in checks
        )
    )
    return PreflightResult(suite, run_id, tuple(results))


def static_check(
    name: str,
    value: bool,
    *,
    required: bool = True,
    detail: str | None = None,
) -> PreflightCheck:
    return PreflightCheck(
        name,
        required,
        "PASS" if value else "FAIL",
        FailureClass.NONE if value else FailureClass.DEPENDENCY_ERROR,
        detail,
    )


async def _redis_probe() -> tuple[bool, str]:
    from app.config.settings import get_settings
    from app.services.redis_service import RedisService

    service = RedisService()
    await service.connect()
    try:
        await service.client.ping()
        settings = get_settings()
        return True, f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    finally:
        await service.close()


async def _http_probe(url: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[bool, str]:
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
        response = (
            await client.post(url, json=body or {})
            if method == "POST"
            else await client.get(url)
        )
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    return True, f"HTTP {response.status_code}"


async def _elasticsearch_probe() -> tuple[bool, str]:
    from app.config.settings import get_settings

    settings = get_settings()
    host = str(settings.es_hosts).split(",", 1)[0].rstrip("/")
    return await _http_probe(f"{host}/_cluster/health")


async def _java_probe() -> tuple[bool, str]:
    from app.config.settings import get_settings

    return await _http_probe(f"{get_settings().java_web_url.rstrip('/')}/actuator/health")


async def _java_snapshot_probe() -> tuple[bool, str]:
    from app.services.java_internal_client import java_internal_client

    payload = await java_internal_client.snapshot_batch([])
    return (
        isinstance(payload, dict)
        and all(
            key in payload
            for key in ("products", "skus", "property_values", "total_stocks")
        )
    ), (
        "snapshotBatch probe ok" if isinstance(payload, dict) else "invalid response"
    )


async def _mcp_probe() -> tuple[bool, str]:
    from app.services.mcp_streamable_client import mcp_streamable_client

    ok = await mcp_streamable_client.check_contract()
    return bool(ok), "contract ok" if ok else "contract unavailable"


async def build_suite_preflight(
    suite: str,
    run_id: str,
    *,
    api_base_url: str | None = None,
    require_java: bool | None = None,
) -> PreflightResult:
    """Run safe probes only; this function never reads a holdout or claims it."""

    from app.config.settings import get_settings

    settings = get_settings()
    checks: list[tuple[str, bool, Callable[[], Awaitable[Any] | Any]]] = []
    checks.append(("redis", suite in {"search-v3", "rag-v5", "agent-v2"}, _redis_probe))
    checks.append(("elasticsearch", suite in {"search-v3", "rag-v5"}, _elasticsearch_probe))

    provider_required = suite in {"search-v3", "rag-v5"}
    embedding_ready = settings.embedding_provider == "local" or bool(settings.embedding_api_key.strip())
    checks.append(
        (
            "embedding-configuration",
            provider_required,
            lambda: (embedding_ready, "local" if settings.embedding_provider == "local" else "api key configured"),
        )
    )
    checks.append(
        (
            "rerank-configuration",
            provider_required and bool(settings.rerank_required),
            lambda: (bool(settings.rerank_api_key.strip()), "api key configured"),
        )
    )

    java_required = suite == "search-v3" if require_java is None else require_java
    checks.append(("java-gateway", java_required, _java_probe))
    checks.append(("java-snapshot-batch", java_required, _java_snapshot_probe))
    if suite == "agent-v2":
        base = (api_base_url or "http://127.0.0.1:7050").rstrip("/")

        async def agent_dependencies_probe() -> tuple[bool, str]:
            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0)) as client:
                response = await client.get(f"{base}/health/dependencies")
            if response.status_code >= 400:
                return False, f"HTTP {response.status_code}"
            payload = response.json()
            required = ("llm", "embeddingProductionReady", "rerank", "javaGateway", "mcp")
            missing = [name for name in required if payload.get(name) is not True]
            return (
                not missing,
                "all production providers ready"
                if not missing
                else "missing: " + ",".join(missing),
            )

        checks.extend(
            [
                ("agent-readiness", True, lambda: _http_probe(f"{base}/health/ready")),
                ("agent-dependencies", True, agent_dependencies_probe),
                ("mcp-contract", True, _mcp_probe),
            ]
        )
    return await run_checks(suite, run_id, checks)
