import copy
import json

import pytest

from benchmarks import run_task_success_eval as live_eval
from app.services.episode_service import sanitize_episode_payload


def _case() -> dict:
    return {
        "id": "case-1",
        "subset": "action_proposal",
        "expected": {
            "terminalStatuses": ["SUCCEEDED"],
            "requiredEvents": ["ORCHESTRATION_DECISION", "TOOL_CALL"],
            "requiredTools": ["PROPOSE_REFUND"],
            "requiredToolArgs": [
                {
                    "tool": "PROPOSE_REFUND",
                    "subset": {"orderItemId": "item-1"},
                }
            ],
            "bizTypes": ["action_confirm"],
            "orchestrationModes": ["workflow"],
            "pendingStatuses": ["CONFIRMED"],
            "actionTypes": ["REFUND"],
            "modelRequired": True,
            "maxTraceSteps": 20,
            "maxTokens": 1000,
            "maxCostCny": 0.1,
        },
    }


def _episode() -> dict:
    return {
        "status": "SUCCEEDED",
        "inputTokens": 100,
        "outputTokens": 50,
        "costCny": 0.02,
        "latencyMs": 1200,
        "ttftMs": 300,
        "conversation": {
            "assistantMessage": "请确认退款",
            "bizType": "action_confirm",
            "sourceRefs": [],
        },
        "steps": [
            {
                "eventType": "ORCHESTRATION_DECISION",
                "status": "OK",
                "output": {
                    "mode": "workflow",
                    "configuredMode": "adaptive",
                },
            },
            {
                "eventType": "TOOL_CALL",
                "status": "OK",
                "toolName": "PROPOSE_REFUND",
                "input": {
                    "args": sanitize_episode_payload(
                        {"userId": "user-1", "orderItemId": "item-1"}
                    )
                },
            },
            {
                "eventType": "LLM_CALL",
                "status": "OK",
                "modelName": "real-provider-model",
            },
        ],
    }


def _rag_step(*, fallback: str | None = None, legacy: bool = False) -> dict:
    runtime = {
        "providerCalls": {
            "embedding": 1,
            "elasticsearchVector": 1,
            "rerank": 1,
        },
        "providerSuccesses": {} if legacy else {"embedding": 1, "rerank": 1},
        "providerFailures": {},
        "providerCacheHits": {},
        "fallbacks": [fallback] if fallback else [],
        "route": "POLICY",
        "observations": {},
    }
    return {
        "eventType": "RAG_RETRIEVAL",
        "status": "OK",
        "output": {"trace": {"runtime": runtime}},
    }


def test_default_live_dataset_is_hash_locked_and_has_required_size():
    cases = live_eval.load_cases(live_eval.DEFAULT_DATASET)
    lock = live_eval.validate_contract(cases, live_eval.DEFAULT_DATASET, live_eval.DEFAULT_LOCK)

    assert len(cases) == 37
    assert lock["resultStatus"] == "NOT_COLLECTED"
    assert lock["datasetSha256"] == live_eval.dataset_sha256(live_eval.DEFAULT_DATASET)


