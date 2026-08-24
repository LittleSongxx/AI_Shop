from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.rag.prompt_builder import RAG_REFUSAL_TEXT
from evaluation.adapters import rag as rag_adapter
from evaluation.adapters.agent import (
    _agent_usage,
    _contains_subset,
    _deterministic_workflow_provider_snapshot,
    _durable_effects,
    _find_action_token,
    _find_owned_pending_action_token,
    _observable_fixture_subset,
    _public_payload_without_secrets_or_untrusted_costs,
    _render_fixture_message,
    _repeated_non_durable_tool_calls,
    _tool_call_budget,
)
from evaluation.adapters.common import provider_complete
from evaluation.core.catalog import _merge_authoritative_availability
from evaluation.core.contracts import (
    CaseResult,
    CaseStatus,
    Domain,
    EvaluationCase,
    PreflightError,
    Split,
)
from evaluation.core.fault_injection import (
    FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
    FailureInjectionScope,
    InjectedFailure,
    active_fault,
    assess_recovery,
    fault_point,
    parse_fault_scenario,
)
from evaluation.core.semantic_judge import (
    build_judge_prompt,
    parse_judge_payload,
    run_semantic_shadow_judge,
)
from evaluation.core.state_diff import build_state_evidence, duplicate_side_effect_count
from evaluation.core.usage import merge_usage, normalize_usage
from evaluation.db_benchmark import QueryObservation, _candidate_selection, _measure
from evaluation.repeat_runner import summarize_repeated_agent


def test_deterministic_workflow_provider_snapshot_requires_route_evidence() -> None:
    episodes = [
        {
            "experiment": {
                "orchestration": {
                    "mode": "workflow",
                    "reason": "deterministic_business_path",
                }
            },
            "steps": [
                {
                    "eventType": "ORCHESTRATION_DECISION",
                    "status": "OK",
                    "nodeName": "orchestration_router",
                },
                {
                    "eventType": "NODE_TRANSITION",
                    "status": "OK",
                    "nodeName": "deterministic_workflow",
                },
            ],
        }
    ]

    snapshot = _deterministic_workflow_provider_snapshot(episodes)

    assert snapshot["notApplicable"] is True
    assert snapshot["notApplicableReason"] == (
        "deterministic_workflow:deterministic_business_path"
    )
    assert snapshot["workflowEvidence"]["fallbackCount"] == 0


def test_deterministic_workflow_provider_snapshot_rejects_fallback_or_missing_trace() -> None:
    fallback_episode = {
        "experiment": {
            "orchestration": {"mode": "workflow", "reason": "configured_workflow"}
        },
        "steps": [
            {
                "eventType": "ORCHESTRATION_FALLBACK",
                "status": "FALLBACK",
                "nodeName": "deterministic_workflow",
            }
        ],
    }
    missing_route_episode = {
        "experiment": {
            "orchestration": {"mode": "workflow", "reason": "configured_workflow"}
        },
        "steps": [],
    }

    assert _deterministic_workflow_provider_snapshot([fallback_episode]) == {}
    assert _deterministic_workflow_provider_snapshot([missing_route_episode]) == {}


def test_order_reference_terminal_trace_proves_llm_not_applicable() -> None:
    snapshot = _deterministic_workflow_provider_snapshot(
        [
            {
                "experiment": {
                    "orderReference": {
                        "outcome": "NO_ELIGIBLE",
                        "route": "finalize",
                        "dependencyError": False,
                    }
                },
                "steps": [
                    {
                        "eventType": "ORDER_REFERENCE_RESOLUTION",
                        "nodeName": "order_reference",
                        "status": "OK",
                    }
                ],
            }
        ]
    )
    assert snapshot["notApplicable"] is True
    assert snapshot["notApplicableReason"] == "deterministic_order_reference:NO_ELIGIBLE"


def test_direct_handoff_trace_proves_llm_not_applicable() -> None:
    snapshot = _deterministic_workflow_provider_snapshot(
        [
            {
                "steps": [
                    {
                        "eventType": "INTENT_DECISION",
                        "nodeName": "api",
                        "status": "OK",
                        "output": {
                            "intent": "COMPLAINT",
                            "next_action": "HANDOFF",
                            "handoff_reason": "FUND_DISPUTE",
                        },
                    },
                    {
                        "eventType": "HANDOFF",
                        "nodeName": "support",
                        "status": "OK",
                        "output": {"reason": "FUND_DISPUTE"},
                    },
                ]
            }
        ]
    )

    complete, facts = provider_complete(
        ["llm"], {"llm": {"requests": 0, "failures": 0, **snapshot}}
    )

    assert snapshot["notApplicableReason"] == "deterministic_handoff:FUND_DISPUTE"
    assert snapshot["workflowEvidence"]["handoffDecisionCount"] == 1
    assert complete == 1
    assert facts["llm"]["notApplicableValid"] is True


