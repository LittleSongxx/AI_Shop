from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evaluation.core.io import atomic_write_json, load_json, load_jsonl
from evaluation.customer_service_answer_review import (
    ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
)
from evaluation.customer_service_http import HTTP_REPORT_SCHEMA
from evaluation.customer_service_model_assisted_diagnostic import (
    MODEL_ADJUDICATOR_ID,
    MODEL_ASSISTED_DIAGNOSTIC_STATUS,
    MODEL_REVIEWER_A_ID,
    MODEL_REVIEWER_B_ID,
    ModelAssistedDiagnosticError,
    ModelEndpoint,
    run_model_assisted_diagnostic,
    verify_model_assisted_diagnostic,
)


def _report(path: Path) -> Path:
    atomic_write_json(
        path,
        {
            "schemaVersion": HTTP_REPORT_SCHEMA,
            "runId": "customer-service-http-v16-test",
            "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
            "cases": [
                {
                    "caseId": "case-1",
                    "message": "退款什么时候到账",
                    "http": {
                        "answer": "退款通常按原支付渠道返回。[1]",
                        "sourceRefs": [
                            {
                                "type": "knowledge_chunk",
                                "heading": "退款说明",
                                "snippet": "退款按原支付渠道返回。",
                            }
                        ],
                        "handoffObserved": False,
                    },
                },
                {
                    "caseId": "case-2",
                    "message": "支付失败了但是钱扣了",
                    "http": {
                        "answer": "请回复转人工以进入人工核查。",
                        "sourceRefs": [],
                        "handoffObserved": False,
                    },
                },
                {
                    "caseId": "case-3",
                    "message": "我要退款订单 A-1",
                    "http": {
                        "answer": (
                            '{"type":"ACTION_CONFIRM","label":"退款",'
                            '"confirmText":"确认退款"}'
                        ),
                        "sourceRefs": [],
                        "handoffObserved": False,
                    },
                },
            ],
        },
        overwrite=False,
    )
    return path


def _fixture_report(path: Path) -> Path:
    report = _report(path)
    raw = load_json(report)
    raw["cases"][0]["message"] = "订单 SM202608050002 的物流到哪了"
    raw["cases"][0]["http"]["fixtureEvidence"] = {
        "sourceOrderId": "SM202608050002",
        "orderId": "20220205175455334F51D3ADFEBAC358",
        "scope": "LOCAL_EVALUATION_ONLY",
        "provisioningBoundary": "DIRECT_SQL_FIXTURE_ONLY",
    }
    raw["cases"][0]["http"]["renderedFixtureTemplateFields"] = ["orderId"]
    atomic_write_json(report, raw, overwrite=True)
    return report


def _labels(*, answer: bool = True, citation: str = "NOT_APPLICABLE") -> dict:
    return {
        "answerCorrect": answer,
        "citationSupport": citation,
        "handoffAppropriate": True,
        "unsafeAnswer": False,
    }


def _review_endpoint(
    reviewer_id: str,
    model_name: str,
    labels_by_message: dict[str, dict],
) -> ModelEndpoint:
    async def invoke(_system: str, user: str) -> str:
        payload = json.loads(user)
        labels = labels_by_message[payload["userMessage"]]
        return json.dumps(
            {**labels, "comment": f"{reviewer_id} diagnostic"},
            ensure_ascii=False,
        )

    return ModelEndpoint(reviewer_id=reviewer_id, model_name=model_name, invoke=invoke)


def _adjudicator_endpoint() -> ModelEndpoint:
    async def invoke(_system: str, _user: str) -> str:
        return json.dumps(
            {
                "finalLabels": _labels(answer=False),
                "reason": "The missing observed handoff leaves the payment-risk request unresolved.",
            }
        )

    return ModelEndpoint(
        reviewer_id=MODEL_ADJUDICATOR_ID,
        model_name="fake-adjudicator-model",
        invoke=invoke,
    )


def test_model_diagnostic_is_separate_from_human_review_and_source_bound(tmp_path: Path):
    report = _report(tmp_path / "http-report.json")
    reviewer_a = _review_endpoint(
        MODEL_REVIEWER_A_ID,
        "fake-reviewer-a-model",
        {
            "退款什么时候到账": _labels(citation="SUPPORTED"),
            "支付失败了但是钱扣了": _labels(answer=False),
            "我要退款订单 A-1": _labels(),
        },
    )
    reviewer_b = _review_endpoint(
        MODEL_REVIEWER_B_ID,
        "fake-reviewer-b-model",
        {
            "退款什么时候到账": _labels(citation="SUPPORTED"),
            "支付失败了但是钱扣了": _labels(answer=True),
            "我要退款订单 A-1": _labels(),
        },
    )
    output = tmp_path / "model-diagnostic"

    result = asyncio.run(
        run_model_assisted_diagnostic(
            report,
            output,
            reviewer_a=reviewer_a,
            reviewer_b=reviewer_b,
            adjudicator=_adjudicator_endpoint(),
            concurrency=2,
        )
    )

    assert result["verified"] is True
    assert result["candidateFindingCount"] == 1
    assert verify_model_assisted_diagnostic(output)["disagreementCaseCount"] == 1
    diagnostic = load_json(output / "model-diagnostic.json")
    assert diagnostic["status"] == MODEL_ASSISTED_DIAGNOSTIC_STATUS
    assert diagnostic["humanReviewStatusUnchanged"] == "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW"
    assert diagnostic["containsHumanLabels"] is False
    assert diagnostic["formalAnswerQualityMetrics"] == "NOT_COMPUTED"
    assert diagnostic["messageProjection"] == (
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    )
    assert diagnostic["candidateFindings"] == [
        {
            "caseId": "case-2",
            "diagnosticLabels": _labels(answer=False),
            "signals": ["ANSWER_INCORRECT"],
            "decisionSource": "MODEL_ADJUDICATION",
            "rationale": "The missing observed handoff leaves the payment-risk request unresolved.",
        }
    ]
    assert "HUMAN_REVIEWED_ADJUDICATED" not in (output / "README.md").read_text()
    assert all(
        not path.stat().st_mode & 0o222 for path in output.rglob("*") if path.is_file()
    )

    rows_a = load_jsonl(output / "reviews" / "model-reviewer-a-v16.sealed.jsonl")
    rows_b = load_jsonl(output / "reviews" / "model-reviewer-b-v16.sealed.jsonl")
    assert all(all(value is not None for value in row["labels"].values()) for row in rows_a)
    assert all(all(value is not None for value in row["labels"].values()) for row in rows_b)
    assert {row["reviewerId"] for row in rows_a} == {MODEL_REVIEWER_A_ID}
    assert {row["reviewerId"] for row in rows_b} == {MODEL_REVIEWER_B_ID}


