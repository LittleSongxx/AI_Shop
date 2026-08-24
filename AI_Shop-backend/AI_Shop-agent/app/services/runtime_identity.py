"""Safe per-process source identity used by the serving readiness contract.

The HTTP API only enqueues Agent work; the graph executes in a separate Worker
and its business tools execute in a separate MCP process.  A live Worker
heartbeat or a reachable MCP endpoint alone therefore cannot prove that all
serving processes loaded the same source tree.  This module freezes a small,
credential-free fingerprint at process startup so readiness and evaluation
preflight can fail closed on an API/Worker/MCP code-version mismatch.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNTIME_IDENTITY_SCHEMA = "aishop-runtime-identity/v1"
_AGENT_ROOT = Path(__file__).resolve().parents[2]
_identity: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _source_files() -> list[Path]:
    """Return executable Agent sources, never runtime state or credentials."""

    app_root = _AGENT_ROOT / "app"
    files = set(app_root.rglob("*.py"))
    files.update(app_root.rglob("*.yml"))
    files.update(app_root.rglob("*.yaml"))
    files.update(app_root.rglob("*.json"))
    files.update(
        path
        for path in (
            _AGENT_ROOT / "pyproject.toml",
            _AGENT_ROOT / "requirements.lock",
        )
        if path.is_file()
    )
    return sorted(path for path in files if path.is_file())


def source_fingerprint() -> dict[str, Any]:
    """Hash file names and contents with stable path boundaries.

    The result intentionally excludes configuration, endpoint URLs, user data,
    datasets, and anything under runtime/evidence directories.  It is safe to
    put in a health response and immutable evidence package.
    """

    files = _source_files()
    digest = hashlib.sha256()
    for path in files:
        name = path.relative_to(_AGENT_ROOT).as_posix().encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return {
        "scope": "agent-app-source-and-runtime-dependencies/v1",
        "sha256": digest.hexdigest(),
        "fileCount": len(files),
    }


def freeze_runtime_identity(process_role: str) -> dict[str, Any]:
    """Capture an identity once for this process and return a detached copy."""

    normalized_role = str(process_role or "").strip().lower()
    if normalized_role not in {"api", "worker", "mcp"}:
        raise ValueError("runtime process role must be api, worker, or mcp")

    global _identity
    if _identity is None:
        _identity = {
            "schemaVersion": RUNTIME_IDENTITY_SCHEMA,
            "processRole": normalized_role,
            "startedAt": _utc_now(),
            "pid": os.getpid(),
            "source": source_fingerprint(),
        }
    elif _identity.get("processRole") != normalized_role:
        raise RuntimeError(
            "one process cannot freeze multiple runtime identities"
        )
    return _public_identity(_identity)


def current_runtime_identity() -> dict[str, Any] | None:
    """Return the frozen identity without implicitly capturing a new one."""

    return _public_identity(_identity) if _identity is not None else None


def _public_identity(value: dict[str, Any]) -> dict[str, Any]:
    source = value.get("source") or {}
    return {
        "schemaVersion": str(value.get("schemaVersion") or RUNTIME_IDENTITY_SCHEMA),
        "processRole": str(value.get("processRole") or ""),
        "startedAt": str(value.get("startedAt") or ""),
        "pid": int(value.get("pid") or 0),
        "source": {
            "scope": str(source.get("scope") or ""),
            "sha256": str(source.get("sha256") or ""),
            "fileCount": int(source.get("fileCount") or 0),
        },
    }
