"""Run process-isolated Agentic Commerce ablations and enforce paired gates."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results"
BASELINES_ROOT = PROJECT_ROOT / "benchmarks" / "baselines"
VARIANT_SCRIPT = Path(__file__).with_name("run_ablation_variant.py")


def _percent(value: Any) -> float:
    return float(value or 0.0)


def _metric(summary: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = summary
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return float(value) if value is not None else None


def _paired_bootstrap_ci(
    differences: list[float], *, samples: int = 2000
) -> tuple[float, float]:
    if not differences:
        return 0.0, 0.0
    # Deterministic LCG avoids another dependency and keeps reports reproducible.
    state = 0xA15EED
    means: list[float] = []
    for _ in range(samples):
        selected: list[float] = []
        for _item in differences:
            state = (1103515245 * state + 12345) % (2**31)
            selected.append(differences[state % len(differences)])
        means.append(sum(selected) / len(selected))
    means.sort()
    low = means[max(0, math.floor(samples * 0.025) - 1)]
    high = means[min(samples - 1, math.ceil(samples * 0.975) - 1)]
    return round(low, 6), round(high, 6)


def _load_run(suite: str, run_id: str) -> dict[str, Any]:
    path = RESULTS_ROOT / suite / run_id / "summary.json"
    if not path.is_file():
        raise ValueError(f"ablation result missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _launch(variant: str, run_id: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            str(VARIANT_SCRIPT),
            "--variant",
            variant,
            "--run-id",
            run_id,
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return _load_run(f"agentic-commerce-v2-ablation-{variant}", run_id)


def _case_map(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(case["caseId"]): case for case in run.get("cases") or []}


def compare_runs(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    deterministic_latency_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    paired_ids = sorted(set(baseline_cases) & set(candidate_cases))
    success_differences = [
        float(candidate_cases[case_id].get("taskSuccess") is True)
        - float(baseline_cases[case_id].get("taskSuccess") is True)
        for case_id in paired_ids
    ]
    latency_differences = [
        float(candidate_cases[case_id].get("latencyMs") or 0)
        - float(baseline_cases[case_id].get("latencyMs") or 0)
        for case_id in paired_ids
    ]
    success_ci = _paired_bootstrap_ci(success_differences)
    latency_ci = _paired_bootstrap_ci(latency_differences)
    baseline_summary = baseline["summary"]
    candidate_summary = candidate["summary"]
    success_delta = _percent(candidate_summary.get("taskSuccessRate")) - _percent(
        baseline_summary.get("taskSuccessRate")
    )
    gates: list[str] = []
    expected_count = len(candidate_cases)
    if int(candidate_summary.get("executedCount") or 0) != expected_count:
        gates.append("not all candidate cases executed")
    if int(candidate_summary.get("criticalSafetyViolationCount") or 0) != 0:
        gates.append("candidate has critical safety violations")
    if success_delta < -0.02:
        gates.append(f"success rate delta {success_delta:.4f} is below -0.02")

    locked = deterministic_latency_baseline
    if locked:
        for metric_path, label in (
            (("latency", "p95Ms"), "p95 latency"),
            (("latency", "p99Ms"), "p99 latency"),
            (("costPerSuccessfulTaskCny",), "cost per success"),
        ):
            reference = _metric(locked, metric_path)
            actual = _metric(candidate_summary, metric_path)
            if reference is None or actual is None:
                gates.append(f"{label} is missing")
            elif reference == 0:
                if actual > 0:
                    gates.append(f"{label} {actual} exceeds zero-cost locked baseline")
            elif actual > reference * 1.15:
                gates.append(
                    f"{label} {actual:.6f} exceeds 1.15x baseline {reference:.6f}"
                )
    return {
        "pairedCases": len(paired_ids),
        "successRateDelta": round(success_delta, 6),
        "successDelta95Ci": success_ci,
        "meanLatencyDeltaMs": round(
            sum(latency_differences) / len(latency_differences), 6
        )
        if latency_differences
        else None,
        "latencyDelta95CiMs": latency_ci,
        "criticalSafetyViolations": candidate_summary.get(
            "criticalSafetyViolationCount"
        ),
        "gateFailures": gates,
        "passed": not gates,
    }


def _load_locked_baseline() -> dict[str, Any] | None:
    path = BASELINES_ROOT / "agentic-commerce-v2-ablation-multi-agent.lock.json"
    if not path.is_file():
        return None
    lock = json.loads(path.read_text(encoding="utf-8"))
    metrics = lock.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("ablation baseline lock has no metrics")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--require-locked-baseline", action="store_true")
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runs = {
        variant: _launch(variant, f"{run_id}-{variant}")
        for variant in ("legacy-single-agent", "multi-agent", "workflow")
    }
    locked = _load_locked_baseline()
    if args.require_locked_baseline and locked is None:
        raise SystemExit("locked multi-agent ablation baseline is required")
    main_comparison = compare_runs(
        runs["legacy-single-agent"],
        runs["multi-agent"],
        deterministic_latency_baseline=locked,
    )
    workflow_comparison = compare_runs(
        runs["multi-agent"],
        runs["workflow"],
        deterministic_latency_baseline=None,
    )
    report = {
        "schemaVersion": "aishop-eval/v1",
        "runId": run_id,
        "processIsolated": True,
        "cacheSharedAcrossVariants": False,
        "variants": {
            name: {
                "suite": run["metadata"]["suite"],
                "runId": run["metadata"]["runId"],
                "summary": run["summary"],
            }
            for name, run in runs.items()
        },
        "mainExperiment": {
            "baseline": "legacy-single-agent",
            "candidate": "multi-agent",
            **main_comparison,
        },
        "secondaryExperiment": {
            "baseline": "multi-agent",
            "candidate": "workflow",
            "scope": "deterministic applicable subsets only",
            **workflow_comparison,
        },
        "lockedBaselineApplied": locked is not None,
    }
    output_dir = RESULTS_ROOT / "agentic-commerce-v2-ablation" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        "\n".join(
            [
                "# Agentic Commerce v2 ablation",
                "",
                f"- Run: `{run_id}`",
                "- Variants executed in independent processes: yes",
                "- Cross-variant process cache reuse: no",
                f"- Main paired cases: {main_comparison['pairedCases']}",
                f"- Main success delta: {main_comparison['successRateDelta']}",
                f"- Main success 95% CI: {main_comparison['successDelta95Ci']}",
                f"- Main gate: {'PASS' if main_comparison['passed'] else 'FAIL'}",
                f"- Workflow paired cases: {workflow_comparison['pairedCases']}",
                f"- Workflow success delta: {workflow_comparison['successRateDelta']}",
                f"- Workflow gate: {'PASS' if workflow_comparison['passed'] else 'FAIL'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not main_comparison["passed"] or not workflow_comparison["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
