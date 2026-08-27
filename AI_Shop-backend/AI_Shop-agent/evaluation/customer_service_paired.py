"""Paired before/after analysis for customer-service routing fixes.

The same cases and labels are compared case-by-case.  Binary outcomes use an
exact McNemar test and aggregate Macro-F1/slot-F1 deltas use a paired,
stratified case bootstrap.  This establishes improvement on the exposed
development set; it never promotes the result to final-unseen evidence.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    load_json,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import percentile
from evaluation.customer_service_gold import (
    _bootstrap_stratum,
    _slot_case_counts_for_maps,
    _slot_maps_equal,
)

REPORT_SCHEMA = "aishop-customer-service-paired-comparison/v1"
PACKAGE_SCHEMA = "aishop-customer-service-paired-evidence-package/v1"
_BOOTSTRAP_SAMPLES = 2_000
_BOOTSTRAP_SEED = 20260826


class CustomerServicePairedError(ValueError):
    """Raised when before/after reports are not comparable."""


def _core_report(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = value.get("rulePreRouter") if isinstance(value, Mapping) else None
    if isinstance(candidate, Mapping):
        return dict(candidate)
    return dict(value)


def _case_map(report: Mapping[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in report.get("cases") or []:
        if not isinstance(raw, Mapping):
            raise CustomerServicePairedError(f"{label} contains a non-object case")
        case_id = str(raw.get("id") or "")
        if not case_id or case_id in result:
            raise CustomerServicePairedError(f"{label} has a missing/duplicate case ID")
        result[case_id] = dict(raw)
    if not result:
        raise CustomerServicePairedError(f"{label} has no cases")
    return result


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prediction(case: Mapping[str, Any]) -> Mapping[str, Any]:
    value = case.get("predicted") or {}
    return value if isinstance(value, Mapping) else {}


def _expected(case: Mapping[str, Any]) -> Mapping[str, Any]:
    value = case.get("expected") or {}
    return value if isinstance(value, Mapping) else {}


def _intent_correct(case: Mapping[str, Any]) -> bool:
    return str(_prediction(case).get("intent")) == str(_expected(case).get("intent"))


def _risk_high_detected(case: Mapping[str, Any]) -> bool:
    return str(_prediction(case).get("riskLevel")) == "HIGH"


def _handoff_detected(case: Mapping[str, Any]) -> bool:
    prediction = _prediction(case)
    return prediction.get("nextAction") == "HANDOFF" or prediction.get("shouldHandoff") is True


def _slot_exact(case: Mapping[str, Any]) -> bool:
    expected_slots = _expected(case).get("slots") or {}
    predicted_slots = _prediction(case).get("entities") or {}
    return bool(expected_slots) and isinstance(predicted_slots, Mapping) and _slot_maps_equal(
        expected_slots, predicted_slots
    )


def _exact_mcnemar_p(improvements: int, regressions: int) -> float:
    discordant = improvements + regressions
    if discordant == 0:
        return 1.0
    tail = min(improvements, regressions)
    probability = sum(math.comb(discordant, index) for index in range(tail + 1)) / (
        2**discordant
    )
    return min(1.0, 2 * probability)


def _binary_comparison(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    *,
    case_ids: Sequence[str],
    predicate: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    before_values = [bool(predicate(before[case_id])) for case_id in case_ids]
    after_values = [bool(predicate(after[case_id])) for case_id in case_ids]
    improvements = [
        case_id
        for case_id, old, new in zip(case_ids, before_values, after_values)
        if not old and new
    ]
    regressions = [
        case_id
        for case_id, old, new in zip(case_ids, before_values, after_values)
        if old and not new
    ]
    denominator = len(case_ids)
    before_hits = sum(before_values)
    after_hits = sum(after_values)
    return {
        "denominator": denominator,
        "before": {
            "numerator": before_hits,
            "value": round(before_hits / denominator, 6) if denominator else None,
        },
        "after": {
            "numerator": after_hits,
            "value": round(after_hits / denominator, 6) if denominator else None,
        },
        "absoluteDelta": (
            round((after_hits - before_hits) / denominator, 6) if denominator else None
        ),
        "improvementCount": len(improvements),
        "improvementCaseIds": improvements,
        "regressionCount": len(regressions),
        "regressionCaseIds": regressions,
        "exactMcNemarTwoSidedP": round(
            _exact_mcnemar_p(len(improvements), len(regressions)), 8
        ),
        "testInterpretation": (
            "PAIRED_DIRECTIONAL_IMPROVEMENT"
            if improvements and not regressions
            else "MIXED_OR_NO_CHANGE"
        ),
    }


def _macro_f1(cases: Sequence[Mapping[str, Any]]) -> float:
    labels = sorted(
        {str(_expected(case).get("intent") or "__MISSING__") for case in cases}
        | {str(_prediction(case).get("intent") or "__MISSING__") for case in cases}
    )
    values: list[float] = []
    for label in labels:
        tp = sum(
            str(_expected(case).get("intent")) == label
            and str(_prediction(case).get("intent")) == label
            for case in cases
        )
        support = sum(str(_expected(case).get("intent")) == label for case in cases)
        predicted = sum(str(_prediction(case).get("intent")) == label for case in cases)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        values.append(0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return sum(values) / len(values) if values else 0.0


def _slot_micro_f1(cases: Sequence[Mapping[str, Any]]) -> float:
    counts = [
        _slot_case_counts_for_maps(
            _expected(case).get("slots") or {},
            _prediction(case).get("entities") or {},
        )[:3]
        for case in cases
    ]
    tp = sum(item[0] for item in counts)
    fp = sum(item[1] for item in counts)
    fn = sum(item[2] for item in counts)
    denominator = 2 * tp + fp + fn
    return 2 * tp / denominator if denominator else 1.0


def _paired_stratified_sample(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    rng: random.Random,
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    grouped: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for pair in pairs:
        grouped[_bootstrap_stratum(pair[1])].append(pair)
    return [
        group[rng.randrange(len(group))]
        for name in sorted(grouped)
        for group in (grouped[name],)
        for _item in group
    ]


def _paired_bootstrap_delta(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    statistic: Callable[[Sequence[Mapping[str, Any]]], float],
    *,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(_BOOTSTRAP_SAMPLES):
        sample = _paired_stratified_sample(pairs, rng)
        before_value = statistic([pair[0] for pair in sample])
        after_value = statistic([pair[1] for pair in sample])
        deltas.append(after_value - before_value)
    point_before = statistic([pair[0] for pair in pairs])
    point_after = statistic([pair[1] for pair in pairs])
    return {
        "before": round(point_before, 6),
        "after": round(point_after, 6),
        "absoluteDelta": round(point_after - point_before, 6),
        "confidenceInterval95": {
            "lower": round(percentile(deltas, 0.025), 6),
            "upper": round(percentile(deltas, 0.975), 6),
            "method": "paired-stratified-case-bootstrap",
            "samples": _BOOTSTRAP_SAMPLES,
            "confidenceLevel": 0.95,
        },
        "bootstrapProbabilityDeltaNotPositive": round(
            sum(delta <= 0 for delta in deltas) / len(deltas), 6
        ),
    }


def compare_customer_service_reports(
    before_path: Path,
    after_path: Path,
    *,
    label_audit_path: Path,
) -> dict[str, Any]:
    """Compare two exact reports on the identical dataset and case order."""

    before_raw = load_json(before_path)
    after_raw = load_json(after_path)
    before_report = _core_report(before_raw)
    after_report = _core_report(after_raw)
    before = _case_map(before_report, label="before report")
    after = _case_map(after_report, label="after report")
    if set(before) != set(after):
        raise CustomerServicePairedError("before/after case ID sets differ")
    case_ids = sorted(before)
    mismatched_expected = [
        case_id
        for case_id in case_ids
        if _canonical(_expected(before[case_id])) != _canonical(_expected(after[case_id]))
    ]
    if mismatched_expected:
        raise CustomerServicePairedError(
            "before/after gold labels differ: " + ", ".join(mismatched_expected)
        )
    before_dataset = before_report.get("dataset") or {}
    after_dataset = after_report.get("dataset") or {}
    if before_dataset.get("sha256") != after_dataset.get("sha256"):
        raise CustomerServicePairedError("before/after dataset SHA-256 differs")

    high_ids = [
        case_id for case_id in case_ids if str(_expected(after[case_id]).get("riskLevel")) == "HIGH"
    ]
    handoff_ids = [
        case_id for case_id in case_ids if _expected(after[case_id]).get("shouldHandoff") is True
    ]
    critical_ids = [
        case_id
        for case_id in case_ids
        if str(_expected(after[case_id]).get("handoffSeverity")) == "CRITICAL"
    ]
    slot_ids = [case_id for case_id in case_ids if bool(_expected(after[case_id]).get("slots"))]
    binary = {
        "intentAccuracy": _binary_comparison(
            before, after, case_ids=case_ids, predicate=_intent_correct
        ),
        "highRiskRecall": _binary_comparison(
            before, after, case_ids=high_ids, predicate=_risk_high_detected
        ),
        "handoffRecall": _binary_comparison(
            before, after, case_ids=handoff_ids, predicate=_handoff_detected
        ),
        "criticalHandoffSuccess": _binary_comparison(
            before, after, case_ids=critical_ids, predicate=_handoff_detected
        ),
        "slotExactMatch": _binary_comparison(
            before, after, case_ids=slot_ids, predicate=_slot_exact
        ),
    }
    pairs = [(before[case_id], after[case_id]) for case_id in case_ids]
    aggregate = {
        "intentMacroF1": _paired_bootstrap_delta(
            pairs, _macro_f1, seed=_BOOTSTRAP_SEED ^ 0xA1
        ),
        "slotEntitySpanF1": _paired_bootstrap_delta(
            pairs, _slot_micro_f1, seed=_BOOTSTRAP_SEED ^ 0xB2
        ),
    }

    label_audit = load_json(label_audit_path)
    issue_ids: dict[str, set[str]] = {
        str(item.get("code")): set(str(case_id) for case_id in item.get("caseIds") or [])
        for item in label_audit.get("findings") or []
    }
    taxonomy_excluded = issue_ids.get("TAXONOMY_RECOMMENT_ACTION_COLLISION", set())
    slot_excluded = {
        case_id
        for code, ids in issue_ids.items()
        if code.startswith("SLOT_")
        for case_id in ids
    }
    intent_clean_pairs = [pair for case_id, pair in zip(case_ids, pairs) if case_id not in taxonomy_excluded]
    slot_clean_pairs = [pair for case_id, pair in zip(case_ids, pairs) if case_id not in slot_excluded]
    clean_diagnostics = {
        "selectionSource": {
            "path": str(label_audit_path),
            "sha256": sha256_file(label_audit_path),
            "note": "Exclusions come from the frozen consistency audit, not from prediction correctness.",
        },
        "intentMacroF1": {
            "excludedCaseIds": sorted(taxonomy_excluded),
            "includedCaseCount": len(intent_clean_pairs),
            **_paired_bootstrap_delta(
                intent_clean_pairs, _macro_f1, seed=_BOOTSTRAP_SEED ^ 0xC3
            ),
        },
        "slotEntitySpanF1": {
            "excludedCaseIds": sorted(slot_excluded),
            "includedCaseCount": len(slot_clean_pairs),
            **_paired_bootstrap_delta(
                slot_clean_pairs, _slot_micro_f1, seed=_BOOTSTRAP_SEED ^ 0xD4
            ),
        },
    }

    old_v1_ids = [case_id for case_id in case_ids if case_id.startswith("cs-gold-v1-")]
    regression_fields = {
        "intent": _intent_correct,
        "riskLevel": lambda case: str(_prediction(case).get("riskLevel"))
        == str(_expected(case).get("riskLevel")),
        "handoff": lambda case: _handoff_detected(case)
        == bool(_expected(case).get("shouldHandoff")),
    }
    old_v1_regressions = {
        field: [
            case_id
            for case_id in old_v1_ids
            if predicate(before[case_id]) and not predicate(after[case_id])
        ]
        for field, predicate in regression_fields.items()
    }
    critical_regressions = binary["criticalHandoffSuccess"]["regressionCaseIds"]
    regression_passed = not critical_regressions and not any(old_v1_regressions.values())
    statistically_clear = all(
        aggregate[name]["confidenceInterval95"]["lower"] > 0
        for name in ("intentMacroF1", "slotEntitySpanF1")
    )
    return {
        "schemaVersion": REPORT_SCHEMA,
        "createdAt": utc_now(),
        "status": "DEVELOPMENT_FIX_EFFECT_CONFIRMED_LABEL_GATE_BLOCKED",
        "comparisonDesign": {
            "paired": True,
            "caseCount": len(case_ids),
            "identicalCaseIds": True,
            "identicalGoldLabels": True,
            "identicalDatasetSha256": before_dataset.get("sha256"),
            "beforeMode": (before_report.get("provenance") or {}).get("mode"),
            "afterMode": (after_report.get("provenance") or {}).get("mode"),
            "knownSetExposure": "FULLY_EXPOSED_DEVELOPMENT_SET",
        },
        "sourceReports": {
            "before": {"path": str(before_path), "sha256": sha256_file(before_path)},
            "after": {"path": str(after_path), "sha256": sha256_file(after_path)},
            "afterResolverSourceSha256": (after_report.get("provenance") or {}).get(
                "resolverSourceSha256"
            ),
        },
        "binaryPairedMetrics": binary,
        "aggregatePairedMetrics": aggregate,
        "labelPolicyCleanDiagnostics": clean_diagnostics,
        "regressionGuard": {
            "oldV1CaseCount": len(old_v1_ids),
            "oldV1PreviouslyCorrectThenWrong": old_v1_regressions,
            "criticalHandoffRegressions": critical_regressions,
            "passed": regression_passed,
        },
        "gates": {
            "pairedAggregateImprovement95CiAboveZero": statistically_clear,
            "implementationRegressionGuardPassed": regression_passed,
            "developmentFixValidated": statistically_clear and regression_passed,
            "labelConsistencyPassed": label_audit.get("gates", {}).get(
                "labelConsistencyPassed", False
            ),
            "provenancePassed": label_audit.get("gates", {}).get("provenancePassed", False),
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
        },
        "interpretation": [
            "The routing change materially improved this exact exposed 120-case development set.",
            "McNemar tests reflect paired binary changes; small safety denominators may remain statistically inconclusive even with zero observed misses.",
            "The result cannot estimate unseen generalization because implementation was informed by these cases.",
            "Label consistency and reviewer-provenance gates remain open, so no release-quality claim is permitted.",
        ],
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Customer-service routing fix — paired comparison",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Cases: `{(report.get('comparisonDesign') or {}).get('caseCount')}` paired cases",
        f"- Development fix validated: `{str(bool((report.get('gates') or {}).get('developmentFixValidated'))).lower()}`",
        "- Release/final eligible: `false / false`",
        "",
        "## Binary paired outcomes",
        "",
        "| Metric | Before | After | Delta | Improved / regressed | Exact McNemar p |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, value in (report.get("binaryPairedMetrics") or {}).items():
        lines.append(
            f"| {name} | {(value.get('before') or {}).get('value')} | "
            f"{(value.get('after') or {}).get('value')} | {value.get('absoluteDelta')} | "
            f"{value.get('improvementCount')} / {value.get('regressionCount')} | "
            f"{value.get('exactMcNemarTwoSidedP')} |"
        )
    lines.extend(["", "## Paired aggregate bootstrap", ""])
    for name, value in (report.get("aggregatePairedMetrics") or {}).items():
        interval = value.get("confidenceInterval95") or {}
        lines.append(
            f"- `{name}`: `{value.get('before')}` → `{value.get('after')}` "
            f"(Δ `{value.get('absoluteDelta')}`, 95% CI `{interval.get('lower')}`–`{interval.get('upper')}`)"
        )
    lines.extend(["", "## Interpretation", ""])
    lines.extend(f"- {item}" for item in report.get("interpretation") or [])
    return "\n".join(lines) + "\n"


def build_paired_evidence_package(
    before_path: Path,
    after_path: Path,
    *,
    label_audit_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite paired package: {output_dir}")
    output_dir.mkdir(parents=True)
    before_raw = load_json(before_path)
    after_raw = load_json(after_path)
    report = compare_customer_service_reports(
        before_path, after_path, label_audit_path=label_audit_path
    )
    atomic_write_json(output_dir / "paired-comparison.json", report, overwrite=False)
    atomic_write_text(output_dir / "paired-comparison.md", _render_markdown(report), overwrite=False)
    atomic_write_json(
        output_dir / "source-projections" / "before-rule-pre-router.json",
        _core_report(before_raw),
        overwrite=False,
    )
    atomic_write_json(
        output_dir / "source-projections" / "after-rule-pre-router.json",
        _core_report(after_raw),
        overwrite=False,
    )
    atomic_write_bytes(
        output_dir / "source" / "label-consistency-audit.json",
        label_audit_path.read_bytes(),
        overwrite=False,
    )
    lifecycle = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": utc_now(),
        "status": report["status"],
        "developmentFixValidated": report["gates"]["developmentFixValidated"],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "blockingControls": [
            "LABEL_POLICY_READJUDICATION",
            "INDEPENDENCE_PROVENANCE",
            "FRESH_HTTP_RUN_AND_BLIND_ANSWER_REVIEW",
            "EXTERNAL_UNSEEN_FINAL_RUN",
        ],
    }
    atomic_write_json(output_dir / "lifecycle.json", lifecycle, overwrite=False)
    atomic_write_text(
        output_dir / "README.md",
        "# Paired customer-service development comparison\n\n"
        "This package binds the exact before/after case projections and paired statistics. "
        "It validates the implementation change only on the exposed development set. Label "
        "and provenance gates remain blocking, so releaseGateEligible is always false.\n",
        overwrite=False,
    )
    files = {
        path.relative_to(output_dir).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name not in {"evidence-manifest.json", "SHA256SUMS"}
    }
    manifest = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": lifecycle["createdAt"],
        "status": lifecycle["status"],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "sourceReports": report["sourceReports"],
        "files": files,
    }
    atomic_write_json(output_dir / "evidence-manifest.json", manifest, overwrite=False)
    checksum_paths = sorted(path for path in output_dir.rglob("*") if path.is_file())
    atomic_write_text(
        output_dir / "SHA256SUMS",
        "\n".join(
            f"{sha256_file(path)}  {path.relative_to(output_dir).as_posix()}"
            for path in checksum_paths
        )
        + "\n",
        overwrite=False,
    )
    return {
        "status": lifecycle["status"],
        "developmentFixValidated": lifecycle["developmentFixValidated"],
        "releaseGateEligible": False,
        "outputDir": str(output_dir),
        "sha256SumsSha256": sha256_file(output_dir / "SHA256SUMS"),
    }


def verify_paired_evidence_package(output_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    checksums = output_dir / "SHA256SUMS"
    if not checksums.is_file():
        return {"valid": False, "errors": ["missing:SHA256SUMS"]}
    for line in checksums.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        path = output_dir / relative
        if not separator or not path.is_file() or sha256_file(path) != digest:
            errors.append(f"checksum:{relative or line}")
    report = load_json(output_dir / "paired-comparison.json")
    lifecycle = load_json(output_dir / "lifecycle.json")
    manifest = load_json(output_dir / "evidence-manifest.json")
    for label, value in (("report", report), ("lifecycle", lifecycle), ("manifest", manifest)):
        if value.get("releaseGateEligible") is not False and label != "report":
            errors.append(f"{label}-release-gate")
    if report.get("gates", {}).get("releaseGateEligible") is not False:
        errors.append("report-release-gate")
    if lifecycle.get("finalUnseenEligible") is not False:
        errors.append("lifecycle-final-unseen")
    return {
        "valid": not errors,
        "status": "VERIFIED" if not errors else "INVALID",
        "errors": errors,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
    }
