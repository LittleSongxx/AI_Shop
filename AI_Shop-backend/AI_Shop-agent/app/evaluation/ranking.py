"""Reusable ranking metrics and paired comparison helpers.

Search and RAG runners pass a ranked list plus explicit graded labels. Unjudged
items can be rejected instead of silently being treated as irrelevant, which
is required for judged-pool datasets such as WANDS.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.evaluation.contracts import percentile

DEFAULT_K_VALUES = (1, 3, 5, 10, 20)


def _round(value: float) -> float:
    return round(float(value), 6)


def _deduplicate(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _labels(values: Mapping[Any, Any]) -> dict[str, float]:
    labels: dict[str, float] = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"relevance grade for {key!r} must be numeric")
        grade = float(value)
        if not math.isfinite(grade) or grade < 0:
            raise ValueError(f"relevance grade for {key!r} must be finite and non-negative")
        labels[str(key)] = grade
    return labels


def _dcg(grades: Sequence[float]) -> float:
    return sum(
        (2.0**grade - 1.0) / math.log2(rank + 1)
        for rank, grade in enumerate(grades, 1)
    )


def ranking_case_metrics(
    ranked_ids: Sequence[Any],
    relevance_grades: Mapping[Any, Any],
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    relevant_threshold: float = 1.0,
    expected_no_results: bool = False,
    judged_pool: bool = False,
) -> dict[str, Any]:
    """Calculate graded and binary ranking metrics for one query."""

    ks = sorted({max(1, int(value)) for value in k_values})
    if not ks:
        raise ValueError("k_values must not be empty")
    labels = _labels(relevance_grades)
    ranked = _deduplicate(ranked_ids)
    if judged_pool:
        unjudged = [item for item in ranked if item not in labels]
        if unjudged:
            raise ValueError(f"ranking contains unjudged items: {unjudged[:5]}")

    relevant = {item for item, grade in labels.items() if grade >= relevant_threshold}
    if expected_no_results:
        if relevant:
            raise ValueError("negative query cannot contain relevant labels")
        return {
            "applicable": False,
            "expectedNoResults": True,
            "returnedCount": len(ranked),
            "noResultCorrect": not ranked,
            "firstRelevantRank": None,
            "lastRelevantRank": None,
            "recall90K": None,
            "metricsByK": {
                str(k): {
                    "recall": None,
                    "hitRate": None,
                    "allRelevantRate": None,
                    "precision": None,
                    "reciprocalRank": None,
                    "ndcg": None,
                    "averagePrecision": None,
                }
                for k in ks
            },
        }
    if not relevant:
        raise ValueError("answerable ranking case must contain at least one relevant label")

    relevant_positions = [
        rank for rank, item in enumerate(ranked, 1) if item in relevant
    ]
    recall90_k: int | None = None
    required_for_90 = math.ceil(len(relevant) * 0.9)
    if len(relevant_positions) >= required_for_90:
        recall90_k = relevant_positions[required_for_90 - 1]

    metrics_by_k: dict[str, dict[str, float]] = {}
    for k in ks:
        top = ranked[:k]
        binary = [item in relevant for item in top]
        hit_count = len(set(top).intersection(relevant))
        first = next((rank for rank, hit in enumerate(binary, 1) if hit), None)
        gains = [labels.get(item, 0.0) for item in top]
        ideal = sorted(labels.values(), reverse=True)[:k]
        ideal_dcg = _dcg(ideal)
        precision_sum = 0.0
        hits_seen = 0
        for rank, hit in enumerate(binary, 1):
            if hit:
                hits_seen += 1
                precision_sum += hits_seen / rank
        ap_denominator = min(len(relevant), k)
        metrics_by_k[str(k)] = {
            "recall": _round(hit_count / len(relevant)),
            "hitRate": float(bool(hit_count)),
            "allRelevantRate": float(hit_count == len(relevant)),
            "precision": _round(hit_count / k),
            "reciprocalRank": _round(1.0 / first) if first else 0.0,
            "ndcg": _round(_dcg(gains) / ideal_dcg) if ideal_dcg else 0.0,
            "averagePrecision": _round(precision_sum / ap_denominator)
            if ap_denominator
            else 0.0,
        }

    return {
        "applicable": True,
        "expectedNoResults": False,
        "returnedCount": len(ranked),
        "relevantCount": len(relevant),
        "noResultCorrect": None,
        "firstRelevantRank": relevant_positions[0] if relevant_positions else None,
        "lastRelevantRank": relevant_positions[-1] if relevant_positions else None,
        "recall90K": recall90_k,
        "metricsByK": metrics_by_k,
    }


def aggregate_ranking_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate rows produced by :func:`ranking_case_metrics`."""

    if not cases:
        raise ValueError("cannot aggregate an empty ranking case list")
    positive = [row for row in cases if bool(row.get("applicable"))]
    negative = [row for row in cases if bool(row.get("expectedNoResults"))]
    keys = sorted(
        {
            int(key)
            for row in positive
            for key in (row.get("metricsByK") or {}).keys()
        }
    )
    curves: dict[str, dict[str, float | int]] = {}
    for k in keys:
        rows = [row["metricsByK"][str(k)] for row in positive]
        curves[str(k)] = {
            "samples": len(rows),
            "recall": _round(sum(float(row["recall"]) for row in rows) / len(rows))
            if rows
            else 0.0,
            "hitRate": _round(sum(float(row["hitRate"]) for row in rows) / len(rows))
            if rows
            else 0.0,
            "allRelevantRate": _round(
                sum(float(row["allRelevantRate"]) for row in rows) / len(rows)
            )
            if rows
            else 0.0,
            "precision": _round(sum(float(row["precision"]) for row in rows) / len(rows))
            if rows
            else 0.0,
            "mrr": _round(
                sum(float(row["reciprocalRank"]) for row in rows) / len(rows)
            )
            if rows
            else 0.0,
            "ndcg": _round(sum(float(row["ndcg"]) for row in rows) / len(rows))
            if rows
            else 0.0,
            "map": _round(
                sum(float(row["averagePrecision"]) for row in rows) / len(rows)
            )
            if rows
            else 0.0,
        }

    first_ranks = [
        int(row["firstRelevantRank"])
        for row in positive
        if row.get("firstRelevantRank")
    ]
    last_ranks = [
        int(row["lastRelevantRank"])
        for row in positive
        if row.get("lastRelevantRank")
    ]
    saturation = [int(row["recall90K"]) for row in positive if row.get("recall90K")]
    return {
        "caseCount": len(cases),
        "rankingCaseCount": len(positive),
        "negativeCaseCount": len(negative),
        "metricCurves": curves,
        "noResultAccuracy": _round(
            sum(bool(row.get("noResultCorrect")) for row in negative) / len(negative)
        )
        if negative
        else None,
        "rankDistribution": {
            "firstRelevantP50": percentile([float(value) for value in first_ranks], 0.5),
            "firstRelevantP95": percentile([float(value) for value in first_ranks], 0.95),
            "lastRelevantP50": percentile([float(value) for value in last_ranks], 0.5),
            "lastRelevantP95": percentile([float(value) for value in last_ranks], 0.95),
            "recall90KP50": percentile([float(value) for value in saturation], 0.5),
            "recall90KP95": percentile([float(value) for value in saturation], 0.95),
            "recall90Coverage": _round(len(saturation) / len(positive))
            if positive
            else 0.0,
        },
    }


