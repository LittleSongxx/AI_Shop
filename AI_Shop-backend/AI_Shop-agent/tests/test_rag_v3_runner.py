from benchmarks.run_rag_v3_eval import (
    build_parser,
    parse_selected_variant,
    retrieval_gate,
)


def test_v3_cli_requires_explicit_holdout_finalization():
    args = build_parser().parse_args(["collect-final", "--run-id", "r1"])

    assert args.finalize_holdout is False
    assert build_parser().parse_args(["replay", "--run-id", "r1"]).phase == "replay"


def test_selected_variant_round_trips_all_frozen_parameters():
    parsed = parse_selected_variant("production:n6:t0.60:m0.05:iexp")

    assert parsed == {
        "variant": "production",
        "rerankTopN": 6,
        "evidenceThreshold": 0.60,
        "topScoreMargin": 0.05,
        "instruction": "exp",
        "rerankChannel": "rerankExperimental",
    }


def test_fresh_gate_reports_failed_retained_without_hiding_metrics():
    metrics = {
        "metricCurves": {
            "3": {"recall": 0.89},
            "5": {"recall": 0.95, "ndcg": 0.90},
            "10": {"mrr": 0.90},
        },
        "noAnswerAccuracy": 1.0,
        "injectionRobustness": 1.0,
        "canonicalCitationCorrectness": 1.0,
        "canonicalCitationCoverage": 1.0,
    }
    provider = {
        "embedding": {"cacheHits": 0, "providerFailures": 0},
        "rerank": {"fallbackCount": 0, "providerFailures": 0},
        "queryExpansion": {"providerFailures": 0},
    }

    gate = retrieval_gate(metrics, provider, 32)

    assert gate["status"] == "FAILED_RETAINED"
    assert gate["checks"]["recallAt3"] is False
