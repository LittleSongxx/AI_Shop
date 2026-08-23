from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.core.contracts import CaseResult, CaseStatus, Domain
from evaluation.core.io import atomic_write_json, atomic_write_jsonl
from evaluation.customer_service_gold import load_gold_dataset
from evaluation.customer_service_http import (
    ANSWER_REVIEW_REPORT_SCHEMA,
    CustomerServiceHttpError,
    build_http_agent_case,
    build_http_report,
    export_answer_review_sheet,
    observe_http_result,
    rebuild_customer_service_http_report,
    score_answer_review,
    verify_customer_service_http_evidence,
    write_customer_service_http_evidence,
)

AGENT_ROOT = Path(__file__).resolve().parents[1]
HUMAN_DATASET = (
    AGENT_ROOT
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-human-v1-20260823"
    / "customer-service-human-v1.jsonl"
)


def _result(*, handoff: bool = False, citation: int = 1) -> CaseResult:
    terminal = "HANDOFF" if handoff else "SUCCEEDED"
    decision = {
        "intent": "PRODUCT_SEARCH",
        "risk_level": "LOW",
        "next_action": "HANDOFF" if handoff else "TOOL",
        "entities": {"amount": "2000"},
        "source": "rule",
    }
    episode = {
        "runId": "run-1",
        "parentRunId": None,
        "status": terminal,
        "intent": "PRODUCT_SEARCH",
        "conversation": {
            "assistantMessage": f"已核验结果 [{citation}]",
            "sourceRefs": [{"citation": 1, "id": "source-1", "factIds": ["fact-1"]}],
        },
        "handoffs": ([{"handoffId": "handoff-1", "status": "SUCCEEDED"}] if handoff else []),
        "steps": [
            {
                "eventType": "INTENT_DECISION",
                "nodeName": "api",
                "status": "OK",
                "output": decision,
            }
        ],
    }
    return CaseResult(
        case_id="cs-gold-v1-001",
        domain=Domain.AGENT,
        status=CaseStatus.PASSED,
        metrics={},
        latency_ms=123.4,
        output={"answer": f"已核验结果 [{citation}]", "episodes": [episode]},
        providers={"llm": {"complete": True}},
        assertions=[],
        usage={"providerCalls": 1, "inputTokens": 10, "outputTokens": 5},
    )


def _perfect_rule(row: dict) -> dict:
    expected = row["expected"]
    return {
        "intent": expected["intent"],
        "riskLevel": expected["riskLevel"],
        "shouldHandoff": expected["shouldHandoff"],
        "nextAction": "HANDOFF" if expected["shouldHandoff"] else "ANSWER",
        "entities": dict(expected.get("slots") or {}),
    }


def test_http_case_uses_production_agent_contract():
    row = load_gold_dataset(HUMAN_DATASET)[0]
    case = build_http_agent_case(row)
    assert case.input == {"turns": [{"message": row["input"]["message"]}]}
    assert case.required_providers == ("agent-runtime", "llm")
    assert case.expected["stateMode"] == "READ_ONLY"


def test_observation_separates_citation_shape_from_semantic_support():
    observation = observe_http_result(_result())
    assert observation["executionOk"] is True
    assert observation["prediction"]["intent"] == "PRODUCT_SEARCH"
    assert observation["handoffObserved"] is False
    assert observation["citationContract"]["contractValid"] is True
    assert observation["citationContract"]["semanticSupportStatus"] == "PENDING_HUMAN_REVIEW"


def test_invalid_citation_is_visible_as_contract_badcase():
    observation = observe_http_result(_result(citation=7))
    assert observation["citationContract"]["contractValid"] is False
    assert observation["citationContract"]["invalidCitationNumbers"] == [7]


def test_retrieval_step_restores_citation_linkage_when_final_envelope_omits_refs():
    result = _result()
    episode = result.output["episodes"][0]
    refs = episode["conversation"].pop("sourceRefs")
    episode["steps"].append(
        {
            "eventType": "RAG_RETRIEVAL",
            "nodeName": "build_context",
            "output": {"sourceRefs": refs},
        }
    )
    observation = observe_http_result(result)
    assert observation["sourceRefs"] == refs
    assert observation["citationContract"]["contractValid"] is True


def test_report_scores_handoff_but_not_final_answer():
    row = load_gold_dataset(HUMAN_DATASET)[0]
    observation = observe_http_result(_result())
    report = build_http_report(
        [row],
        rule_predictions={row["id"]: _perfect_rule(row)},
        observations={row["id"]: observation},
        dataset_path=HUMAN_DATASET,
        run_id="customer-http-test",
        preflight={"passed": True},
    )
    assert report["httpExecution"]["executionRate"]["value"] == 1.0
    assert report["handoffDecision"]["accuracy"]["value"] == 1.0
    assert report["httpRoute"]["metricScope"] == "INTENT_RISK_HANDOFF_ONLY"
    assert report["httpRoute"]["metrics"]["slotEntitySpanF1"]["status"] == "UNAVAILABLE"
    assert report["answerQuality"]["answerCorrectness"] is None
    assert report["answerQuality"]["selfJudged"] is False


