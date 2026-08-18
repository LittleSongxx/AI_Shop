"""Test that quality gates, human review, and FAILED_RETAINED are fail-closed."""

from __future__ import annotations

from types import SimpleNamespace


def test_search_v3_failed_retained_gate_returns_stage_failed():
    """Search v3 package with FAILED_RETAINED quality gate must return FAILED."""
    from benchmarks.eval_runtime import adapters

    search = SimpleNamespace(
        package=lambda _args: {
            "qualityGates": {"status": "FAILED_RETAINED", "passed": False}
        }
    )
    original_import = adapters.importlib.import_module
    adapters.importlib.import_module = lambda name: (
        search if name.endswith("run_search_v3_eval") else original_import(name)
    )
    try:
        from pathlib import Path

        from benchmarks.eval_runtime.registry import SuiteDefinition

        suite = SuiteDefinition(
            "search-v3",
            {"adapter": "search-v3"},
            Path("benchmarks/suites/search-v3.json"),
        )
        import asyncio

        result = asyncio.run(
            adapters.run_stage(
                suite,
                stage="package",
                run_id="search-v3-test-20260818",
                options={},
            )
        )
        assert result.status == "FAILED"
        assert result.result["qualityGates"]["status"] == "FAILED_RETAINED"
    finally:
        adapters.importlib.import_module = original_import


def test_rag_v5_failed_retained_gate_returns_stage_failed():
    """RAG v5 package with FAILED_RETAINED in either stage must return FAILED."""
    from benchmarks.eval_runtime import adapters

    rag = SimpleNamespace(
        package=lambda _args: {
            "qualityGate": {"status": "FAILED_RETAINED", "passed": False}
        }
    )
    original_import = adapters.importlib.import_module
    adapters.importlib.import_module = lambda name: (
        rag
        if name.endswith("run_rag_v5_eval") or name.endswith("run_rag_generation_v5")
        else original_import(name)
    )
    try:
        from pathlib import Path

        from benchmarks.eval_runtime.registry import SuiteDefinition

        suite = SuiteDefinition(
            "rag-v5", {"adapter": "rag-v5"}, Path("benchmarks/suites/rag-v5.json")
        )
        import asyncio

        result = asyncio.run(
            adapters.run_stage(
                suite, stage="package", run_id="rag-v5-test-20260818", options={}
            )
        )
        assert result.status == "FAILED"
    finally:
        adapters.importlib.import_module = original_import


def test_rag_v5_human_review_pending_returns_review_pending():
    """RAG v5 package with HUMAN_REVIEW_PENDING must return REVIEW_PENDING."""
    from benchmarks.eval_runtime import adapters

    retrieval = SimpleNamespace(
        package=lambda _args: {"qualityGate": {"status": "PASSED", "passed": True}}
    )
    generation = SimpleNamespace(
        package=lambda _args: {
            "qualityGate": {"status": "PASSED", "passed": True},
            "humanReviewStatus": "HUMAN_REVIEW_PENDING",
        }
    )
    original_import = adapters.importlib.import_module

    def mock_import(name):
        if name.endswith("run_rag_v5_eval"):
            return retrieval
        if name.endswith("run_rag_generation_v5"):
            return generation
        return original_import(name)

    adapters.importlib.import_module = mock_import
    try:
        from pathlib import Path

        from benchmarks.eval_runtime.registry import SuiteDefinition

        suite = SuiteDefinition(
            "rag-v5", {"adapter": "rag-v5"}, Path("benchmarks/suites/rag-v5.json")
        )
        import asyncio

        result = asyncio.run(
            adapters.run_stage(
                suite, stage="package", run_id="rag-v5-test-20260818", options={}
            )
        )
        assert result.status == "REVIEW_PENDING"
        assert result.result["generation"]["humanReviewStatus"] == "HUMAN_REVIEW_PENDING"
    finally:
        adapters.importlib.import_module = original_import


def test_agent_v2_gate_failures_return_stage_failed():
    """Agent v2 execute with gateFailures must return FAILED."""
    import asyncio

    from benchmarks.eval_runtime import adapters

    async def mock_run_live(args):
        await asyncio.sleep(0)
        return (
            {"gateFailures": ["taskSuccessRate=0.10 < 0.85"]},
            "/dev/null",
        )

    agent = SimpleNamespace(
        DEFAULT_DATASET="dataset.jsonl",
        DEFAULT_LOCK="lock.json",
        run_live=mock_run_live,
    )
    original_import = adapters.importlib.import_module
    adapters.importlib.import_module = lambda name: (
        agent if name.endswith("run_task_success_v2_eval") else original_import(name)
    )
    try:
        from pathlib import Path

        from benchmarks.eval_runtime.registry import SuiteDefinition

        suite = SuiteDefinition(
            "agent-v2", {"adapter": "agent-v2"}, Path("benchmarks/suites/agent-v2.json")
        )

        result = asyncio.run(
            adapters.run_stage(
                suite,
                stage="execute",
                run_id="agent-v2-adaptive-test-20260818",
                options={},
            )
        )
        assert result.status == "FAILED"
        assert "taskSuccessRate" in result.error_message
    finally:
        adapters.importlib.import_module = original_import


def test_deterministic_stage_maps_to_lifecycle_packaged():
    """Deterministic stage must map to PACKAGED lifecycle phase."""
    from pathlib import Path

    from benchmarks.eval import _stage_phase
    from benchmarks.eval_runtime.contracts import RunPhase
    from benchmarks.eval_runtime.registry import SuiteDefinition

    suite = SuiteDefinition(
        "deterministic-search-rag",
        {"adapter": "deterministic"},
        Path("benchmarks/suites/deterministic-search-rag.json"),
    )
    phase = _stage_phase(suite, "deterministic")
    assert phase == RunPhase.PACKAGED


