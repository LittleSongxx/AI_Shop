import json
from pathlib import Path

import pytest

from evaluation.capacity_benchmark import (
    CAPACITY_SCHEMA,
    CapacityBenchmarkError,
    _public_observation,
    benchmark_capacity,
    load_capacity_cases,
    parse_concurrency_levels,
    summarize_capacity_level,
    verify_capacity_evidence,
    write_capacity_evidence,
)
from evaluation.core.contracts import CaseResult, CaseStatus, Domain


def _gold_row(*, case_id="case-1", intent="CHAT", handoff=False, status="HUMAN_VERIFIED"):
    return {
        "id": case_id,
        "input": {"message": "你好"},
        "expected": {
            "intent": intent,
            "riskLevel": "LOW",
            "shouldHandoff": handoff,
            "slots": {},
        },
        "annotation": {"status": status},
    }


def test_capacity_concurrency_parser_accepts_commas_and_repeated_flags():
    assert parse_concurrency_levels(None) == (1, 2, 4, 8)
    assert parse_concurrency_levels(["8", "1,4", "2"]) == (1, 2, 4, 8)
    with pytest.raises(CapacityBenchmarkError):
        parse_concurrency_levels(["not-a-number"])


def test_capacity_selection_accepts_only_human_verified_read_only_cases(monkeypatch):
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.load_gold_dataset",
        lambda _path: [_gold_row()],
    )

    rows, cases = load_capacity_cases(Path("unused.jsonl"), case_ids=("case-1",))

    assert [row["id"] for row in rows] == ["case-1"]
    assert cases[0].expected["stateMode"] == "READ_ONLY"


def test_capacity_order_query_declares_deterministic_provider_contract(monkeypatch):
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.load_gold_dataset",
        lambda _path: [_gold_row(intent="QUERY_ORDER")],
    )

    _rows, cases = load_capacity_cases(Path("unused.jsonl"), case_ids=("case-1",))

    assert cases[0].required_providers == ("agent-runtime",)


@pytest.mark.parametrize(
    "row",
    [
        _gold_row(status="DRAFT_NEEDS_HUMAN_REVIEW"),
        _gold_row(handoff=True),
        _gold_row(intent="REFUND"),
    ],
)
def test_capacity_selection_rejects_untrusted_or_mutating_cases(monkeypatch, row):
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.load_gold_dataset",
        lambda _path: [row],
    )

    with pytest.raises(CapacityBenchmarkError):
        load_capacity_cases(Path("unused.jsonl"), case_ids=("case-1",))


def test_capacity_level_reports_qps_latency_usage_resources_and_badcase():
    observations = [
        {
            "trialId": "ok",
            "status": "PASSED",
            "latencyMs": 100,
            "providerCompleteness": 1,
            "terminalStateCorrectness": 1,
            "stateDiffMatched": True,
            "duplicateSideEffectCount": 0,
            "severeSafetyViolationCount": 0,
            "usage": {
                "inputTokens": 10,
                "outputTokens": 2,
                "providerCalls": 1,
                "unpricedCalls": 1,
                "missingUsageCalls": 0,
                "costCny": None,
                "costStatus": "UNPRICED",
                "usageSource": "langchain.usage_metadata",
            },
            "steps": [{"eventType": "LLM_CALL", "status": "OK", "latencyMs": 80}],
        },
        {
            "trialId": "bad",
            "status": "ERROR",
            "latencyMs": 300,
            "providerCompleteness": 0,
            "terminalStateCorrectness": 0,
            "stateDiffMatched": False,
            "duplicateSideEffectCount": 0,
            "severeSafetyViolationCount": 0,
            "usage": {
                "providerCalls": 1,
                "missingUsageCalls": 1,
                "costCny": None,
                "costStatus": "MISSING_USAGE",
                "missingReason": "provider_error_before_usage",
                "usageSource": "none",
            },
            "steps": [
                {"eventType": "LLM_CALL", "status": "ERROR", "latencyMs": 250}
            ],
            "error": {"type": "TimeoutError"},
        },
    ]

    report = summarize_capacity_level(
        observations,
        concurrency=2,
        wall_seconds=1.0,
        resource_samples=[
            {"hostCpuUsedPercent": 20, "hostMemoryUsedPercent": 40},
            {"hostCpuUsedPercent": 30, "hostMemoryUsedPercent": 42},
        ],
    )

    assert report["achievedQps"] == 2.0
    assert report["successRate"] == 0.5
    assert report["latencyMs"]["p95"] == pytest.approx(290)
    assert report["usage"]["costStatus"] == "MISSING_USAGE"
    assert report["usage"]["missingReasons"] == {"provider_error_before_usage": 1}
    assert report["usage"]["usageSources"] == [
        "langchain.usage_metadata",
        "none",
    ]
    assert report["stageMetrics"]["LLM_CALL"]["errorCount"] == 1
    assert report["resources"]["hostCpuUsedPercent"]["p95"] == pytest.approx(29.5)
    assert report["badcaseTrialIds"] == ["bad"]
    assert report["badcases"] == [
        {
            "trialId": "bad",
            "caseId": "",
            "reasons": [
                "status=ERROR",
                "provider_incomplete",
                "terminal_state_mismatch",
                "state_diff_mismatch",
            ],
        }
    ]
    assert report["pathMetrics"]["UNKNOWN"]["requestCount"] == 2


