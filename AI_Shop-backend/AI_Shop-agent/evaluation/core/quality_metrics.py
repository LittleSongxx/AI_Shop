"""Domain quality metrics that complement the frozen release gate.

The v2 release suite deliberately keeps its published denominator stable.  These
small, deterministic helpers are therefore additive: a development/regression
runner can report recommendation-slate quality, calibration, and repeated-agent
reliability without rewriting the already-published final evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _value(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        raw = item.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip().casefold()
    return ""


def constraint_satisfaction_rate(
    returned_count: int,
    violation_count: int,
) -> float:
    """Return the fraction of returned items that satisfy hard constraints."""

    if returned_count < 0 or violation_count < 0:
        raise ValueError("counts must be non-negative")
    if violation_count > returned_count:
        raise ValueError("violation_count cannot exceed returned_count")
    # An empty slate is not evidence that constraints were satisfied.  Treat it
    # as a failed/undefined observation so callers cannot improve this metric by
    # suppressing all recommendations.  No-result accuracy is reported by the
    # Search suite separately.
    if returned_count == 0:
        return 0.0
    return 1.0 - (violation_count / returned_count)


def intra_list_diversity(
    items: Sequence[Mapping[str, Any]],
    *,
    fields: tuple[tuple[str, ...], ...] = (("category_id", "categoryId", "category"),),
) -> float:
    """Measure categorical novelty as one minus same-value pair similarity.

    Multiple fields can be supplied, for example category and brand.  A pair is
    considered redundant when all supplied non-empty fields are equal.  Pairs
    without comparable metadata are excluded from the denominator; if no pair
    is comparable the metric fails closed to ``0.0``.
    """

    rows = list(items)
    if len(rows) <= 1:
        return 1.0
    redundant = 0
    comparable_pairs = 0
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1 :]:
            comparisons = [
                (_value(left, *aliases), _value(right, *aliases)) for aliases in fields
            ]
            observed = [(a, b) for a, b in comparisons if a and b]
            if not observed:
                # Missing category/brand metadata is unknown, not diverse and
                # not redundant.  Exclude the pair from the denominator and
                # return zero when no pair is comparable (fail closed).
                continue
            comparable_pairs += 1
            if all(a == b for a, b in observed):
                redundant += 1
    if comparable_pairs == 0:
        return 0.0
    return 1.0 - (redundant / comparable_pairs)


def unique_value_ratio(
    items: Sequence[Mapping[str, Any]],
    *keys: str,
) -> float:
    """Return unique non-empty values divided by item count."""

    rows = list(items)
    if not rows:
        return 0.0
    values = {_value(item, *keys) for item in rows}
    values.discard("")
    return len(values) / len(rows)


def catalog_coverage_rate(
    recommended_ids: Sequence[str],
    catalog_ids: Sequence[str],
) -> float:
    """Measure how much of the available catalog a slate exposes."""

    catalog = {str(value) for value in catalog_ids if str(value)}
    if not catalog:
        return 0.0
    recommended = {str(value) for value in recommended_ids if str(value)}
    return len(recommended.intersection(catalog)) / len(catalog)


def novelty_rate(
    recommended_ids: Sequence[str],
    previously_seen_ids: Sequence[str],
) -> float:
    """Fraction of recommendations not already present in the user's history."""

    rows = [str(value) for value in recommended_ids if str(value)]
    if not rows:
        return 0.0
    seen = {str(value) for value in previously_seen_ids if str(value)}
    return sum(value not in seen for value in rows) / len(rows)


def expected_calibration_error(
    confidences: Sequence[float],
    outcomes: Sequence[float | int],
    *,
    bins: int = 10,
) -> float:
    """Compute ECE for confidence scores and binary outcomes.

    This is intentionally deterministic and model-agnostic.  It is suitable for
    evaluating a ranker or an answer judge after probabilities are calibrated;
    it must not be confused with accuracy or used without a held-out slice.
    """

    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have equal length")
    if bins < 1:
        raise ValueError("bins must be positive")
    if not confidences:
        return 0.0
    bucket_rows: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for confidence, outcome in zip(confidences, outcomes):
        score = float(confidence)
        result = float(outcome)
        if not 0.0 <= score <= 1.0 or result not in {0.0, 1.0}:
            raise ValueError("confidence must be in [0,1] and outcome must be binary")
        index = min(bins - 1, int(score * bins))
        bucket_rows[index].append((score, result))
    total = len(confidences)
    return sum(
        (len(rows) / total)
        * abs(sum(score for score, _ in rows) / len(rows) - sum(result for _, result in rows) / len(rows))
        for rows in bucket_rows
        if rows
    )


def brier_score(confidences: Sequence[float], outcomes: Sequence[float | int]) -> float:
    """Return the mean squared probability error for binary outcomes."""

    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have equal length")
    if not confidences:
        return 0.0
    rows = []
    for confidence, outcome in zip(confidences, outcomes):
        score = float(confidence)
        result = float(outcome)
        if not 0.0 <= score <= 1.0 or result not in {0.0, 1.0}:
            raise ValueError("confidence must be in [0,1] and outcome must be binary")
        rows.append((score - result) ** 2)
    return sum(rows) / len(rows)


def pass_power_k(trial_outcomes: Sequence[Sequence[bool | int]]) -> float:
    """Estimate ``pass^k`` as the mean all-trials-success indicator.

    Each inner sequence is one repeated run of the same task.  Empty task
    sequences are invalid because they would otherwise look like success.
    """

    if not trial_outcomes:
        return 0.0
    values: list[float] = []
    for trials in trial_outcomes:
        rows = list(trials)
        if not rows:
            raise ValueError("each task needs at least one trial")
        if any(value not in {0, 1, False, True} for value in rows):
            raise ValueError("trial outcomes must be binary")
        values.append(float(all(bool(value) for value in rows)))
    return sum(values) / len(values)


def slate_quality_metrics(
    items: Sequence[Mapping[str, Any]],
    *,
    catalog_ids: Sequence[str] = (),
    previously_seen_ids: Sequence[str] = (),
    violation_count: int = 0,
) -> dict[str, float | int]:
    """Return the standard recommendation-slate slice in one stable shape."""

    ids = [
        str(item.get("product_id") or item.get("productId") or item.get("id") or "")
        for item in items
    ]
    return {
        "returnedCount": len(items),
        "constraintSatisfactionRate": constraint_satisfaction_rate(
            len(items), violation_count
        ),
        "intraListDiversity": intra_list_diversity(
            items,
            fields=(("category_id", "categoryId", "category", "categoryName"),),
        ),
        "uniqueCategoryRatio": unique_value_ratio(
            items, "category_id", "categoryId", "category", "categoryName"
        ),
        "uniqueBrandRatio": unique_value_ratio(items, "brand", "brandName"),
        "catalogCoverageRate": catalog_coverage_rate(ids, catalog_ids),
        "noveltyRate": novelty_rate(ids, previously_seen_ids),
    }
