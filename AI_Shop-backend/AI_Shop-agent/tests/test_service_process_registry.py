from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_HELPER = _REPO_ROOT / "deploy/service-process-registry.sh"
_BACKEND = _REPO_ROOT / "AI_Shop-backend"


def _run_helper(pid_dir: Path, command: str, *args: object) -> subprocess.CompletedProcess:
    script = f"""
set -euo pipefail
PIDS=$1
BACKEND=$2
warn() {{ :; }}
source "$3"
{command}
"""
    return subprocess.run(
        [
            "bash",
            "-c",
            script,
            "registry-test",
            str(pid_dir),
            str(_BACKEND),
            str(_HELPER),
            *(str(arg) for arg in args),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_pid_record_rejects_reused_pid_without_killing_process(tmp_path: Path):
    process = subprocess.Popen(
        ["bash", "-c", "exec -a 'python app.main:app' sleep 30"]
    )
    try:
        # Popen returns before the child has necessarily completed exec(2).
        # Wait for the command marker used by the registry so this test does
        # not race the short startup window it is meant to exercise.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                command = Path(f"/proc/{process.pid}/cmdline").read_bytes().replace(
                    b"\0", b" "
                ).decode("utf-8", errors="replace")
            except OSError:
                command = ""
            if "app.main:app" in command:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("test process did not reach the expected command marker")
        pidfile = tmp_path / "agent.pid"
        pidfile.write_text(f"{process.pid}\n", encoding="ascii")

        upgraded = _run_helper(tmp_path, "is_running agent")
        assert upgraded.returncode == 0, upgraded.stderr
        saved_pid, saved_start = pidfile.read_text(encoding="ascii").split()
        assert saved_pid == str(process.pid)

        pidfile.write_text(f"{process.pid} {int(saved_start) + 1}\n", encoding="ascii")
        rejected = _run_helper(tmp_path, "is_running agent")
        assert rejected.returncode != 0
        assert not pidfile.exists()
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_port_ownership_is_tied_to_listener_pid(tmp_path: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as client:
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError("test HTTP listener did not start")

        owned = _run_helper(
            tmp_path, 'port_owned_by_pid "$4" "$5"', port, process.pid
        )
        wrong_owner = _run_helper(
            tmp_path, 'port_owned_by_pid "$4" "$5"', port, process.pid + 1
        )
        assert owned.returncode == 0, owned.stderr
        assert wrong_owner.returncode != 0
    finally:
        process.terminate()
        process.wait(timeout=5)
