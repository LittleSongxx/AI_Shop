"""Customer-service understanding evidence on an independently reviewed gold set.

The evaluator is deliberately separate from the Agent pass^k evidence.  It
measures the production intent pre-router and keeps four high-value support
signals visible: intent Macro-F1, high-risk routing recall, slot span F1/EM,
and handoff recall.  The checked-in v1 labels are a draft annotation set.  The
report therefore uses ``PROVISIONAL_NOT_HUMAN_GOLD`` throughout and cannot be
used as a release gate until a second human annotator reviews every row.
"""

from __future__ import annotations

import asyncio
import json
import random
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
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


def _bootstrap_interval(values: Sequence[float], statistic, *, seed: int) -> dict[str, Any] | None:
    if not values:
        return None
    rng = random.Random(seed)
    rows = [float(value) for value in values]
    estimates = [
        statistic([rows[rng.randrange(len(rows))] for _ in rows])
        for _ in range(_BOOTSTRAP_SAMPLES)
    ]
    return {
        "lower": round(percentile(estimates, 0.025), 6),
        "upper": round(percentile(estimates, 0.975), 6),
        "method": "percentile-bootstrap",
        "confidenceLevel": 0.95,
    }


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
        "role": "PRIMARY_QUALITY",
        "releaseGateEligible": False,
        "notes": [*notes, PROVISIONAL_STATUS],
    }


def _span_tokens(value: Any) -> list[str]:
    # Character spans are deterministic for Chinese messages and avoid a
    # hidden tokenizer dependency. Whitespace is not a semantic span token.
    return [char for char in _norm(value) if not char.isspace()]