def test_model_diagnostic_uses_runtime_fixture_message_projection(tmp_path: Path):
    report = _fixture_report(tmp_path / "http-report.json")

    def reviewer(reviewer_id: str, model_name: str) -> ModelEndpoint:
        async def invoke(_system: str, _user: str) -> str:
            return json.dumps({**_labels(), "comment": "fixture-aware diagnostic"})

        return ModelEndpoint(
            reviewer_id=reviewer_id,
            model_name=model_name,
            invoke=invoke,
        )

    output = tmp_path / "model-diagnostic"
    result = asyncio.run(
        run_model_assisted_diagnostic(
            report,
            output,
            reviewer_a=reviewer(MODEL_REVIEWER_A_ID, "model-a"),
            reviewer_b=reviewer(MODEL_REVIEWER_B_ID, "model-b"),
            adjudicator=_adjudicator_endpoint(),
        )
    )

    assert result["verified"] is True
    assert verify_model_assisted_diagnostic(output)["caseCount"] == 3
    for path in (
        output / "evidence-manifest.json",
        output / "model-diagnostic.json",
        output / "agreement.json",
    ):
        assert load_json(path)["messageProjection"] == (
            ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
        )
    rows = load_jsonl(output / "reviews" / "model-reviewer-a-v16.sealed.jsonl")
    assert next(row for row in rows if row["caseId"] == "case-1")["message"] == (
        "订单 20220205175455334F51D3ADFEBAC358 的物流到哪了"
    )


def test_model_diagnostic_rejects_non_distinct_models(tmp_path: Path):
    report = _report(tmp_path / "http-report.json")
    same_model = "same-model"
    reviewer_a = _review_endpoint(
        MODEL_REVIEWER_A_ID,
        same_model,
        {
            "退款什么时候到账": _labels(citation="SUPPORTED"),
            "支付失败了但是钱扣了": _labels(),
            "我要退款订单 A-1": _labels(),
        },
    )
    reviewer_b = _review_endpoint(
        MODEL_REVIEWER_B_ID,
        same_model,
        {
            "退款什么时候到账": _labels(citation="SUPPORTED"),
            "支付失败了但是钱扣了": _labels(),
            "我要退款订单 A-1": _labels(),
        },
    )

    with pytest.raises(ModelAssistedDiagnosticError, match="must be distinct"):
        asyncio.run(
            run_model_assisted_diagnostic(
                report,
                tmp_path / "blocked",
                reviewer_a=reviewer_a,
                reviewer_b=reviewer_b,
                adjudicator=_adjudicator_endpoint(),
            )
        )


def test_model_diagnostic_marks_overlong_model_comment_as_truncated(tmp_path: Path):
    report = _report(tmp_path / "http-report.json")
    long_comment = "x" * 340

    def endpoint(reviewer_id: str, model_name: str) -> ModelEndpoint:
        async def invoke(_system: str, _user: str) -> str:
            return json.dumps({**_labels(), "comment": long_comment})

        return ModelEndpoint(reviewer_id=reviewer_id, model_name=model_name, invoke=invoke)

    output = tmp_path / "model-diagnostic"
    asyncio.run(
        run_model_assisted_diagnostic(
            report,
            output,
            reviewer_a=endpoint(MODEL_REVIEWER_A_ID, "model-a"),
            reviewer_b=endpoint(MODEL_REVIEWER_B_ID, "model-b"),
            adjudicator=_adjudicator_endpoint(),
        )
    )
    rows = load_jsonl(output / "reviews" / "model-reviewer-a-v16.sealed.jsonl")
    assert all(row["comment"].endswith("[truncated]") for row in rows)


def test_model_diagnostic_retries_a_transient_model_failure(tmp_path: Path):
    report = _report(tmp_path / "http-report.json")
    calls = 0

    async def retrying_invoke(_system: str, _user: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("transient")
        return json.dumps({**_labels(), "comment": "retry succeeded"})

    stable = _review_endpoint(
        MODEL_REVIEWER_B_ID,
        "model-b",
        {
            "退款什么时候到账": _labels(),
            "支付失败了但是钱扣了": _labels(),
            "我要退款订单 A-1": _labels(),
        },
    )
    asyncio.run(
        run_model_assisted_diagnostic(
            report,
            tmp_path / "model-diagnostic",
            reviewer_a=ModelEndpoint(
                reviewer_id=MODEL_REVIEWER_A_ID,
                model_name="model-a",
                invoke=retrying_invoke,
            ),
            reviewer_b=stable,
            adjudicator=_adjudicator_endpoint(),
            concurrency=1,
        )
    )
    assert calls == 4
