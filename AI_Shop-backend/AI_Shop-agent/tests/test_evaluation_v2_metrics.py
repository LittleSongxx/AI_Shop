from copy import deepcopy

import pytest

from evaluation.core.config import load_suite
from evaluation.core.gates import evaluate_gates
from evaluation.core.metrics import (
    bootstrap_interval,
    estimate_metric,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank_at_k,
    wilson_interval,
)


def test_standard_ranking_metrics_use_graded_qrels():
    ranking = ["d2", "d3", "d1", "unjudged"]
    qrels = {"d1": 3, "d2": 2, "d3": 0}

    assert recall_at_k(ranking, qrels, 1) == 0.5
    assert recall_at_k(ranking, qrels, 3) == 1.0
    assert reciprocal_rank_at_k(ranking, qrels, 10) == 1.0
    assert 0 < ndcg_at_k(ranking, qrels, 3) < 1


def test_wilson_interval_is_bounded_and_not_fake_certainty():
    lower, upper = wilson_interval(10, 10)

    assert 0.72 < lower < 0.73
    assert upper == pytest.approx(1.0)


def test_bootstrap_interval_is_deterministic():
    values = [0.1, 0.2, 0.8, 0.9]
    first = bootstrap_interval(
        values,
        lambda rows: sum(rows) / len(rows),
        samples=500,
        seed=42,
    )
    second = bootstrap_interval(
        values,
        lambda rows: sum(rows) / len(rows),
        samples=500,
        seed=42,
    )

    assert first == second
    assert first[0] < 0.5 < first[1]


def test_p99_below_100_samples_is_explicitly_descriptive():
    estimate = estimate_metric(
        "latencyMsP99",
        [10, 20, 30],
        kind="latency",
        aggregation="p99",
        bootstrap_samples=500,
        bootstrap_seed=7,
        p99_minimum=100,
    )

    assert estimate.sample_count == 3
    assert estimate.interval_method == "percentile-bootstrap"
    assert estimate.notes == ("DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_100",)


def test_gate_fails_closed_when_a_domain_is_missing():
    suite = load_suite()
    summary = {"domains": {}}

    result = evaluate_gates(summary, suite, split="development")

    assert result["passed"] is False
    assert result["domainOutcomes"] == {
        "search": False,
        "rag": False,
        "agent": False,
    }


def test_gate_fails_when_one_case_is_failed_even_if_aggregates_pass():
    suite = deepcopy(load_suite())
    summary = {"domains": {}}
    for domain, config in suite["domains"].items():
        case_count = int(suite["splitMinimums"]["development"][domain])
        metrics = {}
        for name, metric in config["metrics"].items():
            value = 0.0 if metric["kind"] == "count" else 1.0
            metrics[name] = {"value": value, "sampleCount": case_count}
        status_counts = {"PASSED": case_count}
        if domain == "rag":
            status_counts = {"PASSED": case_count - 1, "FAILED": 1}
        summary["domains"][domain] = {
            "caseCount": case_count,
            "statusCounts": status_counts,
            "metrics": metrics,
        }

    result = evaluate_gates(summary, suite, split="development")

    assert result["passed"] is False
    assert result["domainOutcomes"]["rag"] is False
    decision = next(
        row
        for row in result["decisions"]
        if row["domain"] == "rag" and row["metric"] == "casePassRate"
    )
    assert decision["passed"] is False
    assert decision["observed"] == pytest.approx(17 / 18)
