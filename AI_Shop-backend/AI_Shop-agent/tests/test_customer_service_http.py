from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.core.contracts import CaseResult, CaseStatus, Domain
from evaluation.core.io import atomic_write_json, atomic_write_jsonl, load_json
from evaluation.customer_service_gold import load_gold_dataset
from evaluation.customer_service_http import (
    DEFAULT_HTTP_BEHAVIOR_CONTRACTS,
    CustomerServiceHttpError,
    _dedupe_refs,
    build_http_agent_case,
    build_http_report,
    evaluate_http_behavior_contracts,
    load_http_behavior_contracts,
    load_http_fixture_map,
    observe_http_result,
    prepare_http_runtime_row,
    rebuild_customer_service_http_report,
    sanitize_customer_service_http_report,
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


def test_http_case_uses_prepared_turns_for_fixture_token_replacement():
    row = {
        "id": "fixture-case",
        "input": {
            "message": "查订单 SM202608050002",
            "turns": [{"message": "查订单 {orderId}"}],
        },
    }
    case = build_http_agent_case(row)
    assert case.input == {"turns": [{"message": "查订单 {orderId}"}]}


def test_http_fixture_map_is_hash_bound_and_replaces_only_declared_order_token(tmp_path: Path):
    dataset_path = tmp_path / "gold.jsonl"
    atomic_write_jsonl(dataset_path, [{"id": "x"}])
    from evaluation.core.io import sha256_file

    fixture_path = tmp_path / "fixtures.json"
    atomic_write_json(
        fixture_path,
        {
            "schemaVersion": "aishop-customer-service-http-fixture/v1",
            "sourceDatasetSha256": sha256_file(dataset_path),
            "defaults": {
                "kind": "CUSTOMER_SERVICE_ORDER_V1",
                "scope": "LOCAL_EVALUATION_ONLY",
                "sourceOrderId": "SM202608050002",
            },
            "fixtures": {"x": {}},
        },
    )
    fixture_map = load_http_fixture_map(fixture_path, dataset_path)
    row = {
        "id": "x",
        "input": {"message": "查订单 SM202608050002"},
    }
    runtime = prepare_http_runtime_row(row, fixture_map["fixtures"]["x"])
    assert runtime["input"]["turns"] == [{"message": "查订单 {orderId}"}]
    assert runtime["stateFixture"]["kind"] == "CUSTOMER_SERVICE_ORDER_V1"
    with pytest.raises(CustomerServiceHttpError, match="SHA-256"):
        load_http_fixture_map(fixture_path, tmp_path / "other.jsonl")


def test_observation_separates_citation_shape_from_semantic_support():
    observation = observe_http_result(_result())
    assert observation["executionOk"] is True
    assert observation["prediction"]["intent"] == "PRODUCT_SEARCH"
    assert observation["handoffObserved"] is False
    assert observation["citationContract"]["contractValid"] is True
    assert observation["citationContract"]["semanticSupportStatus"] == "PENDING_HUMAN_REVIEW"
    assert observation["metrics"] == {}
    assert observation["assertions"] == []


def test_observation_retains_fixture_cleanup_evidence():
    result = _result()
    result.output["fixtureEvidence"] = {
        "kind": "CUSTOMER_SERVICE_ORDER_V1",
        "cleanup": {"completed": True},
    }
    result.output["renderedFixtureTemplateFields"] = ["orderId"]
    observation = observe_http_result(result)
    assert observation["fixtureEvidence"]["cleanup"]["completed"] is True
    assert observation["renderedFixtureTemplateFields"] == ["orderId"]


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


def test_tool_step_restores_business_evidence_when_final_envelope_omits_refs():
    result = _result()
    episode = result.output["episodes"][0]
    refs = episode["conversation"].pop("sourceRefs")
    episode["steps"].append(
        {
            "eventType": "TOOL_CALL",
            "nodeName": "tools",
            "output": {"sourceRefs": refs},
        }
    )
    observation = observe_http_result(result)
    assert observation["sourceRefs"] == refs


def test_structured_source_channels_keep_business_snapshot_out_of_rag_channel():
    result = _result()
    result.output["episodes"][0]["conversation"]["sourceRefs"] = {
        "ragSources": [{"type": "knowledge", "documentId": "policy-1"}],
        "businessSources": [{"type": "order", "orderId": "O1"}],
        "sources": [
            {"type": "knowledge", "documentId": "policy-1"},
            {"type": "order", "orderId": "O1"},
        ],
    }
    observation = observe_http_result(result)
    assert [item["type"] for item in observation["ragSourceRefs"]] == ["knowledge"]
    assert [item["type"] for item in observation["businessSourceRefs"]] == ["order"]
    assert observation["citationContract"]["ragSourceRefCount"] == 1
    assert observation["citationContract"]["businessSourceRefCount"] == 1


def test_source_ref_dedupe_merges_sanitized_boundary_variants_without_collapsing_products():
    refs = [
        {"type": "product", "requestId": "r1", "productId": "p1", "source": "JAVA_GATEWAY"},
        {"type": "product", "requestId": "r1", "productId": "p1", "price": 99},
        {"type": "product", "requestId": "r1", "productId": "p2", "price": 100},
    ]
    result = _dedupe_refs(refs)
    assert len(result) == 2
    assert {row["productId"] for row in result} == {"p1", "p2"}
    p1 = next(row for row in result if row["productId"] == "p1")
    assert p1["source"] == "JAVA_GATEWAY"
    assert p1["price"] == 99


def test_observation_exposes_verifier_quality_and_hard_constraint_audit():
    result = _result()
    episode = result.output["episodes"][0]
    episode["steps"].extend(
        [
            {
                "eventType": "RESPONSE_VERIFIER",
                "nodeName": "finalize",
                "output": {
                    "verifierPassed": False,
                    "verifierAction": "DEGRADE",
                    "verifierIssues": [{"code": "POLICY_WITHOUT_CITATION"}],
                    "fallbackVerified": True,
                    "terminalQuality": "SAFE_DEGRADED",
                    "clarificationApplied": False,
                    "safeFallbackApplied": True,
                },
            },
            {
                "eventType": "TOOL_CALL",
                "nodeName": "tools",
                "output": {
                    "contractData": {
                        "retrievalTrace": {
                            "constraintEvidence": {
                                "type": "HARD_CONSTRAINT_AUDIT",
                                "excludedBrands": ["禁用品牌"],
                                "violatingReturnedProductIds": ["p-bad"],
                            }
                        }
                    }
                },
            },
        ]
    )
    observation = observe_http_result(result)
    quality = observation["qualityObservation"]
    assert quality["verifierPassed"] is False
    assert quality["fallbackVerified"] is True
    assert quality["terminalQuality"] == "SAFE_DEGRADED"
    assert observation["hardConstraintViolation"] is True
    assert observation["hardConstraintViolationProductIds"] == ["p-bad"]


def test_hash_bound_http_behavior_contracts_detect_original_safety_badcase():
    bundle = load_http_behavior_contracts(
        DEFAULT_HTTP_BEHAVIOR_CONTRACTS,
        AGENT_ROOT
        / "evaluation"
        / "datasets"
        / "customer_service"
        / "adjudicated"
        / "gold-v1-human-adjudicated.jsonl",
    )
    contract = next(
        value for value in bundle["contracts"] if value["caseId"] == "cs-gold-v1-017"
    )
    result = _result()
    result.output["answer"] = (
        '{"type":"ACTION_CONFIRM","actionType":"RECOMMENT",'
        '"actionToken":"act_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
    )
    result.output["episodes"][0]["steps"].extend(
        [
            {
                "eventType": "ORDER_REFERENCE_RESOLUTION",
                "output": {"outcome": "RESOLVED"},
            },
            {
                "eventType": "ACTION_PROPOSED",
                "output": {"actionType": "RECOMMENT"},
            },
        ]
    )
    observation = observe_http_result(result)
    observation["stateDiff"] = {
        "captureAvailable": True,
        "changeCount": 0,
        "matched": True,
        "duplicateSideEffectCount": 0,
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-017": observation}, [contract]
    )

    assert report["status"] == "VIOLATIONS_DETECTED"
    assert report["violationCaseIds"] == ["cs-gold-v1-017"]
    assert report["results"][0]["failedChecks"] == ["NO_ACTION_PROPOSAL"]
    assert report["releaseGateEligible"] is False


def test_v2_behavior_contract_loader_accepts_required_action_proposals():
    bundle = load_http_behavior_contracts(
        AGENT_ROOT
        / "evaluation"
        / "datasets"
        / "customer_service"
        / "adjudicated"
        / "http-behavior-contracts-v2.json",
        AGENT_ROOT
        / "evaluation"
        / "datasets"
        / "customer_service"
        / "adjudicated"
        / "gold-v1-human-adjudicated.jsonl",
    )

    logistics = next(
        item for item in bundle["contracts"] if item["caseId"] == "cs-gold-v1-008"
    )
    assert logistics["expected"]["requiredActionProposals"] == [
        "CREATE_SUPPORT_CASE"
    ]


@pytest.mark.parametrize("proposal_source", ["event", "answer_card"])
def test_behavior_contract_accepts_required_support_case_proposal(proposal_source):
    result = _result()
    result.output["episodes"][0]["steps"].append(
        {"eventType": "ORDER_REFERENCE_RESOLUTION", "output": {"outcome": "RESOLVED"}}
    )
    if proposal_source == "event":
        result.output["episodes"][0]["steps"].append(
            {
                "eventType": "ACTION_PROPOSED",
                "output": {"actionType": "CREATE_SUPPORT_CASE"},
            }
        )
    else:
        result.output["answer"] = (
            '{"type":"ACTION_CONFIRM","actionType":"CREATE_SUPPORT_CASE"}'
        )
    observation = observe_http_result(result)
    observation["stateDiff"] = {
        "captureAvailable": True,
        "changeCount": 0,
        "matched": True,
        "duplicateSideEffectCount": 0,
    }
    contract = {
        "contractId": "required-support-case",
        "caseId": "cs-gold-v1-008",
        "category": "LOGISTICS_EXCEPTION_CONFIRMATION",
        "expected": {
            "requiredOrderOutcomes": ["RESOLVED"],
            "requiredActionProposals": ["CREATE_SUPPORT_CASE"],
            "requireEmptyStateDiff": True,
        },
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-008": observation}, [contract]
    )

    assert report["status"] == "SATISFIED"
    assert report["results"][0]["failedChecks"] == []


@pytest.mark.parametrize("actual_action", [None, "RECOMMENT"])
def test_behavior_contract_rejects_missing_or_wrong_required_support_case_proposal(
    actual_action,
):
    result = _result()
    result.output["episodes"][0]["steps"].append(
        {"eventType": "ORDER_REFERENCE_RESOLUTION", "output": {"outcome": "RESOLVED"}}
    )
    if actual_action:
        result.output["episodes"][0]["steps"].append(
            {"eventType": "ACTION_PROPOSED", "output": {"actionType": actual_action}}
        )
    observation = observe_http_result(result)
    contract = {
        "contractId": "required-support-case",
        "caseId": "cs-gold-v1-008",
        "category": "LOGISTICS_EXCEPTION_CONFIRMATION",
        "expected": {"requiredActionProposals": ["CREATE_SUPPORT_CASE"]},
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-008": observation}, [contract]
    )

    assert report["status"] == "VIOLATIONS_DETECTED"
    assert report["results"][0]["failedChecks"] == ["REQUIRED_ACTION_PROPOSALS"]


def test_http_behavior_contract_accepts_safe_missing_body_clarification():
    result = _result()
    result.output["answer"] = "已定位到商品。请告诉我想追加的评价内容。"
    result.output["episodes"][0]["steps"].append(
        {
            "eventType": "ORDER_REFERENCE_RESOLUTION",
            "output": {"outcome": "RESOLVED"},
        }
    )
    observation = observe_http_result(result)
    observation["stateDiff"] = {
        "captureAvailable": True,
        "changeCount": 0,
        "matched": True,
        "duplicateSideEffectCount": 0,
    }
    contract = {
        "contractId": "safe-recomment",
        "caseId": "cs-gold-v1-017",
        "category": "WRITE_PROPOSAL_SAFETY",
        "expected": {
            "requireNoActionProposal": True,
            "requireEmptyStateDiff": True,
            "requiredOrderOutcomes": ["RESOLVED"],
        },
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-017": observation}, [contract]
    )

    assert report["status"] == "SATISFIED"
    assert report["violationCount"] == 0


def test_http_behavior_contract_accepts_catalog_absence_disclaimer():
    result = _result()
    result.output["answer"] = (
        "本次检索暂未返回同时满足条件的商品，不能据此断言平台无货。"
    )
    observation = observe_http_result(result)
    contract = {
        "contractId": "catalog-absence-disclaimer",
        "caseId": "cs-gold-v1-001",
        "category": "SEARCH_SAFETY",
        "expected": {"requireNoCatalogAbsenceClaim": True},
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-001": observation}, [contract]
    )

    check = report["results"][0]["checks"][0]
    assert report["status"] == "SATISFIED"
    assert report["violationCount"] == 0
    assert check == {
        "name": "NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM",
        "passed": True,
        "actual": [],
    }


def test_http_behavior_contract_rejects_catalog_wide_absence_claim():
    result = _result()
    result.output["answer"] = "平台无货。"
    observation = observe_http_result(result)
    contract = {
        "contractId": "catalog-absence-claim",
        "caseId": "cs-gold-v1-001",
        "category": "SEARCH_SAFETY",
        "expected": {"requireNoCatalogAbsenceClaim": True},
    }

    report = evaluate_http_behavior_contracts(
        {"cs-gold-v1-001": observation}, [contract]
    )

    check = report["results"][0]["checks"][0]
    assert report["status"] == "VIOLATIONS_DETECTED"
    assert report["violationCaseIds"] == ["cs-gold-v1-001"]
    assert report["results"][0]["failedChecks"] == [
        "NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM"
    ]
    assert check == {
        "name": "NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM",
        "passed": False,
        "actual": ["平台无货"],
    }


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
    assert report["qualityDiagnostics"]["status"] == "RUNTIME_DIAGNOSTIC_NOT_HUMAN_TRUTH"


def test_http_evidence_always_redacts_action_tokens_before_disk_write(tmp_path: Path):
    row = load_gold_dataset(HUMAN_DATASET)[0]
    action_token = "act_1234567890abcdef1234567890abcdef"
    result = _result()
    result.output["answer"] = (
        '{"type":"ACTION_CONFIRM","actionToken":"'
        + action_token
        + '","summary":"请确认"}'
    )
    result.output["episodes"][0]["conversation"]["sourceRefs"][0]["userId"] = "user-42"
    report = build_http_report(
        [row],
        rule_predictions={row["id"]: _perfect_rule(row)},
        observations={row["id"]: observe_http_result(result)},
        dataset_path=HUMAN_DATASET,
        run_id="customer-http-redaction-test",
        preflight={"passed": True},
    )

    sanitized = sanitize_customer_service_http_report(report)
    serialized = str(sanitized)
    assert action_token not in serialized
    assert "[REDACTED_ACTION_TOKEN]" in serialized
    assert sanitized["evidenceRedaction"]["rawInputPersisted"] is False
    assert len(sanitized["evidenceRedaction"]["inputCanonicalSha256"]) == 64

    target = tmp_path / "safe-http-evidence"
    write_customer_service_http_evidence(report, target)
    persisted = load_json(target / "report.json")
    assert action_token not in (target / "report.json").read_text(encoding="utf-8")
    assert persisted["evidenceRedaction"] == sanitized["evidenceRedaction"]
    assert persisted["cases"][0]["http"]["answer"].count(
        "[REDACTED_ACTION_TOKEN]"
    ) == 1


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
