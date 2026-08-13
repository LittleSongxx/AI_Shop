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