def test_direct_handoff_does_not_hide_llm_call_or_orchestration_fallback() -> None:
    base_steps = [
        {
            "eventType": "INTENT_DECISION",
            "status": "OK",
            "output": {"intent": "COMPLAINT", "nextAction": "HANDOFF"},
        },
        {"eventType": "HANDOFF", "status": "OK", "output": {}},
    ]

    assert _deterministic_workflow_provider_snapshot(
        [{"steps": [*base_steps, {"eventType": "LLM_CALL", "status": "ERROR"}]}]
    ) == {}
    assert _deterministic_workflow_provider_snapshot(
        [
            {
                "steps": [
                    *base_steps,
                    {"eventType": "ORCHESTRATION_FALLBACK", "status": "FALLBACK"},
                ]
            }
        ]
    ) == {}


def test_action_token_extraction_is_scoped_and_shape_validated() -> None:
    token = "act_" + "a" * 32
    assert _find_action_token({"authorization": token}) is None
    assert _find_action_token({"token": token}) is None
    assert _find_action_token({"actionToken": token}) == token
    assert _find_action_token({"action_token": token}) == token
    assert _find_action_token({"actionToken": "bearer-secret"}) is None
    assert _find_action_token({"conversation": {"type": "ACTION_CONFIRM", "actionToken": token}}) == token


def test_public_agent_evidence_removes_action_credentials_and_unknown_cost() -> None:
    token = "act_" + "a" * 32
    public = _public_payload_without_secrets_or_untrusted_costs(
        {
            "actionToken": token,
            "assistantMessage": json.dumps(
                {"type": "ACTION_CONFIRM", "actionToken": token}
            ),
            "nested": {"authorization": "Bearer private-evaluation-value"},
            "costCny": 0,
        }
    )

    serialized = json.dumps(public)
    assert token not in serialized
    assert public["actionToken"] == "[REDACTED_SECRET]"
    assert "[REDACTED_ACTION_TOKEN]" in public["assistantMessage"]
    assert public["nested"]["authorization"] == "[REDACTED_SECRET]"
    assert public["costCny"] is None


@pytest.mark.asyncio
async def test_owned_pending_action_token_requires_exact_server_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "act_" + "a" * 32
    lookup = AsyncMock(
        return_value={
            "token": token,
            "userId": "eval-user",
            "runId": "run-1",
            "actionType": "CANCEL_ORDER",
        }
    )
    monkeypatch.setattr(
        "evaluation.adapters.agent.pending_action_store.get_unique_pending_for_run",
        lookup,
    )

    found = await _find_owned_pending_action_token(
        user_id="eval-user",
        run_id="run-1",
        action_type="CANCEL_ORDER",
    )

    assert found == token
    lookup.assert_awaited_once_with(
        user_id="eval-user",
        run_id="run-1",
        action_type="CANCEL_ORDER",
    )


@pytest.mark.asyncio
async def test_owned_pending_action_token_rejects_wrong_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "evaluation.adapters.agent.pending_action_store.get_unique_pending_for_run",
        AsyncMock(
            return_value={
                "token": "act_" + "a" * 32,
                "userId": "other-user",
                "runId": "run-1",
                "actionType": "CANCEL_ORDER",
            }
        ),
    )

    with pytest.raises(RuntimeError, match="wrong ownership"):
        await _find_owned_pending_action_token(
            user_id="eval-user",
            run_id="run-1",
            action_type="CANCEL_ORDER",
        )