def test_draft_dataset_cannot_be_scored_as_full_path_gold():
    row = load_gold_dataset(HUMAN_DATASET)[0]
    row["annotation"] = {"status": "DRAFT_NEEDS_HUMAN_REVIEW"}
    with pytest.raises(CustomerServiceHttpError, match="HUMAN_VERIFIED"):
        build_http_report(
            [row],
            rule_predictions={row["id"]: _perfect_rule(row)},
            observations={row["id"]: observe_http_result(_result())},
            dataset_path=HUMAN_DATASET,
            run_id="customer-http-test",
            preflight={"passed": True},
        )


def test_answer_review_export_and_score(tmp_path: Path):
    row = load_gold_dataset(HUMAN_DATASET)[0]
    report = build_http_report(
        [row],
        rule_predictions={row["id"]: _perfect_rule(row)},
        observations={row["id"]: observe_http_result(_result())},
        dataset_path=HUMAN_DATASET,
        run_id="customer-http-test",
        preflight={"passed": True},
    )
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.jsonl"
    atomic_write_json(report_path, report)
    manifest = export_answer_review_sheet(
        report_path, review_path, reviewer_id="independent-reviewer"
    )
    assert manifest["caseCount"] == 1
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert "expected" not in review
    assert "rulePrediction" not in review
    review["labels"] = {
        "answerCorrect": True,
        "citationSupport": "SUPPORTED",
        "handoffAppropriate": True,
        "unsafeAnswer": False,
    }
    review_path.write_text(
        json.dumps(review, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    scored = score_answer_review(report_path, review_path)
    assert scored["schemaVersion"] == ANSWER_REVIEW_REPORT_SCHEMA
    assert scored["metrics"]["answerCorrectness"]["value"] == 1.0
    assert scored["metrics"]["citationGroundingSupport"]["value"] == 1.0
    assert scored["metrics"]["unsafeAnswerRate"]["value"] == 0.0


def test_answer_review_rejects_model_self_review_shape(tmp_path: Path):
    row = load_gold_dataset(HUMAN_DATASET)[0]
    report = build_http_report(
        [row],
        rule_predictions={row["id"]: _perfect_rule(row)},
        observations={row["id"]: observe_http_result(_result())},
        dataset_path=HUMAN_DATASET,
        run_id="customer-http-test",
        preflight={"passed": True},
    )
    report_path = tmp_path / "report.json"
    review_path = tmp_path / "review.jsonl"
    atomic_write_json(report_path, report)
    export_answer_review_sheet(report_path, review_path, reviewer_id="reviewer")
    with pytest.raises(CustomerServiceHttpError, match="invalid or incomplete"):
        score_answer_review(report_path, review_path)


def test_offline_rebuild_and_http_evidence_are_hash_bound_and_read_only(tmp_path: Path):
    row = load_gold_dataset(HUMAN_DATASET)[0]
    dataset_path = tmp_path / "human.jsonl"
    atomic_write_jsonl(dataset_path, [row])
    result = _result()
    episode = result.output["episodes"][0]
    refs = episode["conversation"].pop("sourceRefs")
    episode["steps"].append(
        {"eventType": "RAG_RETRIEVAL", "output": {"sourceRefs": refs}}
    )
    source = build_http_report(
        [row],
        rule_predictions={row["id"]: _perfect_rule(row)},
        observations={row["id"]: observe_http_result(result)},
        dataset_path=dataset_path,
        run_id="customer-http-rebuild-test",
        preflight={"passed": True},
    )
    source_path = tmp_path / "source.json"
    atomic_write_json(source_path, source)
    rebuilt = rebuild_customer_service_http_report(source_path, dataset_path)
    provenance = rebuilt["observationProvenance"]
    assert provenance["providerCallsReexecuted"] is False
    assert rebuilt["citationContractDiagnostic"]["invalidCaseCount"] == 0
    target = tmp_path / "sealed-http"
    verification = write_customer_service_http_evidence(rebuilt, target)
    assert verification["verified"] is True
    assert verify_customer_service_http_evidence(target)["runId"] == (
        "customer-http-rebuild-test"
    )
    assert all(
        not path.stat().st_mode & 0o222 for path in target.iterdir() if path.is_file()
    )
    with pytest.raises(FileExistsError):
        write_customer_service_http_evidence(rebuilt, target)
