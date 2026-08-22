"""Slice-level quality aggregation and metamorphic checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from evaluation.core.contracts import CaseResult
from evaluation.core.metrics import percentile, wilson_interval

DEFAULT_SEARCH_SLICES = (
    "exact-model-number-brand",
    "chinese-synonym-oral",
    "budget-structured",
    "negative-exclusion",
    "no-result-conflict",
    "fallback-partial-provider",
    "category-brand-comparison",
)


def result_slice_names(result: CaseResult) -> tuple[str, ...]:
    if result.slice:
        return (str(result.slice),)
    output = result.output if isinstance(result.output, Mapping) else {}
    raw = output.get("sliceTags") or output.get("slice_tags") or []
    if isinstance(raw, str):
        raw = [raw]
    values = tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
    return values or ("unsliced",)


def _trace_values(result: CaseResult) -> list[Mapping[str, Any]]:
    output = result.output if isinstance(result.output, Mapping) else {}
    traces = output.get("trace") or output.get("traces") or []
    if isinstance(traces, Mapping):
        traces = [traces]
    return [item for item in traces if isinstance(item, Mapping)]


def _count_signal(results: Sequence[CaseResult], *keys: str) -> int:
    count = 0
    for result in results:
        output = result.output if isinstance(result.output, Mapping) else {}
        provider = result.providers if isinstance(result.providers, Mapping) else {}
        haystack = [output, provider, *_trace_values(result)]
        if any(bool(item.get(key)) for item in haystack for key in keys):
            count += 1
    return count


def _metric_values(results: Sequence[CaseResult], name: str) -> list[float]:
    return [float(result.metrics[name]) for result in results if name in result.metrics]


def _estimate_binary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"value": 0.0, "sampleCount": 0, "interval": None, "notes": ["NO_ELIGIBLE_SAMPLES"]}
    successes = sum(1 for value in values if value == 1)
    lower, upper = wilson_interval(successes, len(values))
    return {
        "value": round(sum(values) / len(values), 6),
        "sampleCount": len(values),
        "interval": {
            "method": "wilson",
            "confidenceLevel": 0.95,
            "lower": round(lower, 6),
            "upper": round(upper, 6),
        },
        "notes": [],
    }


def _estimate_numeric(values: Sequence[float], *, name: str) -> dict[str, Any]:
    if not values:
        return {"value": 0.0, "sampleCount": 0, "interval": None, "notes": ["NO_ELIGIBLE_SAMPLES"]}
    return {
        "value": round(sum(values) / len(values), 6),
        "sampleCount": len(values),
        "interval": None,
        "notes": ["SLICE_DESCRIPTIVE_ESTIMATE", name],
    }


def _latency(results: Sequence[CaseResult]) -> dict[str, float | None]:
    values = [float(result.latency_ms) for result in results if result.status.value != "ERROR"]
    return {
        "p50": round(percentile(values, 0.50), 3) if values else None,
        "p95": round(percentile(values, 0.95), 3) if values else None,
        "p99": round(percentile(values, 0.99), 3) if values else None,
        "sampleCount": len(values),
        "scope": "LOCAL_FULL_STACK_DESCRIPTIVE_NOT_PRODUCTION_SLO",
    }


def aggregate_slice_metrics(
    results: Iterable[CaseResult],
    *,
    expected_slices: Sequence[str] = (),
) -> dict[str, Any]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        for name in result_slice_names(result):
            grouped[name].append(result)
    for name in expected_slices:
        grouped.setdefault(str(name), [])
    output: dict[str, Any] = {}
    for name, rows in sorted(grouped.items()):
        normal = [row for row in rows if not row.fault_scenario]
        fault = [row for row in rows if row.fault_scenario]
        normal_status_values = [1.0 if row.status.value == "PASSED" else 0.0 for row in normal]
        normal_constraint_violations = sum(
            int(row.metrics.get("constraintViolationCount") or 0) for row in normal
        )
        fault_constraint_violations = sum(
            int(row.metrics.get("constraintViolationCount") or 0) for row in fault
        )
        metrics: dict[str, Any] = {
            "casePassRate": _estimate_binary(normal_status_values),
            "providerCompleteness": _estimate_binary(
                _metric_values(normal, "providerCompleteness")
            ),
            "noResultAccuracy": _estimate_binary(_metric_values(normal, "noResultAccuracy")),
            "constraintViolationCount": {
                "value": normal_constraint_violations,
                "sampleCount": len(normal),
                "interval": None,
                "notes": ["FAULT_CASES_EXCLUDED_FROM_NORMAL_DENOMINATOR"],
            },
            "faultConstraintViolationCount": {
                "value": fault_constraint_violations,
                "sampleCount": len(fault),
                "interval": None,
                "notes": ["FAULT_RECOVERY_SIGNAL"],
            },
            "latency": _latency(normal),
            "faultLatency": _latency(fault),
            "fallbackCount": _count_signal(normal, "fallback", "fallbackUsed", "isFallback"),
            "faultFallbackCount": _count_signal(fault, "fallback", "fallbackUsed", "isFallback"),
            "partialFailureCount": _count_signal(normal, "partialFailure", "partial", "partial_failure"),
            "faultPartialFailureCount": _count_signal(fault, "partialFailure", "partial", "partial_failure"),
            "deadlineCount": _count_signal(normal, "deadline", "deadlineExceeded", "deadline_exceeded"),
            "faultDeadlineCount": _count_signal(fault, "deadline", "deadlineExceeded", "deadline_exceeded"),
            "rejectionReasonCount": sum(
                len(
                    (row.output if isinstance(row.output, Mapping) else {}).get("rejectionReasons")
                    or []
                )
                for row in normal
            ),
            "normalQualityCaseCount": len(normal),
            "faultCaseCount": len(fault),
            "faultCasesExcludedFromNormalDenominator": True,
        }
        # Keep a stable metric shape for every slice, including no-result and
        # fault-only slices where ranking metrics have no eligible judgments.
        # A missing denominator is represented explicitly instead of silently
        # disappearing from the report.
        for metric_name in (
            "recallAt3",
            "recallAt5",
            "recallAt10",
            "mrrAt10",
            "ndcgAt5",
            "ndcgAt10",
            "hardConstraintSatisfaction",
        ):
            values = _metric_values(normal, metric_name)
            metrics[metric_name] = _estimate_numeric(values, name=metric_name)
        output[name] = {
            "caseCount": len(rows),
            "statusCounts": {
                status: sum(1 for row in rows if row.status.value == status)
                for status in ("PASSED", "FAILED", "ERROR")
            },
            "metrics": metrics,
            "normalQualityGate": {
                "casePassRate": bool(normal) and all(row.status.value == "PASSED" for row in normal),
                "constraintViolationsZero": sum(
                    int(row.metrics.get("constraintViolationCount") or 0) for row in normal
                )
                == 0,
                "providerComplete": bool(normal)
                and all(int(row.metrics.get("providerCompleteness") or 0) == 1 for row in normal),
            },
            "faultRecovery": {
                "caseCount": len(fault),
                "passed": bool(fault)
                and all(
                    bool((row.fault_scenario or {}).get("recoveryPassed"))
                    for row in fault
                ),
            },
        }
    return output


def metamorphic_check(
    relation: str,
    *,
    base: Mapping[str, Any],
    variant: Mapping[str, Any],
) -> dict[str, Any]:
    """Check monotonic/negative constraints without relying on model self-rating."""

    base_ids = {
        (
            str(item.get("productId") or item.get("product_id") or item.get("id"))
            if isinstance(item, Mapping)
            else str(item)
        )
        for item in (base.get("products") or base.get("ranking") or [])
        if item is not None
    }
    variant_rows = variant.get("products") or variant.get("ranking") or []
    variant_ids = {
        str(item.get("productId") or item.get("product_id") or item.get("id"))
        if isinstance(item, Mapping)
        else str(item)
        for item in variant_rows
    }
    relation = str(relation)
    if relation == "budget_monotonicity":
        passed = base_ids.issubset(variant_ids)
    elif relation == "exclude_brand":
        excluded = {str(value).casefold() for value in variant.get("excludedBrands") or []}
        names = [str(item.get("brand") or item.get("brandName") or "").casefold() for item in variant_rows if isinstance(item, Mapping)]
        passed = not any(any(brand in name for brand in excluded) for name in names)
    elif relation == "no_result_strict":
        passed = not variant_ids
    elif relation == "exact_model":
        passed = bool(variant_ids.intersection(base_ids))
    elif relation == "partial_provider_no_fabrication":
        catalog = {str(item) for item in variant.get("catalogIds") or []}
        passed = bool(catalog) and variant_ids.issubset(catalog)
    else:
        return {"relation": relation, "passed": False, "reason": "UNSUPPORTED_RELATION"}
    return {
        "relation": relation,
        "passed": bool(passed),
        "baseIds": sorted(base_ids),
        "variantIds": sorted(variant_ids),
    }


def evaluate_case_metamorphic_contract(
    *,
    relations: Sequence[str],
    expected: Mapping[str, Any],
    output: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate the single-case metamorphic safety contracts.

    A true paired metamorphic experiment can be supplied by callers through
    ``expected.metamorphicBase``/``output.metamorphicBase``.  When a visible
    case has no paired query, the invariant that can be checked from the
    authoritative returned slate is still recorded.  ``NOT_RUN`` is explicit
    and never counted as a pass.
    """

    rows: list[dict[str, Any]] = []
    products = output.get("products") or output.get("ranking") or []
    product_ids = {
        str(item.get("productId") or item.get("product_id") or item.get("id") or "")
        for item in products
        if isinstance(item, Mapping)
    }
    qrels = {
        str(key)
        for key, grade in (expected.get("qrels") or {}).items()
        if int(grade) > 0
    }
    constraints = expected.get("constraints") or output.get("constraints") or {}
    for relation in relations:
        relation = str(relation)
        if relation == "exact_model":
            passed = bool(product_ids.intersection(qrels))
            reason = "relevant exact-model judgment is present" if passed else "exact model absent"
            state = "CHECKED"
        elif relation == "exclude_brand":
            excluded = {
                str(item).casefold() for item in constraints.get("excludedBrands") or []
            }
            excluded.update(str(item).casefold() for item in constraints.get("excludedTerms") or [])
            names = [
                " ".join(
                    str(item.get(key) or "")
                    for key in ("brand", "brandName", "productName", "product_name")
                ).casefold()
                for item in products
                if isinstance(item, Mapping)
            ]
            passed = not any(term and term in name for term in excluded for name in names)
            reason = "no excluded brand/term returned" if passed else "excluded brand/term returned"
            state = "CHECKED"
        elif relation == "no_result_strict":
            passed = bool(expected.get("noResult")) and not product_ids
            reason = "empty authoritative slate" if passed else "no-result contract violated"
            state = "CHECKED"
        elif relation == "partial_provider_no_fabrication":
            catalog = {
                str(item)
                for item in (output.get("catalogIds") or expected.get("catalogIds") or [])
                if str(item)
            }
            if catalog:
                passed = product_ids.issubset(catalog)
                reason = "returned IDs are a subset of the catalog" if passed else "unknown ID returned"
                state = "CHECKED"
            else:
                passed = False
                reason = "catalog binding unavailable"
                state = "NOT_RUN"
        elif relation == "budget_monotonicity":
            base = output.get("metamorphicBase") or expected.get("metamorphicBase")
            if isinstance(base, Mapping):
                base_ids = {
                    str(item.get("productId") or item.get("product_id") or item.get("id") or "")
                    for item in (base.get("products") or base.get("ranking") or [])
                    if isinstance(item, Mapping)
                }
                passed = base_ids.issubset(product_ids)
                reason = "feasible base slate is preserved" if passed else "budget monotonicity violated"
                state = "CHECKED"
            else:
                passed = False
                reason = "paired lower-budget case unavailable"
                state = "NOT_RUN"
        else:
            passed = False
            reason = "unsupported metamorphic relation"
            state = "NOT_RUN"
        rows.append(
            {
                "relation": relation,
                "status": state,
                "passed": bool(passed),
                "hardGate": False,
                "reason": reason,
            }
        )
    return rows
