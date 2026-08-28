from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from evaluation.text2sql.dataset import (
    DEFAULT_CATALOG,
    DEFAULT_LOCK,
    load_cases,
    verify_human_gold,
)
from evaluation.text2sql.fixture import FIXED_TIMESTAMP, fingerprint
from evaluation.text2sql.io import sha256_file, utc_now, write_json, write_sha256s

REPO_ROOT = Path(__file__).resolve().parents[4]

SOURCE_PATHS = (
    "AI_Shop-backend/AI_Shop-agent/app/api/routes/agent.py",
    "AI_Shop-backend/AI_Shop-agent/app/config/settings.py",
    "AI_Shop-backend/AI_Shop-agent/app/db/analytics_pool.py",
    "AI_Shop-backend/AI_Shop-agent/app/services/analytics_catalog.py",
    "AI_Shop-backend/AI_Shop-agent/app/services/analytics_export_service.py",
    "AI_Shop-backend/AI_Shop-agent/app/services/data_analyst_service.py",
    "AI_Shop-backend/AI_Shop-agent/app/services/llm_factory.py",
    "AI_Shop-backend/AI_Shop-agent/app/observability/llm_metrics.py",
    "AI_Shop-backend/AI_Shop-agent/app/services/sql_guard.py",
    "AI_Shop-backend/AI_Shop-agent/evaluation/text2sql",
    "AI_Shop-backend/AI_Shop-admin/src/main/java/com/aishop/controller/admin/AgentMessageController.java",
    "AI_Shop-backend/AI_Shop-admin/src/main/java/com/aishop/biz/impl/AgentMessageServiceImpl.java",
    "AI_Shop-backend/AI_Shop-admin/src/main/resources/db/migration/R__current_schema.sql",
    "AI_Shop-backend/AI_Shop-common/src/main/java/com/aishop/interceptor/AppInterceptor.java",
    "AI_Shop-backend/AI_Shop-common/src/main/java/com/aishop/security",
    "AI_Shop-front/AI_Shop-admin/src/views/data/DataAnalyst.vue",
    "deploy/provision-analytics-reader.sh",
)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def _command_version(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else f"exit={result.returncode}"


def freeze_inputs(dataset: Path, output: Path) -> dict[str, Any]:
    cases = load_cases(dataset)
    if any(not case.lifecycle.startswith("HUMAN_") for case in cases):
        raise ValueError("official input freeze requires HUMAN_VERIFIED gold")
    gold_verification = verify_human_gold(dataset)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    inputs = output / "inputs"
    inputs.mkdir()
    shutil.copy2(dataset, inputs / dataset.name)
    gold_catalog = dataset.parent / DEFAULT_CATALOG.name
    shutil.copy2(gold_catalog, inputs / DEFAULT_CATALOG.name)
    shutil.copy2(DEFAULT_LOCK, inputs / DEFAULT_LOCK.name)
    source_root = output / "source"
    copied: dict[str, str] = {}
    for relative in SOURCE_PATHS:
        source = REPO_ROOT / relative
        if source.is_dir():
            for child in sorted(source.rglob("*")):
                if not child.is_file() or "__pycache__" in child.parts:
                    continue
                destination = source_root / child.relative_to(REPO_ROOT)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, destination)
                copied[str(child.relative_to(REPO_ROOT))] = sha256_file(child)
        elif source.is_file():
            destination = source_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied[relative] = sha256_file(source)
    path_args = ["--", *SOURCE_PATHS]
    (output / "git-status.txt").write_text(_git("status", "--short"), encoding="utf-8")
    (output / "relevant-working-tree.patch").write_text(
        _git("diff", "--binary", *path_args), encoding="utf-8"
    )
    settings = get_settings()
    runtime_config = {
        "appVersion": settings.app_version,
        "appEnv": settings.app_env,
        "llmBaseUrl": settings.llm_base_url,
        "llmModel": settings.llm_model,
        "llmFallbackModel": settings.llm_fallback_model,
        "llmTimeout": settings.llm_timeout,
        "llmMaxRetries": settings.llm_max_retries,
        "llmPricingCnyPerMillion": settings.llm_pricing_cny_per_million_json,
        "analyticsMaxRows": settings.analytics_max_rows,
        "analyticsMaxResultBytes": settings.analytics_max_result_bytes,
        "analyticsCursorTtlSeconds": settings.analytics_cursor_ttl_seconds,
        "analyticsExportMaxRows": settings.analytics_export_max_rows,
        "analyticsMaxDays": settings.analytics_max_days,
        "analyticsQueryTimeoutMs": settings.analytics_query_timeout_ms,
        "analyticsModelTimeoutSeconds": settings.analytics_model_timeout_seconds,
        "analyticsRequestTimeoutSeconds": settings.analytics_request_timeout_seconds,
        "analyticsEvalFixedNow": settings.analytics_eval_fixed_now,
        "effectiveEvaluationFixedNow": f"{FIXED_TIMESTAMP} Asia/Shanghai",
    }
    manifest = {
        "schemaVersion": "aishop-text2sql-input-freeze/v0",
        "createdAt": utc_now(),
        "git": {
            "head": _git("rev-parse", "HEAD").strip(),
            "branch": _git("branch", "--show-current").strip(),
            "statusSha256": sha256_file(output / "git-status.txt"),
            "relevantPatchSha256": sha256_file(output / "relevant-working-tree.patch"),
        },
        "dataset": {
            "path": dataset.name,
            "sha256": sha256_file(dataset),
            "caseCount": len(cases),
            "lifecycle": sorted({case.lifecycle for case in cases}),
        },
        "catalogSha256": sha256_file(gold_catalog),
        "goldVerification": gold_verification["checks"],
        "candidateLockSha256": sha256_file(DEFAULT_LOCK),
        "runtimeConfigRedacted": runtime_config,
        "promptArtifacts": {
            "dataAnalystServiceSha256": sha256_file(
                REPO_ROOT
                / "AI_Shop-backend/AI_Shop-agent/app/services/data_analyst_service.py"
            ),
            "runtimeCatalogSha256": sha256_file(
                REPO_ROOT
                / "AI_Shop-backend/AI_Shop-agent/app/services/analytics_catalog.py"
            ),
        },
        "fixture": fingerprint(),
        "sourceFiles": copied,
        "versions": {
            "python": sys.version.splitlines()[0],
            "java": _command_version(["java", "-version"]),
            "maven": _command_version(["mvn", "-version"]),
            "docker": _command_version(["docker", "version", "--format", "{{.Server.Version}}"]),
        },
        "secretsIncluded": False,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    write_json(output / "manifest.json", manifest)
    write_sha256s(output)
    return {"output": str(output), **manifest}
