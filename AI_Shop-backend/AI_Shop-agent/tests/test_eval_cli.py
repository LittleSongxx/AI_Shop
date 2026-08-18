from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "benchmarks/eval.py", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_unified_cli_lists_only_formal_suites() -> None:
    result = _run("list")
    assert result["suites"] == [
        "agent-v2",
        "rag-v5",
        "search-v3",
        "text2sql-v1",
        "visual-v1",
    ]


def test_legacy_suite_is_not_in_formal_list() -> None:
    result = _run("list")
    assert "legacy-deterministic" not in result["suites"]


def test_agent_cli_defaults_are_forwarded_by_adapter(monkeypatch) -> None:
    from benchmarks import run_task_success_v2_eval as agent_runner
    from benchmarks.eval_runtime.adapters import run_stage
    from benchmarks.eval_runtime.registry import load_suite

    captured = {}

    async def fake_run_live(args):
        captured.update(vars(args))
        return {"ok": True}, ROOT

    monkeypatch.setattr(agent_runner, "run_live", fake_run_live)
    result = asyncio.run(
        run_stage(
            load_suite("agent-v2"),
            stage="execute",
            run_id="agent-v2-adaptive-abcdef0-20260818",
            options={"fixture_snapshot_id": "fixture-test"},
        )
    )
    assert result.status == "COMPLETE"
    assert captured["dataset"] == agent_runner.DEFAULT_DATASET
    assert captured["lock"] == agent_runner.DEFAULT_LOCK
    assert captured["fixture_snapshot_id"] == "fixture-test"


def test_unified_cli_validates_all_formal_suites() -> None:
    for suite in ("search-v3", "rag-v5", "agent-v2"):
        result = _run("validate", "--suite", suite)
        assert result["status"] == "VALID"
        assert result["suite"] == suite


def test_legacy_deterministic_run_reports_terminal_lifecycle() -> None:
    run_id = f"ci-test-terminal-lifecycle-{uuid.uuid4().hex[:12]}"
    try:
        result = _run(
            "run",
            "--suite",
            "deterministic-search-rag",
            "--stage",
            "deterministic",
            "--run-id",
            run_id,
        )
    finally:
        shutil.rmtree(ROOT / "benchmarks" / "results" / run_id, ignore_errors=True)
        shutil.rmtree(
            ROOT / "benchmarks" / "results" / "search-rag-v1" / run_id,
            ignore_errors=True,
        )

    assert result["status"] == "COMPLETE"
    assert result["lifecycle"]["phase"] == "PACKAGED"
    assert result["lifecycle"]["state"] == "COMPLETE"
