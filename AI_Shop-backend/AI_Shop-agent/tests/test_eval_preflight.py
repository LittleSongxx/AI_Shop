from __future__ import annotations

from benchmarks.eval_runtime.preflight import PreflightCheck, PreflightResult


def test_preflight_result_requires_all_required_checks() -> None:
    result = PreflightResult(
        "search-v3",
        "search-v3-abcdef0-20260818",
        (
            PreflightCheck("required", True, "PASS"),
            PreflightCheck("optional", False, "FAIL"),
        ),
    )
    assert result.passed is True
    assert result.to_dict()["status"] == "READY"


def test_preflight_result_blocks_required_failure() -> None:
    result = PreflightResult(
        "search-v3",
        "search-v3-abcdef0-20260818",
        (PreflightCheck("java", True, "FAIL"),),
    )
    assert result.passed is False
    assert result.to_dict()["status"] == "BLOCKED"