@pytest.mark.asyncio
async def test_owned_pending_action_token_uses_bounded_visibility_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.adapters import agent as agent_adapter

    token = "act_" + "b" * 32
    lookup = AsyncMock(side_effect=[None, None, token])
    monkeypatch.setattr(agent_adapter, "_find_owned_pending_action_token", lookup)
    monkeypatch.setattr(agent_adapter, "_ACTION_TOKEN_POLL_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(agent_adapter, "_ACTION_TOKEN_POLL_INTERVAL_SECONDS", 0.01)

    found, evidence = await agent_adapter._poll_owned_pending_action_token(
        user_id="eval-user",
        run_id="run-1",
        action_type="CANCEL_ORDER",
    )

    assert found == token
    assert evidence["state"] == "FOUND"
    assert evidence["attempts"] == 3
    assert evidence["elapsedMs"] >= 0
    assert lookup.await_count == 3


@pytest.mark.asyncio
async def test_owned_pending_action_token_poll_timeout_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evaluation.adapters import agent as agent_adapter

    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(agent_adapter, "_find_owned_pending_action_token", lookup)
    monkeypatch.setattr(agent_adapter, "_ACTION_TOKEN_POLL_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(agent_adapter, "_ACTION_TOKEN_POLL_INTERVAL_SECONDS", 0.02)

    found, evidence = await agent_adapter._poll_owned_pending_action_token(
        user_id="eval-user",
        run_id="run-1",
        action_type="CANCEL_ORDER",
    )

    assert found is None
    assert evidence["state"] == "NOT_FOUND"
    assert evidence["attempts"] >= 2
    assert lookup.await_count == evidence["attempts"]


def test_fixture_message_renders_only_declared_placeholders() -> None:
    rendered, fields = _render_fixture_message(
        "取消订单 {orderId}，保留任意 {unknown}",
        {"orderId": "EVAL123"},
    )
    assert rendered == "取消订单 EVAL123，保留任意 {unknown}"
    assert fields == ["orderId"]


def test_fixture_message_rejects_unresolved_sensitive_placeholder() -> None:
    with pytest.raises(RuntimeError, match="unresolved"):
        _render_fixture_message(
            "取消订单 {orderItemId}",
            {"orderId": "EVAL123"},
        )


def test_fixture_tool_argument_matches_episode_identifier_fingerprint() -> None:
    fixture_order_id = "20260821123456789ABCDEF123456789"
    other_order_id = "20260821123456789FEDCBA987654321"
    captured_input = {
        "args": _observable_fixture_subset("orderId", fixture_order_id),
    }
    expected = _observable_fixture_subset("orderId", fixture_order_id)

    assert _contains_subset(captured_input, expected)
    assert not _contains_subset(
        captured_input,
        _observable_fixture_subset("orderId", other_order_id),
    )
    assert fixture_order_id not in json.dumps(expected)


class _Prompt:
    def messages(self) -> list[str]:
        return ["ground only on supplied evidence"]


class _RetryingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _messages: object) -> SimpleNamespace:
        self.calls += 1
        if self.calls == 1:
            timeout_error = type("APITimeoutError", (Exception,), {})
            raise timeout_error("transient timeout")
        return SimpleNamespace(
            content="证据不足，无法确认。",
            response_metadata={"model_name": "test-model"},
            usage_metadata={"input_tokens": 12, "output_tokens": 6},
        )


def test_rag_generation_has_no_gold_contract_input_and_records_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _RetryingLLM()
    monkeypatch.setenv("AI_EVAL_RAG_LLM_RETRIES", "1")
    monkeypatch.setenv("AI_EVAL_RAG_LLM_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(rag_adapter, "_evaluation_llm", lambda **_kwargs: llm)
    monkeypatch.setattr(rag_adapter, "build_grounding_prompt", lambda *_args, **_kwargs: _Prompt())
    monkeypatch.setattr(rag_adapter, "grounding_repair_reason", lambda *_args, **_kwargs: None)

    answer, facts = asyncio.run(
        rag_adapter._generate(
            "测试问题",
            {"evidenceState": "INSUFFICIENT", "evidenceItems": []},
        )
    )

    assert "expected" not in inspect.signature(rag_adapter._generate).parameters
    assert answer == "证据不足，无法确认。"
    assert facts["requests"] == 2
    assert facts["successes"] == 1
    assert facts["failures"] == 1
    assert facts["retries"] == 1
    assert facts["usage"]["providerCalls"] == 2
    assert facts["usage"]["retryCount"] == 1
    assert facts["usage"]["costStatus"] == "MISSING_USAGE"
    complete, decisions = provider_complete(["llm"], {"llm": facts})
    assert complete == 1
    assert decisions["llm"]["facts"]["failureAttempts"][0]["retryable"] is True


class _InvalidGroundingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _messages: object) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(
            content=RAG_REFUSAL_TEXT,
            response_metadata={"model_name": "test-model"},
            usage_metadata={"input_tokens": 12, "output_tokens": 6},
        )


def test_rag_grounding_policy_fallback_is_evidence_bound_and_not_an_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _InvalidGroundingLLM()
    monkeypatch.setenv("AI_EVAL_RAG_LLM_RETRIES", "0")
    monkeypatch.setattr(rag_adapter, "_evaluation_llm", lambda **_kwargs: llm)

    answer, facts = asyncio.run(
        rag_adapter._generate(
            "RAG检索不足时的grounding含义是什么？",
            {
                "evidenceState": "SUPPORTED",
                "evidenceItems": [
                    {
                        "citation": 1,
                        "factIds": ["rag.retrieval_and_abstention"],
                        "text": "证据不足时明确说明并建议联系人工客服。",
                    }
                ],
            },
        )
    )

    assert answer.count("[1]") == 2
    assert "证据不足" in answer
    assert facts["boundedRepairAttempted"] is True
    assert facts["deterministicFallbackUsed"] is True
    assert facts["deterministicFallback"]["factId"] == "rag.retrieval_and_abstention"
    assert llm.calls == 2
    assert facts["usage"]["providerCalls"] == 2


class _RepairFailureGroundingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _messages: object) -> SimpleNamespace:
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=RAG_REFUSAL_TEXT,
                response_metadata={"model_name": "test-model"},
                usage_metadata={"input_tokens": 12, "output_tokens": 6},
            )
        timeout_error = type("APITimeoutError", (Exception,), {})
        raise timeout_error("repair timeout")