def incomplete_judgment_case_metrics(
    ranked_ids: Sequence[Any],
    relevance_grades: Mapping[Any, Any],
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    relevant_threshold: float = 1.0,
) -> dict[str, Any]:
    """Evaluate a full-catalog ranking without treating unjudged items as negative.

    ``knownRelevantRecall`` uses only judged relevant documents as its denominator.
    Rank-sensitive quality is reported on the condensed judged ranking, while
    ``judgedRate`` makes the incomplete-pool coverage visible. ``bpref`` follows
    the TREC definition and ignores unjudged documents entirely.
    """

    ks = sorted({max(1, int(value)) for value in k_values})
    if not ks:
        raise ValueError("k_values must not be empty")
    labels = _labels(relevance_grades)
    ranked = _deduplicate(ranked_ids)
    relevant = {item for item, grade in labels.items() if grade >= relevant_threshold}
    nonrelevant = set(labels) - relevant
    if not relevant:
        raise ValueError("incomplete-judgment query must contain judged relevant items")

    condensed = [item for item in ranked if item in labels]
    condensed_metrics = ranking_case_metrics(
        condensed,
        labels,
        k_values=ks,
        relevant_threshold=relevant_threshold,
        judged_pool=True,
    )

    nonrelevant_seen = 0
    preference_sum = 0.0
    denominator = min(len(relevant), len(nonrelevant))
    for item in ranked:
        if item not in labels:
            continue
        if item in nonrelevant:
            nonrelevant_seen += 1
            continue
        if item in relevant:
            preference_sum += (
                1.0
                if denominator == 0
                else 1.0 - min(nonrelevant_seen, len(relevant)) / denominator
            )
    bpref = preference_sum / len(relevant)

    metrics_by_k: dict[str, dict[str, Any]] = {}
    for k in ks:
        top = ranked[:k]
        judged_count = sum(item in labels for item in top)
        known_hits = len(set(top).intersection(relevant))
        condensed_row = condensed_metrics["metricsByK"][str(k)]
        metrics_by_k[str(k)] = {
            "knownRelevantRecall": _round(known_hits / len(relevant)),
            "knownRelevantPrecisionLowerBound": _round(known_hits / k),
            "hitRate": float(bool(known_hits)),
            "allKnownRelevantRate": float(known_hits == len(relevant)),
            "judgedRate": _round(judged_count / k),
            "judgedCount": judged_count,
            "unjudgedCount": len(top) - judged_count,
            "condensedNdcg": condensed_row["ndcg"],
            "condensedReciprocalRank": condensed_row["reciprocalRank"],
            "condensedAveragePrecision": condensed_row["averagePrecision"],
        }
    return {
        "applicable": True,
        "labelScope": "full-catalog-incomplete-qrels",
        "returnedCount": len(ranked),
        "judgedRelevantCount": len(relevant),
        "judgedNonrelevantCount": len(nonrelevant),
        "judgedRetrievedCount": len(condensed),
        "bpref": _round(bpref),
        "metricsByK": metrics_by_k,
    }


