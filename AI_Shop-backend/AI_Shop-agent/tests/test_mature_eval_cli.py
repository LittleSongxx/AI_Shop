import json

import pytest

from benchmarks import run_search_rag_mature_eval as runner


def test_cli_exposes_all_versioned_phases_and_explicit_holdout_finalize():
    parser = runner.build_parser()

    assert parser.parse_args(["prepare"]).phase == "prepare"
    assert parser.parse_args(["collect-dev", "--run-id", "r1"]).phase == "collect-dev"
    assert parser.parse_args(["replay", "--run-id", "r1"]).phase == "replay"
    final = parser.parse_args(
        ["collect-final", "--run-id", "r1", "--finalize-holdout"]
    )
    assert final.finalize_holdout is True
    assert parser.parse_args(["package", "--run-id", "r1"]).phase == "package"


@pytest.mark.asyncio
async def test_final_collection_refuses_implicit_holdout_access():
    args = runner.build_parser().parse_args(["collect-final", "--run-id", "r1"])

    with pytest.raises(ValueError, match="--finalize-holdout"):
        await runner.collect_final(args)


def test_package_requires_all_result_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner, "EVIDENCE_ROOT", tmp_path / "evidence")
    args = runner.build_parser().parse_args(["package", "--run-id", "r1"])

    with pytest.raises(ValueError, match="incomplete run"):
        runner.package(args)


def test_badcase_packaging_keeps_failed_cases():
    report = {
        "cases": {
            "variant": [
                {
                    "caseId": "failed",
                    "metrics": {"metricsByK": {"5": {"recall": 0.5}}},
                },
                {
                    "caseId": "passed",
                    "metrics": {"metricsByK": {"5": {"recall": 1.0}}},
                },
            ]
        }
    }

    assert runner._badcases(json.loads(json.dumps(report))) == [
        {
            "variant": "variant",
            "caseId": "failed",
            "metric": "recall@5",
            "value": 0.5,
        }
    ]


def test_compact_packaging_removes_only_raw_per_case_and_provider_rows():
    report = {
        "variantMetrics": {
            "rrf": {
                "metricCurves": {"5": {"recall": 0.8}},
                "perCase": [{"query": "private raw row"}],
            }
        },
        "pairedDeltas": {
            "rerank:ndcg@5": {
                "meanDelta": 0.1,
                "winTieLoss": {"wins": 2, "ties": 1, "losses": 0},
                "confidenceInterval": {"lower": 0.01, "upper": 0.2},
                "perCase": [{"caseId": "case-1", "delta": 0.1}],
            }
        },
        "providerFacts": {
            "embedding": {
                "providerRequests": 3,
                "providerFailures": 0,
                "responseRecords": [{"vectorSha256": "abc"}],
            },
            "responseFacts": [{"candidateIds": ["raw"]}],
        },
    }

    variants = runner._compact_variant_metrics(report)
    deltas = runner._compact_paired_deltas(report)
    provider = runner._compact_provider_facts(report)

    assert variants["rrf"]["metricCurves"]["5"]["recall"] == 0.8
    assert "perCase" not in variants["rrf"]
    assert deltas["rerank:ndcg@5"]["meanDelta"] == 0.1
    assert "perCase" not in deltas["rerank:ndcg@5"]
    assert provider == {
        "embedding": {"providerRequests": 3, "providerFailures": 0}
    }


def test_selected_badcases_include_negative_query_false_positive():
    variant = "full_rerank:c50:rrf60:n6"
    reports = {
        "chinese-final-replay": {
            "cases": {
                variant: [
                    {
                        "caseId": "negative-failure",
                        "metrics": {
                            "expectedNoResults": True,
                            "noResultCorrect": False,
                            "metricsByK": {"5": {"recall": None}},
                        },
                    }
                ]
            }
        }
    }
    frozen = {"search": {"selectedVariant": variant}}

    badcases = runner._selected_badcases(reports, frozen)

    assert badcases["chinese-final-replay"] == [
        {
            "variant": variant,
            "caseId": "negative-failure",
            "metric": "noResultCorrect",
            "value": False,
        }
    ]


def test_component_ablation_pairs_identical_cases_without_raw_rows():
    def row(case_id, ndcg, recall, mrr):
        return {
            "caseId": case_id,
            "metrics": {
                "applicable": True,
                "metricsByK": {
                    "5": {"ndcg": ndcg, "recall": recall},
                    "10": {"reciprocalRank": mrr},
                },
            },
        }

    report = {
        "cases": {
            "rrf": [row("a", 0.5, 0.5, 0.5), row("b", 0.5, 1.0, 0.5)],
            "rerank": [row("a", 1.0, 1.0, 1.0), row("b", 1.0, 1.0, 1.0)],
        }
    }

    result = runner._component_ablation(report, {"rerank": ("rrf", "rerank")})

    comparison = result["rerank"]["metrics"]["ndcg@5"]
    assert comparison["meanDelta"] == 0.5
    assert comparison["winTieLoss"] == {"wins": 2, "ties": 0, "losses": 0}
    assert "perCase" not in comparison


def test_search_split_metrics_do_not_mix_fresh_and_challenge():
    report = {
        "cases": {
            "selected": [
                {
                    "split": "fresh_holdout",
                    "queryType": "standard",
                    "metrics": {
                        "applicable": True,
                        "expectedNoResults": False,
                        "firstRelevantRank": 1,
                        "lastRelevantRank": 1,
                        "recall90K": 1,
                        "metricsByK": {
                            "5": {
                                "recall": 1.0,
                                "hitRate": 1.0,
                                "allRelevantRate": 1.0,
                                "precision": 0.2,
                                "reciprocalRank": 1.0,
                                "ndcg": 1.0,
                                "averagePrecision": 1.0,
                            }
                        },
                    },
                },
                {
                    "split": "challenge",
                    "queryType": "conflict",
                    "metrics": {
                        "applicable": False,
                        "expectedNoResults": True,
                        "noResultCorrect": True,
                        "metricsByK": {"5": {"recall": None}},
                    },
                },
            ]
        }
    }

    fresh = runner._search_split_metrics(report, "selected", "fresh_holdout")
    conflict = runner._search_split_metrics(
        report, "selected", "challenge", query_type="conflict"
    )

    assert fresh["caseCount"] == 1
    assert fresh["metricCurves"]["5"]["recall"] == 1.0
    assert conflict["caseCount"] == 1
    assert conflict["negativeCaseCount"] == 1
    assert conflict["noResultAccuracy"] == 1.0
