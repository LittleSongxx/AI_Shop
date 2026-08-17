from __future__ import annotations

from typing import Any, Mapping

import httpx
import pytest

from benchmarks import run_task_success_v2_eval as v2


def test_runtime_state_references_resolve_typed_values() -> None:
    state = {
        "steps": {
            "search": {
                "message": {
                    "bizData": [
                        {"productId": "p1", "position": 1},
                        {"productId": "p2", "position": 2},
                    ]
                }
            }
        }
    }
    value = {
        "ids": [
            {"$state": "steps.search.message.bizData.0.productId"},
            {"$state": "steps.search.message.bizData.1.productId"},
        ],
        "position": {"$state": "steps.search.message.bizData.0.position"},
    }

    assert v2.resolve_state_references(value, state) == {
        "ids": ["p1", "p2"],
        "position": 1,
    }


def test_runtime_state_references_fail_closed_when_path_is_missing() -> None:
    with pytest.raises(v2.EvaluationContractError, match="does not exist"):
        v2.resolve_state_references({"$state": "steps.search.data.requestId"}, {})


def test_state_checks_support_wildcards_without_exposing_values() -> None:
    checks = [
        {"name": "all_sony", "path": "rows.*.brand", "op": "allEq", "value": "Sony"},
        {"name": "two_unique", "path": "rows.*.id", "op": "uniqueCountEq", "value": 2},
        {"name": "no_forgery", "path": "rows.*.id", "op": "notContains", "value": "bad"},
    ]

    assertions = v2.evaluate_state_checks(
        checks,
        {"rows": [{"id": "secret-1", "brand": "Sony"}, {"id": "secret-2", "brand": "Sony"}]},
    )

    assert all(item["passed"] for item in assertions)
    assert "secret-1" not in str(assertions)
    assert "secret-2" not in str(assertions)


def test_expected_antifraud_rejection_is_a_passing_safety_assertion() -> None:
    assertions = v2._envelope_assertions(
        {"envelopeCode": 600, "infoContains": "invalid attribution"},
        {"code": 600, "info": "invalid attribution or expired", "data": None},
    )

    assert all(item["passed"] for item in assertions)
    assert all(item["category"] == "SAFETY" for item in assertions)


class _UnusedStateReader:
    async def database_now(self) -> str:
        return "2026-08-17T10:00:00"

    async def read(self, source: str, query: Mapping[str, Any], state: Mapping[str, Any]) -> Any:
        raise AssertionError("state reader must not be called")


@pytest.mark.asyncio
async def test_payment_success_requires_private_internal_token() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
        evaluator = v2.SequenceEvaluator(
            client=client,
            agent_base_url="http://agent",
            gateway_base_url="http://gateway",
            internal_token=None,
            timeout_seconds=1,
            expected_configured_mode="adaptive",
            state_reader=_UnusedStateReader(),
        )
        state = {"case": {"authToken": "user-token", "userId": "u1"}}

        with pytest.raises(v2.EvaluationContractError, match="AISHOP_INTERNAL_TOKEN"):
            await evaluator._dispatch(
                "markPaymentSuccess",
                {"payOrderId": "pay-1", "channelOrderId": "channel-1"},
                {"envelopeCode": 200},
                state,
            )


class _StubSequenceEvaluator(v2.SequenceEvaluator):
    def __init__(self, *, provider_complete: bool) -> None:
        self.expected_configured_mode = "adaptive"
        self.provider_complete = provider_complete
        self.state_reader = _UnusedStateReader()

    async def _dispatch(
        self,
        action: str,
        params: Mapping[str, Any],
        expect: Mapping[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "data": {"ok": True},
            "assertions": [],
            "provider": {
                "complete": self.provider_complete,
                "llmCallCount": 1,
                "failedLlmCalls": 0 if self.provider_complete else 1,
                "modelNames": ["real-model"],
                "rag": {"complete": True, "retrievalEventCount": 0},
            },
            "metrics": {
                "traceSteps": 1,
                "inputTokens": 10,
                "outputTokens": 5,
                "costCny": 0.01,
                "ttftMs": 10,
            },
        }


def _minimal_sequence() -> dict[str, Any]:
    return {
        "id": "sequence-test",
        "subset": "sequence",
        "input": {"authToken": "token", "expectedUserId": "u1"},
        "steps": [
            {
                "id": "send",
                "action": "sendMessage",
                "params": {"message": "hello"},
                "expect": {},
            }
        ],
        "expected": {
            "requiredActions": ["sendMessage"],
            "providerComplete": True,
            "maxActions": 1,
        },
    }


@pytest.mark.asyncio
async def test_sequence_fails_closed_on_incomplete_provider_evidence() -> None:
    result = await _StubSequenceEvaluator(provider_complete=False).execute(_minimal_sequence())

    assert result["taskSuccess"] is False
    assert result["provider"]["complete"] is False
    provider_assertion = next(
        item for item in result["assertions"] if item["name"] == "sequence_provider_complete"
    )
    assert provider_assertion["passed"] is False


@pytest.mark.asyncio
async def test_sequence_accepts_complete_provider_evidence() -> None:
    result = await _StubSequenceEvaluator(provider_complete=True).execute(_minimal_sequence())

    assert result["taskSuccess"] is True
    assert result["provider"]["complete"] is True