def aggregate_incomplete_judgment_cases(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate rows produced by :func:`incomplete_judgment_case_metrics`."""

    if not cases:
        raise ValueError("cannot aggregate an empty incomplete-judgment case list")
    keys = sorted(
        {
            int(key)
            for row in cases
            for key in (row.get("metricsByK") or {}).keys()
        }
    )
    curves: dict[str, dict[str, float | int]] = {}
    for k in keys:
        rows = [row["metricsByK"][str(k)] for row in cases]

        def mean(field: str) -> float:
            return _round(sum(float(row[field]) for row in rows) / len(rows))

        curves[str(k)] = {
            "samples": len(rows),
            "knownRelevantRecall": mean("knownRelevantRecall"),
            "knownRelevantPrecisionLowerBound": mean(
                "knownRelevantPrecisionLowerBound"
            ),
            "hitRate": mean("hitRate"),
            "allKnownRelevantRate": mean("allKnownRelevantRate"),
            "judgedRate": mean("judgedRate"),
            "condensedNdcg": mean("condensedNdcg"),
            "condensedMrr": mean("condensedReciprocalRank"),
            "condensedMap": mean("condensedAveragePrecision"),
        }
    return {
        "caseCount": len(cases),
        "labelScope": "full-catalog-incomplete-qrels",
        "metricCurves": curves,
        "bpref": _round(sum(float(row["bpref"]) for row in cases) / len(cases)),
        "judgedRelevantCount": sum(int(row["judgedRelevantCount"]) for row in cases),
        "judgedNonrelevantCount": sum(
            int(row["judgedNonrelevantCount"]) for row in cases
        ),
    }


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    seed: int = 20260813,
    iterations: int = 4_000,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Return a deterministic percentile-bootstrap confidence interval."""

    sample = [float(value) for value in values]
    if not sample:
        return {"samples": 0, "mean": None, "lower": None, "upper": None}
    if iterations < 100:
        raise ValueError("bootstrap iterations must be at least 100")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    rng = random.Random(seed)
    size = len(sample)
    estimates = [
        sum(sample[rng.randrange(size)] for _ in range(size)) / size
        for _ in range(iterations)
    ]
    estimates.sort()
    alpha = (1.0 - confidence) / 2.0

    def at(quantile: float) -> float:
        index = min(
            len(estimates) - 1,
            max(0, math.floor(quantile * (len(estimates) - 1))),
        )
        return estimates[index]

    return {
        "samples": size,
        "mean": _round(sum(sample) / size),
        "lower": _round(at(alpha)),
        "upper": _round(at(1.0 - alpha)),
        "confidence": confidence,
        "iterations": iterations,
        "seed": seed,
    }


def paired_ranking_comparison(
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
    *,
    metric_path: Sequence[str],
    case_id_field: str = "caseId",
    seed: int = 20260813,
) -> dict[str, Any]:
    """Compare two variants on exactly the same query IDs."""

    baseline_by_id = {str(row[case_id_field]): row for row in baseline}
    candidate_by_id = {str(row[case_id_field]): row for row in candidate}
    if set(baseline_by_id) != set(candidate_by_id):
        missing = sorted(set(baseline_by_id).symmetric_difference(candidate_by_id))
        raise ValueError(f"paired variants contain different case IDs: {missing[:5]}")

    def value(row: Mapping[str, Any]) -> float:
        current: Any = row
        for key in metric_path:
            if not isinstance(current, Mapping) or key not in current:
                raise ValueError(f"metric path {'/'.join(metric_path)} is missing")
            current = current[key]
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ValueError(f"metric path {'/'.join(metric_path)} is not numeric")
        return float(current)

    per_case: list[dict[str, Any]] = []
    differences: list[float] = []
    wins = ties = losses = 0
    for case_id in sorted(baseline_by_id):
        base = value(baseline_by_id[case_id])
        cand = value(candidate_by_id[case_id])
        delta = cand - base
        differences.append(delta)
        if delta > 1e-12:
            wins += 1
        elif delta < -1e-12:
            losses += 1
        else:
            ties += 1
        per_case.append(
            {
                "caseId": case_id,
                "baseline": _round(base),
                "candidate": _round(cand),
                "delta": _round(delta),
            }
        )
    ci = bootstrap_mean_ci(differences, seed=seed)
    return {
        "metric": ".".join(metric_path),
        "samples": len(differences),
        "meanDelta": ci["mean"],
        "winTieLoss": {"wins": wins, "ties": ties, "losses": losses},
        "confidenceInterval": ci,
        "statisticallySupported": bool(
            ci["lower"] is not None and ci["lower"] > 0
        ),
        "perCase": per_case,
    }


def aggregate_stage_latency(
    rows: Sequence[Mapping[str, Any]],
    *,
    stage_field: str = "stageLatencyMs",
) -> dict[str, Any]:
    """Aggregate independently measured stage latency without inventing samples."""

    stages = sorted(
        {
            str(stage)
            for row in rows
            for stage in ((row.get(stage_field) or {}).keys())
        }
    )
    result: dict[str, Any] = {}
    for stage in stages:
        values = [
            float((row.get(stage_field) or {})[stage])
            for row in rows
            if isinstance(row.get(stage_field), Mapping)
            and isinstance((row.get(stage_field) or {}).get(stage), (int, float))
            and not isinstance((row.get(stage_field) or {}).get(stage), bool)
        ]
        result[stage] = {
            "samples": len(values),
            "p50Ms": _round(percentile(values, 0.5)) if values else None,
            "p95Ms": _round(percentile(values, 0.95)) if values else None,
            "p99Ms": _round(percentile(values, 0.99)) if values else None,
            "maxMs": _round(max(values)) if values else None,
            "p99Reliable": len(values) >= 100,
        }
    return result
