from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from benchmarks import run_rag_generation_eval as runner


class FakeChunk:
    def __init__(self, content="", *, input_tokens=None, output_tokens=None):
        self.content = content
        self.usage_metadata = {}
        if input_tokens is not None:
            self.usage_metadata["input_tokens"] = input_tokens
        if output_tokens is not None:
            self.usage_metadata["output_tokens"] = output_tokens
        self.response_metadata = {}


class FakeStreamingLlm:
    def __init__(self, answers):
        self.answers = answers

    async def astream(self, messages):
        prompt = str(messages[-1].content)
        answer = next(value for query, value in self.answers.items() if query in prompt)
        midpoint = max(1, len(answer) // 2)
        yield FakeChunk(answer[:midpoint])
        yield FakeChunk(answer[midpoint:])
        yield FakeChunk(input_tokens=120, output_tokens=24)


class FakeStats:
    def __init__(self, payload):
        self.payload = payload

    def snapshot(self):
        return dict(self.payload)


def _ref(case):
    expected = (case.get("relevantRefs") or [])[0]
    if expected["type"] == "faq":
        return {
            "type": "faq",
            "id": f"faq_{expected['questionId']}",
            "questionId": expected["questionId"],
            "source": "FAQ",
            "retrieval": "rerank",
            "score": 0.95,
            "snippet": " ".join(case.get("answerKeywords") or []),
        }
    return {
        "type": "knowledge_chunk",
        "id": f"chunk_{case['id']}",
        "source": expected["source"],
        "heading": expected["heading"],
        "retrieval": "rerank",
        "score": 0.95,
        "snippet": " ".join(case.get("answerKeywords") or []),
    }


def _answer(case):
    if case.get("noAnswer"):
        return runner.REFUSAL_TEXT
    return f"{'、'.join(case.get('answerKeywords') or [])}。[1]"


def test_generation_selection_is_hash_locked_and_balanced():
    cases, selection = runner.load_selection()

    assert [case["id"] for case in cases] == selection["caseIds"]
    assert len(cases) == 10
    assert sum(case.get("subset") == "faq" for case in cases) == 4
    assert sum(case.get("subset") == "knowledge" for case in cases) == 3
    assert sum(case.get("subset") == "no_answer" for case in cases) == 1
    assert sum(case.get("subset") == "injection" for case in cases) == 2
    assert sum(bool(case.get("noAnswer")) for case in cases) == 2


@pytest.mark.asyncio
async def test_stream_answer_collects_first_token_and_provider_usage():
    llm = FakeStreamingLlm({"测试问题": "测试答案。[1]"})

    result = await runner.stream_answer(
        llm, runner.build_evidence_prompt("测试问题", [{"snippet": "测试证据"}])
    )

    assert result["answer"] == "测试答案。[1]"
    assert result["generationTtftMs"] is not None
    assert result["generationLatencyMs"] >= result["generationTtftMs"]
    assert result["inputTokens"] == 120
    assert result["outputTokens"] == 24


def test_answer_metrics_require_keyword_and_valid_citation():
    case = {
        "relevantRefs": [{"type": "faq", "questionId": "9002"}],
        "answerKeywords": ["一张", "优惠券"],
    }
    refs = [{"type": "faq", "questionId": "9002", "snippet": "一张优惠券"}]

    passed = runner._answer_metrics(case, "一次只能使用一张优惠券。[1]", refs)
    invalid = runner._answer_metrics(case, "可以使用一张优惠券。[2]", refs)

    assert passed["success"] is True
    assert passed["citationCorrectness"] == 1.0
    assert passed["labelCitationPrecision"] == 1.0
    assert passed["citationCoverage"] == 1.0
    assert invalid["success"] is False
    assert invalid["invalidCitationIndexes"] == [2]


def test_no_answer_requires_the_exact_refusal_without_citation():
    case = {"noAnswer": True, "relevantRefs": [], "answerKeywords": []}

    assert runner._answer_metrics(case, runner.REFUSAL_TEXT, [])["success"] is True
    assert (
        runner._answer_metrics(case, runner.REFUSAL_TEXT + " [1]", [])["success"]
        is False
    )
    assert runner._answer_metrics(case, "我不知道。", [])["success"] is False


def test_answer_metrics_accept_semantically_duplicate_published_citation():
    case = {
        "relevantRefs": [{"type": "faq", "questionId": "9002"}],
        "answerKeywords": ["一张", "优惠券"],
    }
    refs = [
        {"type": "faq", "questionId": "9002", "snippet": "一张优惠券"},
        {
            "type": "knowledge_chunk",
            "source": "03-membership-and-coupons.md",
            "heading": "使用限制",
            "snippet": "一个订单只能选择一张优惠券",
        },
    ]

    metrics = runner._answer_metrics(case, "一次只能使用一张优惠券。[1][2]", refs)

    assert metrics["success"] is True
    assert metrics["citationCorrectness"] == 1.0
    assert metrics["labelCitationPrecision"] == 0.5


@pytest.mark.asyncio
async def test_full_generation_runner_writes_ten_case_review_template(
    monkeypatch, tmp_path
):
    cases, _selection = runner.load_selection()
    answers = {str(case["query"]): _answer(case) for case in cases}
    refs_by_query = {
        str(case["query"]): ([] if case.get("noAnswer") else [_ref(case)])
        for case in cases
    }

    class FakeRetriever:
        async def search_faq_with_trace(self, query, **_kwargs):
            refs = refs_by_query[query]
            return {
                "source_refs": refs,
                "trace": {"hit": bool(refs), "latencyMs": 1.0},
            }

    @contextmanager
    def embedding_scope(**_kwargs):
        yield FakeStats(
            {
                "bypassCache": True,
                "requests": 10,
                "cacheHits": 0,
                "providerRequests": 10,
                "providerSuccesses": 10,
                "providerFailures": 0,
                "breakerRejections": 0,
            }
        )

    @contextmanager
    def rerank_scope():
        yield FakeStats(
            {
                "eligibleRequests": 8,
                "providerRequests": 8,
                "providerSuccesses": 8,
                "providerFailures": 0,
                "fallbackCount": 0,
                "fallbackReasons": {},
            }
        )

    monkeypatch.setattr(runner, "rag_retriever", FakeRetriever())
    monkeypatch.setattr(runner, "embedding_evaluation_scope", embedding_scope)
    monkeypatch.setattr(runner, "rerank_evaluation_scope", rerank_scope)
    monkeypatch.setattr(runner, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(runner, "BASELINES_ROOT", tmp_path / "baselines")
    monkeypatch.setattr(
        runner.redis_service, "ensure_connected", AsyncMock()
    )
    monkeypatch.setattr(runner.redis_service, "close", AsyncMock())
    monkeypatch.setattr(
        runner,
        "validate_live_contract",
        AsyncMock(return_value={"knowledgeRelease": 5}),
    )

    evaluation, result_dir, failures = await runner.run(
        run_id="fake-live-generation",
        llm=FakeStreamingLlm(answers),
    )

    assert failures == []
    assert evaluation.summary["caseCount"] == 10
    assert evaluation.summary["taskSuccesses"] == 10
    assert evaluation.summary["generationMetrics"] == {
        "keywordCoverage": 1.0,
        "citationCorrectness": 1.0,
        "labelCitationPrecision": 1.0,
        "citationCoverage": 1.0,
        "noAnswerAccuracy": 1.0,
        "injectionRobustness": 1.0,
        "invalidCitationCount": 0,
    }
    review = runner.json.loads(
        (result_dir / "review-template.json").read_text(encoding="utf-8")
    )
    assert review["reviewerType"] == "AI_ASSISTED_INITIAL_REVIEW"
    assert review["status"] == "PENDING"
    assert len(review["cases"]) == 10
    assert all(row["verdict"] == "PENDING" for row in review["cases"])


def test_error_result_is_executed_and_does_not_substitute_an_answer():
    case = {"id": "rag-holdout-001", "subset": "faq", "priority": "P0"}

    result = runner._error_result(
        run_id="error-run", case=case, exc=TimeoutError("provider timeout")
    )

    assert result.status == "ERROR"
    assert result.executed is True
    assert result.task_success is False
    assert result.error_type == "TimeoutError"
    assert result.observations["answerAvailable"] is False
