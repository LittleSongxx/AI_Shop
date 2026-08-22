"""Load the ports owned by ``start.sh`` before importing production clients.

The local launcher persists the selected ports in ``run/runtime.env``.  That
file is intentionally not the Agent's dotenv file and therefore does not
contain derived URLs such as ``JAVA_WEB_URL``.  Evaluation is a separate
process, so it must reconstruct those values itself instead of silently
falling back to the development defaults.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from evaluation.core.io import REPO_ROOT

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _runtime_path(path: Path | None = None) -> Path:
    explicit = str(os.getenv("AISHOP_RUNTIME_ENV", "")).strip()
    return path or (Path(explicit) if explicit else REPO_ROOT / "run" / "runtime.env")


def _read_assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid runtime env at {path}:{line_number}: {exc}") from exc
        if len(parts) != 1 or "=" not in parts[0]:
            raise ValueError(f"invalid runtime env assignment at {path}:{line_number}")
        key, value = parts[0].split("=", 1)
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid runtime env key at {path}:{line_number}: {key!r}")
        values[key] = value
    return values


def _setdefault(key: str, value: str | None) -> None:
    if value is not None and value != "":
        os.environ.setdefault(key, value)


def load_runtime_environment(path: Path | None = None) -> dict[str, object]:
    """Load local launcher values without overriding explicit process env.

    CI and production runners normally inject their own URLs and credentials.
    ``setdefault`` preserves that contract while making a locally launched
    evaluation use the same dynamic ports as the already-running full stack.
    The returned metadata is safe to log because it contains no values.
    """

    runtime_path = _runtime_path(path)
    if not runtime_path.is_file():
        return {"loaded": False, "path": str(runtime_path), "keys": []}

    values = _read_assignments(runtime_path)
    for key, value in values.items():
        os.environ.setdefault(key, value)

    # start.sh persists base ports and exports these derived settings only to
    # service children.  The evaluator is a new child process, so derive them
    # here unless an explicit value was provided by the caller.
    _setdefault("APP_PORT", values.get("AGENT_PORT"))
    _setdefault("WORKER_METRICS_PORT", values.get("AGENT_WORKER_METRICS_PORT"))
    _setdefault(
        "AGENT_BASE_URL",
        f"http://127.0.0.1:{values['AGENT_PORT']}" if values.get("AGENT_PORT") else None,
    )
    _setdefault(
        "JAVA_WEB_URL",
        f"http://127.0.0.1:{values['GATEWAY_PORT']}" if values.get("GATEWAY_PORT") else None,
    )
    _setdefault(
        "MCP_SERVER_URL",
        f"http://127.0.0.1:{values['MCP_PORT']}" if values.get("MCP_PORT") else None,
    )
    _setdefault(
        "ES_HOSTS",
        f"http://127.0.0.1:{values['ES_PORT']}" if values.get("ES_PORT") else None,
    )

    return {
        "loaded": True,
        "path": str(runtime_path),
        "keys": sorted(values),
        "derived": [
            key
            for key in (
                "APP_PORT",
                "WORKER_METRICS_PORT",
                "AGENT_BASE_URL",
                "JAVA_WEB_URL",
                "MCP_SERVER_URL",
                "ES_HOSTS",
            )
            if key in os.environ
        ],
    }
