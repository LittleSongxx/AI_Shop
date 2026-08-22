from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from evaluation.core.contracts import CaseResult, MetricEstimate


def _round(value: float) -> float:
    return round(float(value), 6)


def recall_at_k(ranking: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    relevant = {str(doc_id) for doc_id, grade in qrels.items() if int(grade) > 0}
    if not relevant:
        raise ValueError("recall is undefined without a relevant judgment")
    retrieved = {str(doc_id) for doc_id in ranking[:k]}
    return len(relevant.intersection(retrieved)) / len(relevant)


def reciprocal_rank_at_k(
    ranking: Sequence[str],
    qrels: Mapping[str, int],
    k: int,
) -> float:
    for rank, doc_id in enumerate(ranking[:k], 1):
        if int(qrels.get(str(doc_id), 0)) > 0:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranking: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    def gain(grade: int, rank: int) -> float:
        return (2 ** max(0, int(grade)) - 1) / math.log2(rank + 1)

    actual = sum(
        gain(int(qrels.get(str(doc_id), 0)), rank) for rank, doc_id in enumerate(ranking[:k], 1)
    )
    ideal_grades = sorted((int(value) for value in qrels.values()), reverse=True)[:k]
    ideal = sum(gain(grade, rank) for rank, grade in enumerate(ideal_grades, 1))
    return actual / ideal if ideal > 0 else 0.0


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires at least one observation")
    proportion = successes / total
    denominator = 1 + (z * z / total)
    centre = proportion + (z * z / (2 * total))
    margin = z * math.sqrt((proportion * (1 - proportion) / total) + (z * z / (4 * total * total)))
    return (
        max(0.0, (centre - margin) / denominator),
        min(1.0, (centre + margin) / denominator),
    )


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def bootstrap_interval(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float],
    *,
    samples: int,
    seed: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap interval requires at least one observation")
    if samples < 200:
        raise ValueError("bootstrap interval requires at least 200 resamples")
    rows = [float(value) for value in values]
    generator = random.Random(seed)
    estimates = [
        statistic([rows[generator.randrange(len(rows))] for _ in rows]) for _ in range(samples)
    ]
    alpha = (1 - confidence) / 2
    return percentile(estimates, alpha), percentile(estimates, 1 - alpha)


def _metric_seed(base_seed: int, name: str) -> int:
    suffix = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    return base_seed ^ suffix


def estimate_metric(
    name: str,
    values: Sequence[float],
    *,
    kind: str,
    aggregation: str,
    bootstrap_samples: int,
    bootstrap_seed: int,
    p99_minimum: int,
) -> MetricEstimate:
    rows = [float(value) for value in values]
    notes: list[str] = []
    if not rows:
        return MetricEstimate(
            name=name,
            value=0.0,
            sample_count=0,
            kind=kind,
            notes=("NO_ELIGIBLE_SAMPLES",),
        )
    if aggregation == "sum":
        return MetricEstimate(
            name=name,
            value=_round(sum(rows)),
            sample_count=len(rows),
            kind=kind,
        )
    if aggregation.startswith("p"):
        quantile = int(aggregation[1:]) / 100
        value = percentile(rows, quantile)
        lower, upper = bootstrap_interval(
            rows,
            lambda sample: percentile(sample, quantile),
            samples=bootstrap_samples,
            seed=_metric_seed(bootstrap_seed, name),
        )
        if aggregation == "p99" and len(rows) < p99_minimum:
            notes.append(f"DESCRIPTIVE_ONLY_SAMPLE_COUNT_BELOW_{p99_minimum}")
        return MetricEstimate(
            name=name,
            value=_round(value),
            sample_count=len(rows),
            kind=kind,
            interval_method="percentile-bootstrap",
            confidence_level=0.95,
            lower=_round(lower),
            upper=_round(upper),
            notes=tuple(notes),
        )
    mean = sum(rows) / len(rows)
    if kind == "binary":
        if any(value not in {0.0, 1.0} for value in rows):
            raise ValueError(f"binary metric {name} contains non-binary values")
        lower, upper = wilson_interval(round(sum(rows)), len(rows))
        return MetricEstimate(
            name=name,
            value=_round(mean),
            sample_count=len(rows),
            kind=kind,
            interval_method="wilson",
            confidence_level=0.95,
            lower=_round(lower),
            upper=_round(upper),
        )
    lower, upper = bootstrap_interval(
        rows,
        lambda sample: sum(sample) / len(sample),
        samples=bootstrap_samples,
        seed=_metric_seed(bootstrap_seed, name),
    )
    return MetricEstimate(
        name=name,
        value=_round(mean),
        sample_count=len(rows),
        kind=kind,
        interval_method="percentile-bootstrap",
        confidence_level=0.95,
        lower=_round(lower),
        upper=_round(upper),
    )


def aggregate_domain(
    results: Sequence[CaseResult],
    domain_config: Mapping[str, Any],
    statistical_policy: Mapping[str, Any],
) -> dict[str, Any]:
    estimates: dict[str, Any] = {}
    for name, metric_config in domain_config["metrics"].items():
        aggregation = str(metric_config["aggregation"])
        if str(metric_config["kind"]) == "latency":
            values = [
                float(result.latency_ms) for result in results if result.status.value != "ERROR"
            ]
        elif name == "executionRate":
            values = [0.0 if result.status.value == "ERROR" else 1.0 for result in results]
        elif name == "runtimeErrorCount":
            values = [1.0 if result.status.value == "ERROR" else 0.0 for result in results]
        else:
            values = [float(result.metrics[name]) for result in results if name in result.metrics]
        estimate = estimate_metric(
            name,
            values,
            kind=str(metric_config["kind"]),
            aggregation=aggregation,
            bootstrap_samples=int(statistical_policy["bootstrapSamples"]),
            bootstrap_seed=int(statistical_policy["bootstrapSeed"]),
            p99_minimum=int(statistical_policy["p99MinimumSampleCount"]),
        )
        estimates[name] = estimate.public()
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status.value] = status_counts.get(result.status.value, 0) + 1
    return {
        "caseCount": len(results),
        "statusCounts": status_counts,
        "metrics": estimates,
    }
