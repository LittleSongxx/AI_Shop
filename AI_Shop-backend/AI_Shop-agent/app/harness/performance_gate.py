from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DeterministicThresholds:
    enqueue_p95_ms: float = 500
    queue_p95_ms: float = 1_000
    ttft_p95_ms: float = 2_000
    total_p95_ms: float = 5_000


def percentile(values: list[float], quantile: float = 0.95) -> float | None:
    if not values:
        return None
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    success = sum(item.get("terminalSuccess") is True and not item.get("error") for item in results)

    def distribution(field: str, *, suffix: str = "") -> dict[str, Any]:
        values = [float(item[field]) for item in results if item.get(field) is not None]
        return {
            "samples": len(values),
            f"p50{suffix}": round(percentile(values, 0.5) or 0.0, 3)
            if values
            else None,
            f"p95{suffix}": round(percentile(values) or 0.0, 3)
            if values
            else None,
            f"p99{suffix}": round(percentile(values, 0.99) or 0.0, 3)
            if values
            else None,
            f"max{suffix}": round(max(values), 3) if values else None,
        }

    input_tokens = sum(int(item.get("inputTokens") or 0) for item in results)
    output_tokens = sum(int(item.get("outputTokens") or 0) for item in results)
    total_cost = round(sum(float(item.get("costCny") or 0) for item in results), 8)

    return {
        "requests": total,
        "terminalSuccesses": success,
        "terminalSuccessRate": round(success / total, 4) if total else 0.0,
        "errorCount": total - success,
        "enqueue": distribution("enqueueMs", suffix="Ms"),
        "queue": distribution("queueMs", suffix="Ms"),
        "ttft": distribution("ttftMs", suffix="Ms"),
        "total": distribution("totalMs", suffix="Ms"),
        "steps": distribution("stepCount"),
        "modelCalls": distribution("modelCallCount"),
        "toolCalls": distribution("toolCallCount"),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": input_tokens + output_tokens,
        "costCny": total_cost,
        "costPerSuccessCny": round(total_cost / success, 8) if success else None,
        "sampleDisclosure": {
            "p99Reliable": total >= 100,
            "message": (
                None
                if total >= 100
                else "fewer than 100 samples; p99 is descriptive only"
            ),
        },
    }


def evaluate_performance_gate(
    summary: dict[str, Any],
    *,
    mode: str,
    thresholds: DeterministicThresholds | None = None,
    live_success_rate: float = 0.95,
) -> dict[str, Any]:
    mode = str(mode or "").lower()
    violations: list[str] = []
    requests = int(summary.get("requests") or 0)
    if requests <= 0:
        violations.append("no requests were measured")
    if mode == "live":
        actual = float(summary.get("terminalSuccessRate") or 0)
        if actual < live_success_rate:
            violations.append(f"terminal success rate {actual:.3f} < {live_success_rate:.3f}")
    elif mode == "deterministic":
        limits = thresholds or DeterministicThresholds()
        if int(summary.get("errorCount") or 0) != 0:
            violations.append(f"error count {summary.get('errorCount')} != 0")
        for name, limit in (
            ("enqueue", limits.enqueue_p95_ms),
            ("queue", limits.queue_p95_ms),
            ("ttft", limits.ttft_p95_ms),
            ("total", limits.total_p95_ms),
        ):
            metric = summary.get(name) or {}
            if int(metric.get("samples") or 0) != requests:
                violations.append(f"{name} has {metric.get('samples') or 0}/{requests} samples")
                continue
            actual = float(metric.get("p95Ms") or 0)
            if actual > limit:
                violations.append(f"{name} p95 {actual:.1f}ms > {limit:.1f}ms")
    else:
        raise ValueError("mode must be deterministic or live")
    return {
        "mode": mode,
        "passed": not violations,
        "violations": violations,
        "thresholds": (
            asdict(thresholds or DeterministicThresholds())
            if mode == "deterministic"
            else {"terminal_success_rate": live_success_rate}
        ),
    }