def test_contract_rejects_dataset_drift(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "suite": live_eval.SUITE,
                "datasetSha256": "wrong",
                "caseCount": 1,
                "requiredSubsets": [],
                "thresholds": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(live_eval.EvaluationContractError, match="SHA-256"):
        live_eval.validate_contract(live_eval.load_cases(dataset), dataset, lock)


def test_placeholder_resolution_requires_nonblank_private_binding():
    value = {"token": "${TOKEN_001}", "message": "订单 ${ORDER_ID_001}"}

    with pytest.raises(live_eval.EvaluationContractError, match="TOKEN_001"):
        live_eval.resolve_placeholders(
            value,
            {"TOKEN_001": "", "ORDER_ID_001": "order-1"},
        )

    assert live_eval.resolve_placeholders(
        value,
        {"TOKEN_001": "secret", "ORDER_ID_001": "order-1"},
    ) == {"token": "secret", "message": "订单 order-1"}


def test_episode_scoring_uses_trace_args_and_authoritative_pending_state():
    result = live_eval.evaluate_episode(
        _case(),
        _episode(),
        pending={"statusName": "CONFIRMED", "actionType": "REFUND"},
        expected_configured_mode="adaptive",
    )

    assert result["taskSuccess"] is True
    assert result["tools"] == ["PROPOSE_REFUND"]
    assert result["provider"]["modelNames"] == ["real-provider-model"]


def test_read_only_contract_detects_a_write_tool_as_safety_violation():
    case = _case()
    case["expected"] = {
        "terminalStatuses": ["SUCCEEDED"],
        "requiredTools": [],
        "forbiddenTools": ["PROPOSE_REFUND"],
        "noWriteTools": True,
    }

    result = live_eval.evaluate_episode(case, _episode())

    assert result["taskSuccess"] is False
    safety = [item for item in result["assertions"] if item["category"] == "SAFETY"]
    assert safety
    assert all(item["passed"] is False for item in safety)


def test_failed_llm_call_makes_provider_incomplete():
    episode = copy.deepcopy(_episode())
    episode["steps"][-1]["status"] = "ERROR"

    result = live_eval.evaluate_episode(
        _case(),
        episode,
        pending={"statusName": "CONFIRMED", "actionType": "REFUND"},
    )

    assert result["provider"]["complete"] is False
    assert result["taskSuccess"] is False


def test_rag_case_requires_balanced_real_provider_trace():
    case = _case()
    case["expected"] = {
        "terminalStatuses": ["SUCCEEDED"],
        "requiredEvents": ["RAG_RETRIEVAL"],
        "requiredTools": [],
        "modelRequired": True,
    }
    episode = _episode()
    episode["steps"].insert(1, _rag_step())

    result = live_eval.evaluate_episode(case, episode)

    assert result["taskSuccess"] is True
    assert result["provider"]["complete"] is True
    assert result["provider"]["rag"]["providerSuccesses"] == {
        "embedding": 1,
        "rerank": 1,
    }


@pytest.mark.parametrize(
    ("rag_step", "expected_fallbacks"),
    [
        (_rag_step(legacy=True), []),
        (_rag_step(fallback="rerank_provider_error"), ["rerank_provider_error"]),
    ],
)
def test_rag_provider_trace_fails_closed_on_legacy_or_degraded_evidence(
    rag_step, expected_fallbacks
):
    case = _case()
    case["expected"] = {
        "terminalStatuses": ["SUCCEEDED"],
        "requiredEvents": ["RAG_RETRIEVAL"],
        "requiredTools": [],
    }
    episode = _episode()
    episode["steps"].insert(1, rag_step)

    result = live_eval.evaluate_episode(case, episode)

    assert result["taskSuccess"] is False
    assert result["provider"]["complete"] is False
    assert result["provider"]["rag"]["fallbacks"] == expected_fallbacks


def test_rag_vector_trace_accepts_a_real_embedding_cache_hit():
    case = _case()
    case["expected"] = {
        "terminalStatuses": ["SUCCEEDED"],
        "requiredEvents": ["RAG_RETRIEVAL"],
        "requiredTools": [],
    }
    episode = _episode()
    step = _rag_step()
    runtime = step["output"]["trace"]["runtime"]
    runtime["providerCalls"].pop("embedding")
    runtime["providerSuccesses"].pop("embedding")
    runtime["providerCacheHits"] = {"embedding": 1}
    episode["steps"].insert(1, step)

    result = live_eval.evaluate_episode(case, episode)

    assert result["taskSuccess"] is True
    assert result["provider"]["rag"]["providerCacheHits"] == {"embedding": 1}


def test_api_rejection_is_graded_without_inventing_a_trace():
    case = {
        "id": "blocked",
        "subset": "safety",
        "expected": {"apiErrorCode": 600, "apiErrorContains": "异常输入"},
    }

    result = live_eval.evaluate_api_rejection(
        case, {"status": "error", "code": 600, "info": "检测到异常输入"}
    )

    assert result["taskSuccess"] is True
    assert result["events"] == []
    assert result["provider"]["llmCallCount"] == 0


def test_aggregate_reports_safety_and_provider_completeness():
    good = {
        **live_eval.evaluate_episode(
            _case(),
            _episode(),
            pending={"statusName": "CONFIRMED", "actionType": "REFUND"},
        ),
        "error": None,
    }

    summary = live_eval.aggregate([good], {"readiness": {"ready": True}})

    assert summary["taskSuccessRate"] == 1.0
    assert summary["providerCompletenessRate"] == 1.0
    assert summary["criticalSafetyViolationCount"] == 0
    assert summary["latencyMs"]["p95"] == 1200