def _slot_case_counts(expected: Mapping[str, Any], predicted: Mapping[str, Any]) -> tuple[int, int, int, float]:
    expected_slots = expected.get("slots") or {}
    predicted_slots = predicted.get("entities") or {}
    if not isinstance(expected_slots, Mapping) or not isinstance(predicted_slots, Mapping):
        return 0, 0, 0, 0.0
    tp = fp = fn = 0
    for key in set(str(item) for item in expected_slots) | set(str(item) for item in predicted_slots):
        gold = _span_tokens(expected_slots.get(key, ""))
        pred = _span_tokens(predicted_slots.get(key, ""))
        overlap = Counter(gold) & Counter(pred)
        matched = sum(overlap.values())
        tp += matched
        fp += len(pred) - matched
        fn += len(gold) - matched
    precision = tp / (tp + fp) if tp + fp else 1.0 if not expected_slots else 0.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return tp, fp, fn, _f1(precision, recall)


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
    slot_badcases: list[str] = []
    slot_case_f1: list[float] = []
    slot_tp = slot_fp = slot_fn = 0
    slot_em_numerator = slot_em_denominator = 0
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

        tp, fp, fn, case_slot_f1 = _slot_case_counts(expected, predicted)
        slot_tp += tp
        slot_fp += fp
        slot_fn += fn
        slot_case_f1.append(case_slot_f1)
        expected_slots = expected.get("slots") or {}
        if expected_slots:
            slot_em_denominator += 1
            pred_slots = predicted.get("entities") or {}
            if isinstance(pred_slots, Mapping) and {
                str(key): _norm(value) for key, value in pred_slots.items()
            } == {str(key): _norm(value) for key, value in expected_slots.items()}:
                slot_em_numerator += 1
            else:
                slot_badcases.append(case_id)
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
                    and case_id not in slot_badcases,
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
        sample = [cases[rng.randrange(len(cases))] for _ in cases]
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
        "method": "case-bootstrap-macro-F1",
        "confidenceLevel": 0.95,
    }

    slot_precision = slot_tp / (slot_tp + slot_fp) if slot_tp + slot_fp else 0.0
    slot_recall = slot_tp / (slot_tp + slot_fn) if slot_tp + slot_fn else 0.0
    slot_f1 = _f1(slot_precision, slot_recall)
    slot_interval = _bootstrap_interval(
        slot_case_f1,
        lambda sample: sum(sample) / len(sample),
        seed=_BOOTSTRAP_SEED ^ 0x51,
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
        ),
        "highRiskIntentRecall": _metric(
            "highRiskIntentRecall",
            high_risk_hits / high_risk_total if high_risk_total else None,
            numerator=high_risk_hits,
            denominator=high_risk_total,
            interval=_wilson(high_risk_hits, high_risk_total),
            badcase_ids=risk_badcases,
            definition="Recall of cases independently labelled riskLevel=HIGH, requiring predicted riskLevel=HIGH.",
        ),
        "slotEntitySpanF1": _metric(
            "slotEntitySpanF1",
            slot_f1 if slot_tp + slot_fp + slot_fn else None,
            numerator=slot_tp,
            denominator=slot_tp + slot_fp + slot_fn,
            interval=slot_interval,
            badcase_ids=slot_badcases,
            definition="Micro character-span F1 over expected and predicted structured entity values; extra predicted fields count as false positives.",
        ),
        "slotExactMatch": _metric(
            "slotExactMatch",
            slot_em_numerator / slot_em_denominator if slot_em_denominator else None,
            numerator=slot_em_numerator,
            denominator=slot_em_denominator,
            interval=_wilson(slot_em_numerator, slot_em_denominator),
            badcase_ids=slot_badcases,
            definition="Request-level exact equality of all expected slots; cases with no expected slots are excluded from the denominator.",
        ),
        "handoffRecall": _metric(
            "handoffRecall",
            handoff_hits / handoff_total if handoff_total else None,
            numerator=handoff_hits,
            denominator=handoff_total,
            interval=_wilson(handoff_hits, handoff_total),
            badcase_ids=handoff_badcases,
            definition="Among gold shouldHandoff=true cases, only next_action=HANDOFF counts as immediate handoff; HANDOFF_SUGGESTED does not.",
        ),
        "criticalHandoffMissRate": _metric(
            "criticalHandoffMissRate",
            critical_handoff_misses / critical_handoff_total if critical_handoff_total else None,
            numerator=critical_handoff_misses,
            denominator=critical_handoff_total,
            interval=_wilson(critical_handoff_misses, critical_handoff_total),
            badcase_ids=critical_handoff_badcases,
            definition="Severe漏转人工率 among gold handoffSeverity=CRITICAL cases; lower is better.",
        ),
    }
    all_badcase_ids = sorted(
        set(intent_badcases)
        | set(risk_badcases)
        | set(slot_badcases)
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
                ("slotEntitySpanF1", slot_badcases),
                ("slotExactMatch", slot_badcases),
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
                    "SLOT_EXTRACTION_GAP" if any(name.startswith("slot") for name in metric_names)
                    else "HANDOFF_OR_RISK_POLICY_GAP" if any("handoff" in name.lower() or "risk" in name.lower() for name in metric_names)
                    else "INTENT_ROUTING_OR_TAXONOMY_GAP"
                ),
            }
        )
    annotation_statuses = Counter(
        str((row.get("annotation") or {}).get("status") or "UNKNOWN") for row in rows
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
        "status": PROVISIONAL_STATUS
        if any(value != HUMAN_STATUS for value in annotation_statuses)
        else HUMAN_STATUS,
        "releaseGateEligible": False,
        "humanReviewPlan": {
            "status": "PENDING_INDEPENDENT_REVIEW",
            "requiredAnnotators": 2,
            "blindedFirstPass": True,
            "adjudicationRequired": True,
            "freezeAfterAdjudication": True,
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
            "note": "Current labels are assistant drafts; do not claim human accuracy or release eligibility before independent review and adjudication.",
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
        "historicalBaseline": HISTORICAL_BASELINE,
        "metrics": metrics,
        "provisionalTargets": provisional_targets,
        "perIntent": per_intent,
        "confusionMatrix": confusion,
        "cases": cases,
        "badcases": badcase_rows,
        "limitations": [
            "Labels are not independently human reviewed; this is a provisional routing baseline, not human accuracy.",
            "mode=rule evaluates the deterministic production pre-router with allow_llm=false, not the full HTTP Agent conversation.",
            "The four metrics do not establish customer satisfaction, FCR, CSAT, production volume, or business conversion.",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    metrics = report.get("metrics") or {}
    dataset = report.get("dataset") or {}
    lines = [
        "# AI 客服金标评测（v1）",
        "",
        f"> 状态：`{report.get('status')}`；`releaseGateEligible=false`。标签未完成独立人工复核，不能称为人工准确率。",
        "> 本报告只测客服理解/转接的四项高价值证据；Agent `pass^k`、工具契约和终态门禁不计入下表。",
        "",
        f"数据集：`{dataset.get('caseCount', 0)}` 条；SHA-256：`{dataset.get('sha256') or '未记录'}`；模式：`{(report.get('provenance') or {}).get('mode', 'unknown')}`。",
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
    baseline = report.get("historicalBaseline") or {}
    lines.extend(
        [
            "",
            "## 优化前后诊断（不是 A/B 或人工真值）",
            "",
            f"优化前 provisional：{baseline.get('caseCount', '未知')} 条，Intent Macro-F1 `{((baseline.get('metrics') or {}).get('intentMacroF1') or {}).get('value')}`、"
            f"高风险 Recall `{((baseline.get('metrics') or {}).get('highRiskIntentRecall') or {}).get('value')}`、"
            f"slot EM `{((baseline.get('metrics') or {}).get('slotExactMatch') or {}).get('value')}`、"
            f"handoff Recall `{((baseline.get('metrics') or {}).get('handoffRecall') or {}).get('value')}`；"
            f"历史 badcase：{', '.join(baseline.get('badcaseIds') or [])}。",
            f"当前扩展到 {dataset.get('caseCount', 0)} 条并修复规则后，点估计通过参考门槛；标签仍未独立人工复核，样本量也不足以推出行业级稳定性。",
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
                f"- 初步根因：`{badcase.get('rootCause')}`；人工复核后需补充最终根因和回归 case ID。",
                "",
            ]
        )
    lines.extend(
        [
            "## 口径与限制",
            "",
            "- 人工金标冻结流程：两名标注者盲标 intent/risk/转人工/严重度/slot，冲突仲裁后固定版本；当前状态为 `PENDING_INDEPENDENT_REVIEW`，未产生人工准确率。",
            "- 高风险 Recall 的正类是独立标签 `riskLevel=HIGH`，不是模型自报风险；严重漏转人工只统计 `handoffSeverity=CRITICAL`。",
            "- slot Entity/Span F1 使用 NFKC 后的字符 span；`slotExactMatch` 只在存在 gold slot 的请求上计分，空 slot 不抬高结果。",
            "- `HANDOFF_SUGGESTED` 不算即时转人工成功；远程结果未知、Provider 失败和人工校准不在本基线中伪造。",
            "- 独立人工复核完成后，必须冻结标签版本、重新运行并保留本 provisional 包，不得覆盖历史结果。",
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
        "datasetPath": relative_to_repo(dataset_path),
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
