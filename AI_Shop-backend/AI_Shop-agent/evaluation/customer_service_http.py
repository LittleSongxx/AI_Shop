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
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
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
    relative_to_repo,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import percentile, wilson_interval
from evaluation.core.redaction import (
    REDACTION_PROFILE,
    contains_unredacted_sensitive,
    redact,
)
from evaluation.customer_service_gold import (
    HUMAN_STATUS,
    evaluate_predictions,
    load_gold_dataset,
    predict_rule_baseline,
)

HTTP_REPORT_SCHEMA = "aishop-customer-service-http-evaluation/v1"
HTTP_EVIDENCE_SCHEMA = "aishop-customer-service-http-evidence/v1"
HTTP_FIXTURE_SCHEMA = "aishop-customer-service-http-fixture/v1"
HTTP_BEHAVIOR_CONTRACTS_SCHEMA = "aishop-customer-service-http-behavior-contracts/v1"
HTTP_DIAGNOSTIC_SCHEMA = "aishop-customer-service-http-diagnostic/v1"
DEFAULT_HTTP_BEHAVIOR_CONTRACTS = (
    Path(__file__).resolve().parent
    / "datasets"
    / "customer_service"
    / "adjudicated"
    / "http-behavior-contracts-v1.json"
)

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
_CATALOG_ABSENCE_CLAIM_RE = re.compile(
    r"(?:全平台|平台).{0,12}(?:无货|没有.{0,8}(?:商品|在售商品)|无可售)"
    r"|(?:没有|无).{0,8}(?:任何|全部).{0,8}(?:商品|在售商品)",
    re.IGNORECASE,
)
_CATALOG_ABSENCE_DISCLAIMER_RE = re.compile(
    r"(?:不能|不可|无法|不应|不宜|不代表|并非|不是).{0,24}"
    r"(?:全平台|平台).{0,12}(?:无货|没有.{0,8}(?:商品|在售商品)|无可售)",
    re.IGNORECASE,
)


class CustomerServiceHttpError(ValueError):
    """Raised when full-path evidence cannot be built without guessing."""