def test_rag_grounding_policy_fallback_keeps_repair_failure_in_usage_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _RepairFailureGroundingLLM()
    monkeypatch.setenv("AI_EVAL_RAG_LLM_RETRIES", "0")
    monkeypatch.setattr(rag_adapter, "_evaluation_llm", lambda **_kwargs: llm)

    answer, facts = asyncio.run(
        rag_adapter._generate(
            "证据不足时grounding要求系统怎样回答？",
            {
                "evidenceState": "SUPPORTED",
                "evidenceItems": [
                    {
                        "citation": 1,
                        "factIds": ["rag.retrieval_and_abstention"],
                        "text": "证据不足时明确说明并建议联系人工客服。",
                    }
                ],
            },
        )
    )

    assert "证据不足" in answer
    assert facts["deterministicFallbackUsed"] is True
    assert facts["failures"] == 1
    assert facts["repairRemaining"] == "repair provider failed: APITimeoutError"
    assert facts["usage"]["providerCalls"] == 2


@pytest.mark.parametrize("provider", ["embedding", "rerank", "llm"])
def test_provider_not_applicable_requires_reason_and_zero_calls(provider: str) -> None:
    request_key = "requests" if provider == "llm" else "providerRequests"
    failure_key = "failures" if provider == "llm" else "providerFailures"
    valid = {
        "notApplicable": True,
        "notApplicableReason": "deterministic_short_path",
        request_key: 0,
        failure_key: 0,
    }
    complete, decisions = provider_complete([provider], {provider: valid})
    assert complete == 1
    assert decisions[provider]["notApplicableValid"] is True

    for invalid in (
        {**valid, "notApplicableReason": ""},
        {**valid, request_key: 1},
        {**valid, failure_key: 1},
    ):
        complete, decisions = provider_complete([provider], {provider: invalid})
        assert complete == 0
        assert decisions[provider]["notApplicableValid"] is False


@pytest.mark.parametrize(
    "snapshot",
    [
        {"products": [], "total_stocks": {"p1": 1}},
        {"products": [{"product_id": "p1", "status": 1}], "total_stocks": {}},
        {"products": [{"product_id": "p1", "status": 1}], "total_stocks": None},
        {"products": [{"product_id": "p1", "status": 1}], "total_stocks": {"p1": -1}},
        {"products": [{"product_id": "p1", "status": 1}], "total_stocks": {"p1": "nan"}},
        {"products": [{"product_id": "p1", "status": 1}], "total_stocks": {"p1": 1.5}},
    ],
)
def test_catalog_merge_rejects_missing_or_invalid_authoritative_facts(snapshot: dict) -> None:
    with pytest.raises(PreflightError):
        _merge_authoritative_availability(
            [{"productId": "p1", "productName": "test", "status": 1}], snapshot
        )


def test_catalog_merge_marks_off_shelf_and_zero_stock_products_unavailable() -> None:
    merged = _merge_authoritative_availability(
        [
            {"productId": "off", "productName": "off", "status": 1},
            {"productId": "empty", "productName": "empty", "status": 1},
        ],
        {
            "products": [
                {"product_id": "off", "status": 0},
                {"product_id": "empty", "status": 1},
            ],
            "total_stocks": {"off": 10, "empty": 0},
        },
    )

    assert merged[0]["indexStatus"] == 1
    assert merged[0]["authoritativeAvailable"] is False
    assert merged[1]["inStock"] is False
    assert merged[1]["authoritativeAvailable"] is False


def test_missing_usage_and_unknown_price_are_never_zero_cost_claims() -> None:
    missing = normalize_usage(None, provider="llm", default_calls=1)
    unpriced = normalize_usage(
        {"inputTokens": 10, "outputTokens": 4, "providerCalls": 1}, provider="llm"
    )
    priced = normalize_usage(
        {"inputTokens": 1_000_000, "outputTokens": 500_000, "providerCalls": 1},
        pricing={"input": 2, "output": 4},
    )

    assert missing["costStatus"] == "MISSING_USAGE"
    assert missing["costCny"] is None
    assert missing["missingUsageCalls"] == 1
    assert unpriced["costStatus"] == "UNPRICED"
    assert unpriced["costCny"] is None
    assert priced["costStatus"] == "PRICED"
    assert priced["costCny"] == pytest.approx(4)
    assert merge_usage([priced, unpriced])["costCny"] is None