def test_public_observation_hashes_answer_and_preserves_safety_evidence():
    result = CaseResult(
        case_id="case-1",
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={
            "providerCompleteness": 1,
            "terminalStateCorrectness": 1,
            "severeSafetyViolationCount": 0,
        },
        latency_ms=123,
        output={
            "answer": "private answer",
            "tools": [],
            "episodes": [
                {
                    "steps": [
                        {
                            "eventType": "AGENT_POLICY",
                            "output": {
                                "policy": "DETERMINISTIC_SOCIAL_REPLY",
                                "deterministicSocialReply": True,
                                "llmSkipped": True,
                                "secret": "must-not-leak",
                            },
                        }
                    ]
                }
            ],
        },
        providers={},
        assertions=[],
        usage={"providerCalls": 0, "costCny": None, "costStatus": "MISSING_USAGE"},
        state_diff={"matched": True, "duplicateSideEffectCount": 0},
    )

    public = _public_observation(
        result,
        case_id="case-1",
        trial_id="trial-1",
        request_id="request-1",
        intent="CHAT",
        required_providers=("agent-runtime", "llm"),
    )

    assert public["answer"]["rawStored"] is False
    assert public["answer"]["chars"] == len("private answer")
    assert "private answer" not in json.dumps(public)
    assert public["stateDiffMatched"] is True
    assert public["intent"] == "CHAT"
    assert public["requiredProviders"] == ["agent-runtime", "llm"]
    assert public["executionPath"] == "DETERMINISTIC"
    assert public["policyFacts"] == [
        {
            "policy": "DETERMINISTIC_SOCIAL_REPLY",
            "deterministicSocialReply": True,
            "llmSkipped": True,
        }
    ]
    assert "must-not-leak" not in json.dumps(public)


def test_capacity_evidence_is_immutable_and_hash_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.CAPACITY_BENCHMARK_ROOT", tmp_path
    )
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.source_fingerprint",
        lambda: {
            "source": {"sha256": "a" * 64},
            "providerConfigurationSha256": "b" * 64,
        },
    )
    report = {
        "schemaVersion": CAPACITY_SCHEMA,
        "runId": "capacity-test",
        "createdAt": "2026-08-23T00:00:00.000Z",
        "dataset": {"sha256": "c" * 64},
        "preflight": {"passed": True},
        "levels": {},
        "notProductionSlo": True,
        "normalQualityDenominatorExcluded": True,
    }

    root, _digest = write_capacity_evidence(
        report, [], benchmark_id="capacity-test"
    )

    assert verify_capacity_evidence(root)["valid"] is True
    with pytest.raises(FileExistsError):
        write_capacity_evidence(report, [], benchmark_id="capacity-test")


@pytest.mark.asyncio
async def test_capacity_warmup_is_excluded_from_measured_observations(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.load_gold_dataset",
        lambda _path: [_gold_row()],
    )
    rows, cases = load_capacity_cases(Path("unused.jsonl"), case_ids=("case-1",))
    monkeypatch.setattr(
        "evaluation.capacity_benchmark.load_capacity_cases",
        lambda _path, case_ids: (rows, cases),
    )
    calls = []

    async def fake_run(case, *, user_id, timeout_seconds, trial_context):
        calls.append(trial_context.trial_id)
        return CaseResult(
            case_id=case.case_id,
            domain=Domain.AGENT,
            status=CaseStatus.PASSED,
            metrics={
                "providerCompleteness": 1,
                "terminalStateCorrectness": 1,
                "severeSafetyViolationCount": 0,
            },
            latency_ms=10,
            output={"answer": "ok", "tools": [], "episodes": []},
            providers={},
            assertions=[],
            usage={
                "providerCalls": 0,
                "costCny": None,
                "costStatus": "NOT_APPLICABLE",
                "notApplicableReason": "no_llm_call",
            },
            state_diff={"matched": True, "duplicateSideEffectCount": 0},
        )

    async def no_resource_samples(stop, *, interval_seconds):
        await stop.wait()
        return []

    monkeypatch.setattr(
        "evaluation.capacity_benchmark.run_agent_case", fake_run
    )
    monkeypatch.setattr(
        "evaluation.capacity_benchmark._sample_resources", no_resource_samples
    )
    dataset = tmp_path / "human.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")

    report, observations = await benchmark_capacity(
        dataset,
        run_id="capacity-warmup-test",
        concurrencies=(1, 2),
        requests_per_level=2,
        warmup_requests=2,
        case_ids=("case-1",),
        preflight={"passed": True},
    )

    assert len(calls) == 6
    assert len(observations) == 4
    assert report["warmup"]["requestCount"] == 2
    assert report["warmup"]["successfulCount"] == 2
    assert report["warmup"]["excludedFromMeasuredLevels"] is True
    assert report["warmup"]["observationsStored"] is False
    assert report["configuration"]["warmupRequests"] == 2
