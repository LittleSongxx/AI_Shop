from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import httpx

from evaluation.core.contracts import EvaluationCase, PreflightError
from evaluation.core.fingerprints import provider_configuration
from evaluation.core.io import utc_now


def agent_base_url() -> str:
    from app.config.settings import get_settings

    settings = get_settings()
    return os.getenv(
        "AI_EVAL_AGENT_URL",
        os.getenv("AGENT_BASE_URL", f"http://127.0.0.1:{settings.app_port}"),
    ).rstrip("/")


async def _probe_http(client: httpx.AsyncClient, name: str, url: str) -> dict[str, Any]:
    try:
        response = await client.get(url, timeout=10)
        response.raise_for_status()
        return {
            "name": name,
            "ok": True,
            "statusCode": response.status_code,
            "url": url,
        }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "error": type(exc).__name__,
            "url": url,
        }


async def run_preflight(cases: Sequence[EvaluationCase]) -> dict[str, Any]:
    from app.config.settings import get_settings
    from app.db.pool import acquire
    from app.rag.embedding import embed_text, embedding_evaluation_scope
    from app.rag.retriever import rag_retriever, rerank_evaluation_scope
    from app.services.llm_factory import chat_llm_config, chat_llm_for_config
    from app.services.redis_service import redis_service
    from evaluation.core.agent_fixtures import verify_write_fixture_prerequisites
    from evaluation.core.catalog import verify_live_catalog

    settings = get_settings()
    required = {provider for case in cases for provider in case.required_providers}
    configuration = provider_configuration()
    missing = [
        provider
        for provider in ("llm", "embedding", "rerank")
        if provider in required and not configuration[provider]["configured"]
    ]
    if missing:
        raise PreflightError(f"required providers are not configured: {missing}")
    checks: list[dict[str, Any]] = []
    async with httpx.AsyncClient(trust_env=False) as client:
        checks.append(
            await _probe_http(
                client,
                "java-gateway",
                f"{settings.java_web_url.rstrip('/')}/actuator/health",
            )
        )
        es_host = settings.es_hosts.split(",")[0].strip().rstrip("/")
        checks.append(await _probe_http(client, "elasticsearch", f"{es_host}/_cluster/health"))
        if any(case.domain.value == "agent" for case in cases):
            checks.append(
                await _probe_http(
                    client,
                    "agent-readiness",
                    f"{agent_base_url()}/health/ready",
                )
            )
    try:
        # The live Agent connects Redis during FastAPI lifespan, but the
        # standalone evaluator has no lifespan.  Ensure the client exists
        # before probing it; otherwise every CLI preflight falsely fails.
        await redis_service.ensure_connected()
        checks.append({"name": "redis", "ok": bool(await redis_service.client.ping())})
    except Exception as exc:
        checks.append({"name": "redis", "ok": False, "error": type(exc).__name__})
    try:
        async with acquire() as cursor:
            await cursor.execute("SELECT 1 AS ok")
            row = await cursor.fetchone()
        checks.append({"name": "mysql", "ok": bool(row and row.get("ok") == 1)})
    except Exception as exc:
        checks.append({"name": "mysql", "ok": False, "error": type(exc).__name__})
    try:
        checks.append(
            {
                "name": "product-catalog",
                "ok": True,
                "facts": await verify_live_catalog(),
            }
        )
    except Exception as exc:
        checks.append({"name": "product-catalog", "ok": False, "error": type(exc).__name__})

    if "embedding" in required:
        try:
            with embedding_evaluation_scope(bypass_cache=True) as stats:
                vector = await embed_text("AI Shop evaluation provider preflight")
            snapshot = stats.snapshot()
            ok = (
                vector is not None
                and len(vector) == settings.embedding_dimensions
                and snapshot["providerSuccesses"] == 1
            )
            checks.append({"name": "embedding-provider", "ok": ok, "facts": snapshot})
        except Exception as exc:
            checks.append({"name": "embedding-provider", "ok": False, "error": type(exc).__name__})
    if "rerank" in required:
        try:
            products = [
                {"product_id": "preflight-1", "product_name": "无线耳机"},
                {"product_id": "preflight-2", "product_name": "机械键盘"},
            ]
            with rerank_evaluation_scope() as stats:
                await rag_retriever.rerank_products("耳机", products, 2)
            snapshot = stats.snapshot()
            ok = snapshot["providerSuccesses"] == 1 and snapshot["fallbackCount"] == 0
            checks.append({"name": "rerank-provider", "ok": ok, "facts": snapshot})
        except Exception as exc:
            checks.append({"name": "rerank-provider", "ok": False, "error": type(exc).__name__})
    if "llm" in required:
        try:
            llm = chat_llm_for_config(chat_llm_config(disable_thinking=True))
            response = await llm.ainvoke("只回复 OK")
            ok = bool(str(getattr(response, "content", "") or "").strip())
            checks.append(
                {
                    "name": "llm-provider",
                    "ok": ok,
                    "model": settings.llm_model,
                }
            )
        except Exception as exc:
            checks.append({"name": "llm-provider", "ok": False, "error": type(exc).__name__})
    write_fixture_cases = [
        case
        for case in cases
        if case.domain.value == "agent"
        and isinstance(case.state_fixture, dict)
        and case.state_fixture.get("provision")
    ]
    if write_fixture_cases:
        try:
            facts = await verify_write_fixture_prerequisites()
            checks.append({"name": "agent-write-fixture-boundary", "ok": True, "facts": facts})
        except Exception as exc:
            checks.append(
                {
                    "name": "agent-write-fixture-boundary",
                    "ok": False,
                    "error": type(exc).__name__,
                }
            )
    failed = [check["name"] for check in checks if not check.get("ok")]
    if failed:
        raise PreflightError(f"required preflight checks failed: {failed}")
    return {
        "schemaVersion": "aishop-evaluation-preflight/v2",
        "checkedAt": utc_now(),
        "passed": True,
        "requiredProviders": sorted(required),
        "configuration": configuration,
        "checks": checks,
    }