def test_zero_call_usage_is_neutral_when_real_provider_usage_is_aggregated() -> None:
    deterministic = normalize_usage(None, provider="agent-runtime", default_calls=0)
    unpriced = normalize_usage(
        {"inputTokens": 10, "outputTokens": 4, "providerCalls": 1}, provider="llm"
    )

    merged = merge_usage([deterministic, unpriced])

    assert merged["providerCalls"] == 1
    assert merged["missingUsageCalls"] == 0
    assert merged["costStatus"] == "UNPRICED"
    assert merged["usageReported"] is True
    assert deterministic["costStatus"] == "NOT_APPLICABLE"
    assert deterministic["usageSource"] == "not_applicable"


def test_real_call_without_usage_marks_aggregate_missing() -> None:
    missing = normalize_usage(None, provider="llm", default_calls=1)
    unpriced = normalize_usage(
        {"inputTokens": 10, "outputTokens": 4, "providerCalls": 1}, provider="llm"
    )

    merged = merge_usage([missing, unpriced])

    assert merged["providerCalls"] == 2
    assert merged["missingUsageCalls"] == 1
    assert merged["costStatus"] == "MISSING_USAGE"
    assert merged["costCny"] is None
    assert merged["usageReported"] is False


def test_agent_repeated_read_tool_is_efficiency_signal_not_duplicate_side_effect() -> None:
    episodes = [
        {
            "steps": [
                {"eventType": "TOOL_CALL", "toolName": "SEARCH_PRODUCTS"},
                {"eventType": "TOOL_CALL", "toolName": "SEARCH_PRODUCTS"},
            ]
        }
    ]

    effects = _durable_effects(episodes, state_mode="READ_ONLY")
    budget = _tool_call_budget(
        ["SEARCH_PRODUCTS", "SEARCH_PRODUCTS"], {"SEARCH_PRODUCTS": 1}
    )

    assert effects == []
    assert duplicate_side_effect_count(effects) == 0
    assert budget["satisfied"] is False
    assert _repeated_non_durable_tool_calls(episodes, state_mode="READ_ONLY") == 1


def test_agent_repeated_write_is_a_duplicate_durable_side_effect() -> None:
    write_step = {
        "eventType": "ORDER_CREATED",
        "toolName": "CREATE_ORDER",
        "mutatesState": True,
        "businessKey": "checkout-1",
        "resourceId": "order-1",
    }
    effects = _durable_effects(
        [{"steps": [write_step, dict(write_step)]}], state_mode="WRITE_CONFIRMED"
    )

    assert len(effects) == 2
    assert duplicate_side_effect_count(effects) == 1


def test_agent_usage_counts_exact_llm_steps_and_preserves_missing_usage() -> None:
    usage = _agent_usage(
        [
            {
                "inputTokens": 12,
                "outputTokens": 5,
                "steps": [
                    {
                        "eventType": "LLM_CALL",
                        "status": "OK",
                        "modelName": "model-a",
                        "input": {"fallback": False},
                        "output": {
                            "model": "model-a",
                            "inputTokens": 12,
                            "outputTokens": 5,
                        },
                    },
                    {"eventType": "TOOL_CALL", "toolName": "SEARCH_PRODUCTS"},
                    {
                        "eventType": "LLM_CALL",
                        "status": "ERROR",
                        "modelName": "model-a",
                        "input": {"fallback": True},
                        "output": None,
                    },
                ],
            }
        ]
    )

    assert usage["providerCalls"] == 2
    assert usage["inputTokens"] == 12
    assert usage["outputTokens"] == 5
    assert usage["missingUsageCalls"] == 1
    assert usage["fallbackCalls"] == 1
    assert usage["costStatus"] == "MISSING_USAGE"
    assert usage["costCny"] is None
    assert usage["tokenTotalsMatchEpisodes"] is True


def test_agent_usage_does_not_turn_zero_llm_calls_into_one() -> None:
    usage = _agent_usage(
        [{"inputTokens": 0, "outputTokens": 0, "modelName": None, "steps": []}]
    )

    assert usage["providerCalls"] == 0
    assert usage["missingUsageCalls"] == 0
    assert usage["costStatus"] == "NOT_APPLICABLE"
    assert usage["costCny"] is None


def test_state_diff_is_hashed_structured_and_read_only_fails_on_write() -> None:
    before = {"order": {"status": "PENDING", "stock": 2}}
    after = {"order": {"status": "PAID", "stock": 1}}
    evidence = build_state_evidence(
        before,
        after,
        assertions=[
            {"path": "/order/status", "operator": "equals", "value": "PAID"},
            {"path": "/order/stock", "operator": "delta", "value": -1},
        ],
        read_only=True,
    )

    assert evidence["beforeHash"] != evidence["afterHash"]
    assert evidence["changeCount"] == 2
    assert evidence["matched"] is False
    assert duplicate_side_effect_count(
        [
            {"type": "ORDER_CREATED", "businessKey": "k", "resourceId": "1"},
            {"type": "ORDER_CREATED", "businessKey": "k", "resourceId": "1"},
        ]
    ) == 1


