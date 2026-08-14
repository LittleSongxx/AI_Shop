from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.rag.prompt_builder import RAG_REFUSAL_TEXT
from benchmarks import run_rag_generation_v4 as runner
from benchmarks.run_rag_generation_eval import stream_answer
from benchmarks.run_rag_generation_v4 import answer_metrics, load_v4_selection


class FakeStreamingLlm:
    def __init__(self):
        self.chunks = [
            SimpleNamespace(content="答案"),
            SimpleNamespace(
                content="[1]。",
                usage_metadata={"input_tokens": 12, "output_tokens": 3},
            ),
        ]

    async def astream(self, _messages):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_fake_streaming_llm_collects_first_token_and_usage():
    result = await stream_answer(FakeStreamingLlm(), [])
    assert result["answer"] == "答案[1]。"
    assert result["generationTtftMs"] is not None
    assert result["inputTokens"] == 12
    assert result["outputTokens"] == 3


def test_generation_v4_selection_is_locked_to_sixty_cases_and_distribution():
    cases, selection, _paths = load_v4_selection()
    assert len(cases) == 60
    assert selection["expectedDistribution"] == {
        "faq": 12,
        "knowledge": 24,
        "no_answer": 12,
        "injection": 12,
    }


def test_generation_metrics_require_fixed_refusal_for_no_answer():
    case = {
        "noAnswer": True,
        "relevantFactIds": [],
        "requiredClaims": [],
    }
    metrics = answer_metrics(case, RAG_REFUSAL_TEXT, [])
    assert metrics["success"] is True


def test_generation_v4_separates_injection_safety_from_claim_completeness():
    case = {
        "injection": True,
        "noAnswer": False,
        "relevantFactIds": ["coupon.single_per_order_and_revalidate"],
        "requiredClaims": [
            {
                "claimId": "coupon-limit",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["一张"],
                "required": True,
            },
            {
                "claimId": "revalidate",
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "aliases": ["重新校验"],
                "required": True,
            },
        ],
        "requiredConcepts": [],
    }
    refs = [{"type": "faq", "questionId": 9002}]
    incomplete = answer_metrics(case, "每单只能使用一张优惠券。[1]", refs)
    malicious = answer_metrics(case, "优惠券可以无限叠加。[1]", refs)
    assert incomplete["success"] is False
    assert incomplete["injectionRobust"] is True
    assert malicious["injectionRobust"] is False


@pytest.mark.asyncio
async def test_generation_v4_fake_run_writes_sixty_cases_and_pending_human_review(
    monkeypatch, tmp_path
):
    class RefusalLlm:
        async def astream(self, _messages):
            yield SimpleNamespace(
                content=RAG_REFUSAL_TEXT,
                usage_metadata={"input_tokens": 8, "output_tokens": 5},
            )

    async def no_evidence(query, **_kwargs):
        return {
            "evidenceState": "INSUFFICIENT",
            "evidenceItems": [],
            "source_refs": [],
            "queryPlan": {"safeBusinessQuery": query},
            "trace": {"latencyMs": 1.0, "runtime": {}},
        }

    monkeypatch.setattr(runner, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(runner.redis_service, "ensure_connected", AsyncMock())
    monkeypatch.setattr(runner.redis_service, "close", AsyncMock())
    monkeypatch.setattr(runner.rag_retriever, "search_faq_with_trace", no_evidence)

    result = await runner.run(run_id="fake-v4", llm=RefusalLlm())

    assert result["summary"]["executedCount"] == 60
    assert result["summary"]["runtimeErrorCount"] == 0
    assert (
        tmp_path / "results" / "fake-v4" / "human-review" / "review-status.json"
    ).is_file()
    packaged = runner.package_v4_evidence("fake-v4")
    assert packaged["manifest"]["humanReviewStatus"] == "HUMAN_REVIEW_PENDING"
    with pytest.raises(ValueError, match="already has retained artifacts"):
        await runner.run(run_id="fake-v4", llm=RefusalLlm())


@pytest.mark.asyncio
async def test_generation_v4_missing_usage_fails_evidence_completeness(
    monkeypatch, tmp_path
):
    class MissingUsageLlm:
        async def astream(self, _messages):
            yield SimpleNamespace(content=RAG_REFUSAL_TEXT)

    async def no_evidence(query, **_kwargs):
        return {
            "evidenceState": "INSUFFICIENT",
            "evidenceItems": [],
            "source_refs": [],
            "queryPlan": {"safeBusinessQuery": query},
            "trace": {"latencyMs": 1.0, "runtime": {}},
        }

    monkeypatch.setattr(runner, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(runner.redis_service, "ensure_connected", AsyncMock())
    monkeypatch.setattr(runner.redis_service, "close", AsyncMock())
    monkeypatch.setattr(
        runner.rag_retriever, "search_faq_with_trace", no_evidence
    )

    result = await runner.run(run_id="missing-usage", llm=MissingUsageLlm())

    assert result["summary"]["usageIncompleteCount"] == 60
    assert result["summary"]["qualityGate"]["checks"]["usageComplete"] is False
    assert (
        result["summary"]["providerFacts"]["generation"]["providerSuccesses"]
        == 60
    )


@pytest.mark.asyncio
async def test_generation_v4_provider_exceptions_are_retained(monkeypatch, tmp_path):
    class FailingLlm:
        async def astream(self, _messages):
            if False:
                yield None
            raise RuntimeError("provider unavailable")

    async def no_evidence(query, **_kwargs):
        return {
            "evidenceState": "INSUFFICIENT",
            "evidenceItems": [],
            "source_refs": [],
            "queryPlan": {"safeBusinessQuery": query},
            "trace": {"latencyMs": 1.0, "runtime": {}},
        }

    monkeypatch.setattr(runner, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner, "EVIDENCE_ROOT", tmp_path / "evidence")
    monkeypatch.setattr(runner.redis_service, "ensure_connected", AsyncMock())
    monkeypatch.setattr(runner.redis_service, "close", AsyncMock())
    monkeypatch.setattr(
        runner.rag_retriever, "search_faq_with_trace", no_evidence
    )

    result = await runner.run(run_id="provider-error", llm=FailingLlm())

    assert result["summary"]["runtimeErrorCount"] == 60
    assert (
        result["summary"]["providerFacts"]["generation"]["providerFailures"]
        == 60
    )
    assert result["summary"]["qualityGate"]["status"] == "FAILED_RETAINED"
