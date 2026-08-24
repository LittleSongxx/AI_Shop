"""Customer-service understanding evidence on an independently reviewed gold set.

The evaluator is deliberately separate from the Agent pass^k evidence. It
measures the production intent pre-router and keeps four high-value support
signals visible: intent Macro-F1, high-risk routing recall, slot span F1/EM,
and handoff recall. The default source file is a draft-compatible dataset;
when a separately frozen review package is supplied, the report is marked
``HUMAN_VERIFIED`` but still does not become a release gate automatically.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_json,
    atomic_write_text,
    load_jsonl,
    relative_to_repo,
    sha256_file,
)
from evaluation.core.metrics import percentile, wilson_interval

GOLD_SCHEMA = "aishop-customer-service-gold/v1"
REPORT_SCHEMA = "aishop-customer-service-evidence/v1"
PROVISIONAL_STATUS = "PROVISIONAL_NOT_HUMAN_GOLD"
HUMAN_STATUS = "HUMAN_VERIFIED"
_BOOTSTRAP_SAMPLES = 2_000
_BOOTSTRAP_SEED = 20260822
DEFAULT_DATASET = Path(__file__).resolve().parent / "datasets" / "customer_service" / "gold-v1.jsonl"
DEFAULT_REPORT = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "evaluation"
    / "customer-service"
    / "客服金标评测.md"
).resolve()
DEFAULT_JSON_REPORT = DEFAULT_REPORT.with_suffix(".json")

# These are the fields currently emitted by the production intent pre-router.
# Human reviewers may label richer business slots; those remain in the primary
# human-gold score, while the projection below is a diagnostic of extractor
# coverage rather than a replacement denominator.
PRODUCTION_CANONICAL_SLOT_FIELDS = frozenset(
    {"orderId", "orderItemId", "productId", "productName", "amount"}
)

# Human reviewers naturally write money with a currency symbol/suffix while
# the production extractor commonly emits a bare numeric value. Keep raw span
# metrics primary and expose normalization only as a diagnostic.
MONEY_SLOT_FIELDS = frozenset(
    {"amount", "budget", "price", "minPrice", "maxPrice", "estimatedPayable"}
)
_MONEY_MARKERS = (
    ("人民币", "CNY"),
    ("RMB", "CNY"),
    ("CNY", "CNY"),
    ("¥", "CNY"),
    ("￥", "CNY"),
    ("元", "CNY"),
    ("块钱", "CNY"),
    ("块", "CNY"),
    ("美元", "USD"),
    ("USD", "USD"),
    ("$", "USD"),
    ("欧元", "EUR"),
    ("EUR", "EUR"),
    ("€", "EUR"),
)

# This is the sealed diagnostic snapshot captured before the first resolver
# patch.  It is intentionally retained in the new report so a reader can see
# which failures were fixed instead of seeing only the post-fix point estimate.
HISTORICAL_BASELINE = {
    "label": "gold-v1-pre-optimization",
    "datasetSha256": "826114a806c879fe047b382d2e3a0519cf1428f19bb10efe70c137c8af73f1d3",
    "caseCount": 32,
    "status": PROVISIONAL_STATUS,
    "metrics": {
        "intentMacroF1": {"value": 0.849524, "numerator": 16.990477, "denominator": 20},
        "highRiskIntentRecall": {"value": 0.333333, "numerator": 1, "denominator": 3},
        "slotEntitySpanF1": {"value": 0.964286, "numerator": 243, "denominator": 261},
        "slotExactMatch": {"value": 0.857143, "numerator": 18, "denominator": 21},
        "handoffRecall": {"value": 0.8, "numerator": 4, "denominator": 5},
        "criticalHandoffMissRate": {"value": 0.333333, "numerator": 1, "denominator": 3},
    },
    "badcaseIds": [
        "cs-gold-v1-001",
        "cs-gold-v1-002",
        "cs-gold-v1-003",
        "cs-gold-v1-011",
        "cs-gold-v1-022",
        "cs-gold-v1-026",
        "cs-gold-v1-031",
        "cs-gold-v1-032",
    ],
    "note": "Recorded before resolver changes; provisional diagnostic only, not an A/B or human-verified result.",
}


class CustomerServiceGoldError(ValueError):
    """Raised when a gold set or prediction cannot be evaluated fail-closed."""


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _public(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.casefold())


def _path_provenance(path: Path) -> str:
    """Keep CLI reports usable for temporary holdouts outside the repository."""

    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def load_gold_dataset(path: Path) -> list[dict[str, Any]]:
    """Load and validate the independent customer-service dataset."""

    rows = load_jsonl(path)
    if not rows:
        raise CustomerServiceGoldError(f"customer-service gold set is empty: {path}")
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        label = f"{path}:{index}"
        if row.get("schemaVersion") != GOLD_SCHEMA:
            raise CustomerServiceGoldError(f"{label}: schemaVersion must be {GOLD_SCHEMA}")
        case_id = str(row.get("id") or "")
        if not case_id or case_id in seen:
            raise CustomerServiceGoldError(f"{label}: id is empty or duplicated")
        seen.add(case_id)
        message = ((row.get("input") or {}).get("message"))
        expected = row.get("expected")
        annotation = row.get("annotation")
        if not isinstance(message, str) or not message.strip():
            raise CustomerServiceGoldError(f"{label}: input.message is required")
        if not isinstance(expected, dict) or not str(expected.get("intent") or ""):
            raise CustomerServiceGoldError(f"{label}: expected.intent is required")
        if expected.get("riskLevel") not in {"LOW", "MEDIUM", "HIGH"}:
            raise CustomerServiceGoldError(f"{label}: expected.riskLevel is invalid")
        if not isinstance(expected.get("shouldHandoff"), bool):
            raise CustomerServiceGoldError(f"{label}: expected.shouldHandoff must be boolean")
        handoff_severity = expected.get("handoffSeverity")
        if expected["shouldHandoff"] and handoff_severity not in {"NORMAL", "CRITICAL"}:
            raise CustomerServiceGoldError(
                f"{label}: expected.handoffSeverity is required for handoff cases"
            )
        if not expected["shouldHandoff"] and handoff_severity is not None:
            raise CustomerServiceGoldError(
                f"{label}: expected.handoffSeverity must be empty without handoff"
            )
        slots = expected.get("slots")
        if not isinstance(slots, dict) or any(
            not str(key).strip() or value in (None, "") for key, value in slots.items()
        ):
            raise CustomerServiceGoldError(f"{label}: expected.slots must be a non-null map")
        if not isinstance(annotation, dict) or annotation.get("status") not in {
            "DRAFT_NEEDS_HUMAN_REVIEW",
            HUMAN_STATUS,
        }:
            raise CustomerServiceGoldError(
                f"{label}: annotation.status must be DRAFT_NEEDS_HUMAN_REVIEW or {HUMAN_STATUS}"
            )
        if annotation.get("status") == HUMAN_STATUS:
            reviewers = annotation.get("reviewers")
            if (
                not isinstance(reviewers, list)
                or len(reviewers) != 2
                or any(not isinstance(item, str) or not item.strip() for item in reviewers)
                or len({item.strip() for item in reviewers}) != 2
            ):
                raise CustomerServiceGoldError(
                    f"{label}: HUMAN_VERIFIED requires two distinct reviewers"
                )
            if not isinstance(annotation.get("adjudicator"), str) or not annotation[
                "adjudicator"
            ].strip():
                raise CustomerServiceGoldError(
                    f"{label}: HUMAN_VERIFIED requires an adjudicator"
                )
            review_evidence = annotation.get("reviewEvidence")
            if not isinstance(review_evidence, dict) or not _is_sha256(
                review_evidence.get("sourceDatasetSha256")
            ):
                raise CustomerServiceGoldError(
                    f"{label}: HUMAN_VERIFIED requires a SHA-256 reviewEvidence.sourceDatasetSha256"
                )
            for hash_field in ("reviewASha256", "reviewBSha256"):
                if not _is_sha256(review_evidence.get(hash_field)):
                    raise CustomerServiceGoldError(
                        f"{label}: HUMAN_VERIFIED requires SHA-256 {hash_field}"
                    )
        slice_tags = row.get("sliceTags", [])
        if not isinstance(slice_tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in slice_tags
        ):
            raise CustomerServiceGoldError(f"{label}: sliceTags must be a list of non-empty strings")
        if row.get("difficulty") is not None and row.get("difficulty") not in {
            "easy",
            "medium",
            "hard",
        }:
            raise CustomerServiceGoldError(f"{label}: difficulty must be easy, medium, or hard")
    return rows


def decision_to_prediction(decision: Any) -> dict[str, Any]:
    """Project a production ``IntentDecision`` without inventing slots."""

    return {
        "intent": _public(decision.intent),
        "confidence": float(decision.confidence),
        "riskLevel": _public(decision.risk_level),
        "nextAction": _public(decision.next_action),
        "shouldHandoff": _public(decision.next_action) == "HANDOFF",
        "handoffReason": decision.handoff_reason,
        "entities": {
            str(key): str(value)
            for key, value in (decision.entities or {}).items()
            if value not in (None, "")
        },
        "requestMode": _public(decision.request_mode),
        "source": str(decision.source or ""),
    }


async def predict_rule_baseline(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Run the deterministic production pre-router, with no LLM or tool call."""

    # Importing production modules is intentionally lazy: pure metric tests do
    # not need MySQL, Redis, or a configured Provider.
    from app.domain.intent.classifier import resolve_intent

    predictions: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row["id"])
        message = str((row.get("input") or {}).get("message") or "")
        try:
            decision = await resolve_intent(
                f"customer-service-gold-{case_id}",
                message,
                allow_llm=False,
                record_metrics=False,
            )
            predictions[case_id] = decision_to_prediction(decision)
        except Exception as exc:  # evidence must expose failures, never hide them
            predictions[case_id] = {
                "intent": "__ERROR__",
                "riskLevel": "UNKNOWN",
                "nextAction": "ERROR",
                "shouldHandoff": False,
                "entities": {},
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
    return predictions


def _f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def _bootstrap_interval(
    values: Sequence[float],
    statistic,
    *,
    seed: int,
    strata: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    if not values:
        return None
    rng = random.Random(seed)
    rows = [float(value) for value in values]
    if strata is not None:
        if len(strata) != len(rows):
            raise CustomerServiceGoldError("bootstrap strata and values must have equal length")
        grouped: dict[str, list[float]] = defaultdict(list)
        for stratum, value in zip(strata, rows):
            grouped[str(stratum)].append(value)
        estimates = []
        for _ in range(_BOOTSTRAP_SAMPLES):
            sample = [
                group[rng.randrange(len(group))]
                for name in sorted(grouped)
                for group in (grouped[name],)
                for _item in group
            ]
            estimates.append(statistic(sample))
        method = "stratified-percentile-bootstrap"
    else:
        estimates = [
            statistic([rows[rng.randrange(len(rows))] for _ in rows])
            for _ in range(_BOOTSTRAP_SAMPLES)
        ]
        method = "percentile-bootstrap"
    return {
        "lower": round(percentile(estimates, 0.025), 6),
        "upper": round(percentile(estimates, 0.975), 6),
        "method": method,
        "confidenceLevel": 0.95,
        **(
            {"stratumCount": len(set(str(stratum) for stratum in strata))}
            if strata is not None
            else {}
        ),
    }


def _bootstrap_micro_f1_interval(
    counts: Sequence[tuple[int, int, int]],
    *,
    seed: int,
    strata: Sequence[str],
) -> dict[str, Any] | None:
    """Bootstrap complete cases while preserving the reported micro-F1 statistic."""

    if not counts:
        return None
    if len(strata) != len(counts):
        raise CustomerServiceGoldError(
            "bootstrap strata and slot counts must have equal length"
        )
    grouped: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for stratum, value in zip(strata, counts):
        grouped[str(stratum)].append(value)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(_BOOTSTRAP_SAMPLES):
        sample = [
            group[rng.randrange(len(group))]
            for name in sorted(grouped)
            for group in (grouped[name],)
            for _item in group
        ]
        tp = sum(value[0] for value in sample)
        fp = sum(value[1] for value in sample)
        fn = sum(value[2] for value in sample)
        denominator = 2 * tp + fp + fn
        estimates.append(2 * tp / denominator if denominator else 1.0)
    return {
        "lower": round(percentile(estimates, 0.025), 6),
        "upper": round(percentile(estimates, 0.975), 6),
        "method": "stratified-case-bootstrap-micro-F1",
        "confidenceLevel": 0.95,
        "stratumCount": len(grouped),
    }


def _bootstrap_stratum(case: Mapping[str, Any]) -> str:
    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
    return "|".join(
        (
            str(expected.get("intent") or "UNKNOWN"),
            str(expected.get("riskLevel") or "UNKNOWN"),
            "HANDOFF" if bool(expected.get("shouldHandoff")) else "NO_HANDOFF",
        )
    )


def _stratified_case_sample(
    cases: Sequence[Mapping[str, Any]], rng: random.Random
) -> list[Mapping[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[_bootstrap_stratum(case)].append(case)
    return [
        group[rng.randrange(len(group))]
        for name in sorted(grouped)
        for group in (grouped[name],)
        for _item in group
    ]


def _wilson(successes: int, total: int) -> dict[str, Any] | None:
    if total <= 0:
        return None
    lower, upper = wilson_interval(successes, total)
    return {
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "method": "wilson",
        "confidenceLevel": 0.95,
    }


def _metric(
    name: str,
    value: float | None,
    *,
    numerator: int | float,
    denominator: int,
    interval: dict[str, Any] | None,
    badcase_ids: Sequence[str],
    definition: str,
    notes: Sequence[str] = (),
    evidence_status: str = PROVISIONAL_STATUS,
    role: str = "PRIMARY_QUALITY",
    release_gate_eligible: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": "MEASURED" if value is not None else "UNAVAILABLE",
        "value": None if value is None else round(float(value), 6),
        "numerator": numerator,
        "denominator": denominator,
        "confidenceInterval95": interval,
        "badcaseCount": len(list(dict.fromkeys(badcase_ids))),
        "badcaseIds": list(dict.fromkeys(str(case_id) for case_id in badcase_ids)),
        "definition": definition,
        "role": role,
        "releaseGateEligible": release_gate_eligible,
        "notes": [*notes, evidence_status],
    }


def _span_tokens(value: Any) -> list[str]:
    # Character spans are deterministic for Chinese messages and avoid a
    # hidden tokenizer dependency. Whitespace is not a semantic span token.
    return [char for char in _norm(value) if not char.isspace()]


def _normalize_money(value: Any) -> str | None:
    """Return a currency-qualified numeric value for diagnostic comparisons."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace(",", "").replace(" ", "").replace("\u3000", "")
    currency: str | None = None
    for marker, code in sorted(_MONEY_MARKERS, key=lambda item: len(item[0]), reverse=True):
        if marker.casefold() in text.casefold():
            currency = code
            text = re.sub(re.escape(marker), "", text, flags=re.IGNORECASE)
    # Do not guess through ranges, qualifiers or prose. Those remain raw
    # strings and still appear in the primary span metric.
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    # Bare numeric values are treated as CNY only inside a money-labelled
    # slot; this mirrors the Chinese customer-service dataset convention.
    return f"{currency or 'CNY'}:{normalized or '0'}"


def _normalize_slot_value(key: Any, value: Any) -> str:
    field = str(key)
    if field in MONEY_SLOT_FIELDS:
        normalized_money = _normalize_money(value)
        if normalized_money is not None:
            return normalized_money
    return _norm(value)


def _slot_case_counts_for_maps(
    expected_slots: Mapping[str, Any],
    predicted_slots: Mapping[str, Any],
    *,
    value_normalizer=None,
) -> tuple[int, int, int, float]:
    if not isinstance(expected_slots, Mapping) or not isinstance(predicted_slots, Mapping):
        return 0, 0, 0, 0.0
    tp = fp = fn = 0
    normalizer = value_normalizer or (lambda _key, value: _norm(value))
    for key in set(str(item) for item in expected_slots) | set(str(item) for item in predicted_slots):
        gold = _span_tokens(normalizer(key, expected_slots.get(key, "")))
        pred = _span_tokens(normalizer(key, predicted_slots.get(key, "")))
        overlap = Counter(gold) & Counter(pred)
        matched = sum(overlap.values())
        tp += matched
        fp += len(pred) - matched
        fn += len(gold) - matched
    precision = tp / (tp + fp) if tp + fp else 1.0 if not expected_slots else 0.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return tp, fp, fn, _f1(precision, recall)


def _slot_case_counts(expected: Mapping[str, Any], predicted: Mapping[str, Any]) -> tuple[int, int, int, float]:
    return _slot_case_counts_for_maps(
        expected.get("slots") or {},
        predicted.get("entities") or {},
    )


def _normalized_slot_case_counts(
    expected: Mapping[str, Any], predicted: Mapping[str, Any]
) -> tuple[int, int, int, float]:
    return _slot_case_counts_for_maps(
        expected.get("slots") or {},
        predicted.get("entities") or {},
        value_normalizer=_normalize_slot_value,
    )


def _project_canonical_slots(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): raw
        for key, raw in value.items()
        if str(key) in PRODUCTION_CANONICAL_SLOT_FIELDS and raw not in (None, "")
    }


def _slot_maps_equal(expected_slots: Mapping[str, Any], predicted_slots: Mapping[str, Any]) -> bool:
    return (
        {str(key): _norm(value) for key, value in expected_slots.items()}
        == {str(key): _norm(value) for key, value in predicted_slots.items()}
    )


def _normalized_slot_maps_equal(
    expected_slots: Mapping[str, Any], predicted_slots: Mapping[str, Any]
) -> bool:
    return {
        str(key): _normalize_slot_value(key, value)
        for key, value in expected_slots.items()
    } == {
        str(key): _normalize_slot_value(key, value)
        for key, value in predicted_slots.items()
    }


def _slot_badcase_root_cause(
    expected_slots: Mapping[str, Any],
    predicted_slots: Mapping[str, Any],
) -> str:
    """Separate schema coverage and formatting gaps from genuine canonical misses."""

    expected_canonical = _project_canonical_slots(expected_slots)
    predicted_canonical = _project_canonical_slots(predicted_slots)
    extension_keys = set(expected_slots) - PRODUCTION_CANONICAL_SLOT_FIELDS
    canonical_tp, canonical_fp, canonical_fn, _ = _slot_case_counts_for_maps(
        expected_canonical,
        predicted_canonical,
    )
    if extension_keys and canonical_fn == 0 and canonical_fp == 0:
        return "GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED"
    if expected_canonical and _slot_maps_equal(expected_canonical, predicted_canonical) and extension_keys:
        return "GOLD_SCHEMA_EXTENSION_NOT_PRODUCTION_MAPPED"
    if (
        expected_canonical
        and set(expected_canonical) == set(predicted_canonical)
        and canonical_tp
        and not _slot_maps_equal(expected_canonical, predicted_canonical)
    ):
        # A value overlap with a key/value mismatch is most often a currency or
        # whitespace normalization issue; preserve it as a separate replay
        # category instead of calling it an extraction miss.
        expected_values = {_norm(value) for value in expected_canonical.values()}
        predicted_values = {_norm(value) for value in predicted_canonical.values()}
        if expected_values & predicted_values:
            return "SLOT_NORMALIZATION_GAP"
        if _normalized_slot_maps_equal(expected_canonical, predicted_canonical):
            return "SLOT_NORMALIZATION_GAP"
    return "SLOT_EXTRACTION_GAP"


def _empty_prediction() -> dict[str, Any]:
    return {
        "intent": "__MISSING__",
        "riskLevel": "UNKNOWN",
        "nextAction": "ERROR",
        "shouldHandoff": False,
        "entities": {},
        "error": "prediction_missing",
    }


def evaluate_predictions(
    rows: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the four support metrics and metric-specific badcases."""

    if not rows:
        raise CustomerServiceGoldError("cannot evaluate an empty customer-service gold set")
    annotation_statuses = Counter(
        str((row.get("annotation") or {}).get("status") or "UNKNOWN") for row in rows
    )
    evidence_status = (
        HUMAN_STATUS
        if annotation_statuses and all(status == HUMAN_STATUS for status in annotation_statuses)
        else PROVISIONAL_STATUS
    )
    cases: list[dict[str, Any]] = []
    intent_labels = sorted(
        {str((row.get("expected") or {}).get("intent") or "") for row in rows}
        | {str((predictions.get(str(row["id"])) or {}).get("intent") or "") for row in rows}
    )
    confusion: dict[str, dict[str, int]] = {
        label: {other: 0 for other in intent_labels} for label in intent_labels
    }
    per_intent: dict[str, dict[str, Any]] = {}
    intent_badcases: list[str] = []
    risk_badcases: list[str] = []
    slot_f1_badcases: list[str] = []
    slot_em_badcases: list[str] = []
    slot_case_counts: list[tuple[int, int, int]] = []
    slot_case_strata: list[str] = []
    slot_tp = slot_fp = slot_fn = 0
    slot_em_numerator = slot_em_denominator = 0
    normalized_slot_f1_badcases: list[str] = []
    normalized_slot_em_badcases: list[str] = []
    normalized_slot_case_counts: list[tuple[int, int, int]] = []
    normalized_slot_case_strata: list[str] = []
    normalized_slot_tp = normalized_slot_fp = normalized_slot_fn = 0
    normalized_slot_em_numerator = normalized_slot_em_denominator = 0
    canonical_slot_f1_badcases: list[str] = []
    canonical_slot_em_badcases: list[str] = []
    canonical_slot_case_counts: list[tuple[int, int, int]] = []
    canonical_slot_case_strata: list[str] = []
    canonical_slot_tp = canonical_slot_fp = canonical_slot_fn = 0
    canonical_slot_em_numerator = canonical_slot_em_denominator = 0
    extension_only_slot_case_count = 0
    extension_field_counts: Counter[str] = Counter()
    handoff_badcases: list[str] = []
    critical_handoff_badcases: list[str] = []
    high_risk_total = high_risk_hits = 0
    handoff_total = handoff_hits = 0
    critical_handoff_total = critical_handoff_misses = 0

    for row in rows:
        case_id = str(row["id"])
        expected = row["expected"]
        predicted = dict(predictions.get(case_id) or _empty_prediction())
        expected_intent = str(expected.get("intent") or "")
        predicted_intent = str(predicted.get("intent") or "__MISSING__")
        expected_risk = str(expected.get("riskLevel") or "")
        predicted_risk = str(predicted.get("riskLevel") or "UNKNOWN")
        expected_handoff = bool(expected.get("shouldHandoff"))
        predicted_handoff = bool(predicted.get("shouldHandoff"))
        if expected_intent not in confusion:
            confusion[expected_intent] = {label: 0 for label in intent_labels}
        if predicted_intent not in confusion[expected_intent]:
            for values in confusion.values():
                values[predicted_intent] = 0
            confusion[expected_intent][predicted_intent] = 0
        confusion[expected_intent][predicted_intent] += 1
        intent_match = expected_intent == predicted_intent
        if not intent_match:
            intent_badcases.append(case_id)
        if expected_risk == "HIGH":
            high_risk_total += 1
            if predicted_risk == "HIGH":
                high_risk_hits += 1
            else:
                risk_badcases.append(case_id)
        if expected_handoff:
            handoff_total += 1
            if predicted_handoff:
                handoff_hits += 1
            else:
                handoff_badcases.append(case_id)
        if expected.get("handoffSeverity") == "CRITICAL":
            critical_handoff_total += 1
            if not predicted_handoff:
                critical_handoff_misses += 1
                critical_handoff_badcases.append(case_id)

        tp, fp, fn, _case_slot_f1 = _slot_case_counts(expected, predicted)
        slot_tp += tp
        slot_fp += fp
        slot_fn += fn
        slot_case_counts.append((tp, fp, fn))
        if fp or fn:
            slot_f1_badcases.append(case_id)
        slot_case_strata.append(
            f"{expected_intent}|{expected_risk}|"
            f"{'HANDOFF' if expected_handoff else 'NO_HANDOFF'}|"
            f"{'HAS_SLOT' if expected.get('slots') else 'NO_SLOT'}"
        )
        expected_slots = expected.get("slots") or {}
        predicted_slots = predicted.get("entities") or {}
        if expected_slots:
            slot_em_denominator += 1
            if isinstance(predicted_slots, Mapping) and _slot_maps_equal(
                expected_slots, predicted_slots
            ):
                slot_em_numerator += 1
            else:
                slot_em_badcases.append(case_id)

        normalized_tp, normalized_fp, normalized_fn, _normalized_case_f1 = (
            _normalized_slot_case_counts(expected, predicted)
        )
        normalized_slot_tp += normalized_tp
        normalized_slot_fp += normalized_fp
        normalized_slot_fn += normalized_fn
        normalized_slot_case_counts.append(
            (normalized_tp, normalized_fp, normalized_fn)
        )
        normalized_slot_case_strata.append(
            f"{expected_intent}|{expected_risk}|"
            f"{'HANDOFF' if expected_handoff else 'NO_HANDOFF'}|"
            f"{'HAS_SLOT' if expected_slots else 'NO_SLOT'}"
        )
        if normalized_fp or normalized_fn:
            normalized_slot_f1_badcases.append(case_id)
        if expected_slots:
            normalized_slot_em_denominator += 1
            if isinstance(predicted_slots, Mapping) and _normalized_slot_maps_equal(
                expected_slots, predicted_slots
            ):
                normalized_slot_em_numerator += 1
            else:
                normalized_slot_em_badcases.append(case_id)
        expected_canonical_slots = _project_canonical_slots(expected_slots)
        predicted_canonical_slots = _project_canonical_slots(predicted_slots)
        extension_keys = set(expected_slots) - PRODUCTION_CANONICAL_SLOT_FIELDS
        if extension_keys:
            extension_field_counts.update(extension_keys)
        if expected_slots and not expected_canonical_slots:
            extension_only_slot_case_count += 1
        if expected_canonical_slots:
            canonical_tp_case, canonical_fp_case, canonical_fn_case, _canonical_case_f1 = (
                _slot_case_counts_for_maps(
                    expected_canonical_slots,
                    predicted_canonical_slots,
                )
            )
            canonical_slot_tp += canonical_tp_case
            canonical_slot_fp += canonical_fp_case
            canonical_slot_fn += canonical_fn_case
            canonical_slot_case_counts.append(
                (canonical_tp_case, canonical_fp_case, canonical_fn_case)
            )
            if canonical_fp_case or canonical_fn_case:
                canonical_slot_f1_badcases.append(case_id)
            canonical_slot_case_strata.append(
                f"{expected_intent}|{expected_risk}|"
                f"{'HANDOFF' if expected_handoff else 'NO_HANDOFF'}"
            )
            canonical_slot_em_denominator += 1
            if _slot_maps_equal(expected_canonical_slots, predicted_canonical_slots):
                canonical_slot_em_numerator += 1
            else:
                canonical_slot_em_badcases.append(case_id)
        cases.append(
            {
                "id": case_id,
                "message": (row.get("input") or {}).get("message"),
                "sliceTags": list(row.get("sliceTags") or []),
                "difficulty": row.get("difficulty"),
                "expected": expected,
                "predicted": predicted,
                "matches": {
                    "intent": intent_match,
                    "riskLevel": expected_risk == predicted_risk,
                    "handoff": expected_handoff == predicted_handoff,
                    "slotExactMatch": bool(expected_slots)
                    and case_id not in slot_em_badcases,
                },
            }
        )

    # Derive per-intent statistics after the complete confusion matrix exists.
    for label in sorted(confusion):
        tp = confusion[label].get(label, 0)
        support = sum(confusion[label].values())
        predicted_count = sum(values.get(label, 0) for values in confusion.values())
        precision = tp / predicted_count if predicted_count else 0.0
        recall = tp / support if support else 0.0
        f1 = _f1(precision, recall)
        per_intent[label] = {
            "support": support,
            "predicted": predicted_count,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "badcaseIds": [
                case["id"] for case in cases if case["expected"]["intent"] == label and not case["matches"]["intent"]
            ],
        }
    macro_values = [float(values["f1"]) for values in per_intent.values()]
    macro_f1 = sum(macro_values) / len(macro_values) if macro_values else None
    # Bootstrap complete requests, then recompute macro-F1 for every sample;
    # this keeps the interval aligned with the metric's case-level denominator.
    rng = random.Random(_BOOTSTRAP_SEED)
    macro_samples: list[float] = []
    for _ in range(_BOOTSTRAP_SAMPLES):
        sample = _stratified_case_sample(cases, rng)
        labels = sorted({case["expected"]["intent"] for case in cases} | {case["predicted"].get("intent", "__MISSING__") for case in sample})
        values: list[float] = []
        for label in labels:
            label_tp = sum(case["expected"]["intent"] == label == case["predicted"].get("intent") for case in sample)
            label_support = sum(case["expected"]["intent"] == label for case in sample)
            label_predicted = sum(case["predicted"].get("intent") == label for case in sample)
            values.append(_f1(label_tp / label_predicted if label_predicted else 0.0, label_tp / label_support if label_support else 0.0))
        macro_samples.append(sum(values) / len(values) if values else 0.0)
    macro_interval = {
        "lower": round(percentile(macro_samples, 0.025), 6),
        "upper": round(percentile(macro_samples, 0.975), 6),
        "method": "stratified-case-bootstrap-macro-F1",
        "confidenceLevel": 0.95,
        "stratumCount": len({_bootstrap_stratum(case) for case in cases}),
    }

    slot_precision = slot_tp / (slot_tp + slot_fp) if slot_tp + slot_fp else 0.0
    slot_recall = slot_tp / (slot_tp + slot_fn) if slot_tp + slot_fn else 0.0
    slot_f1 = _f1(slot_precision, slot_recall)
    slot_interval = _bootstrap_micro_f1_interval(
        slot_case_counts,
        seed=_BOOTSTRAP_SEED ^ 0x51,
        strata=slot_case_strata,
    )
    normalized_slot_precision = (
        normalized_slot_tp / (normalized_slot_tp + normalized_slot_fp)
        if normalized_slot_tp + normalized_slot_fp
        else 0.0
    )
    normalized_slot_recall = (
        normalized_slot_tp / (normalized_slot_tp + normalized_slot_fn)
        if normalized_slot_tp + normalized_slot_fn
        else 0.0
    )
    normalized_slot_f1 = _f1(normalized_slot_precision, normalized_slot_recall)
    normalized_slot_interval = _bootstrap_micro_f1_interval(
        normalized_slot_case_counts,
        seed=_BOOTSTRAP_SEED ^ 0x5D,
        strata=normalized_slot_case_strata,
    )
    canonical_slot_precision = (
        canonical_slot_tp / (canonical_slot_tp + canonical_slot_fp)
        if canonical_slot_tp + canonical_slot_fp
        else 0.0
    )
    canonical_slot_recall = (
        canonical_slot_tp / (canonical_slot_tp + canonical_slot_fn)
        if canonical_slot_tp + canonical_slot_fn
        else 0.0
    )
    canonical_slot_f1 = _f1(canonical_slot_precision, canonical_slot_recall)
    canonical_slot_interval = _bootstrap_micro_f1_interval(
        canonical_slot_case_counts,
        seed=_BOOTSTRAP_SEED ^ 0xA7,
        strata=canonical_slot_case_strata,
    )
    metrics = {
        "intentMacroF1": _metric(
            "intentMacroF1",
            macro_f1,
            numerator=round(sum(macro_values), 6),
            denominator=len(macro_values),
            interval=macro_interval,
            badcase_ids=intent_badcases,
            definition="Gold intent labels are macro-averaged across the observed label set; prediction outside the set is counted as a miss/false positive.",
            evidence_status=evidence_status,
        ),
        "highRiskIntentRecall": _metric(
            "highRiskIntentRecall",
            high_risk_hits / high_risk_total if high_risk_total else None,
            numerator=high_risk_hits,
            denominator=high_risk_total,
            interval=_wilson(high_risk_hits, high_risk_total),
            badcase_ids=risk_badcases,
            definition="Recall of cases independently labelled riskLevel=HIGH, requiring predicted riskLevel=HIGH.",
            evidence_status=evidence_status,
        ),
        "slotEntitySpanF1": _metric(
            "slotEntitySpanF1",
            slot_f1 if slot_tp + slot_fp + slot_fn else None,
            numerator=2 * slot_tp,
            denominator=2 * slot_tp + slot_fp + slot_fn,
            interval=slot_interval,
            badcase_ids=slot_f1_badcases,
            definition="Micro character-span F1 over expected and predicted structured entity values; extra predicted fields count as false positives.",
            notes=(f"componentCounts: TP={slot_tp}, FP={slot_fp}, FN={slot_fn}",),
            evidence_status=evidence_status,
        ),
        "slotExactMatch": _metric(
            "slotExactMatch",
            slot_em_numerator / slot_em_denominator if slot_em_denominator else None,
            numerator=slot_em_numerator,
            denominator=slot_em_denominator,
            interval=_wilson(slot_em_numerator, slot_em_denominator),
            badcase_ids=slot_em_badcases,
            definition="Request-level exact equality of all expected slots; cases with no expected slots are excluded from the denominator.",
            evidence_status=evidence_status,
        ),
        "normalizedSlotEntitySpanF1": _metric(
            "normalizedSlotEntitySpanF1",
            normalized_slot_f1
            if normalized_slot_tp + normalized_slot_fp + normalized_slot_fn
            else None,
            numerator=2 * normalized_slot_tp,
            denominator=2 * normalized_slot_tp
            + normalized_slot_fp
            + normalized_slot_fn,
            interval=normalized_slot_interval,
            badcase_ids=normalized_slot_f1_badcases,
            definition="Diagnostic micro character-span F1 after numeric/currency normalization of money-labelled slots; raw span F1 remains primary.",
            notes=(
                f"componentCounts: TP={normalized_slot_tp}, FP={normalized_slot_fp}, FN={normalized_slot_fn}",
                "Money normalization is diagnostic only and does not change the raw metric or historical denominator.",
            ),
            evidence_status=evidence_status,
            role="DIAGNOSTIC_NORMALIZATION",
        ),
        "normalizedSlotExactMatch": _metric(
            "normalizedSlotExactMatch",
            normalized_slot_em_numerator / normalized_slot_em_denominator
            if normalized_slot_em_denominator
            else None,
            numerator=normalized_slot_em_numerator,
            denominator=normalized_slot_em_denominator,
            interval=_wilson(
                normalized_slot_em_numerator, normalized_slot_em_denominator
            ),
            badcase_ids=normalized_slot_em_badcases,
            definition="Diagnostic request-level slot equality after numeric/currency normalization of money-labelled slots; raw slot EM remains primary.",
            notes=(
                "Values such as 199, 199元 and ¥199.00 compare as CNY:199; incompatible currencies do not compare equal.",
                "Money normalization is diagnostic only and does not change the raw metric or historical denominator.",
            ),
            evidence_status=evidence_status,
            role="DIAGNOSTIC_NORMALIZATION",
        ),
        "handoffRecall": _metric(
            "handoffRecall",
            handoff_hits / handoff_total if handoff_total else None,
            numerator=handoff_hits,
            denominator=handoff_total,
            interval=_wilson(handoff_hits, handoff_total),
            badcase_ids=handoff_badcases,
            definition="Among gold shouldHandoff=true cases, only next_action=HANDOFF counts as immediate handoff; HANDOFF_SUGGESTED does not.",
            evidence_status=evidence_status,
        ),
        "criticalHandoffMissRate": _metric(
            "criticalHandoffMissRate",
            critical_handoff_misses / critical_handoff_total if critical_handoff_total else None,
            numerator=critical_handoff_misses,
            denominator=critical_handoff_total,
            interval=_wilson(critical_handoff_misses, critical_handoff_total),
            badcase_ids=critical_handoff_badcases,
            definition="Severe漏转人工率 among gold handoffSeverity=CRITICAL cases; lower is better.",
            evidence_status=evidence_status,
        ),
    }
    metrics["slotEntitySpanF1"]["componentCounts"] = {
        "truePositive": slot_tp,
        "falsePositive": slot_fp,
        "falseNegative": slot_fn,
    }
    metrics["normalizedSlotEntitySpanF1"]["componentCounts"] = {
        "truePositive": normalized_slot_tp,
        "falsePositive": normalized_slot_fp,
        "falseNegative": normalized_slot_fn,
    }
    canonical_slot_metrics = {
        "canonicalSlotEntitySpanF1": {
            "name": "canonicalSlotEntitySpanF1",
            "status": "MEASURED" if canonical_slot_tp + canonical_slot_fp + canonical_slot_fn else "UNAVAILABLE",
            "value": round(canonical_slot_f1, 6)
            if canonical_slot_tp + canonical_slot_fp + canonical_slot_fn
            else None,
            "numerator": 2 * canonical_slot_tp,
            "denominator": 2 * canonical_slot_tp
            + canonical_slot_fp
            + canonical_slot_fn,
            "componentCounts": {
                "truePositive": canonical_slot_tp,
                "falsePositive": canonical_slot_fp,
                "falseNegative": canonical_slot_fn,
            },
            "confidenceInterval95": canonical_slot_interval,
            "badcaseCount": len(canonical_slot_f1_badcases),
            "badcaseIds": canonical_slot_f1_badcases,
            "definition": "Diagnostic micro character-span F1 after projecting both sides to the five fields currently emitted by the production pre-router; it does not replace the full human-schema slot metric.",
            "role": "DIAGNOSTIC_SCHEMA_ALIGNMENT",
            "releaseGateEligible": False,
        },
        "canonicalSlotExactMatch": {
            "name": "canonicalSlotExactMatch",
            "status": "MEASURED" if canonical_slot_em_denominator else "UNAVAILABLE",
            "value": round(canonical_slot_em_numerator / canonical_slot_em_denominator, 6)
            if canonical_slot_em_denominator
            else None,
            "numerator": canonical_slot_em_numerator,
            "denominator": canonical_slot_em_denominator,
            "confidenceInterval95": _wilson(
                canonical_slot_em_numerator, canonical_slot_em_denominator
            ),
            "badcaseCount": len(canonical_slot_em_badcases),
            "badcaseIds": canonical_slot_em_badcases,
            "definition": "Diagnostic request-level exact match after projecting to production canonical slots; extension-only human slots are excluded from this denominator.",
            "role": "DIAGNOSTIC_SCHEMA_ALIGNMENT",
            "releaseGateEligible": False,
        },
    }
    all_badcase_ids = sorted(
        set(intent_badcases)
        | set(risk_badcases)
        | set(slot_f1_badcases)
        | set(slot_em_badcases)
        | set(normalized_slot_f1_badcases)
        | set(normalized_slot_em_badcases)
        | set(handoff_badcases)
        | set(critical_handoff_badcases)
    )
    badcase_rows = []
    for case in cases:
        if case["id"] not in all_badcase_ids:
            continue
        metric_names = [
            name
            for name, ids in (
                ("intentMacroF1", intent_badcases),
                ("highRiskIntentRecall", risk_badcases),
                ("slotEntitySpanF1", slot_f1_badcases),
                ("slotExactMatch", slot_em_badcases),
                ("normalizedSlotEntitySpanF1", normalized_slot_f1_badcases),
                ("normalizedSlotExactMatch", normalized_slot_em_badcases),
                ("handoffRecall", handoff_badcases),
                ("criticalHandoffMissRate", critical_handoff_badcases),
            )
            if case["id"] in ids
        ]
        badcase_rows.append(
            {
                "caseId": case["id"],
                "metrics": metric_names,
                "message": case["message"],
                "sliceTags": case.get("sliceTags", []),
                "difficulty": case.get("difficulty"),
                "expected": case["expected"],
                "predicted": case["predicted"],
                "rootCause": (
                    _slot_badcase_root_cause(
                        case["expected"].get("slots") or {},
                        case["predicted"].get("entities") or {},
                    )
                    if any(name.startswith("slot") for name in metric_names)
                    else "HANDOFF_OR_RISK_POLICY_GAP"
                    if any("handoff" in name.lower() or "risk" in name.lower() for name in metric_names)
                    else "INTENT_ROUTING_OR_TAXONOMY_GAP"
                ),
            }
        )
    target_specs = {
        "intentMacroF1": (0.90, "higher"),
        "highRiskIntentRecall": (1.0, "higher"),
        "slotEntitySpanF1": (0.95, "higher"),
        "slotExactMatch": (0.90, "higher"),
        "handoffRecall": (1.0, "higher"),
        "criticalHandoffMissRate": (0.0, "lower"),
    }
    provisional_targets = {}
    for name, (target, direction) in target_specs.items():
        value = metrics[name].get("value")
        passed = (
            value is not None
            and (value >= target if direction == "higher" else value <= target)
        )
        provisional_targets[name] = {
            "target": target,
            "direction": direction,
            "pointEstimatePasses": passed,
            "interpretation": "项目秋招参考门槛，不是统一行业标准；需人工复核和更大样本后才可作为 release gate。",
        }
    return {
        "schemaVersion": REPORT_SCHEMA,
        "status": evidence_status,
        "releaseGateEligible": False,
        "humanReviewPlan": {
            "status": "COMPLETE" if evidence_status == HUMAN_STATUS else "PENDING_INDEPENDENT_REVIEW",
            "requiredAnnotators": 2,
            "blindedFirstPass": True,
            "adjudicationRequired": True,
            "freezeAfterAdjudication": True,
            "adjudicationComplete": evidence_status == HUMAN_STATUS,
            "fields": [
                "intent",
                "riskLevel",
                "shouldHandoff",
                "handoffSeverity",
                "slots",
            ],
            "metricsAfterFreeze": [
                "intentMacroF1",
                "highRiskIntentRecall",
                "slotEntitySpanF1",
                "slotExactMatch",
                "handoffRecall",
            ],
            "note": (
                "Labels were independently reviewed and adjudicated; release publication still requires an explicit project gate decision."
                if evidence_status == HUMAN_STATUS
                else "Current labels are assistant drafts; do not claim human accuracy or release eligibility before independent review and adjudication."
            ),
        },
        "dataset": {
            "path": str(provenance.get("datasetPath") if provenance else ""),
            "sha256": str(provenance.get("datasetSha256") if provenance else ""),
            "caseCount": len(rows),
            "annotationStatuses": dict(annotation_statuses),
            "sliceCounts": {
                tag: sum(tag in (row.get("sliceTags") or []) for row in rows)
                for tag in sorted(
                    {
                        tag
                        for row in rows
                        for tag in (row.get("sliceTags") or [])
                    }
                )
            },
        },
        "provenance": dict(provenance or {}),
        "bootstrapPolicy": {
            "method": "stratified-percentile-bootstrap",
            "samples": _BOOTSTRAP_SAMPLES,
            "seed": _BOOTSTRAP_SEED,
            "primaryStrata": ["intent", "riskLevel", "shouldHandoff"],
            "binaryMetrics": "Wilson intervals retained for directly binomial metrics",
        },
        "historicalBaseline": HISTORICAL_BASELINE,
        "metrics": metrics,
        "canonicalSlotDiagnostics": {
            "productionFields": sorted(PRODUCTION_CANONICAL_SLOT_FIELDS),
            "extensionOnlyCaseCount": extension_only_slot_case_count,
            "extensionFieldCounts": dict(sorted(extension_field_counts.items())),
            "metrics": canonical_slot_metrics,
            "note": "This projection is a schema-alignment diagnostic. Full human-schema slot metrics remain the primary reported quality signal.",
        },
        "provisionalTargets": provisional_targets,
        "perIntent": per_intent,
        "confusionMatrix": confusion,
        "cases": cases,
        "badcases": badcase_rows,
        "limitations": [
            "Labels are not independently human reviewed; this is a provisional routing baseline, not human accuracy."
            if evidence_status != HUMAN_STATUS
            else "Human labels are frozen for this dataset version; the result still measures offline understanding, not customer satisfaction or production success.",
            "mode=rule evaluates the deterministic production pre-router with allow_llm=false, not the full HTTP Agent conversation.",
            "The core metrics do not establish customer satisfaction, FCR, CSAT, production volume, or business conversion.",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    dataset = report.get("dataset") or {}
    human_verified = report.get("status") == HUMAN_STATUS
    review_intro = (
        "当前数据集已完成双人独立复核并冻结；以下命令仅用于复核流程复现和新版本生成。"
        if human_verified
        else "当前仓库只冻结 draft 数据，人工复核工具已就绪但尚未产生人工标签。"
    )
    lines = [
        "# AI 客服金标评测（v1）",
        "",
        (
            f"> 状态：`{report.get('status')}`；`releaseGateEligible=false`。标签已完成独立人工复核，但结果仍是离线理解指标。"
            if human_verified
            else f"> 状态：`{report.get('status')}`；`releaseGateEligible=false`。标签未完成独立人工复核，不能称为人工准确率。"
        ),
        "> 本报告只测客服理解/转接的核心质量证据；Agent `pass^k`、工具契约和终态门禁不计入下表。",
        "",
        f"数据集：`{dataset.get('caseCount', 0)}` 条；SHA-256：`{dataset.get('sha256') or '未记录'}`；模式：`{(report.get('provenance') or {}).get('mode', 'unknown')}`。",
        "",
        "## 人工金标闭环",
        "",
        review_intro,
        "",
        "```bash",
        "conda activate shop",
        "cd AI_Shop-backend/AI_Shop-agent",
        "python -m evaluation.cli customer-service-review export --annotator reviewer-a --output /tmp/reviewer-a.open.jsonl",
        "python -m evaluation.cli customer-service-review export --annotator reviewer-b --output /tmp/reviewer-b.open.jsonl",
        "# 两位标注者独立填写 labels 后分别封存",
        "python -m evaluation.cli customer-service-review seal --review /tmp/reviewer-a.open.jsonl --output /tmp/reviewer-a.sealed.jsonl",
        "python -m evaluation.cli customer-service-review seal --review /tmp/reviewer-b.open.jsonl --output /tmp/reviewer-b.sealed.jsonl",
        "python -m evaluation.cli customer-service-review merge --review-a /tmp/reviewer-a.sealed.jsonl --review-b /tmp/reviewer-b.sealed.jsonl --adjudication /tmp/adjudication.final.jsonl --output-dataset /tmp/customer-service-human-v1.jsonl --evidence /tmp/customer-service-human-v1.evidence.json",
        "```",
        "",
        "流程为 `OPEN -> SEALED -> HUMAN_VERIFIED`；sheet 带源数据/内容 SHA-256，禁止写入 `expected`、模型预测或隐藏字段，冲突必须逐 case 仲裁。当前结果虽已完成人工复核，仍是离线质量证据，且不会自动进入 release gate。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 值 | 分子/分母 | 95% CI | badcase |",
        "|---|---:|---:|---|---:|",
    ]
    for name in (
        "intentMacroF1",
        "highRiskIntentRecall",
        "slotEntitySpanF1",
        "slotExactMatch",
        "handoffRecall",
        "criticalHandoffMissRate",
    ):
        metric = metrics.get(name) or {}
        interval = metric.get("confidenceInterval95") or {}
        ci = (
            f"[{interval.get('lower')}, {interval.get('upper')}]"
            if interval
            else "不可得"
        )
        lines.append(
            f"| `{name}` | {metric.get('value') if metric.get('value') is not None else '不可得'} | "
            f"{metric.get('numerator')}/{metric.get('denominator')} | {ci} | "
            f"{metric.get('badcaseCount', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 证据版本边界",
            "",
            "- 当前 60 条规则预路由结果是同一 HUMAN_VERIFIED 数据集上的 paired replay；槽位修复证据包 `customer-service-slot-replay-v1-20260823` 只证明无回归/修复，不是新 holdout 泛化结果。",
            "- HTTP 最终答案另有独立 `HUMAN_REVIEWED_ADJUDICATED` 证据包；固定旧回放的引用语义支持为 `6/30 eligible`（20.0%），不能从本报告的意图/槽位结果推导生成答案质量。",
            "- HTTP 新输出必须重新双人盲审；旧答案 labels 绑定 source run 和答案 SHA-256，不能迁移到新代码结果。",
        ]
    )
    baseline = report.get("historicalBaseline") or {}
    lines.extend(
        [
            "",
            "## 生产槽位对齐诊断",
            "",
            "以下投影只覆盖当前生产抽取器已实现的 `orderId/orderItemId/productId/productName/amount`，不替换上面的完整人工 schema 主指标。",
        ]
    )
    canonical = (report.get("canonicalSlotDiagnostics") or {}).get("metrics") or {}
    for name in ("canonicalSlotEntitySpanF1", "canonicalSlotExactMatch"):
        metric = canonical.get(name) or {}
        interval = metric.get("confidenceInterval95") or {}
        ci = (
            f"[{interval.get('lower')}, {interval.get('upper')}]"
            if interval
            else "不可得"
        )
        lines.append(
            f"- `{name}`：{metric.get('value') if metric.get('value') is not None else '不可得'} "
            f"（{metric.get('numerator')}/{metric.get('denominator')}，95% CI {ci}，"
            f"badcase `{', '.join(metric.get('badcaseIds') or []) or '无'}`）。"
        )
    lines.extend(
        [
            f"- 扩展 schema-only 案件：`{(report.get('canonicalSlotDiagnostics') or {}).get('extensionOnlyCaseCount', 0)}`；"
            "这些案件不应直接归因于生产 extractor 漏抽。",
            "",
            "## 优化前后诊断（不是 A/B 或人工真值）",
            "",
            f"优化前 provisional：{baseline.get('caseCount', '未知')} 条，Intent Macro-F1 `{((baseline.get('metrics') or {}).get('intentMacroF1') or {}).get('value')}`、"
            f"高风险 Recall `{((baseline.get('metrics') or {}).get('highRiskIntentRecall') or {}).get('value')}`、"
            f"slot EM `{((baseline.get('metrics') or {}).get('slotExactMatch') or {}).get('value')}`、"
            f"handoff Recall `{((baseline.get('metrics') or {}).get('handoffRecall') or {}).get('value')}`；"
            f"历史 badcase：{', '.join(baseline.get('badcaseIds') or [])}。",
            (
                f"当前扩展到 {dataset.get('caseCount', 0)} 条并完成双人复核，点估计通过参考门槛；样本量仍不足以推出行业级稳定性。"
                if human_verified
                else f"当前扩展到 {dataset.get('caseCount', 0)} 条并修复规则后，点估计通过参考门槛；标签仍未独立人工复核，样本量也不足以推出行业级稳定性。"
            ),
            "扩展切片：" + "; ".join(
                f"{tag}={count}" for tag, count in sorted((dataset.get('sliceCounts') or {}).items())
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Intent 明细",
            "",
            "| Intent | support | Precision | Recall | F1 | badcase |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for intent, values in sorted((report.get("perIntent") or {}).items()):
        lines.append(
            f"| `{intent}` | {values.get('support')} | {values.get('precision')} | "
            f"{values.get('recall')} | {values.get('f1')} | "
            f"{', '.join(values.get('badcaseIds') or []) or '无'} |"
        )
    lines.extend(["", "## Badcase（逐指标）", ""])
    for badcase in report.get("badcases") or []:
        lines.extend(
            [
                f"### `{badcase.get('caseId')}` · {', '.join(badcase.get('metrics') or [])}",
                f"- 输入：{badcase.get('message')}",
                f"- 切片/难度：`{', '.join(badcase.get('sliceTags') or []) or '未标注'}` / `{badcase.get('difficulty') or '未标注'}`",
                f"- 期望：`{json.dumps(badcase.get('expected'), ensure_ascii=False, sort_keys=True)}`",
                f"- 实际：`{json.dumps(badcase.get('predicted'), ensure_ascii=False, sort_keys=True)}`",
                f"- 根因分类：`{badcase.get('rootCause')}`；标签已冻结，需将该 case 纳入对应回归切片。",
                "",
            ]
        )
    lines.extend(
        [
            "## 口径与限制",
            "",
            (
                "- 人工金标冻结流程已完成：两名标注者盲标 intent/risk/转人工/严重度/slot，并完成冲突仲裁；当前标签版本可复核，但仍不代表线上客服成功率。"
                if human_verified
                else "- 人工金标冻结流程：两名标注者盲标 intent/risk/转人工/严重度/slot，冲突仲裁后固定版本；当前状态为 `PENDING_INDEPENDENT_REVIEW`，未产生人工准确率。"
            ),
            "- 高风险 Recall 的正类是独立标签 `riskLevel=HIGH`，不是模型自报风险；严重漏转人工只统计 `handoffSeverity=CRITICAL`。",
            "- slot Entity/Span F1 使用 NFKC 后的字符 span；`slotExactMatch` 只在存在 gold slot 的请求上计分，空 slot 不抬高结果。",
            "- `HANDOFF_SUGGESTED` 不算即时转人工成功；远程结果未知、Provider 失败和人工校准不在本基线中伪造。",
            (
                "- 本版本人工复核已完成；后续修订必须生成新数据集版本并保留当前包，不得覆盖历史结果。"
                if human_verified
                else "- 独立人工复核完成后，必须冻结标签版本、重新运行并保留本 provisional 包，不得覆盖历史结果。"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


async def run_customer_service_gold(
    dataset_path: Path,
    *,
    mode: str = "rule",
    output_path: Path | None = None,
    json_output_path: Path | None = None,
) -> dict[str, Any]:
    if mode != "rule":
        raise CustomerServiceGoldError("only --mode rule is implemented; live mode requires a reviewed protocol")
    rows = load_gold_dataset(dataset_path)
    predictions = await predict_rule_baseline(rows)
    classifier_path = Path(__file__).resolve().parents[1] / "app" / "domain" / "intent" / "classifier.py"
    provenance = {
        "mode": mode,
        "datasetPath": _path_provenance(dataset_path),
        "datasetSha256": sha256_file(dataset_path),
        "productionResolver": "app.domain.intent.classifier.resolve_intent",
        "allowLlm": False,
        "providerFingerprint": "DETERMINISTIC_RULE_BASELINE",
        "resolverSourceSha256": sha256_file(classifier_path) if classifier_path.is_file() else None,
    }
    report = evaluate_predictions(rows, predictions, provenance=provenance)
    if json_output_path:
        atomic_write_json(json_output_path, report)
    if output_path:
        atomic_write_text(output_path, render_markdown(report))
    return report


def run_sync(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(run_customer_service_gold(*args, **kwargs))
