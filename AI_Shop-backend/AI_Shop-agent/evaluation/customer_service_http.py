"""Customer-service quality evaluation through the production HTTP Agent path.

The existing customer-service gold evaluator deliberately measures only the
deterministic pre-router.  This module keeps that score intact and adds a
separate full-path observation.  Intent and handoff can be compared with the
already human-verified labels; answer correctness and grounding require a new
blind human review and therefore remain unavailable until such a sheet is
completed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.adapters.agent import run_agent_case
from evaluation.core.contracts import (
    CASE_SCHEMA_VERSION_V3,
    CaseResult,
    Domain,
    EvaluationCase,
    Split,
)
from evaluation.core.io import (
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import percentile, wilson_interval
from evaluation.customer_service_gold import (
    HUMAN_STATUS,
    evaluate_predictions,
    load_gold_dataset,
    predict_rule_baseline,
)

HTTP_REPORT_SCHEMA = "aishop-customer-service-http-evaluation/v1"
ANSWER_REVIEW_SCHEMA = "aishop-customer-service-answer-review/v1"
ANSWER_REVIEW_REPORT_SCHEMA = "aishop-customer-service-answer-review-report/v1"
HTTP_EVIDENCE_SCHEMA = "aishop-customer-service-http-evidence/v1"

_TERMINAL = {
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "HANDOFF",
    "DEGRADED",
    "FALLBACK",
    "INCONCLUSIVE",
    "MANUAL_REVIEW",
}
_CITATION_RE = re.compile(r"\[(\d+)]")
_ANSWER_LABELS = {
    "answerCorrect": {True, False},
    "citationSupport": {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"},
    "handoffAppropriate": {True, False},
    "unsafeAnswer": {True, False},
}


class CustomerServiceHttpError(ValueError):
    """Raised when full-path evidence cannot be built without guessing."""


def _portable_path(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _evaluation_user_id(run_id: str, case_id: str) -> str:
    material = f"{run_id}\0{case_id}".encode("utf-8")
    return "ev" + hashlib.sha256(material).hexdigest()[:13]


def build_http_agent_case(row: Mapping[str, Any]) -> EvaluationCase:
    """Project one support row onto the normal Agent adapter contract."""

    case_id = str(row.get("id") or "")
    message = str((row.get("input") or {}).get("message") or "")
    return EvaluationCase(
        case_id=case_id,
        split=Split.DEVELOPMENT,
        domain=Domain.AGENT,
        input={"turns": [{"message": message}]},
        expected={
            "terminalStatuses": sorted(_TERMINAL),
            "requiredTools": [],
            "forbiddenTools": [],
            "requiredEvents": [],
            "outputPatterns": [],
            "stateMode": "READ_ONLY",
        },
        required_providers=("agent-runtime", "llm"),
        tags=("customer-service-http",),
        slice_tags=tuple(str(value) for value in row.get("sliceTags") or ()),
        schema_version=CASE_SCHEMA_VERSION_V3,
    )


def _steps(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(step)
        for episode in episodes
        for step in episode.get("steps") or []
        if isinstance(step, Mapping)
    ]


def _intent_decision(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    decisions = [
        step.get("output")
        for step in _steps(episodes)
        if str(step.get("eventType") or "") == "INTENT_DECISION"
        and str(step.get("nodeName") or "") == "api"
        and isinstance(step.get("output"), Mapping)
    ]
    if not decisions:
        decisions = [
            step.get("output")
            for step in _steps(episodes)
            if str(step.get("eventType") or "") == "INTENT_DECISION"
            and isinstance(step.get("output"), Mapping)
        ]
    return dict(decisions[0]) if decisions else {}


def _source_refs(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for episode in episodes:
        conversation = episode.get("conversation")
        candidates: list[Any] = []
        if isinstance(conversation, Mapping):
            candidates.extend(conversation.get("sourceRefs") or [])
        # The production adapter may omit sourceRefs from the final conversation
        # envelope after redaction. The retrieval step is still an authoritative
        # trace of the evidence selected for answer generation.
        for step in episode.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            if str(step.get("eventType") or "") != "RAG_RETRIEVAL":
                continue
            output = step.get("output")
            if isinstance(output, Mapping):
                candidates.extend(output.get("sourceRefs") or [])
        for value in candidates:
            if not isinstance(value, Mapping):
                continue
            item = dict(value)
            digest = sha256_bytes(canonical_json_bytes(item))
            if digest not in seen:
                refs.append(item)
                seen.add(digest)
    return refs


def _citation_contract(answer: str, source_refs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    citations = [int(value) for value in _CITATION_RE.findall(answer)]
    declared = {
        int(ref["citation"])
        for ref in source_refs
        if str(ref.get("citation") or "").isdigit()
    }
    if not declared and source_refs:
        declared = set(range(1, len(source_refs) + 1))
    invalid = sorted(set(citations) - declared) if citations else []
    return {
        "answerCitationNumbers": citations,
        "declaredCitationNumbers": sorted(declared),
        "sourceRefCount": len(source_refs),
        "invalidCitationNumbers": invalid,
        "contractValid": not invalid,
        "semanticSupportStatus": "PENDING_HUMAN_REVIEW",
        "note": "Citation shape is observable; whether a source actually supports the answer is not auto-scored.",
    }


def _normalize_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive citation linkage from preserved traces without re-running a Provider."""

    normalized = dict(observation)
    episodes = [
        dict(value)
        for value in observation.get("episodes") or []
        if isinstance(value, Mapping)
    ]
    refs = _source_refs(episodes)
    if not refs:
        refs = [
            dict(value)
            for value in observation.get("sourceRefs") or []
            if isinstance(value, Mapping)
        ]
    answer = str(observation.get("answer") or "")
    normalized["sourceRefs"] = refs
    normalized["citationContract"] = _citation_contract(answer, refs)
    return normalized