def test_fault_injection_is_context_local_and_restored() -> None:
    scenario = parse_fault_scenario(
        {
            "id": "embedding-timeout",
            "target": "embedding",
            "mode": "timeout",
            "expected": {"unsafeAnswer": False, "hardConstraintBypass": False},
        }
    )
    assert active_fault("embedding") is None
    with FailureInjectionScope(scenario) as scope:
        with pytest.raises(InjectedFailure):
            fault_point("embedding")
        assert fault_point("embedding") is None
        assert len(scope.events) == 1
        assert scope.events[0] == {
            "eventType": "FAULT_INJECTED",
            "scenarioId": "embedding-timeout",
            "target": "embedding",
            "mode": "timeout",
            "faultEvidenceLevel": FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
        }
    assert active_fault("embedding") is None


def test_fault_recovery_fails_closed_on_missing_metrics_or_error_case() -> None:
    scenario = parse_fault_scenario(
        {
            "id": "embedding-timeout",
            "target": "embedding",
            "mode": "timeout",
            "expected": {
                "fallbackAllowed": True,
                "unsafeAnswer": False,
                "hardConstraintBypass": False,
                "terminalState": "DEGRADED",
            },
        }
    )
    trace = [
        {
            "eventType": "FAULT_INJECTED",
            "scenarioId": scenario.scenario_id,
            "target": scenario.target,
            "mode": scenario.mode,
            "faultEvidenceLevel": FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
        }
    ]
    base = {
        "failureTrace": trace,
        "fallbackUsed": True,
        "unsafeAnswer": False,
        "hardConstraintBypass": False,
        "terminalState": "DEGRADED",
        "faultEvidenceLevel": FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
        "requestOutcomeObserved": True,
        "caseStatus": "FAILED",
        "nextRequestRecovered": True,
    }
    assert assess_recovery(scenario, base)["passed"] is True

    missing_safety = assess_recovery(scenario, {**base, "unsafeAnswer": None})
    assert missing_safety["passed"] is False
    assert next(
        row for row in missing_safety["checks"] if row["name"] == "unsafeAnswer"
    )["observed"] is None

    error_case = assess_recovery(
        scenario,
        {**base, "caseStatus": "ERROR", "requestOutcomeObserved": False},
    )
    assert error_case["passed"] is False
    assert next(
        row for row in error_case["checks"] if row["name"] == "caseStatusNotError"
    )["passed"] is False


def test_fault_recovery_preserves_not_applicable_hard_constraint_state() -> None:
    scenario = parse_fault_scenario(
        {
            "id": "redis-checkpoint-failure",
            "target": "redis-checkpoint",
            "mode": "exception",
            "gateMode": "HARD",
            "expected": {
                "fallbackAllowed": False,
                "unsafeAnswer": False,
                "hardConstraintBypass": "NOT_APPLICABLE",
                "terminalState": "SUCCEEDED",
            },
        }
    )
    trace = [
        {
            "eventType": "FAULT_INJECTED",
            "scenarioId": scenario.scenario_id,
            "target": scenario.target,
            "mode": scenario.mode,
            "faultEvidenceLevel": FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
        }
    ]
    evidence = assess_recovery(
        scenario,
        {
            "failureTrace": trace,
            "fallbackUsed": False,
            "unsafeAnswer": False,
            "hardConstraintBypass": "NOT_APPLICABLE",
            "terminalState": "SUCCEEDED",
            "faultEvidenceLevel": FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
            "requestOutcomeObserved": True,
            "caseStatus": "FAILED",
            "nextRequestRecovered": True,
        },
    )
    assert evidence["passed"] is True
    assert next(
        row for row in evidence["checks"] if row["name"] == "hardConstraintBypass"
    ) == {
        "name": "hardConstraintBypass",
        "expected": "NOT_APPLICABLE",
        "observed": "NOT_APPLICABLE",
        "passed": True,
    }


def test_non_production_fault_boundary_cannot_enter_hard_gate() -> None:
    with pytest.raises(Exception, match="cannot use HARD gate"):
        parse_fault_scenario(
            {
                "id": "request-duplicate",
                "target": "request",
                "mode": "duplicate",
                "gateMode": "HARD",
                "expected": {
                    "unsafeAnswer": False,
                    "hardConstraintBypass": False,
                },
            }
        )
    scenario = parse_fault_scenario(
        {
            "id": "request-duplicate",
            "target": "request",
            "mode": "duplicate",
            "gateMode": "SHADOW",
            "expected": {
                "unsafeAnswer": False,
                "hardConstraintBypass": False,
            },
        }
    )
    assert scenario.public()["declaredEvidenceLevel"] == "HARNESS_BOUNDARY"
    assert scenario.gate_mode == "SHADOW"


