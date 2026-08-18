"""Build immutable, non-secret identity manifests for evaluation runs."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import EvalRunManifest, EvidenceLevel
from .evidence import EvidenceError, EvidenceStore
from .registry import SuiteDefinition

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else "UNKNOWN"


def _safe_host(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(text if "://" in text else f"https://{text}")
        return parsed.netloc or parsed.path.split("/", 1)[0]
    except Exception:
        return None


def _provider_bundle(suite: SuiteDefinition) -> dict[str, Any]:
    """Return provider/model fingerprints without exposing secrets."""

    try:
        from app.config.settings import get_settings

        settings = get_settings()
        llm_key = bool(str(settings.llm_api_key or "").strip())
        embedding_key = bool(str(settings.embedding_api_key or "").strip())
        rerank_key = bool(str(settings.rerank_api_key or "").strip())
        visual_key = bool(str(settings.visual_api_key or "").strip())
        return {
            "llm": {
                "configured": llm_key,
                "model": str(settings.llm_model),
                "endpointHost": _safe_host(settings.llm_base_url),
            },
            "embedding": {
                "configured": str(settings.embedding_provider) == "local" or embedding_key,
                "provider": str(settings.embedding_provider),
                "model": str(settings.embedding_model),
                "endpointHost": _safe_host(settings.embedding_base_url),
            },
            "rerank": {
                "configured": rerank_key,
                "required": bool(settings.rerank_required),
                "model": str(settings.rerank_model),
                "endpointHost": _safe_host(settings.rerank_base_url),
            },
            "visual": {
                "configured": visual_key,
                "enabled": bool(settings.visual_search_enabled),
                "groundingModel": str(settings.visual_grounding_model),
                "embeddingModel": str(settings.visual_embedding_model),
                "rerankModel": str(settings.visual_rerank_model),
            },
            "fallbackPolicy": (
                "FORBIDDEN_FOR_FORMAL_LIVE"
                if suite.contract.get("profile") == "local-live"
                else "COMPATIBILITY_ONLY"
            ),
        }
    except Exception as exc:
        return {
            "settings": "UNAVAILABLE",
            "errorType": type(exc).__name__,
            "fallbackPolicy": (
                "FORBIDDEN_FOR_FORMAL_LIVE"
                if suite.contract.get("profile") == "local-live"
                else "COMPATIBILITY_ONLY"
            ),
        }


def _dataset_lock(suite: SuiteDefinition) -> dict[str, Any]:
    candidates: list[str] = []
    for key in ("suiteLock", "datasetLock", "lock"):
        value = suite.contract.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    unique = list(dict.fromkeys(candidates))
    return {
        "files": [
            {
                "path": relative,
                "sha256": _sha256(PROJECT_ROOT / relative),
                "present": (PROJECT_ROOT / relative).is_file(),
            }
            for relative in unique
        ],
        "declaredDatasetSha256": suite.contract.get("datasetSha256"),
        "declaredKnownDatasetSha256": suite.contract.get("knownDatasetSha256"),
    }


def evidence_level_for(suite: SuiteDefinition, *, execution_mode: str) -> EvidenceLevel:
    if suite.contract.get("legacy") or execution_mode in {"deterministic", "replay"}:
        return EvidenceLevel.E1 if execution_mode == "deterministic" else EvidenceLevel.E2
    if execution_mode in {"real-user", "pilot"}:
        return EvidenceLevel.E4
    return EvidenceLevel.E3


def build_manifest(
    suite: SuiteDefinition,
    run_id: str,
    lifecycle: dict[str, str],
    *,
    fixture_snapshot_id: str | None = None,
    knowledge_release: str | int | None = None,
    execution_mode: str = "fresh",
) -> EvalRunManifest:
    return EvalRunManifest(
        suite=suite.suite_id,
        run_id=run_id,
        git_sha=_git_sha(),
        dataset_lock=_dataset_lock(suite),
        provider_bundle=_provider_bundle(suite),
        fixture_snapshot_id=fixture_snapshot_id,
        knowledge_release=knowledge_release,
        lifecycle={
            "phase": str(lifecycle.get("phase") or "VALIDATED"),
            "state": str(lifecycle.get("state") or "IN_PROGRESS"),
        },
        evidence_level=evidence_level_for(suite, execution_mode=execution_mode),
        execution_mode=execution_mode,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def ensure_run_manifest(
    store: EvidenceStore,
    suite: SuiteDefinition,
    run_id: str,
    lifecycle: dict[str, str],
    *,
    fixture_snapshot_id: str | None = None,
    knowledge_release: str | int | None = None,
    execution_mode: str = "fresh",
) -> dict[str, Any]:
    manifest = build_manifest(
        suite,
        run_id,
        lifecycle,
        fixture_snapshot_id=fixture_snapshot_id,
        knowledge_release=knowledge_release,
        execution_mode=execution_mode,
    ).to_dict()
    path = store.path("run-manifest.json")
    if path.is_file():
        import json

        existing = json.loads(path.read_text(encoding="utf-8"))
        identity_keys = (
            "suite",
            "runId",
            "gitSha",
            "datasetLock",
            "providerBundle",
            "fixtureSnapshotId",
            "knowledgeRelease",
            "evidenceLevel",
            "executionMode",
        )
        if any(existing.get(key) != manifest.get(key) for key in identity_keys):
            raise EvidenceError("run manifest identity changed during an evaluation")
        return existing
    store.write_json("run-manifest.json", manifest)
    return manifest


def write_final_manifest(
    store: EvidenceStore,
    suite: SuiteDefinition,
    run_id: str,
    lifecycle: dict[str, str],
    *,
    stage_status: str,
    fixture_snapshot_id: str | None = None,
    knowledge_release: str | int | None = None,
    execution_mode: str = "fresh",
) -> dict[str, Any]:
    import json

    manifest = build_manifest(
        suite,
        run_id,
        lifecycle,
        fixture_snapshot_id=fixture_snapshot_id,
        knowledge_release=knowledge_release,
        execution_mode=execution_mode,
    ).to_dict()
    manifest["stageStatus"] = stage_status
    manifest["terminal"] = lifecycle.get("phase") in {"PACKAGED", "BLOCKED", "FAILED_RETAINED"}
    path = store.path("manifest.json")
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise EvidenceError("final evaluation manifest is immutable")
        return existing
    store.write_json("manifest.json", manifest)
    return manifest
