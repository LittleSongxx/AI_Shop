from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from evaluation.core.io import (
    AGENT_ROOT,
    REPO_ROOT,
    canonical_json_bytes,
    hash_named_files,
    sha256_bytes,
    sha256_file,
    utc_now,
)


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _source_files() -> list[Path]:
    files: set[Path] = set()
    for root, patterns in (
        (AGENT_ROOT / "app", ("*.py", "*.yml", "*.yaml", "*.json")),
        (AGENT_ROOT / "evaluation", ("*.py", "*.json")),
    ):
        for pattern in patterns:
            files.update(root.rglob(pattern))
    files.update(
        path
        for path in (
            AGENT_ROOT / "pyproject.toml",
            AGENT_ROOT / "requirements.lock",
        )
        if path.is_file()
    )
    excluded = {
        AGENT_ROOT / "evaluation" / "datasets" / "locks" / "consumed-final.json",
    }
    return sorted(
        path
        for path in files
        if path not in excluded and ".state" not in path.parts and ".runs" not in path.parts
    )


def _knowledge_files() -> list[Path]:
    root = REPO_ROOT / "AI_Shop-backend" / "data"
    files: list[Path] = []
    for directory in (root / "demo_knowledge_v2", root / "demo_knowledge_v3"):
        files.extend(directory.glob("*.md"))
        files.extend(directory.glob("*.json"))
    return sorted(files)


def _git_facts() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {
        "commit": commit,
        "worktreeDirty": bool(status.strip()),
    }


def provider_configuration() -> dict[str, Any]:
    from app.config.settings import get_settings

    settings = get_settings()
    return {
        "llm": {
            "configured": bool(settings.llm_api_key.strip()),
            "endpoint": _safe_endpoint(settings.llm_base_url),
            "model": settings.llm_model,
            "timeoutSeconds": settings.llm_timeout,
            "maxRetries": settings.llm_max_retries,
            "intentStructuredOutputMode": settings.intent_structured_output_mode,
        },
        "embedding": {
            "configured": bool(settings.embedding_api_key.strip()),
            "provider": settings.embedding_provider,
            "endpoint": _safe_endpoint(settings.embedding_base_url),
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
        },
        "rerank": {
            "configured": bool(settings.rerank_api_key.strip()),
            "endpoint": _safe_endpoint(settings.rerank_base_url),
            "model": settings.rerank_model,
            "format": settings.rerank_api_format,
            "topN": settings.rerank_top_n,
        },
        "runtime": {
            "javaEndpoint": _safe_endpoint(settings.java_web_url),
            "elasticsearchEndpoints": [
                _safe_endpoint(value) for value in settings.es_hosts.split(",") if value.strip()
            ],
            "elasticsearchIndex": settings.es_index,
            "vectorField": settings.es_vector_field,
            "redisHost": settings.redis_host,
            "redisPort": settings.redis_port,
            "redisDatabase": settings.redis_db,
        },
    }


def source_fingerprint() -> dict[str, Any]:
    source_files = _source_files()
    knowledge_files = _knowledge_files()
    provider = provider_configuration()
    catalog_path = (
        REPO_ROOT
        / "AI_Shop-backend"
        / "data"
        / "demo_knowledge_v3"
        / "catalog.v3.json"
    )
    return {
        "capturedAt": utc_now(),
        "git": _git_facts(),
        "source": {
            "sha256": hash_named_files(source_files),
            "fileCount": len(source_files),
        },
        "knowledge": {
            "sha256": hash_named_files(knowledge_files),
            "fileCount": len(knowledge_files),
            "catalogSha256": sha256_file(catalog_path),
        },
        "providerConfiguration": provider,
        "providerConfigurationSha256": sha256_bytes(canonical_json_bytes(provider)),
    }


def stable_fingerprint(value: dict[str, Any]) -> dict[str, Any]:
    stable = json.loads(json.dumps(value))
    stable.pop("capturedAt", None)
    if isinstance(stable.get("git"), dict):
        stable["git"].pop("worktreeDirty", None)
    return stable


def environment_facts() -> dict[str, Any]:
    return {
        "capturedAt": utc_now(),
        "executionMode": "LOCAL_FULL_STACK",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "latencyBoundary": (
            "Client-observed local full-stack latency; descriptive evidence, not a production SLO."
        ),
    }