@pytest.mark.parametrize(
    "target,mode",
    [
        ("redis-checkpoint", "exception"),
        ("worker-deadline", "timeout"),
        ("mcp-tool", "5xx"),
    ],
)
def test_authorized_cross_process_boundaries_can_enter_hard_gate(
    target: str, mode: str
) -> None:
    scenario = parse_fault_scenario(
        {
            "id": f"{target}-{mode}",
            "target": target,
            "mode": mode,
            "gateMode": "HARD",
            "expected": {
                "unsafeAnswer": False,
                "hardConstraintBypass": False,
            },
        }
    )
    assert scenario.public()["declaredEvidenceLevel"] == "PRODUCTION_BOUNDARY"
    assert scenario.gate_mode == "HARD"


def test_db_benchmark_uses_unique_candidates_and_observed_driver_counts() -> None:
    selection = _candidate_selection(100)
    assert selection["candidateCount"] == 100
    assert selection["uniqueCandidateCount"] == 100
    assert len(selection["ids"]) == len(set(selection["ids"])) == 100
    assert selection["catalogFixtureCandidateCount"] > 0
    assert selection["nonFixtureCandidateCount"] > 0

    async def observed(product_ids: object) -> QueryObservation:
        count = len(product_ids)  # type: ignore[arg-type]
        return QueryObservation(
            round_trips=count,
            connection_acquisitions=1,
            returned_rows=count - 1,
            result_sha256="a" * 64,
        )

    result = asyncio.run(_measure(observed, selection["ids"][:10], iterations=3))
    assert result["roundTrips"] == 10
    assert result["roundTripsPerSuccessfulIteration"] == [10, 10, 10]
    assert result["totalRoundTrips"] == 30
    assert result["connectionAcquisitions"] == 1
    assert result["totalConnectionAcquisitions"] == 3
    assert result["returnedRowsPerSuccessfulIteration"] == [9, 9, 9]
    assert result["resultSha256PerSuccessfulIteration"] == ["a" * 64] * 3
    assert result["stableResult"] is True
    assert result["counterSource"] == "COUNTED_CURSOR_EXECUTE_AND_POOL_ACQUIRE_CALLS"


def test_semantic_judge_validates_span_and_marks_provider_failure_unavailable() -> None:
    answer = "支持七天退货"
    valid = {
        "judgments": [
            {
                "claimId": "returns",
                "answerSpan": {"start": 0, "end": len(answer), "text": answer},
                "evidenceFactIds": ["returns.seven-days"],
                "evidenceSourceIds": ["policy-v3"],
                "label": "SUPPORTED",
                "confidence": 0.9,
                "abstainReason": None,
            }
        ]
    }
    rows = parse_judge_payload(valid, claim_ids=["returns"], answer=answer)
    assert rows[0]["label"] == "SUPPORTED"
    assert rows[0]["spanNormalized"] is False
    assert rows[0]["answerSpan"]["candidateId"] is None
    wrong_unicode_offset = {
        "judgments": [
            {
                **valid["judgments"][0],
                "answerSpan": {"start": 0, "end": 2, "text": answer},
            }
        ]
    }
    normalized = parse_judge_payload(
        wrong_unicode_offset, claim_ids=["returns"], answer=answer
    )
    assert normalized[0]["answerSpan"] == {
        "candidateId": None,
        "start": 0,
        "end": len(answer),
        "text": answer,
    }
    assert normalized[0]["spanNormalized"] is True
    invalid = {"judgments": [{**valid["judgments"][0], "answerSpan": {"start": 99, "end": 100, "text": "x"}}]}
    with pytest.raises(Exception, match="out of bounds"):
        parse_judge_payload(invalid, claim_ids=["returns"], answer=answer)
    empty_span = {
        "judgments": [
            {
                **valid["judgments"][0],
                "answerSpan": {"start": 0, "end": 0, "text": ""},
            }
        ]
    }
    with pytest.raises(Exception, match="out of bounds"):
        parse_judge_payload(empty_span, claim_ids=["returns"], answer=answer)
    with pytest.raises(Exception, match="unknown evidence fact"):
        parse_judge_payload(
            valid,
            claim_ids=["returns"],
            answer=answer,
            evidence_fact_ids=["different-fact"],
        )

    async def fail(_: str):
        raise TimeoutError("judge timeout")

    result = asyncio.run(
        run_semantic_shadow_judge(
            answer=answer,
            claims=[{"claimId": "returns"}],
            evidence=[],
            invoke=fail,
            provider="test",
            model="judge",
            timeout_seconds=0.01,
            retries=1,
        )
    )
    assert result["available"] is False
    assert result["judgments"][0]["label"] == "UNAVAILABLE"
    assert result["hardGate"] is False
    assert result["attempts"] == 2
    assert result["retryCount"] == 1
    assert [row["type"] for row in result["failureAttempts"]] == [
        "TimeoutError",
        "TimeoutError",
    ]


