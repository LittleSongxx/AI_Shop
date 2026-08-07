from app.harness.performance_gate import (
    DeterministicThresholds,
    evaluate_performance_gate,
    percentile,
    summarize_results,
)


def _result(value: float, *, success: bool = True, error: str | None = None):
    return {
        "enqueueMs": value,
        "queueMs": value * 2,
        "ttftMs": value * 3,
        "totalMs": value * 4,
        "terminalSuccess": success,
        "error": error,
    }


def test_percentile_uses_nearest_rank():
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([], 0.95) is None


def test_deterministic_gate_enforces_all_samples_errors_and_p95():
    summary = summarize_results([_result(100) for _ in range(100)])
    gate = evaluate_performance_gate(summary, mode="deterministic")
    assert gate["passed"] is True

    failed = summarize_results([_result(100) for _ in range(99)] + [_result(900)])
    gate = evaluate_performance_gate(
        failed,
        mode="deterministic",
        thresholds=DeterministicThresholds(enqueue_p95_ms=50),
    )
    assert gate["passed"] is False
    assert any("enqueue p95" in item for item in gate["violations"])


def test_missing_queue_measurement_and_terminal_error_fail_hard_gate():
    result = _result(100, success=False, error="WS_ERROR")
    result["queueMs"] = None
    summary = summarize_results([result])
    gate = evaluate_performance_gate(summary, mode="deterministic")
    assert gate["passed"] is False
    assert any("error count" in item for item in gate["violations"])
    assert any("queue has 0/1 samples" in item for item in gate["violations"])


def test_live_gate_checks_only_terminal_success_rate():
    results = [_result(10) for _ in range(19)] + [
        _result(10, success=False, error="TERMINAL_ERROR")
    ]
    summary = summarize_results(results)
    assert summary["terminalSuccessRate"] == 0.95
    assert evaluate_performance_gate(summary, mode="live")["passed"] is True
    assert (
        evaluate_performance_gate(summary, mode="live", live_success_rate=0.951)["passed"] is False
    )
