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
