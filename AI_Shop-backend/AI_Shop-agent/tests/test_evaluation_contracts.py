from app.evaluation.contracts import (
    EvaluationAssertion,
    EvaluationCaseResult,
    aggregate_case_results,
)


def _case(*, latency_ms: float | None) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        suite="contract-test",
        runId="run-1",
        caseId="case-1",
        subset="contract",
        split="test",
        priority="P0",
        status="PASSED",
        executed=True,
        taskSuccess=True,
        assertions=[EvaluationAssertion(name="ok", passed=True)],
        latencyMs=latency_ms,
        evidenceSource="SYNTHETIC",
        executionMode="deterministic",
    )


def test_p99_disclosure_uses_latency_sample_count():
    summary = aggregate_case_results([_case(latency_ms=None)])

    assert summary["latency"]["samples"] == 0
    assert summary["sampleDisclosure"] == {
        "p99Reliable": False,
        "message": "未采集延迟样本，P99 不可用。",
    }


def test_p99_disclosure_marks_small_latency_sample():
    summary = aggregate_case_results([_case(latency_ms=12.5)])

    assert summary["latency"]["samples"] == 1
    assert summary["sampleDisclosure"]["p99Reliable"] is False
    assert "样本少于 100" in summary["sampleDisclosure"]["message"]
