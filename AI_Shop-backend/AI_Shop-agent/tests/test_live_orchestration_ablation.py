import copy

import pytest

from benchmarks import compare_live_orchestration_ablation as ablation


def _report(mode: str) -> dict:
    return {
        "schemaVersion": "aishop-live-eval/v1",
        "metadata": {
            "runId": f"run-{mode}",
            "executionMode": "LIVE_FULL_STACK",
            "simulated": False,
            "configuredOrchestrationMode": mode,
            "datasetSha256": "dataset-sha",
            "fixtureSnapshotId": "fixture-v1",
            "caseCount": 2,
        },
        "providerPreflight": {
            "dependencies": {
                "llm": True,
                "embeddingProvider": "openai",
                "embeddingProductionReady": True,
                "rerank": True,
                "javaGateway": True,
                "mcp": True,
            }
        },
        "summary": {
            "taskSuccessRate": 1.0,
            "executionCompletenessRate": 1.0,
            "providerCompletenessRate": 1.0,
            "observedModels": ["provider-model"],
        },
        "cases": [
            {
                "caseId": "a",
                "taskSuccess": True,
                "metrics": {
                    "latencyMs": 100,
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "costCny": 0.01,
                },
            },
            {
                "caseId": "b",
                "taskSuccess": True,
                "metrics": {
                    "latencyMs": 200,
                    "inputTokens": 20,
                    "outputTokens": 10,
                    "costCny": 0.02,
                },
            },
        ],
    }


def _reports() -> dict:
    return {mode: _report(mode) for mode in ablation.EXPECTED_MODES}


def test_live_ablation_accepts_only_strictly_paired_real_runs():
    report = ablation.build_comparison(_reports(), "paired-run")

    assert report["metadata"]["executionMode"] == "LIVE_FULL_STACK_PAIRED"
    assert report["metadata"]["simulated"] is False
    assert len(report["metadata"]["caseIds"]) == 2
    assert len(report["comparisons"]) == 3
    assert all(item["meanTaskSuccessDelta"] == 0 for item in report["comparisons"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda reports: reports["workflow"]["metadata"].update(datasetSha256="other"),
            "dataset SHA",
        ),
        (
            lambda reports: reports["workflow"]["summary"].update(
                observedModels=["different-model"]
            ),
            "model set",
        ),
        (
            lambda reports: reports["workflow"]["summary"].update(providerCompletenessRate=0.5),
            "provider completeness",
        ),
        (
            lambda reports: reports["workflow"]["metadata"].update(
                fixtureSnapshotId="different-fixture"
            ),
            "fixture snapshot",
        ),
    ],
)
def test_live_ablation_rejects_noncomparable_runs(mutation, message):
    reports = copy.deepcopy(_reports())
    mutation(reports)

    with pytest.raises(ablation.AblationContractError, match=message):
        ablation.validate_reports(reports)


def test_pairwise_metrics_are_computed_from_matching_case_ids():
    reports = _reports()
    reports["multi_agent"]["cases"][0]["taskSuccess"] = False
    reports["multi_agent"]["cases"][0]["metrics"]["latencyMs"] = 150
    reports["multi_agent"]["cases"][0]["metrics"]["inputTokens"] = 30

    comparison = ablation.compare_pair("workflow", "multi_agent", reports)

    assert comparison["pairedCases"] == 2
    assert comparison["meanTaskSuccessDelta"] == -0.5
    assert comparison["meanLatencyDeltaMs"] == 25
    assert comparison["meanTokenDelta"] == 10