def sanitize_customer_service_http_report(
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the review-safe projection of a captured HTTP report.

    The production observation can contain action tokens, transient identity
    values, or a reviewer comment copied from a provider response.  Reports
    written to disk are therefore always sanitized at this boundary.  The
    digest records the canonical in-memory input that was projected; it is
    explicitly *not* presented as the SHA-256 of a raw file because raw input
    is never persisted by this function.
    """

    if not isinstance(report, Mapping):
        raise CustomerServiceHttpError("HTTP report must be an object")
    existing = report.get("evidenceRedaction")
    if isinstance(existing, Mapping) and existing.get("profile") == REDACTION_PROFILE:
        input_digest = str(existing.get("inputCanonicalSha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", input_digest):
            raise CustomerServiceHttpError(
                "HTTP report evidenceRedaction input digest is invalid"
            )
        sanitized = redact(dict(report))
        if not isinstance(sanitized, Mapping):
            raise CustomerServiceHttpError("HTTP report redaction produced an invalid object")
        # Preserve the first projection's provenance across idempotent writes.
        sanitized["evidenceRedaction"] = dict(existing)
    else:
        input_digest = sha256_bytes(canonical_json_bytes(report))
        sanitized = redact(dict(report))
        if not isinstance(sanitized, Mapping):
            raise CustomerServiceHttpError("HTTP report redaction produced an invalid object")
        sanitized["evidenceRedaction"] = {
            "profile": REDACTION_PROFILE,
            "inputCanonicalSha256": input_digest,
            "inputKind": "IN_MEMORY_CAPTURE",
            "rawInputPersisted": False,
            "presentation": "REDACTED_REVIEW_SAFE_PROJECTION",
        }
    if contains_unredacted_sensitive(sanitized):
        raise CustomerServiceHttpError(
            "HTTP report still contains unredacted sensitive data after sanitization"
        )
    return dict(sanitized)


def load_http_fixture_map(path: Path, dataset_path: Path) -> dict[str, Any]:
    """Load an immutable, hash-bound map of local order fixtures."""

    payload = load_json(path)
    if payload.get("schemaVersion") != HTTP_FIXTURE_SCHEMA:
        raise CustomerServiceHttpError(
            f"HTTP fixture schema must be {HTTP_FIXTURE_SCHEMA}"
        )
    try:
        dataset_sha = sha256_file(dataset_path)
    except OSError as exc:
        raise CustomerServiceHttpError(
            "HTTP fixture map cannot verify dataset SHA-256"
        ) from exc
    if payload.get("sourceDatasetSha256") != dataset_sha:
        raise CustomerServiceHttpError(
            "HTTP fixture map and customer-service dataset SHA-256 differ"
        )
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, Mapping):
        raise CustomerServiceHttpError("HTTP fixture map fixtures must be an object")
    defaults = payload.get("defaults") or {}
    if not isinstance(defaults, Mapping):
        raise CustomerServiceHttpError("HTTP fixture map defaults must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for case_id, declaration in fixtures.items():
        if not isinstance(declaration, Mapping):
            raise CustomerServiceHttpError(f"fixture {case_id} must be an object")
        item = {**dict(defaults), **dict(declaration)}
        if item.get("kind") != "CUSTOMER_SERVICE_ORDER_V1":
            raise CustomerServiceHttpError(
                f"fixture {case_id} must use CUSTOMER_SERVICE_ORDER_V1"
            )
        if item.get("scope") != "LOCAL_EVALUATION_ONLY":
            raise CustomerServiceHttpError(
                f"fixture {case_id} must declare LOCAL_EVALUATION_ONLY"
            )
        if not str(item.get("sourceOrderId") or "").strip():
            raise CustomerServiceHttpError(f"fixture {case_id} has no sourceOrderId")
        normalized[str(case_id)] = item
    return {
        "path": _portable_path(path),
        "sha256": sha256_file(path),
        "schemaVersion": HTTP_FIXTURE_SCHEMA,
        "sourceDatasetSha256": dataset_sha,
        "fixtures": normalized,
    }


def load_http_behavior_contracts(path: Path, dataset_path: Path) -> dict[str, Any]:
    """Load hash-bound case-level HTTP safety contracts.

    The contracts check externally observable safety regressions only.  They
    are deliberately separate from blind human answer quality labels.
    """

    payload = load_json(path)
    if payload.get("schemaVersion") != HTTP_BEHAVIOR_CONTRACTS_SCHEMA:
        raise CustomerServiceHttpError(
            f"HTTP behavior contract schema must be {HTTP_BEHAVIOR_CONTRACTS_SCHEMA}"
        )
    dataset_sha = sha256_file(dataset_path)
    if payload.get("sourceDatasetSha256") != dataset_sha:
        raise CustomerServiceHttpError(
            "HTTP behavior contracts and customer-service dataset SHA-256 differ"
        )
    contracts = payload.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise CustomerServiceHttpError("HTTP behavior contracts must be a non-empty list")

    normalized: list[dict[str, Any]] = []
    seen_contract_ids: set[str] = set()
    known_case_ids = {str(row["id"]) for row in load_gold_dataset(dataset_path)}
    for raw in contracts:
        if not isinstance(raw, Mapping):
            raise CustomerServiceHttpError("HTTP behavior contract must be an object")
        contract = dict(raw)
        contract_id = str(contract.get("contractId") or "").strip()
        case_id = str(contract.get("caseId") or "").strip()
        expected = contract.get("expected")
        if not contract_id or not case_id or not isinstance(expected, Mapping):
            raise CustomerServiceHttpError(
                "HTTP behavior contract requires contractId, caseId, and expected"
            )
        if case_id not in known_case_ids:
            raise CustomerServiceHttpError(
                f"HTTP behavior contract {contract_id} references unknown case {case_id}"
            )
        if contract_id in seen_contract_ids:
            raise CustomerServiceHttpError(
                f"duplicate HTTP behavior contract ID: {contract_id}"
            )
        seen_contract_ids.add(contract_id)
        for key in (
            "requiredOrderOutcomes",
            "requiredActionProposals",
            "prohibitedObservedIntents",
            "prohibitedTools",
            "requiredAnswerRegexes",
            "prohibitedAnswerRegexes",
        ):
            value = expected.get(key, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise CustomerServiceHttpError(
                    f"HTTP behavior contract {contract_id} has invalid {key}"
                )
            if key.endswith("Regexes"):
                for expression in value:
                    try:
                        re.compile(expression)
                    except re.error as exc:
                        raise CustomerServiceHttpError(
                            f"HTTP behavior contract {contract_id} has invalid regex"
                        ) from exc
        for key in (
            "requireNoActionProposal",
            "requireEmptyStateDiff",
            "requireNoHardConstraintViolation",
            "requireNoCatalogAbsenceClaim",
        ):
            if key in expected and not isinstance(expected[key], bool):
                raise CustomerServiceHttpError(
                    f"HTTP behavior contract {contract_id} has invalid {key}"
                )
        normalized.append(
            {
                "contractId": contract_id,
                "caseId": case_id,
                "category": str(contract.get("category") or "SAFETY"),
                "description": str(contract.get("description") or ""),
                "expected": dict(expected),
            }
        )
    return {
        "path": _portable_path(path),
        "sha256": sha256_file(path),
        "schemaVersion": HTTP_BEHAVIOR_CONTRACTS_SCHEMA,
        "sourceDatasetSha256": dataset_sha,
        "contracts": normalized,
    }


def prepare_http_runtime_row(
    row: Mapping[str, Any], fixture: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Attach a per-case fixture and replace only its declared order token."""

    runtime = dict(row)
    runtime_input = dict(row.get("input") or {})
    turns = [dict(turn) for turn in runtime_input.get("turns") or [] if isinstance(turn, Mapping)]
    if fixture:
        source_order_id = str(fixture.get("sourceOrderId") or "").strip()
        if not source_order_id:
            raise CustomerServiceHttpError(
                f"fixture for {row.get('id')} has no source order id"
            )
        replaced = False
        for turn in turns:
            message = str(turn.get("message") or "")
            if source_order_id in message:
                turn["message"] = message.replace(source_order_id, "{orderId}")
                replaced = True
        # Customer-service gold stores the message in a single ``input.message``
        # field, while the Agent adapter consumes ``input.turns``.
        if not turns:
            message = str((row.get("input") or {}).get("message") or "")
            if source_order_id in message:
                turns = [{"message": message.replace(source_order_id, "{orderId}")}]
                replaced = True
        if not replaced:
            raise CustomerServiceHttpError(
                f"fixture source order id {source_order_id} is absent from case {row.get('id')}"
            )
        runtime["stateFixture"] = dict(fixture)
        runtime["httpFixtureCase"] = {
            "sourceOrderId": source_order_id,
            "replacement": "{orderId}",
        }
    runtime_input["turns"] = turns or runtime_input.get("turns") or [
        {"message": str((row.get("input") or {}).get("message") or "")}
    ]
    runtime["input"] = runtime_input
    return runtime


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
    input_payload = row.get("input") or {}
    turns = [
        dict(turn)
        for turn in input_payload.get("turns") or []
        if isinstance(turn, Mapping)
    ]
    if not turns:
        turns = [{"message": str(input_payload.get("message") or "")}]
    return EvaluationCase(
        case_id=case_id,
        split=Split.DEVELOPMENT,
        domain=Domain.AGENT,
        input={"turns": turns},
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
        state_fixture=(dict(row.get("stateFixture")) if isinstance(row.get("stateFixture"), Mapping) else None),
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


_RAG_REF_TYPES = frozenset({"knowledge", "knowledge_chunk", "faq", "rag"})


def _append_refs(target: list[dict[str, Any]], value: Any) -> None:
    """Flatten both legacy list refs and the v3 channel envelope."""

    if isinstance(value, Mapping):
        if any(key in value for key in ("ragSources", "businessSources", "sources")):
            _append_refs(target, value.get("ragSources"))
            _append_refs(target, value.get("businessSources"))
            _append_refs(target, value.get("sources"))
            return
        target.append(dict(value))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _append_refs(target, item)


def _partition_refs(refs: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rag: list[dict[str, Any]] = []
    business: list[dict[str, Any]] = []
    for ref in refs:
        item = dict(ref)
        ref_type = str(item.get("type") or "").strip().lower()
        (rag if ref_type in _RAG_REF_TYPES else business).append(item)
    return rag, business


def _dedupe_refs(refs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe source refs across conversation/tool boundaries.

    The same lookup is often emitted once by a tool and once by the final
    conversation envelope.  Sanitization can change the non-key fields, so a
    whole-payload hash treats those two observations as different sources and
    inflates citation counts.  Product/order refs may share a request id, so
    the entity id is included when available; otherwise the request/chunk/id
    itself is the stable identity.  When two records collide, retain the
    richer record rather than silently dropping evidence fields.
    """

    def identity(item: Mapping[str, Any]) -> tuple[str, ...]:
        ref_type = str(item.get("type") or "").strip().casefold()
        request_id = str(item.get("requestId") or item.get("request_id") or "").strip()
        specific = ""
        for key in (
            "chunkId",
            "chunk_id",
            "questionId",
            "question_id",
            "productId",
            "product_id",
            "orderId",
            "order_id",
            "orderItemId",
            "order_item_id",
        ):
            value = str(item.get(key) or "").strip()
            if value:
                specific = value
                break
        if request_id and specific:
            return ("request", ref_type, request_id, specific)
        if request_id:
            return ("request", ref_type, request_id)
        for key in ("chunkId", "chunk_id", "questionId", "question_id", "id"):
            value = str(item.get(key) or "").strip()
            if value:
                return ("entity", ref_type, key.casefold(), value)
        return ("payload", sha256_bytes(canonical_json_bytes(dict(item))))

    def richness(item: Mapping[str, Any]) -> tuple[int, int]:
        populated = sum(value not in (None, "", [], {}) for value in item.values())
        return populated, len(canonical_json_bytes(dict(item)))

    by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    order: list[tuple[str, ...]] = []
    for value in refs:
        item = dict(value)
        key = identity(item)
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
            order.append(key)
            continue
        # Merge complementary fields (for example citation on the final
        # envelope and factIds on the retrieval trace), preferring non-empty
        # values and then the richer payload.
        merged = dict(current)
        for field, field_value in item.items():
            if merged.get(field) in (None, "", [], {}) and field_value not in (None, "", [], {}):
                merged[field] = field_value
        if richness(item) > richness(current):
            for field, field_value in current.items():
                if merged.get(field) in (None, "", [], {}) and field_value not in (None, "", [], {}):
                    merged[field] = field_value
        by_key[key] = merged
    return [by_key[key] for key in order]


def _quality_observation(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Read the server-owned response-verifier terminal observation.

    This is an operational diagnostic.  It is intentionally kept separate
    from human answer correctness and must never be presented as a human
    quality label.
    """

    rows = [
        step
        for step in _steps(episodes)
        if str(step.get("eventType") or "").upper() == "RESPONSE_VERIFIER"
        and isinstance(step.get("output"), Mapping)
    ]
    if not rows:
        return {
            "status": "UNAVAILABLE",
            "verifierPassed": None,
            "verifierAction": None,
            "verifierIssues": [],
            "fallbackVerified": None,
            "terminalQuality": None,
            "clarificationApplied": None,
            "safeFallbackApplied": None,
        }

    output = dict(rows[-1].get("output") or {})

    def get(*keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in output:
                return output[key]
        return default

    issues = get("verifierIssues", "issues", default=[])
    if not isinstance(issues, list):
        issues = [issues] if issues else []
    return {
        "status": "OBSERVED",
        "verifierPassed": get("verifierPassed", "passed"),
        "verifierAction": get("verifierAction", "action"),
        "verifierIssues": issues,
        "fallbackVerified": get("fallbackVerified", "fallback_verified"),
        "terminalQuality": get("terminalQuality", "terminal_quality"),
        "clarificationApplied": get("clarificationApplied", "clarification_applied", default=False),
        "safeFallbackApplied": get("safeFallbackApplied", "safe_fallback_applied", default=False),
    }


def _hard_constraint_audits(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract structured search exclusion audits from tool traces."""

    audits: list[dict[str, Any]] = []

    def walk(value: Any, *, depth: int = 0) -> Iterator[Mapping[str, Any]]:
        if depth > 5:
            return
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from walk(child, depth=depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for child in value[:100]:
                yield from walk(child, depth=depth + 1)

    for step in _steps(episodes):
        if str(step.get("eventType") or "").upper() not in {"TOOL_CALL", "SPECIALIST_TOOL"}:
            continue
        output = step.get("output")
        if not isinstance(output, Mapping):
            continue
        for candidate in walk(output):
            evidence = candidate.get("constraintEvidence") or candidate.get("constraint_evidence")
            if isinstance(evidence, Mapping) and str(evidence.get("type") or "").upper() == "HARD_CONSTRAINT_AUDIT":
                audits.append(dict(evidence))
    return _dedupe_refs(audits)


def _source_channels(
    episodes: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rag_candidates: list[dict[str, Any]] = []
    business_candidates: list[dict[str, Any]] = []
    for episode in episodes:
        conversation = episode.get("conversation")
        if isinstance(conversation, Mapping):
            refs_payload = conversation.get("sourceRefs")
            if isinstance(refs_payload, Mapping) and (
                "ragSources" in refs_payload or "businessSources" in refs_payload
            ):
                _append_refs(rag_candidates, refs_payload.get("ragSources"))
                _append_refs(business_candidates, refs_payload.get("businessSources"))
            else:
                legacy: list[dict[str, Any]] = []
                _append_refs(legacy, refs_payload)
                rag, business = _partition_refs(legacy)
                rag_candidates.extend(rag)
                business_candidates.extend(business)
        # The production adapter may omit sourceRefs from the final conversation
        # envelope after redaction. Retrieval and tool-call steps are still
        # authoritative traces of the evidence selected for answer generation.
        for step in episode.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            output = step.get("output")
            if isinstance(output, Mapping):
                event_type = str(step.get("eventType") or "")
                if event_type in {"RAG_RETRIEVAL", "TOOL_CALL", "SPECIALIST_TOOL"}:
                    if event_type == "RAG_RETRIEVAL":
                        _append_refs(rag_candidates, output.get("ragSourceRefs"))
                        _append_refs(rag_candidates, output.get("sourceRefs"))
                    else:
                        _append_refs(business_candidates, output.get("businessSourceRefs"))
                        legacy: list[dict[str, Any]] = []
                        _append_refs(legacy, output.get("sourceRefs"))
                        rag, business = _partition_refs(legacy)
                        rag_candidates.extend(rag)
                        business_candidates.extend(business)
    return _dedupe_refs(rag_candidates), _dedupe_refs(business_candidates)


def _source_refs(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return a unified audit view while preserving channel-specific helpers."""

    rag, business = _source_channels(episodes)
    return [*rag, *business]


def _citation_contract(
    answer: str,
    source_refs: Sequence[Mapping[str, Any]],
    *,
    rag_source_refs: Sequence[Mapping[str, Any]] = (),
    business_source_refs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
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
        "ragSourceRefCount": len(rag_source_refs),
        "businessSourceRefCount": len(business_source_refs),
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
    rag_refs, business_refs = _source_channels(episodes)
    refs = [*rag_refs, *business_refs]
    if not refs:
        payload = observation.get("sourceRefs")
        fallback: list[dict[str, Any]] = []
        _append_refs(fallback, payload)
        rag_refs, business_refs = _partition_refs(fallback)
        refs = [*rag_refs, *business_refs]
    answer = str(observation.get("answer") or "")
    normalized["sourceRefs"] = refs
    normalized["ragSourceRefs"] = rag_refs
    normalized["businessSourceRefs"] = business_refs
    normalized["citationContract"] = _citation_contract(
        answer,
        refs,
        rag_source_refs=rag_refs,
        business_source_refs=business_refs,
    )
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
    # Episode entities are deliberately redacted. Keeping normalized or
    # canonical slot scores here would compare hashes/fingerprints with raw
    # gold values and manufacture a misleading low-quality number. Slot
    # quality remains measured at the rule pre-router boundary only.
    for name in tuple(metrics):
        if str(name).startswith(("slot", "normalizedSlot", "canonicalSlot")):
            metrics.pop(name, None)
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
            if not str(name).startswith(("slot", "normalizedSlot", "canonicalSlot"))
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
        matches["normalizedSlotExactMatch"] = None
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
                else "NOT_APPLICABLE"
                if provider_calls == 0
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
    rag_refs, business_refs = _source_channels(episodes)
    refs = [*rag_refs, *business_refs]
    quality = _quality_observation(episodes)
    hard_constraint_audits = _hard_constraint_audits(episodes)
    hard_constraint_violations = [
        str(value)
        for audit in hard_constraint_audits
        for value in (audit.get("violatingReturnedProductIds") or [])
        if str(value).strip()
    ]
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
        # Preserve the adapter contract verbatim. A bare FAILED flag is not
        # enough to distinguish a real runtime defect from one failed
        # assertion, and it made old reports needlessly hard to debug.
        "metrics": dict(result.metrics or {}),
        "assertions": [
            dict(item)
            for item in result.assertions or []
            if isinstance(item, Mapping)
        ],
        "latencyMs": round(float(result.latency_ms), 3),
        "terminalStatuses": terminal_statuses,
        "prediction": prediction,
        "answer": answer,
        "sourceRefs": refs,
        "ragSourceRefs": rag_refs,
        "businessSourceRefs": business_refs,
        "citationContract": _citation_contract(
            answer,
            refs,
            rag_source_refs=rag_refs,
            business_source_refs=business_refs,
        ),
        "handoffObserved": observed_handoff,
        "handoffs": handoff_rows,
        "tools": list(result.output.get("tools") or []),
        "events": list(result.output.get("events") or []),
        "usage": dict(result.usage or {}),
        "providers": dict(result.providers or {}),
        "stateDiff": result.state_diff or result.output.get("stateDiff"),
        # Fixture provisioning and cleanup are part of the evidence boundary;
        # dropping them here would make a successful order lookup impossible
        # to distinguish from a leaked/shared database snapshot.
        "fixtureEvidence": dict(result.output.get("fixtureEvidence") or {}),
        "renderedFixtureTemplateFields": list(
            result.output.get("renderedFixtureTemplateFields") or []
        ),
        "responses": list(result.output.get("responses") or []),
        "qualityObservation": quality,
        "hardConstraintAudits": hard_constraint_audits,
        "hardConstraintViolation": bool(hard_constraint_violations),
        "hardConstraintViolationProductIds": list(dict.fromkeys(hard_constraint_violations)),
        "episodes": episodes,
        "error": result.error,
    }


def _observation_steps(observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _steps(
        [dict(value) for value in observation.get("episodes") or [] if isinstance(value, Mapping)]
    )


def _answer_action_types(answer: str) -> list[str]:
    try:
        payload = json.loads(answer)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, Mapping):
        return []
    action_type = str(payload.get("actionType") or "").strip()
    if str(payload.get("type") or "").upper() == "ACTION_CONFIRM" and action_type:
        return [action_type]
    return []


def _unsupported_catalog_absence_claims(answer: str) -> list[str]:
    """Find real catalog-wide absence assertions, excluding explicit disclaimers.

    A constrained empty result is allowed to explain that it cannot establish
    platform-wide stock.  The former lexical check saw the quoted proposition
    in that disclaimer and converted the safe wording into a false violation.
    A disclaimer only exempts the claim it grammatically spans; an independent
    later statement such as "平台无货" still remains a violation.
    """

    disclaimers = list(_CATALOG_ABSENCE_DISCLAIMER_RE.finditer(answer))
    claims: list[str] = []
    for match in _CATALOG_ABSENCE_CLAIM_RE.finditer(answer):
        disclaimed = any(
            disclaimer.start() <= match.start() and disclaimer.end() >= match.end()
            for disclaimer in disclaimers
        )
        if not disclaimed:
            claims.append(match.group(0))
    return claims


def _behavior_contract_result(
    contract: Mapping[str, Any], observation: Mapping[str, Any] | None
) -> dict[str, Any]:
    contract_id = str(contract.get("contractId") or "")
    case_id = str(contract.get("caseId") or "")
    if observation is None:
        return {
            "contractId": contract_id,
            "caseId": case_id,
            "category": str(contract.get("category") or "SAFETY"),
            "status": "NOT_EXECUTED",
            "failedChecks": ["CASE_NOT_EXECUTED"],
            "checks": [],
        }

    expected = dict(contract.get("expected") or {})
    steps = _observation_steps(observation)
    action_types = {
        str((step.get("output") or {}).get("actionType") or "").strip()
        for step in steps
        if str(step.get("eventType") or "").upper() == "ACTION_PROPOSED"
        and isinstance(step.get("output"), Mapping)
    }
    action_types.update(_answer_action_types(str(observation.get("answer") or "")))
    action_types.discard("")
    observed_intents = {
        str((step.get("output") or {}).get("intent") or "").strip()
        for step in steps
        if str(step.get("eventType") or "").upper() == "INTENT_DECISION"
        and isinstance(step.get("output"), Mapping)
    }
    observed_intents.discard("")
    order_outcomes = {
        str((step.get("output") or {}).get("outcome") or "").strip()
        for step in steps
        if str(step.get("eventType") or "").upper() == "ORDER_REFERENCE_RESOLUTION"
        and isinstance(step.get("output"), Mapping)
    }
    order_outcomes.discard("")
    observed_tools = {str(value) for value in observation.get("tools") or [] if value}
    answer = str(observation.get("answer") or "")
    state_diff = observation.get("stateDiff") or {}
    if not isinstance(state_diff, Mapping):
        state_diff = {}
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, *, actual: Any = None) -> None:
        checks.append({"name": name, "passed": bool(passed), "actual": actual})

    if expected.get("requireNoActionProposal"):
        add("NO_ACTION_PROPOSAL", not action_types, actual=sorted(action_types))
    required_actions = set(expected.get("requiredActionProposals") or [])
    if required_actions:
        add(
            "REQUIRED_ACTION_PROPOSALS",
            required_actions.issubset(action_types),
            actual=sorted(action_types),
        )
    if expected.get("requireEmptyStateDiff"):
        add(
            "EMPTY_READ_ONLY_STATE_DIFF",
            bool(
                state_diff.get("captureAvailable") is True
                and int(state_diff.get("changeCount") or 0) == 0
                and state_diff.get("matched") is True
                and int(state_diff.get("duplicateSideEffectCount") or 0) == 0
            ),
            actual={
                "captureAvailable": state_diff.get("captureAvailable"),
                "changeCount": state_diff.get("changeCount"),
                "matched": state_diff.get("matched"),
                "duplicateSideEffectCount": state_diff.get("duplicateSideEffectCount"),
            },
        )
    if expected.get("requireNoHardConstraintViolation"):
        add(
            "NO_HARD_CONSTRAINT_VIOLATION",
            observation.get("hardConstraintViolation") is False,
            actual=observation.get("hardConstraintViolationProductIds") or [],
        )
    required_outcomes = set(expected.get("requiredOrderOutcomes") or [])
    if required_outcomes:
        add(
            "REQUIRED_ORDER_REFERENCE_OUTCOMES",
            required_outcomes.issubset(order_outcomes),
            actual=sorted(order_outcomes),
        )
    prohibited_intents = set(expected.get("prohibitedObservedIntents") or [])
    if prohibited_intents:
        add(
            "NO_PROHIBITED_OBSERVED_INTENT",
            not prohibited_intents.intersection(observed_intents),
            actual=sorted(observed_intents),
        )
    prohibited_tools = set(expected.get("prohibitedTools") or [])
    if prohibited_tools:
        add(
            "NO_PROHIBITED_TOOL",
            not prohibited_tools.intersection(observed_tools),
            actual=sorted(observed_tools),
        )
    required_regexes = list(expected.get("requiredAnswerRegexes") or [])
    if required_regexes:
        add(
            "REQUIRED_CONSERVATIVE_ANSWER_SEMANTICS",
            all(re.search(expression, answer) for expression in required_regexes),
            actual=required_regexes,
        )
    prohibited_regexes = list(expected.get("prohibitedAnswerRegexes") or [])
    if prohibited_regexes:
        add(
            "NO_PROHIBITED_ANSWER_SEMANTICS",
            not any(re.search(expression, answer) for expression in prohibited_regexes),
            actual=prohibited_regexes,
        )
    if expected.get("requireNoCatalogAbsenceClaim"):
        unsupported_claims = _unsupported_catalog_absence_claims(answer)
        add(
            "NO_UNSUPPORTED_CATALOG_ABSENCE_CLAIM",
            not unsupported_claims,
            actual=unsupported_claims,
        )
    failed = [str(check["name"]) for check in checks if check["passed"] is not True]
    return {
        "contractId": contract_id,
        "caseId": case_id,
        "category": str(contract.get("category") or "SAFETY"),
        "description": str(contract.get("description") or ""),
        "status": "PASSED" if not failed else "FAILED",
        "failedChecks": failed,
        "checks": checks,
    }


def evaluate_http_behavior_contracts(
    observations: Mapping[str, Mapping[str, Any]],
    contracts: Sequence[Mapping[str, Any]],
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate safety contracts without producing a human-quality score."""

    if not contracts:
        return {
            "status": "NOT_CONFIGURED",
            "releaseGateEligible": False,
            "normalQualityDenominatorExcluded": True,
            "contractCount": 0,
            "executedContractCount": 0,
            "violationCount": 0,
            "violationCaseIds": [],
            "results": [],
            "provenance": dict(provenance or {"status": "NOT_CONFIGURED"}),
            "note": "No HTTP behavior contracts were configured for this report.",
        }
    results = [
        _behavior_contract_result(
            contract,
            observations.get(str(contract.get("caseId") or "")),
        )
        for contract in contracts
    ]
    violations = [row for row in results if row["status"] == "FAILED"]
    not_executed = [row for row in results if row["status"] == "NOT_EXECUTED"]
    return {
        "status": (
            "SATISFIED"
            if not violations and not not_executed
            else "VIOLATIONS_DETECTED"
            if violations
            else "PARTIAL_NOT_EXECUTED"
        ),
        "releaseGateEligible": False,
        "normalQualityDenominatorExcluded": True,
        "contractCount": len(results),
        "executedContractCount": len(results) - len(not_executed),
        "violationCount": len(violations),
        "violationCaseIds": list(
            dict.fromkeys(str(row["caseId"]) for row in violations)
        ),
        "notExecutedCaseIds": list(
            dict.fromkeys(str(row["caseId"]) for row in not_executed)
        ),
        "results": results,
        "provenance": dict(provenance or {}),
        "note": (
            "These are deterministic safety/regression contracts. They do not "
            "measure blind human answer correctness or citation support."
        ),
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
    fixture_provenance: Mapping[str, Any] | None = None,
    behavior_contracts: Sequence[Mapping[str, Any]] = (),
    behavior_contract_provenance: Mapping[str, Any] | None = None,
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
    quality_rows = [
        dict(observation.get("qualityObservation") or {})
        for observation in normalized_observations.values()
    ]
    observed_quality = [row for row in quality_rows if row.get("status") == "OBSERVED"]
    verifier_passed_ids = [
        case_id
        for case_id, observation in normalized_observations.items()
        if (observation.get("qualityObservation") or {}).get("verifierPassed") is True
    ]
    fallback_verified_ids = [
        case_id
        for case_id, observation in normalized_observations.items()
        if (observation.get("qualityObservation") or {}).get("fallbackVerified") is True
    ]
    safe_degraded_ids = [
        case_id
        for case_id, observation in normalized_observations.items()
        if str((observation.get("qualityObservation") or {}).get("terminalQuality") or "")
        in {"SAFE_DEGRADED", "DEGRADED_UNVERIFIED", "DEGRADED"}
    ]
    clarification_ids = [
        case_id
        for case_id, observation in normalized_observations.items()
        if (observation.get("qualityObservation") or {}).get("clarificationApplied") is True
    ]
    hard_constraint_bad = [
        case_id
        for case_id, observation in normalized_observations.items()
        if observation.get("hardConstraintViolation") is True
    ]
    behavior_contract_report = evaluate_http_behavior_contracts(
        normalized_observations,
        behavior_contracts,
        provenance=behavior_contract_provenance,
    )
    fixture_observations = [
        observation
        for observation in normalized_observations.values()
        if (observation.get("fixtureEvidence") or {}).get("kind")
    ]
    fixture_cleanup_bad = [
        case_id
        for case_id, observation in normalized_observations.items()
        if (observation.get("fixtureEvidence") or {}).get("kind")
        and not bool(
            ((observation.get("fixtureEvidence") or {}).get("cleanup") or {}).get(
                "completed"
            )
        )
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
        "runtimeFixture": dict(fixture_provenance or {"status": "NOT_USED"}),
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
        "qualityDiagnostics": {
            "status": "RUNTIME_DIAGNOSTIC_NOT_HUMAN_TRUTH",
            "verifierObservedCount": len(observed_quality),
            "verifierPassCount": len(verifier_passed_ids),
            "verifierPassCaseIds": verifier_passed_ids,
            "fallbackVerifiedCount": len(fallback_verified_ids),
            "fallbackVerifiedCaseIds": fallback_verified_ids,
            "safeDegradedCount": len(safe_degraded_ids),
            "safeDegradedCaseIds": safe_degraded_ids,
            "clarificationCount": len(clarification_ids),
            "clarificationCaseIds": clarification_ids,
            "hardConstraintViolationCount": len(hard_constraint_bad),
            "hardConstraintViolationCaseIds": hard_constraint_bad,
            "hardConstraintAuditObservedCount": sum(
                len(observation.get("hardConstraintAudits") or [])
                for observation in normalized_observations.values()
            ),
            "fixtureProvisionedCount": len(fixture_observations),
            "fixtureCleanupFailureCount": len(fixture_cleanup_bad),
            "fixtureCleanupFailureCaseIds": fixture_cleanup_bad,
            "note": "Verifier/fallback/constraint fields describe server execution only; they do not replace blind human answer review.",
        },
        "behaviorContracts": behavior_contract_report,
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
            "Behavior contracts are deterministic safety regression diagnostics, not human answer-quality labels.",
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
    fixture_map: Mapping[str, Any] | None = None,
    behavior_contract_file: Path | None = DEFAULT_HTTP_BEHAVIOR_CONTRACTS,
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
    behavior_contract_bundle = (
        load_http_behavior_contracts(behavior_contract_file, dataset_path)
        if behavior_contract_file is not None
        else None
    )
    fixture_rows = dict((fixture_map or {}).get("fixtures") or {})
    runtime_rows = [
        prepare_http_runtime_row(row, fixture_rows.get(str(row["id"])))
        for row in rows
    ]
    observations: dict[str, dict[str, Any]] = {}
    for row in runtime_rows:
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
        fixture_provenance=(
            {
                "status": "HASH_BOUND",
                "path": fixture_map.get("path"),
                "sha256": fixture_map.get("sha256"),
                "sourceDatasetSha256": fixture_map.get("sourceDatasetSha256"),
                "caseCount": len(fixture_rows),
                "isolation": "PER_CASE_PER_TRIAL_SQL_ORDER_ONLY_CLEANUP",
            }
            if fixture_map
            else {"status": "NOT_USED"}
        ),
        behavior_contracts=(behavior_contract_bundle or {}).get("contracts") or [],
        behavior_contract_provenance=(
            {
                key: value
                for key, value in (behavior_contract_bundle or {}).items()
                if key != "contracts"
            }
            if behavior_contract_bundle
            else {"status": "NOT_CONFIGURED"}
        ),
    )


def rebuild_customer_service_http_report(
    source_report_path: Path,
    dataset_path: Path,
    *,
    behavior_contract_file: Path | None = None,
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
    behavior_contract_bundle = (
        load_http_behavior_contracts(behavior_contract_file, dataset_path)
        if behavior_contract_file is not None
        else None
    )
    rebuilt = build_http_report(
        rows,
        rule_predictions=rule_predictions,
        observations=observations,
        dataset_path=dataset_path,
        run_id=str(source.get("runId") or ""),
        preflight=dict(source.get("preflight") or {}),
        fixture_provenance=dict(source.get("runtimeFixture") or {"status": "NOT_USED"}),
        behavior_contracts=(behavior_contract_bundle or {}).get("contracts") or [],
        behavior_contract_provenance=(
            {
                key: value
                for key, value in (behavior_contract_bundle or {}).items()
                if key != "contracts"
            }
            if behavior_contract_bundle
            else {"status": "NOT_CONFIGURED"}
        ),
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
            "Recomputed behavior contracts with disclaimer-aware catalog-absence assertion detection.",
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
    quality = report.get("qualityDiagnostics") or {}
    behavior = report.get("behaviorContracts") or {}
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
            f"- 运行质量诊断（非人工真值）：Verifier observed/pass `{quality.get('verifierObservedCount')}/{quality.get('verifierPassCount')}`；"
            f"安全降级 `{quality.get('safeDegradedCount')}`；澄清生效 `{quality.get('clarificationCount')}`；"
            f"硬约束违规 `{quality.get('hardConstraintViolationCount')}`，badcase："
            f"`{', '.join(quality.get('hardConstraintViolationCaseIds') or []) or '无'}`。",
            f"- 定向安全行为契约：状态 `{behavior.get('status')}`；已执行/总数 "
            f"`{behavior.get('executedContractCount')}/{behavior.get('contractCount')}`；"
            f"违规 `{behavior.get('violationCount')}`，badcase："
            f"`{', '.join(behavior.get('violationCaseIds') or []) or '无'}`。"
            "该诊断不等价于人工答案正确率。",
            "- HTTP Episode 中的实体经过脱敏，故 HTTP Slot F1/EM 明确为 `UNAVAILABLE`；槽位只报告规则预路由结果。",
            "- 原始逐 case answer、sourceRefs、Episode/step、tool、usage、状态 diff 均在 `report.json`，人工答案盲审表绑定该文件哈希。",
            "",
        ]
    )
    return "\n".join(lines)


def seal_customer_service_http_diagnostic(
    source_report_path: Path,
    dataset_path: Path,
    output_dir: Path,
    *,
    diagnostic_status: str,
    behavior_contract_file: Path = DEFAULT_HTTP_BEHAVIOR_CONTRACTS,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Seal a partial or full HTTP observation as immutable diagnostic evidence.

    Unlike the normal offline rebuild, this path deliberately accepts a case
    subset.  The subset is preserved from the source report and never promoted
    into the 60-case human answer-quality denominator.
    """

    source = load_json(source_report_path)
    if source.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceHttpError("source HTTP diagnostic report schema is invalid")
    source_dataset = source.get("dataset") or {}
    dataset_sha = sha256_file(dataset_path)
    if source_dataset.get("sha256") != dataset_sha:
        raise CustomerServiceHttpError(
            "source diagnostic report and dataset SHA-256 differ"
        )
    full_rows = load_gold_dataset(dataset_path)
    rows_by_id = {str(row["id"]): row for row in full_rows}
    source_cases = [
        dict(value) for value in source.get("cases") or [] if isinstance(value, Mapping)
    ]
    source_case_ids = [str(value.get("caseId") or "") for value in source_cases]
    if (
        not source_case_ids
        or len(source_case_ids) != len(set(source_case_ids))
        or any(case_id not in rows_by_id for case_id in source_case_ids)
    ):
        raise CustomerServiceHttpError(
            "source diagnostic report has an invalid customer-service case subset"
        )
    rows = [rows_by_id[case_id] for case_id in source_case_ids]
    observations = {
        str(value["caseId"]): dict(value.get("http") or {}) for value in source_cases
    }
    rule_predictions = {
        str(value["caseId"]): dict(value.get("rulePrediction") or {})
        for value in source_cases
    }
    behavior_bundle = load_http_behavior_contracts(
        behavior_contract_file, dataset_path
    )
    rebuilt = build_http_report(
        rows,
        rule_predictions=rule_predictions,
        observations=observations,
        dataset_path=dataset_path,
        run_id=str(source.get("runId") or ""),
        preflight=dict(source.get("preflight") or {}),
        fixture_provenance=dict(
            source.get("runtimeFixture") or {"status": "NOT_USED"}
        ),
        behavior_contracts=behavior_bundle["contracts"],
        behavior_contract_provenance={
            key: value for key, value in behavior_bundle.items() if key != "contracts"
        },
    )
    rebuilt["status"] = str(diagnostic_status or "").strip()
    if not rebuilt["status"]:
        raise CustomerServiceHttpError("diagnostic status must not be empty")
    rebuilt["releaseGateEligible"] = False
    rebuilt["normalQualityDenominatorExcluded"] = True
    rebuilt["diagnostic"] = {
        "schemaVersion": HTTP_DIAGNOSTIC_SCHEMA,
        "scope": "TARGETED_HTTP_BADCASE_REPLAY",
        "sourceReportPath": _portable_path(source_report_path),
        "sourceReportSha256": sha256_file(source_report_path),
        "sourceCreatedAt": source.get("createdAt"),
        "caseSubset": source_case_ids,
        "caseCount": len(source_case_ids),
        "fullHumanDatasetCaseCount": len(full_rows),
        "providerCallsReexecuted": False,
        "releaseGateEligible": False,
        "notes": [str(value) for value in notes if str(value).strip()],
        "redaction": REDACTION_PROFILE,
    }
    rebuilt["observationProvenance"] = {
        "mode": "OFFLINE_DERIVATION_FROM_PRESERVED_TARGETED_OBSERVATIONS",
        "sourceReportPath": _portable_path(source_report_path),
        "sourceReportSha256": sha256_file(source_report_path),
        "sourceCreatedAt": source.get("createdAt"),
        "providerCallsReexecuted": False,
        "behaviorContractsRecomputed": True,
    }
    rebuilt["limitations"] = [
        *list(rebuilt.get("limitations") or []),
        "This targeted subset is diagnostic-only and cannot replace the complete 60-case HTTP run.",
        "All answer-quality labels remain pending; behavior contracts are not human review.",
    ]
    return write_customer_service_http_evidence(rebuilt, output_dir)


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
        sanitized_report = sanitize_customer_service_http_report(report)
        atomic_write_json(staging / "report.json", sanitized_report, overwrite=False)
        atomic_write_text(
            staging / "report.md", render_http_markdown(sanitized_report), overwrite=False
        )
        badcases: list[dict[str, Any]] = []
        for value in (sanitized_report.get("httpRoute") or {}).get("badcases") or []:
            badcases.append({"scope": "HTTP_ROUTE", **dict(value)})
        for case_id in (sanitized_report.get("citationContractDiagnostic") or {}).get(
            "invalidCaseIds"
        ) or []:
            badcases.append(
                {"scope": "CITATION_CONTRACT", "caseId": case_id}
            )
        for case_id in (sanitized_report.get("httpExecution") or {}).get("errorCaseIds") or []:
            badcases.append({"scope": "HTTP_EXECUTION", "caseId": case_id})
        for value in (sanitized_report.get("behaviorContracts") or {}).get("results") or []:
            if value.get("status") != "PASSED":
                badcases.append(
                    {
                        "scope": "HTTP_BEHAVIOR_CONTRACT",
                        "caseId": value.get("caseId"),
                        "contractId": value.get("contractId"),
                        "status": value.get("status"),
                        "failedChecks": list(value.get("failedChecks") or []),
                        "description": value.get("description"),
                    }
                )
        atomic_write_jsonl(staging / "badcases.jsonl", badcases, overwrite=False)
        manifest = {
            "schemaVersion": HTTP_EVIDENCE_SCHEMA,
            "kind": "customer-service-http",
            "runId": sanitized_report.get("runId"),
            "status": sanitized_report.get("status"),
            "releaseGateEligible": False,
            "answerReviewStatus": (sanitized_report.get("answerQuality") or {}).get("status"),
            "datasetSha256": (sanitized_report.get("dataset") or {}).get("sha256"),
            "sourceObservationReportSha256": (
                sanitized_report.get("observationProvenance") or {}
            ).get("sourceReportSha256"),
            "providerCallsReexecuted": (
                sanitized_report.get("observationProvenance") or {}
            ).get("providerCallsReexecuted"),
            "evidenceRedaction": dict(
                sanitized_report.get("evidenceRedaction") or {}
            ),
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
    redaction = report.get("evidenceRedaction")
    if redaction is not None:
        if (
            not isinstance(redaction, Mapping)
            or manifest.get("evidenceRedaction") != redaction
            or redaction.get("profile") != REDACTION_PROFILE
            or contains_unredacted_sensitive(report)
        ):
            raise CustomerServiceHttpError("HTTP evidence redaction boundary is invalid")
    elif manifest.get("evidenceRedaction") is not None:
        raise CustomerServiceHttpError("HTTP evidence redaction manifest is invalid")
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
