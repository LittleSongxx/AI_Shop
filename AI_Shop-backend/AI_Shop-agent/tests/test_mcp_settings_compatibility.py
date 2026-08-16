from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_mcp_server_import_has_no_incomplete_settings_warning() -> None:
    agent_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-c",
            "import app.mcp_server.server",
        ],
        cwd=agent_root,
        check=True,
    )
