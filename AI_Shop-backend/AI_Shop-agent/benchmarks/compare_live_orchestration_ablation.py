#!/usr/bin/env python3
"""Compare three real full-stack orchestration runs on one frozen task suite."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SUITE = "orchestration-live-ablation-v1"
EXPECTED_MODES = ("workflow", "single_agent", "multi_agent")
RESULTS_ROOT = Path(__file__).with_name("results") / SUITE


class AblationContractError(ValueError):
    pass


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AblationContractError(f"cannot read live report: {path}") from exc
    if not isinstance(report, dict):
        raise AblationContractError(f"report must be a JSON object: {path}")
    return report


def _case_map(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    cases = report.get("cases") or []
    return {str(case.get("caseId") or ""): case for case in cases}


def validate_reports(reports: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    dataset_hashes: set[str] = set()
    snapshots: set[str] = set()
    model_sets: set[tuple[str, ...]] = set()
    case_sets: set[tuple[str, ...]] = set()
    provider_fingerprints: set[str] = set()
    for mode in EXPECTED_MODES:
        report = reports.get(mode) or {}
        metadata = report.get("metadata") or {}
        summary = report.get("summary") or {}
        if report.get("schemaVersion") != "aishop-live-eval/v1":
            errors.append(f"{mode}: unsupported report schema")
        if metadata.get("executionMode") != "LIVE_FULL_STACK":
            errors.append(f"{mode}: executionMode is not LIVE_FULL_STACK")
        if metadata.get("simulated") is not False:
            errors.append(f"{mode}: simulated flag must be false")
        if metadata.get("configuredOrchestrationMode") != mode:
            errors.append(f"{mode}: configured orchestration mode was not observed")
        digest = str(metadata.get("datasetSha256") or "")
        if not digest:
            errors.append(f"{mode}: dataset SHA is missing")
        dataset_hashes.add(digest)
        snapshot = str(metadata.get("fixtureSnapshotId") or "")
        if not snapshot:
            errors.append(f"{mode}: fixtureSnapshotId is required for paired comparison")
        snapshots.add(snapshot)
        models = tuple(sorted(str(item) for item in summary.get("observedModels") or []))
        if not models:
            errors.append(f"{mode}: no real model was observed")
        model_sets.add(models)
        cases = _case_map(report)
        if "" in cases or len(cases) != int(metadata.get("caseCount") or 0):
            errors.append(f"{mode}: case IDs/count are incomplete")
        case_sets.add(tuple(sorted(cases)))
        if float(summary.get("executionCompletenessRate") or 0) != 1.0:
            errors.append(f"{mode}: execution completeness is not 100%")
        if float(summary.get("providerCompletenessRate") or 0) != 1.0:
            errors.append(f"{mode}: provider completeness is not 100%")
        preflight = report.get("providerPreflight") or {}
        dependencies = preflight.get("dependencies") or {}
        provider_projection = {
            key: dependencies.get(key)
            for key in (
                "llm",
                "embeddingProvider",
                "embeddingProductionReady",
                "rerank",
                "javaGateway",
                "mcp",
            )
        }
        provider_fingerprints.add(
            json.dumps(provider_projection, ensure_ascii=True, sort_keys=True)
        )
    if len(dataset_hashes) != 1:
        errors.append("dataset SHA differs across modes")
    if len(snapshots) != 1:
        errors.append("fixture snapshot differs across modes")
    if len(model_sets) != 1:
        errors.append("observed model set differs across modes")
    if len(case_sets) != 1:
        errors.append("paired case IDs differ across modes")
    if len(provider_fingerprints) != 1:
        errors.append("provider preflight fingerprint differs across modes")
    if errors:
        raise AblationContractError("live ablation contract invalid:\n- " + "\n- ".join(errors))
    return {
        "datasetSha256": next(iter(dataset_hashes)),
        "fixtureSnapshotId": next(iter(snapshots)),
        "observedModels": list(next(iter(model_sets))),
        "caseIds": list(next(iter(case_sets))),
        "providerFingerprint": next(iter(provider_fingerprints)),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _paired_bootstrap_ci(values: list[float], samples: int = 2000) -> list[float] | None:
    if not values:
        return None
    state = 0xA15EED
    means: list[float] = []
    for _ in range(samples):
        selected: list[float] = []
        for _value in values:
            state = (1103515245 * state + 12345) % (2**31)
            selected.append(values[state % len(values)])
        means.append(sum(selected) / len(selected))
    means.sort()
    low = means[max(0, math.floor(samples * 0.025) - 1)]
    high = means[min(samples - 1, math.ceil(samples * 0.975) - 1)]
    return [round(low, 6), round(high, 6)]


def _numeric_case_metric(case: Mapping[str, Any], key: str) -> float | None:
    value = (case.get("metrics") or {}).get(key)
    return float(value) if value is not None else None


def compare_pair(
    baseline_name: str,
    candidate_name: str,
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = _case_map(reports[baseline_name])
    candidate = _case_map(reports[candidate_name])
    case_ids = sorted(baseline)
    success_deltas = [
        float(candidate[case_id].get("taskSuccess") is True)
        - float(baseline[case_id].get("taskSuccess") is True)
        for case_id in case_ids
    ]
    latency_deltas: list[float] = []
    token_deltas: list[float] = []
    cost_deltas: list[float] = []
    for case_id in case_ids:
        baseline_latency = _numeric_case_metric(baseline[case_id], "latencyMs")
        candidate_latency = _numeric_case_metric(candidate[case_id], "latencyMs")
        if baseline_latency is not None and candidate_latency is not None:
            latency_deltas.append(candidate_latency - baseline_latency)
        baseline_tokens = sum(
            _numeric_case_metric(baseline[case_id], key) or 0
            for key in ("inputTokens", "outputTokens")
        )
        candidate_tokens = sum(
            _numeric_case_metric(candidate[case_id], key) or 0
            for key in ("inputTokens", "outputTokens")
        )
        token_deltas.append(candidate_tokens - baseline_tokens)
        baseline_cost = _numeric_case_metric(baseline[case_id], "costCny") or 0
        candidate_cost = _numeric_case_metric(candidate[case_id], "costCny") or 0
        cost_deltas.append(candidate_cost - baseline_cost)
    return {
        "baseline": baseline_name,
        "candidate": candidate_name,
        "pairedCases": len(case_ids),
        "meanTaskSuccessDelta": _mean(success_deltas),
        "taskSuccessDelta95Ci": _paired_bootstrap_ci(success_deltas),
        "meanLatencyDeltaMs": _mean(latency_deltas),
        "latencyDelta95CiMs": _paired_bootstrap_ci(latency_deltas),
        "meanTokenDelta": _mean(token_deltas),
        "meanCostDeltaCny": _mean(cost_deltas),
    }


def build_comparison(reports: Mapping[str, Mapping[str, Any]], run_id: str) -> dict[str, Any]:
    contract = validate_reports(reports)
    return {
        "schemaVersion": "aishop-live-ablation/v1",
        "metadata": {
            "suite": SUITE,
            "runId": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "executionMode": "LIVE_FULL_STACK_PAIRED",
            "simulated": False,
            **contract,
        },
        "variants": {
            mode: {
                "runId": reports[mode]["metadata"].get("runId"),
                "summary": reports[mode]["summary"],
            }
            for mode in EXPECTED_MODES
        },
        "comparisons": [
            compare_pair("workflow", "single_agent", reports),
            compare_pair("workflow", "multi_agent", reports),
            compare_pair("single_agent", "multi_agent", reports),
        ],
        "interpretation": (
            "Paired descriptive comparison only. No mode is declared superior without "
            "both a task-success gain and acceptable latency/cost trade-off."
        ),
    }


def write_report(report: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Live orchestration ablation",
        "",
        f"- Run: `{report['metadata']['runId']}`",
        f"- Dataset SHA-256: `{report['metadata']['datasetSha256']}`",
        f"- Fixture snapshot: `{report['metadata']['fixtureSnapshotId']}`",
        f"- Models: {', '.join(report['metadata']['observedModels'])}",
        f"- Paired cases: {len(report['metadata']['caseIds'])}",
        "- Simulated: no",
        "",
        "## Pairwise results",
        "",
    ]
    for comparison in report["comparisons"]:
        lines.append(
            "- "
            f"{comparison['baseline']} -> {comparison['candidate']}: "
            f"success delta {comparison['meanTaskSuccessDelta']}, "
            f"latency delta {comparison['meanLatencyDeltaMs']} ms, "
            f"token delta {comparison['meanTokenDelta']}"
        )
    lines.extend(["", report["interpretation"]])
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--single-agent", type=Path, required=True)
    parser.add_argument("--multi-agent", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        reports = {
            "workflow": load_report(args.workflow),
            "single_agent": load_report(args.single_agent),
            "multi_agent": load_report(args.multi_agent),
        }
        report = build_comparison(reports, run_id)
        output_dir = RESULTS_ROOT / run_id
        write_report(report, output_dir)
    except AblationContractError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps({"resultDir": str(output_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
