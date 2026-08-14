import pytest

from app.evaluation.ranking import (
    aggregate_incomplete_judgment_cases,
    aggregate_ranking_cases,
    aggregate_stage_latency,
    bootstrap_mean_ci,
    incomplete_judgment_case_metrics,
    paired_ranking_comparison,
    ranking_case_metrics,
)


def test_ranking_metrics_cover_multiple_k_and_graded_ndcg():
    row = ranking_case_metrics(
        ["weak", "best", "other", "good"],
        {"best": 3, "good": 2, "weak": 1},
        k_values=[1, 3, 5],
        relevant_threshold=2,
    )

    assert row["metricsByK"]["1"]["recall"] == 0.0
    assert row["metricsByK"]["3"]["recall"] == 0.5
    assert row["metricsByK"]["5"]["recall"] == 1.0
    assert row["metricsByK"]["1"]["ndcg"] > 0
    assert row["metricsByK"]["3"]["ndcg"] > row["metricsByK"]["1"]["ndcg"]
    assert row["firstRelevantRank"] == 2
    assert row["lastRelevantRank"] == 4
    assert row["recall90K"] == 4


def test_map_denominator_respects_k_and_relevant_count():
    row = ranking_case_metrics(
        ["r1", "noise", "r2"],
        {"r1": 2, "r2": 2, "r3": 2},
        k_values=[1, 3],
        relevant_threshold=2,
    )

    assert row["metricsByK"]["1"]["averagePrecision"] == 1.0
    assert row["metricsByK"]["3"]["averagePrecision"] == pytest.approx(
        (1.0 + 2.0 / 3.0) / 3.0, abs=1e-6
    )


def test_negative_query_has_no_result_accuracy_without_fake_recall():
    correct = ranking_case_metrics([], {}, expected_no_results=True, k_values=[5])
    wrong = ranking_case_metrics(["unexpected"], {}, expected_no_results=True, k_values=[5])
    report = aggregate_ranking_cases([correct, wrong])

    assert correct["metricsByK"]["5"]["recall"] is None
    assert report["rankingCaseCount"] == 0
    assert report["negativeCaseCount"] == 2
    assert report["noResultAccuracy"] == 0.5


def test_judged_pool_rejects_unjudged_documents():
    with pytest.raises(ValueError, match="unjudged"):
        ranking_case_metrics(
            ["judged", "not-in-pool"],
            {"judged": 2},
            judged_pool=True,
        )


def test_incomplete_judgment_metrics_keep_unjudged_results_neutral():
    row = incomplete_judgment_case_metrics(
        ["unjudged", "relevant", "nonrelevant", "relevant-2"],
        {"relevant": 2, "relevant-2": 1, "nonrelevant": 0},
        k_values=[1, 2, 4],
    )

    assert row["metricsByK"]["1"]["knownRelevantRecall"] == 0.0
    assert row["metricsByK"]["1"]["judgedRate"] == 0.0
    assert row["metricsByK"]["2"]["knownRelevantRecall"] == 0.5
    assert row["metricsByK"]["4"]["knownRelevantRecall"] == 1.0
    assert row["bpref"] == 0.5
    report = aggregate_incomplete_judgment_cases([row])
    assert report["metricCurves"]["4"]["condensedNdcg"] > 0
    assert report["labelScope"] == "full-catalog-incomplete-qrels"


def test_aggregate_reports_rank_saturation_and_metric_curves():
    rows = [
        ranking_case_metrics(["a", "b"], {"a": 2}, k_values=[1, 3]),
        ranking_case_metrics(["x", "c"], {"c": 3}, k_values=[1, 3]),
    ]
    report = aggregate_ranking_cases(rows)

    assert report["metricCurves"]["1"]["recall"] == 0.5
    assert report["metricCurves"]["3"]["recall"] == 1.0
    assert report["rankDistribution"]["recall90KP50"] == 1.0
    assert report["rankDistribution"]["recall90KP95"] == 2.0


def test_bootstrap_and_paired_comparison_are_reproducible():
    first = bootstrap_mean_ci([0.1, -0.1, 0.2], seed=7, iterations=500)
    second = bootstrap_mean_ci([0.1, -0.1, 0.2], seed=7, iterations=500)
    baseline = [
        {"caseId": "a", "metrics": {"ndcg": 0.2}},
        {"caseId": "b", "metrics": {"ndcg": 0.3}},
    ]
    candidate = [
        {"caseId": "a", "metrics": {"ndcg": 0.4}},
        {"caseId": "b", "metrics": {"ndcg": 0.3}},
    ]

    assert first == second
    comparison = paired_ranking_comparison(
        baseline,
        candidate,
        metric_path=["metrics", "ndcg"],
        seed=7,
    )
    assert comparison["meanDelta"] == 0.1
    assert comparison["winTieLoss"] == {"wins": 1, "ties": 1, "losses": 0}
    assert len(comparison["perCase"]) == 2


def test_stage_latency_keeps_sample_disclosure():
    report = aggregate_stage_latency(
        [
            {"stageLatencyMs": {"embedding": 3, "bm25": 2}},
            {"stageLatencyMs": {"embedding": 5}},
        ]
    )

    assert report["embedding"]["samples"] == 2
    assert report["embedding"]["p95Ms"] == 5.0
    assert report["embedding"]["p99Reliable"] is False
    assert report["bm25"]["samples"] == 1