def test_semantic_judge_prompt_supplies_exact_auditable_span_candidates() -> None:
    answer = "退款重试耗尽后进入人工复核。管理员随后处理！"
    prompt, _digest = build_judge_prompt(
        answer=answer,
        claims=[{"claimId": "manual"}],
        evidence=[],
    )
    candidates = json.loads(prompt)["untrusted"]["allowedAnswerSpans"]
    assert candidates == [
        {
            "candidateId": "span-1",
            "start": 0,
            "end": 14,
            "text": "退款重试耗尽后进入人工复核。",
        },
        {
            "candidateId": "span-2",
            "start": 14,
            "end": len(answer),
            "text": "管理员随后处理！",
        },
    ]
    valid = {
        "judgments": [
            {
                "claimId": "manual",
                "answerSpan": candidates[0],
                "evidenceFactIds": [],
                "evidenceSourceIds": [],
                "label": "SUPPORTED",
                "confidence": 0.9,
                "abstainReason": None,
            }
        ]
    }
    parsed = parse_judge_payload(valid, claim_ids=["manual"], answer=answer)
    assert parsed[0]["answerSpan"] == candidates[0]
    valid["judgments"][0]["answerSpan"] = {**candidates[0], "text": "重述文本"}
    with pytest.raises(Exception, match="candidate is invalid"):
        parse_judge_payload(valid, claim_ids=["manual"], answer=answer)


def test_semantic_judge_prompt_hides_fact_ids_outside_claim_allowlists() -> None:
    prompt, _digest = build_judge_prompt(
        answer="订单详情可查看轨迹。",
        claims=[{"claimId": "tracking", "factIds": ["logistics.tracking"]}],
        evidence=[
            {
                "citation": 1,
                "factIds": ["logistics.tracking", "unrelated.fact"],
                "ref": {
                    "id": "knowledge-1",
                    "factIds": ["logistics.tracking", "unrelated.fact"],
                },
                "text": "订单详情可查看轨迹。",
            }
        ],
    )
    contract = json.loads(prompt)
    claim = contract["untrusted"]["claims"][0]
    evidence = contract["untrusted"]["evidence"][0]

    assert claim["allowedEvidenceFactIds"] == ["logistics.tracking"]
    assert evidence["factIds"] == ["logistics.tracking"]
    assert evidence["ref"]["factIds"] == ["logistics.tracking"]


def test_repeated_agent_summary_fails_closed_on_missing_state_diff() -> None:
    case = EvaluationCase(
        case_id="agent-dev-repeat-contract",
        split=Split.DEVELOPMENT,
        domain=Domain.AGENT,
        input={"turns": [{"message": "确认下单"}]},
        expected={"requiredTools": ["create_order"], "terminalStatuses": ["SUCCEEDED"]},
        required_providers=("agent-runtime",),
        tags=("critical", "write"),
    )
    result = CaseResult(
        case_id=case.case_id,
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={"terminalStateCorrectness": 1, "retryIdempotency": 1},
        latency_ms=1,
        output={"tools": ["create_order"]},
        providers={},
        assertions=[],
        trial_id="trial-1",
        usage=normalize_usage(None),
    )

    summary = summarize_repeated_agent([case], [result], k=1)

    assert summary["pass^1"] == 1
    assert summary["stateDiffMatchRate"] == 0
    assert summary["hardGate"]["passed"] is False


def test_repeated_agent_summary_uses_all_cases_when_critical_labels_are_empty() -> None:
    case = EvaluationCase(
        case_id="agent-dev-unlabelled-critical",
        split=Split.DEVELOPMENT,
        domain=Domain.AGENT,
        input={"turns": [{"message": "查商品"}]},
        expected={"terminalStatuses": ["SUCCEEDED"]},
        required_providers=("agent-runtime",),
    )
    result = CaseResult(
        case_id=case.case_id,
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={"terminalStateCorrectness": 1},
        latency_ms=1,
        output={},
        providers={},
        assertions=[],
        trial_id="trial-1",
        state_diff={"matched": True, "duplicateSideEffectCount": 0},
        usage=normalize_usage(None),
    )

    summary = summarize_repeated_agent([case], [result], k=1)

    assert summary["criticalCaseCount"] == 1
    assert summary["criticalSelectionPolicy"] == (
        "ALL_AGENT_CASES_FALLBACK_WHEN_NO_CRITICAL_LABEL"
    )
    assert summary["criticalWorkflowPassPower"] == 1.0
    assert summary["hardGate"]["passed"] is True