def test_legacy_completion_sets_terminal_state(tmp_path):
    from benchmarks.eval_runtime.contracts import RunPhase, RunState
    from benchmarks.eval_runtime.lifecycle import RunLifecycle

    lifecycle = RunLifecycle(
        tmp_path / "lifecycle.json",
        suite="deterministic-search-rag",
        run_id="ci-test-lifecycle-search-rag",
    )
    snapshot = lifecycle.complete_legacy(details={"stage": "deterministic"})

    assert snapshot["phase"] == RunPhase.PACKAGED
    assert snapshot["state"] == RunState.COMPLETE
    assert snapshot["history"][-1]["compatibility"] == "legacy-deterministic"


def test_visual_provider_missing_is_blocked():
    import asyncio
    from pathlib import Path

    from benchmarks.eval_runtime import adapters
    from benchmarks.eval_runtime.registry import SuiteDefinition

    visual = SimpleNamespace(
        run_live=lambda _limit=None: asyncio.sleep(
            0,
            result={
                "report": {},
                "providerComplete": False,
                "fallbackUsed": False,
            },
        )
    )
    original_import = adapters.importlib.import_module
    adapters.importlib.import_module = lambda name: (
        visual if name.endswith("run_visual_relevance") else original_import(name)
    )
    try:
        suite = SuiteDefinition(
            "visual-v1",
            {
                "adapter": "visual-v1",
                "providerPolicy": "FAIL_CLOSED_NO_FALLBACK",
            },
            Path("benchmarks/suites/visual-v1.json"),
        )
        result = asyncio.run(
            adapters.run_stage(suite, stage="execute", run_id="visual-v1-test", options={})
        )
        assert result.status == "BLOCKED"
    finally:
        adapters.importlib.import_module = original_import


def test_text2sql_without_real_predictions_is_blocked():
    import asyncio
    from pathlib import Path

    from benchmarks.eval_runtime import adapters
    from benchmarks.eval_runtime.registry import SuiteDefinition

    suite = SuiteDefinition(
        "text2sql-v1",
        {
            "adapter": "text2sql-v1",
            "providerPolicy": "FAIL_CLOSED_NO_FALLBACK",
        },
        Path("benchmarks/suites/text2sql-v1.json"),
    )
    result = asyncio.run(
        adapters.run_stage(suite, stage="execute", run_id="text2sql-v1-test", options={})
    )
    assert result.status == "BLOCKED"


def test_text2sql_provider_trace_cannot_be_overridden_by_top_level_flag():
    from benchmarks import text2sql_eval

    prediction = {
        "providerComplete": True,
        "resultCorrect": True,
        "narrativeConsistent": True,
        "trace": {
            "traceId": "trace-1",
            "modelVersion": "model-1",
            "inputTokens": 10,
            "outputTokens": 10,
            "costCny": 0.01,
            "sqlHash": "hash-1",
            "providerComplete": False,
        },
    }
    case = text2sql_eval.load_cases()[0]
    report = text2sql_eval.evaluate_predictions([case], {case.case_id: prediction})
    assert report["providerCompleteness"] == 0.0
    assert report["traceCompleteness"] == 0.0


def test_visual_and_text2sql_successful_execute_can_be_packaged(tmp_path, monkeypatch):
    import argparse
    import asyncio
    from pathlib import Path

    from benchmarks import eval as eval_module
    from benchmarks.eval_runtime.contracts import RunPhase, StageResult
    from benchmarks.eval_runtime.registry import SuiteDefinition

    suite = SuiteDefinition(
        "visual-v1",
        {
            "adapter": "visual-v1",
            "resultRoot": str(tmp_path / "results"),
            "stages": ["execute", "package"],
        },
        Path("benchmarks/suites/visual-v1.json"),
    )
    run_id = "visual-v1-abcdef0-20260818"
    lifecycle = eval_module._lifecycle(suite, run_id)
    lifecycle.transition(RunPhase.PREFLIGHTED, details={"preflight": "PASS"})
    preflight = eval_module._run_root(suite, run_id) / "preflight.json"
    preflight.write_text('{"status":"READY"}\n', encoding="utf-8")
    monkeypatch.setattr(eval_module, "load_suite", lambda _name: suite)
    monkeypatch.setattr(eval_module, "_ensure_manifest", lambda *_args: {})
    monkeypatch.setattr(eval_module, "_write_terminal_manifest", lambda *_args: {})

    async def fake_run_stage(_suite, *, stage, run_id, options):
        return StageResult(stage=stage, result={"ok": True})

    monkeypatch.setattr(eval_module, "run_stage", fake_run_stage)
    execute_args = argparse.Namespace(
        command="run",
        suite="visual-v1",
        stage="execute",
        run_id=run_id,
        release_version=0,
        fixture_snapshot_id=None,
    )
    executed = asyncio.run(eval_module.command_run(execute_args))
    assert executed["lifecycle"]["phase"] == "FINAL_COLLECTED"
    assert executed["lifecycle"]["state"] == "IN_PROGRESS"

    package_values = vars(execute_args).copy()
    package_values["stage"] = "package"
    package_args = argparse.Namespace(**package_values)
    packaged = asyncio.run(eval_module.command_run(package_args))
    assert packaged["lifecycle"]["phase"] == "PACKAGED"
    assert packaged["lifecycle"]["state"] == "COMPLETE"