def _unavailable_slot_metric(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "UNAVAILABLE",
        "value": None,
        "numerator": 0,
        "denominator": 0,
        "confidenceInterval95": None,
        "badcaseCount": 0,
        "badcaseIds": [],
        "role": "NOT_MEASURED_AT_HTTP_BOUNDARY",
        "releaseGateEligible": False,
        "reason": "Episode entity values are redacted; raw gold slots cannot be compared safely.",
    }


def _routing_only_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Remove slot claims that cannot survive the production redaction boundary."""

    routed = dict(report)
    metrics = dict(routed.get("metrics") or {})
    metrics["slotEntitySpanF1"] = _unavailable_slot_metric("slotEntitySpanF1")
    metrics["slotExactMatch"] = _unavailable_slot_metric("slotExactMatch")
    routed["metrics"] = metrics
    routed["metricScope"] = "INTENT_RISK_HANDOFF_ONLY"
    routed["canonicalSlotDiagnostics"] = {
        "status": "UNAVAILABLE",
        "reason": "HTTP Episode entities are redacted; canonical slot quality remains in rulePreRouter.",
        "metrics": {
            "canonicalSlotEntitySpanF1": _unavailable_slot_metric(
                "canonicalSlotEntitySpanF1"
            ),
            "canonicalSlotExactMatch": _unavailable_slot_metric(
                "canonicalSlotExactMatch"
            ),
        },
    }
    sanitized_badcases: list[dict[str, Any]] = []
    for value in routed.get("badcases") or []:
        row = dict(value)
        names = [
            str(name)
            for name in row.get("metrics") or []
            if not str(name).startswith("slot")
        ]
        if not names:
            continue
        row["metrics"] = names
        row["rootCause"] = (
            "HANDOFF_OR_RISK_POLICY_GAP"
            if any("handoff" in name.lower() or "risk" in name.lower() for name in names)
            else "INTENT_ROUTING_OR_TAXONOMY_GAP"
        )
        sanitized_badcases.append(row)
    routed["badcases"] = sanitized_badcases
    cases = []
    for value in routed.get("cases") or []:
        row = dict(value)
        matches = dict(row.get("matches") or {})
        matches["slotExactMatch"] = None
        row["matches"] = matches
        cases.append(row)
    routed["cases"] = cases
    routed["limitations"] = [
        *list(routed.get("limitations") or []),
        "HTTP slot metrics are intentionally unavailable because Episode redaction prevents raw-value equivalence checks.",
    ]
    return routed


def _runtime_metrics(observations: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    values = list(observations.values())
    latencies = [
        float(value["latencyMs"])
        for value in values
        if value.get("latencyMs") is not None
    ]
    usage_rows = [
        dict(value.get("usage") or {})
        for value in values
        if isinstance(value.get("usage"), Mapping)
    ]
    input_tokens = sum(int(value.get("inputTokens") or 0) for value in usage_rows)
    output_tokens = sum(int(value.get("outputTokens") or 0) for value in usage_rows)
    provider_calls = sum(int(value.get("providerCalls") or 0) for value in usage_rows)
    priced_calls = sum(int(value.get("pricedCalls") or 0) for value in usage_rows)
    unpriced_calls = sum(int(value.get("unpricedCalls") or 0) for value in usage_rows)
    missing_usage_calls = sum(
        int(value.get("missingUsageCalls") or 0) for value in usage_rows
    )
    known_costs = [
        float(value["costCny"])
        for value in usage_rows
        if value.get("costCny") is not None
    ]
    fully_priced = (
        provider_calls > 0
        and priced_calls == provider_calls
        and unpriced_calls == 0
        and missing_usage_calls == 0
        and len(known_costs) == len([row for row in usage_rows if row.get("providerCalls")])
    )
    return {
        "latencyMs": {
            "sampleCount": len(latencies),
            "p50": round(percentile(latencies, 0.5), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "p99": round(percentile(latencies, 0.99), 3) if latencies else None,
            "boundary": "LOCAL_FULL_STACK_NOT_PRODUCTION_SLO",
        },
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "providerCalls": provider_calls,
            "pricedCalls": priced_calls,
            "unpricedCalls": unpriced_calls,
            "missingUsageCalls": missing_usage_calls,
            "costCny": round(sum(known_costs), 6) if fully_priced else None,
            "costStatus": (
                "PRICED"
                if fully_priced
                else "MISSING_USAGE"
                if missing_usage_calls
                else "UNPRICED"
            ),
        },
    }


def observe_http_result(result: CaseResult) -> dict[str, Any]:
    """Extract support-specific facts from a normal Agent result."""

    raw_episodes = result.output.get("episodes") or []
    episodes = [dict(value) for value in raw_episodes if isinstance(value, Mapping)]
    roots = [episode for episode in episodes if not episode.get("parentRunId")]
    decision = _intent_decision(episodes)
    terminal_statuses = [str(value.get("status") or "") for value in roots]
    handoff_rows = [
        dict(item)
        for episode in episodes
        for item in episode.get("handoffs") or []
        if isinstance(item, Mapping)
    ]
    handoff_events = [
        step
        for step in _steps(episodes)
        if "HANDOFF" in str(step.get("eventType") or "").upper()
    ]
    observed_handoff = (
        str(decision.get("next_action") or decision.get("nextAction") or "") == "HANDOFF"
        or "HANDOFF" in terminal_statuses
        or bool(handoff_rows)
        or bool(handoff_events)
    )
    answer = str(result.output.get("answer") or "")
    refs = _source_refs(episodes)
    root_intent = next(
        (str(episode.get("intent") or "") for episode in roots if episode.get("intent")),
        "",
    )
    prediction = {
        "intent": str(decision.get("intent") or root_intent or "__MISSING__"),
        "confidence": decision.get("confidence"),
        "riskLevel": str(
            decision.get("risk_level") or decision.get("riskLevel") or "UNKNOWN"
        ),
        "nextAction": str(
            decision.get("next_action") or decision.get("nextAction") or "UNKNOWN"
        ),
        "shouldHandoff": observed_handoff,
        "handoffReason": decision.get("handoff_reason") or decision.get("handoffReason"),
        "entities": dict(decision.get("entities") or {}),
        "requestMode": decision.get("request_mode") or decision.get("requestMode"),
        "source": decision.get("source"),
    }
    execution_ok = (
        result.error is None
        and bool(roots)
        and all(status in _TERMINAL for status in terminal_statuses)
    )
    return {
        "executionOk": execution_ok,
        "adapterStatus": result.status.value,
        "latencyMs": round(float(result.latency_ms), 3),
        "terminalStatuses": terminal_statuses,
        "prediction": prediction,
        "answer": answer,
        "sourceRefs": refs,
        "citationContract": _citation_contract(answer, refs),
        "handoffObserved": observed_handoff,
        "handoffs": handoff_rows,
        "tools": list(result.output.get("tools") or []),
        "events": list(result.output.get("events") or []),
        "usage": dict(result.usage or {}),
        "providers": dict(result.providers or {}),
        "stateDiff": result.state_diff or result.output.get("stateDiff"),
        "responses": list(result.output.get("responses") or []),
        "episodes": episodes,
        "error": result.error,
    }


def _ratio_metric(successes: int, total: int, *, badcase_ids: Sequence[str]) -> dict[str, Any]:
    if total <= 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "numerator": successes,
            "denominator": total,
            "confidenceInterval95": None,
            "badcaseIds": list(badcase_ids),
        }
    lower, upper = wilson_interval(successes, total)
    return {
        "status": "MEASURED",
        "value": round(successes / total, 6),
        "numerator": successes,
        "denominator": total,
        "confidenceInterval95": {
            "lower": round(lower, 6),
            "upper": round(upper, 6),
            "method": "wilson",
            "confidenceLevel": 0.95,
        },
        "badcaseIds": list(dict.fromkeys(badcase_ids)),
    }


def _handoff_metrics(
    rows: Sequence[Mapping[str, Any]], observations: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    false_positive: list[str] = []
    false_negative: list[str] = []
    for row in rows:
        case_id = str(row["id"])
        expected = bool((row.get("expected") or {}).get("shouldHandoff"))
        observed = bool((observations.get(case_id) or {}).get("handoffObserved"))
        if expected and observed:
            tp += 1
        elif expected:
            fn += 1
            false_negative.append(case_id)
        elif observed:
            fp += 1
            false_positive.append(case_id)
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "confusion": {"truePositive": tp, "falsePositive": fp, "falseNegative": fn, "trueNegative": tn},
        "accuracy": _ratio_metric(tp + tn, len(rows), badcase_ids=[*false_positive, *false_negative]),
        "precision": None if precision is None else round(precision, 6),
        "recall": _ratio_metric(tp, tp + fn, badcase_ids=false_negative),
        "f1": None if f1 is None else round(f1, 6),
        "falsePositiveCaseIds": false_positive,
        "falseNegativeCaseIds": false_negative,
        "criticalMissCaseIds": [
            str(row["id"])
            for row in rows
            if (row.get("expected") or {}).get("handoffSeverity") == "CRITICAL"
            and str(row["id"]) in false_negative
        ],
    }


def build_http_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    rule_predictions: Mapping[str, Mapping[str, Any]],
    observations: Mapping[str, Mapping[str, Any]],
    dataset_path: Path,
    run_id: str,
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    if not rows or any(
        (row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in rows
    ):
        raise CustomerServiceHttpError("full-path scoring requires an entirely HUMAN_VERIFIED dataset")
    normalized_observations = {
        str(case_id): _normalize_observation(observation)
        for case_id, observation in observations.items()
    }
    missing = [str(row["id"]) for row in rows if str(row["id"]) not in observations]
    http_predictions = {
        case_id: dict(observation.get("prediction") or {})
        for case_id, observation in normalized_observations.items()
    }
    rule_report = evaluate_predictions(
        rows,
        rule_predictions,
        provenance={
            "mode": "rule-pre-router",
            "datasetPath": _portable_path(dataset_path),
            "datasetSha256": sha256_file(dataset_path),
            "allowLlm": False,
        },
    )
    http_route_report = _routing_only_report(
        evaluate_predictions(
            rows,
            http_predictions,
            provenance={
                "mode": "production-http-agent-observed-routing",
                "datasetPath": _portable_path(dataset_path),
                "datasetSha256": sha256_file(dataset_path),
                "allowLlm": True,
            },
        )
    )
    execution_bad = [
        str(row["id"])
        for row in rows
        if not bool(
            (normalized_observations.get(str(row["id"])) or {}).get("executionOk")
        )
    ]
    citation_bad = [
        case_id
        for case_id, observation in normalized_observations.items()
        if not bool((observation.get("citationContract") or {}).get("contractValid", True))
    ]
    cases = []
    for row in rows:
        case_id = str(row["id"])
        cases.append(
            {
                "caseId": case_id,
                "message": (row.get("input") or {}).get("message"),
                "expected": dict(row.get("expected") or {}),
                "sliceTags": list(row.get("sliceTags") or []),
                "difficulty": row.get("difficulty"),
                "rulePrediction": dict(rule_predictions.get(case_id) or {}),
                "http": dict(normalized_observations.get(case_id) or {}),
                "answerReviewStatus": "PENDING_HUMAN_REVIEW",
            }
        )
    return {
        "schemaVersion": HTTP_REPORT_SCHEMA,
        "runId": run_id,
        "status": (
            "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW"
            if not execution_bad and not missing
            else "PARTIAL_EXECUTION_PENDING_HUMAN_ANSWER_REVIEW"
        ),
        "releaseGateEligible": False,
        "normalQualityDenominatorExcluded": True,
        "createdAt": utc_now(),
        "dataset": {
            "path": _portable_path(dataset_path),
            "sha256": sha256_file(dataset_path),
            "caseCount": len(rows),
            "annotationStatus": HUMAN_STATUS,
        },
        "preflight": dict(preflight),
        "rulePreRouter": rule_report,
        "httpRoute": http_route_report,
        "httpExecution": {
            "executionRate": _ratio_metric(
                len(rows) - len(execution_bad) - len(missing),
                len(rows),
                badcase_ids=[*execution_bad, *missing],
            ),
            "errorCaseIds": execution_bad,
            "missingCaseIds": missing,
        },
        "handoffDecision": _handoff_metrics(rows, normalized_observations),
        "runtimeMetrics": _runtime_metrics(normalized_observations),
        "citationContractDiagnostic": {
            "invalidCaseCount": len(citation_bad),
            "invalidCaseIds": citation_bad,
            "semanticSupportMeasured": False,
            "note": "Only citation shape/linkage is automatic. Grounding support requires the answer review below.",
        },
        "answerQuality": {
            "status": "PENDING_HUMAN_REVIEW",
            "answerCorrectness": None,
            "citationGroundingSupport": None,
            "unsafeAnswerRate": None,
            "reviewCoverage": {"numerator": 0, "denominator": len(rows)},
            "selfJudged": False,
        },
        "cases": cases,
        "limitations": [
            "Full-path intent is read from the production Episode trace; it does not replace the separately reported rule pre-router score.",
            "HTTP Episode slot values are redacted, so only the rule pre-router carries slot F1/EM; HTTP slot metrics are unavailable.",
            "Final-answer correctness and citation support are unavailable until an independent reviewer completes the blind answer sheet.",
            "HTTP timings are local full-stack observations, not a production SLO.",
            "This auxiliary run does not modify or republish the v9 final evidence.",
        ],
    }


async def run_customer_service_http(
    dataset_path: Path,
    *,
    run_id: str,
    preflight: Mapping[str, Any],
    timeout_seconds: float = 240.0,
    case_ids: Sequence[str] = (),
) -> dict[str, Any]:
    rows = load_gold_dataset(dataset_path)
    selected = {str(value) for value in case_ids if str(value)}
    if selected:
        known = {str(row["id"]) for row in rows}
        unknown = sorted(selected - known)
        if unknown:
            raise CustomerServiceHttpError(f"unknown customer-service case IDs: {unknown}")
        rows = [row for row in rows if str(row["id"]) in selected]
    rule_predictions = await predict_rule_baseline(rows)
    observations: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row["id"])
        case = build_http_agent_case(row)
        try:
            result = await run_agent_case(
                case,
                user_id=_evaluation_user_id(run_id, case_id),
                timeout_seconds=timeout_seconds,
            )
            observations[case_id] = observe_http_result(result)
        except Exception as exc:
            observations[case_id] = {
                "executionOk": False,
                "prediction": {},
                "handoffObserved": False,
                "answer": "",
                "sourceRefs": [],
                "citationContract": _citation_contract("", []),
                "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
            }
    return build_http_report(
        rows,
        rule_predictions=rule_predictions,
        observations=observations,
        dataset_path=dataset_path,
        run_id=run_id,
        preflight=preflight,
    )


def export_answer_review_sheet(
    report_path: Path,
    output_path: Path,
    *,
    reviewer_id: str,
) -> dict[str, Any]:
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise CustomerServiceHttpError("reviewer_id is required")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite answer review: {output_path}")
    report = load_json(report_path)
    if report.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceHttpError("answer review source is not a customer-service HTTP report")
    rows = []
    for case in report.get("cases") or []:
        http = case.get("http") if isinstance(case.get("http"), Mapping) else {}
        rows.append(
            {
                "schemaVersion": ANSWER_REVIEW_SCHEMA,
                "caseId": str(case.get("caseId") or ""),
                "reviewerId": reviewer,
                "sourceRunId": report.get("runId"),
                "sourceReportSha256": sha256_file(report_path),
                "message": case.get("message"),
                "answer": http.get("answer") or "",
                "sourceRefs": http.get("sourceRefs") or [],
                "observedHandoff": bool(http.get("handoffObserved")),
                "labels": {
                    "answerCorrect": None,
                    "citationSupport": None,
                    "handoffAppropriate": None,
                    "unsafeAnswer": None,
                },
                "comment": "",
            }
        )
    atomic_write_jsonl(output_path, rows, overwrite=False)
    manifest = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "lifecycle": "OPEN",
        "reviewerId": reviewer,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _portable_path(report_path),
        "sourceReportSha256": sha256_file(report_path),
        "sheetPath": _portable_path(output_path),
        "sheetSha256AtExport": sha256_file(output_path),
        "caseCount": len(rows),
        "blinding": "Gold expected labels and rule/HTTP intent predictions omitted",
    }
    atomic_write_json(
        output_path.with_suffix(output_path.suffix + ".manifest.json"),
        manifest,
        overwrite=False,
    )
    return manifest


def score_answer_review(report_path: Path, review_path: Path) -> dict[str, Any]:
    report = load_json(report_path)
    review_rows = load_jsonl(review_path)
    if report.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceHttpError("answer review source report schema is invalid")
    expected_ids = {str(case.get("caseId") or "") for case in report.get("cases") or []}
    observed_ids: set[str] = set()
    reviewer_ids: set[str] = set()
    badcases: list[dict[str, Any]] = []
    counts = {
        "answerCorrect": 0,
        "citationSupported": 0,
        "citationEligible": 0,
        "handoffAppropriate": 0,
        "unsafeAnswer": 0,
    }
    source_hash = sha256_file(report_path)
    for index, row in enumerate(review_rows, 1):
        label = f"{review_path}:{index}"
        if row.get("schemaVersion") != ANSWER_REVIEW_SCHEMA:
            raise CustomerServiceHttpError(f"{label}: answer review schema is invalid")
        case_id = str(row.get("caseId") or "")
        if case_id not in expected_ids or case_id in observed_ids:
            raise CustomerServiceHttpError(f"{label}: case ID is unknown or duplicated")
        if row.get("sourceReportSha256") != source_hash:
            raise CustomerServiceHttpError(f"{label}: source report hash differs")
        labels = row.get("labels")
        if not isinstance(labels, Mapping) or set(labels) != set(_ANSWER_LABELS):
            raise CustomerServiceHttpError(f"{label}: labels are incomplete")
        for field, allowed in _ANSWER_LABELS.items():
            if labels.get(field) not in allowed:
                raise CustomerServiceHttpError(f"{label}: {field} is invalid or incomplete")
        reviewer = str(row.get("reviewerId") or "").strip()
        if not reviewer:
            raise CustomerServiceHttpError(f"{label}: reviewerId is required")
        reviewer_ids.add(reviewer)
        observed_ids.add(case_id)
        counts["answerCorrect"] += int(labels["answerCorrect"] is True)
        if labels["citationSupport"] != "NOT_APPLICABLE":
            counts["citationEligible"] += 1
            counts["citationSupported"] += int(labels["citationSupport"] == "SUPPORTED")
        counts["handoffAppropriate"] += int(labels["handoffAppropriate"] is True)
        counts["unsafeAnswer"] += int(labels["unsafeAnswer"] is True)
        failed = [
            name
            for name, passed in (
                ("answerCorrect", labels["answerCorrect"] is True),
                (
                    "citationSupport",
                    labels["citationSupport"] in {"SUPPORTED", "NOT_APPLICABLE"},
                ),
                ("handoffAppropriate", labels["handoffAppropriate"] is True),
                ("unsafeAnswer", labels["unsafeAnswer"] is False),
            )
            if not passed
        ]
        if failed:
            badcases.append(
                {
                    "caseId": case_id,
                    "failedMetrics": failed,
                    "labels": dict(labels),
                    "comment": row.get("comment") or "",
                }
            )
    if observed_ids != expected_ids:
        raise CustomerServiceHttpError(
            f"answer review coverage mismatch; missing={sorted(expected_ids - observed_ids)}"
        )
    total = len(expected_ids)
    return {
        "schemaVersion": ANSWER_REVIEW_REPORT_SCHEMA,
        "status": "HUMAN_REVIEWED_SINGLE_RATER",
        "releaseGateEligible": False,
        "sourceRunId": report.get("runId"),
        "sourceReportSha256": source_hash,
        "reviewPath": _portable_path(review_path),
        "reviewSha256": sha256_file(review_path),
        "reviewerIds": sorted(reviewer_ids),
        "caseCount": total,
        "metrics": {
            "answerCorrectness": _ratio_metric(
                counts["answerCorrect"],
                total,
                badcase_ids=[row["caseId"] for row in badcases if "answerCorrect" in row["failedMetrics"]],
            ),
            "citationGroundingSupport": _ratio_metric(
                counts["citationSupported"],
                counts["citationEligible"],
                badcase_ids=[row["caseId"] for row in badcases if "citationSupport" in row["failedMetrics"]],
            ),
            "handoffAppropriateness": _ratio_metric(
                counts["handoffAppropriate"],
                total,
                badcase_ids=[row["caseId"] for row in badcases if "handoffAppropriate" in row["failedMetrics"]],
            ),
            "unsafeAnswerRate": _ratio_metric(
                counts["unsafeAnswer"],
                total,
                badcase_ids=[row["caseId"] for row in badcases if "unsafeAnswer" in row["failedMetrics"]],
            ),
        },
        "badcases": badcases,
        "limitations": [
            "This is one independent human rating pass, not dual-review agreement or adjudicated gold.",
            "The Agent/LLM does not grade its own final answer.",
        ],
        "createdAt": utc_now(),
    }


def rebuild_customer_service_http_report(
    source_report_path: Path,
    dataset_path: Path,
) -> dict[str, Any]:
    """Rebuild derived metrics from already captured HTTP observations."""

    source = load_json(source_report_path)
    if source.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceHttpError("source HTTP report schema is invalid")
    rows = load_gold_dataset(dataset_path)
    dataset_sha = sha256_file(dataset_path)
    source_dataset = source.get("dataset") or {}
    if source_dataset.get("sha256") != dataset_sha:
        raise CustomerServiceHttpError("source report and dataset SHA-256 differ")
    source_cases = {
        str(value.get("caseId") or ""): value
        for value in source.get("cases") or []
        if isinstance(value, Mapping)
    }
    expected_ids = {str(row["id"]) for row in rows}
    if set(source_cases) != expected_ids:
        raise CustomerServiceHttpError("source report case set differs from dataset")
    observations = {
        case_id: dict(source_cases[case_id].get("http") or {})
        for case_id in sorted(expected_ids)
    }
    rule_predictions = {
        case_id: dict(source_cases[case_id].get("rulePrediction") or {})
        for case_id in sorted(expected_ids)
    }
    rebuilt = build_http_report(
        rows,
        rule_predictions=rule_predictions,
        observations=observations,
        dataset_path=dataset_path,
        run_id=str(source.get("runId") or ""),
        preflight=dict(source.get("preflight") or {}),
    )
    rebuilt["observationProvenance"] = {
        "mode": "OFFLINE_REBUILD_FROM_PRESERVED_OBSERVATIONS",
        "sourceReportPath": _portable_path(source_report_path),
        "sourceReportSha256": sha256_file(source_report_path),
        "sourceCreatedAt": source.get("createdAt"),
        "providerCallsReexecuted": False,
        "changes": [
            "Recomputed citation linkage from final conversation and RAG_RETRIEVAL traces.",
            "Removed HTTP slot scoring across the redaction boundary.",
            "Recomputed rule-pre-router micro slot F1 bootstrap with TP/FP/FN aggregation.",
        ],
    }
    rebuilt["limitations"] = [
        *list(rebuilt.get("limitations") or []),
        "Derived metrics were rebuilt offline from the original observations; no case or Provider call was re-executed.",
    ]
    return rebuilt


def render_http_markdown(report: Mapping[str, Any]) -> str:
    route_metrics = ((report.get("httpRoute") or {}).get("metrics") or {})
    rule_metrics = ((report.get("rulePreRouter") or {}).get("metrics") or {})
    handoff = report.get("handoffDecision") or {}
    runtime = report.get("runtimeMetrics") or {}
    latency = runtime.get("latencyMs") or {}
    usage = runtime.get("usage") or {}
    citation = report.get("citationContractDiagnostic") or {}
    lines = [
        "# AI 客服 HTTP/LLM 全链路证据",
        "",
        f"> `{report.get('status')}`；答案质量仍待独立人工盲审，不进入 release gate。",
        "",
        f"Run：`{report.get('runId')}`；样本：`{((report.get('dataset') or {}).get('caseCount'))}`；"
        f"数据 SHA-256：`{((report.get('dataset') or {}).get('sha256'))}`。",
        "",
        "| 指标 | 数值 | 分子/分母 | badcase |",
        "|---|---:|---:|---|",
    ]
    for label, metric in (
        ("HTTP Intent Macro-F1", route_metrics.get("intentMacroF1") or {}),
        ("HTTP High-risk Recall", route_metrics.get("highRiskIntentRecall") or {}),
        ("HTTP Handoff Recall", handoff.get("recall") or {}),
        ("规则 Slot micro F1", rule_metrics.get("slotEntitySpanF1") or {}),
        ("规则 Slot EM", rule_metrics.get("slotExactMatch") or {}),
    ):
        lines.append(
            f"| {label} | {metric.get('value')} | {metric.get('numerator')}/{metric.get('denominator')} | "
            f"{', '.join(metric.get('badcaseIds') or []) or '-'} |"
        )
    lines.extend(
        [
            "",
            f"- HTTP 执行：`{(((report.get('httpExecution') or {}).get('executionRate') or {}).get('numerator'))}/"
            f"{(((report.get('httpExecution') or {}).get('executionRate') or {}).get('denominator'))}`；"
            f"转人工混淆矩阵：`{handoff.get('confusion')}`。",
            f"- 引用结构无效：`{citation.get('invalidCaseCount')}`，case："
            f"`{', '.join(citation.get('invalidCaseIds') or []) or '无'}`；语义支持仍由人工评分。",
            f"- 本地全链路延迟 P50/P95/P99：`{latency.get('p50')}/{latency.get('p95')}/{latency.get('p99')} ms`，不是生产 SLO。",
            f"- Usage：input/output token `{usage.get('inputTokens')}/{usage.get('outputTokens')}`，"
            f"Provider calls `{usage.get('providerCalls')}`，费用状态 `{usage.get('costStatus')}`，"
            f"costCny `{usage.get('costCny')}`。未知费用不记为 0。",
            "- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。",
            "- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。",
            "",
        ]
    )
    return "\n".join(lines)


def _evidence_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _evidence_sums(root: Path) -> str:
    values = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(values.items()))


def _assert_http_evidence_boundary(path: Path) -> None:
    resolved = path.resolve()
    for protected in (
        EVIDENCE_ROOT.resolve(),
        (EVIDENCE_ROOT.parent / "archive").resolve(),
    ):
        try:
            resolved.relative_to(protected)
        except ValueError:
            continue
        raise CustomerServiceHttpError(
            f"customer-service HTTP benchmark cannot write inside {protected}"
        )


def write_customer_service_http_evidence(
    report: Mapping[str, Any], output_dir: Path
) -> dict[str, Any]:
    _assert_http_evidence_boundary(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite HTTP benchmark: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        atomic_write_json(staging / "report.json", report, overwrite=False)
        atomic_write_text(
            staging / "report.md", render_http_markdown(report), overwrite=False
        )
        badcases: list[dict[str, Any]] = []
        for value in (report.get("httpRoute") or {}).get("badcases") or []:
            badcases.append({"scope": "HTTP_ROUTE", **dict(value)})
        for case_id in (report.get("citationContractDiagnostic") or {}).get(
            "invalidCaseIds"
        ) or []:
            badcases.append(
                {"scope": "CITATION_CONTRACT", "caseId": case_id}
            )
        for case_id in (report.get("httpExecution") or {}).get("errorCaseIds") or []:
            badcases.append({"scope": "HTTP_EXECUTION", "caseId": case_id})
        atomic_write_jsonl(staging / "badcases.jsonl", badcases, overwrite=False)
        manifest = {
            "schemaVersion": HTTP_EVIDENCE_SCHEMA,
            "kind": "customer-service-http",
            "runId": report.get("runId"),
            "status": report.get("status"),
            "releaseGateEligible": False,
            "answerReviewStatus": (report.get("answerQuality") or {}).get("status"),
            "datasetSha256": (report.get("dataset") or {}).get("sha256"),
            "sourceObservationReportSha256": (
                report.get("observationProvenance") or {}
            ).get("sourceReportSha256"),
            "providerCallsReexecuted": (
                report.get("observationProvenance") or {}
            ).get("providerCallsReexecuted"),
            "createdAt": utc_now(),
            "files": _evidence_inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _evidence_sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verify_customer_service_http_evidence(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_customer_service_http_evidence(output_dir)


def verify_customer_service_http_evidence(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    sums_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or not sums_path.is_file():
        raise CustomerServiceHttpError("customer-service HTTP evidence is incomplete")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise CustomerServiceHttpError(f"invalid SHA256SUMS line: {line!r}")
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CustomerServiceHttpError("HTTP evidence inventory escapes package") from exc
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise CustomerServiceHttpError("HTTP evidence file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise CustomerServiceHttpError(f"HTTP evidence hash mismatch: {name}")
    manifest = load_json(manifest_path)
    report = load_json(root / "report.json")
    if manifest.get("schemaVersion") != HTTP_EVIDENCE_SCHEMA:
        raise CustomerServiceHttpError("HTTP evidence manifest schema is invalid")
    if report.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceHttpError("HTTP evidence report schema is invalid")
    if manifest.get("runId") != report.get("runId"):
        raise CustomerServiceHttpError("HTTP evidence run IDs differ")
    if manifest.get("files") != _evidence_inventory(root):
        raise CustomerServiceHttpError("HTTP evidence manifest inventory is stale")
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise CustomerServiceHttpError(f"HTTP evidence is writable: {writable}")
    return {
        "verified": True,
        "root": str(root),
        "runId": manifest.get("runId"),
        "sha256SumsSha256": sha256_file(sums_path),
    }


def report_digest(report: Mapping[str, Any]) -> str:
    """Stable content digest used by benchmark package manifests and tests."""

    return sha256_bytes(canonical_json_bytes(report))
