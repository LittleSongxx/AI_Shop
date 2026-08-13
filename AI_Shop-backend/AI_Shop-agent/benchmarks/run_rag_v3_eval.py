"""Collect, freeze, finalize and package the canonical-fact RAG v3 evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.evaluation.artifacts import git_commit, workspace_sha256  # noqa: E402
from app.rag.canonical_facts import DEFAULT_CATALOG_PATH  # noqa: E402
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.build_rag_v3_datasets import (  # noqa: E402
    FRESH_PATH,
    GENERATION_PATH,
    KNOWN_PATH,
    PUBLIC_PATH,
    finalize_holdout,
    sha256_file,
)
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    combined_sha,
)
from benchmarks.mature_eval.rag_pipeline import (  # noqa: E402
    RAG_V3_EXPERIMENT_INSTRUCTION,
    choose_rag_configuration,
    collect_rag_cases,
    load_rag_v3_sets,
    replay_rag_collection,
)

SUITE = "rag-retrieval-live-v3"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / SUITE
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70)
TOP_N_VALUES = (3, 6, 10)
MARGINS = (0.0, 0.05, 0.10)
RERANK_CHANNELS = ("rerank", "rerankExperimental")
K_VALUES = (1, 3, 5, 10, 20)
SELECTED_PATTERN = re.compile(
    r"production:n(?P<top_n>\d+):t(?P<threshold>\d+(?:\.\d+)?):"
    r"m(?P<margin>\d+(?:\.\d+)?):i(?P<instruction>base|exp)"
)


def parse_selected_variant(value: str) -> dict[str, Any]:
    match = SELECTED_PATTERN.fullmatch(str(value or ""))
    if not match:
        raise ValueError(f"invalid frozen RAG v3 variant: {value!r}")
    return {
        "variant": "production",
        "rerankTopN": int(match.group("top_n")),
        "evidenceThreshold": float(match.group("threshold")),
        "topScoreMargin": float(match.group("margin")),
        "instruction": match.group("instruction"),
        "rerankChannel": (
            "rerankExperimental"
            if match.group("instruction") == "exp"
            else "rerank"
        ),
    }


def _replay(collection: Path, splits: set[str]) -> dict[str, Any]:
    return replay_rag_collection(
        collection,
        rerank_top_n_values=TOP_N_VALUES,
        evidence_thresholds=THRESHOLDS,
        top_score_margins=MARGINS,
        rerank_channels=RERANK_CHANNELS,
        k_values=K_VALUES,
        split_filter=splits,
    )


async def collect_dev(args: argparse.Namespace) -> dict[str, Any]:
    sets = load_rag_v3_sets(PUBLIC_PATH, KNOWN_PATH)
    cases = [*sets["public"], *sets["known_regression"]]
    run_root = RESULTS_ROOT / args.run_id
    if (run_root / "frozen-config.json").exists():
        raise ValueError("dev configuration is already frozen; collection is immutable")
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_cases(
            cases,
            output_path=run_root / "raw" / "rag-v3-dev-collection.json.gz",
            candidate_size=args.candidate_size,
            collect_instruction_ablation=True,
        )
    finally:
        await redis_service.close()
    return {
        "phase": "collect-dev",
        "runId": args.run_id,
        "caseCount": len(collection["cases"]),
        "providerFacts": collection["providerFacts"],
    }


def replay_dev(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    collection = run_root / "raw" / "rag-v3-dev-collection.json.gz"
    frozen_path = run_root / "frozen-config.json"
    replay_path = run_root / "rag-v3-dev-replay.json"
    if frozen_path.is_file():
        if not replay_path.is_file():
            raise ValueError("frozen configuration exists without its dev replay")
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        if frozen.get("runId") != args.run_id:
            raise ValueError("frozen configuration run ID mismatch")
        return {
            "phase": "replay",
            "runId": args.run_id,
            "selected": frozen["rag"],
            "reusedFrozenConfig": True,
        }
    report = _replay(collection, {"public", "known_regression"})
    public_report = _replay(collection, {"public"})
    regression_report = _replay(collection, {"known_regression"})
    selected = choose_rag_configuration(report)
    parsed = parse_selected_variant(selected["selectedVariant"])
    selected["parameters"] = parsed
    selected["instructionText"] = (
        RAG_V3_EXPERIMENT_INSTRUCTION
        if parsed["instruction"] == "exp"
        else get_settings().rerank_instruct
    )
    selected_key = str(selected["selectedVariant"])
    settings = get_settings()
    baseline_report = replay_rag_collection(
        collection,
        rerank_top_n_values=(settings.rerank_top_n,),
        evidence_thresholds=(settings.rag_evidence_min_relevance,),
        top_score_margins=(settings.rag_evidence_top_score_margin,),
        rerank_channels=("rerank",),
        k_values=K_VALUES,
        split_filter={"known_regression"},
    )
    baseline_key = next(
        key
        for key in baseline_report["variantMetrics"]
        if key.startswith("production:")
    )
    selected_regression = regression_report["variantMetrics"][selected_key]
    baseline_regression = baseline_report["variantMetrics"][baseline_key]
    regression_guard = _regression_guard(selected_regression, baseline_regression)
    report["splitReports"] = {
        "public": public_report,
        "knownRegression": regression_report,
    }
    report["productionBaseline"] = {
        "variant": baseline_key,
        "metrics": baseline_regression,
    }
    atomic_write_json(replay_path, report)
    source_provider = (report.get("providerFacts") or {}).get(
        "sourceCollectionProviderFacts"
    ) or {}
    frozen = {
        "schemaVersion": 1,
        "suite": SUITE,
        "runId": args.run_id,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "selectionData": "48 public/dev + 64 known regression; no fresh holdout",
        "rag": selected,
        "devCompleteness": _provider_completeness(
            source_provider,
            len(selected["selectedMetrics"].get("perCase") or []),
            expected_case_count=112,
        ),
        "regressionGuard": regression_guard,
        "candidateSize": int(args.candidate_size),
        "datasetSha256": combined_sha(
            [
                PUBLIC_PATH,
                PUBLIC_PATH.with_suffix(".lock.json"),
                KNOWN_PATH,
                KNOWN_PATH.with_suffix(".lock.json"),
                DEFAULT_CATALOG_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
    }
    atomic_write_json(frozen_path, frozen)
    return {"phase": "replay", "runId": args.run_id, "selected": selected}


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_root = RESULTS_ROOT / args.run_id
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("run replay before finalizing the fresh holdout")
    finalization_path = run_root / "finalization.json"
    collection_path = run_root / "raw" / "rag-v3-final-collection.json.gz"
    if finalization_path.is_file() and not collection_path.is_file():
        raise ValueError("finalization exists without the locked raw collection")
    if finalization_path.is_file():
        replay_path = run_root / "rag-v3-final-replay.json"
        if not replay_path.is_file():
            raise ValueError("finalization exists without its fresh replay")
        finalized = json.loads(finalization_path.read_text(encoding="utf-8"))
        if finalized.get("frozenConfigSha256") != sha256_file(frozen_path):
            raise ValueError("finalization frozen-config SHA mismatch")
        report = json.loads(replay_path.read_text(encoding="utf-8"))
        selected = str(finalized.get("selectedVariant") or "")
        return {
            "phase": "collect-final",
            "runId": args.run_id,
            "caseCount": 32,
            "selectedMetrics": (report.get("variantMetrics") or {}).get(selected),
            "qualityGate": finalized["qualityGate"],
            "reusedFinalization": True,
        }
    if not FRESH_PATH.is_file():
        finalize_holdout(frozen_path)
    fresh_lock = json.loads(FRESH_PATH.with_suffix(".lock.json").read_text(encoding="utf-8"))
    if fresh_lock.get("frozenConfigSha256") != sha256_file(frozen_path):
        raise ValueError("fresh holdout was finalized against another frozen config")
    sets = load_rag_v3_sets(PUBLIC_PATH, KNOWN_PATH, FRESH_PATH)
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    frozen_rag = frozen["rag"]
    frozen_parameters = frozen_rag["parameters"]
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_cases(
            sets["fresh_holdout"],
            output_path=collection_path,
            candidate_size=int(frozen.get("candidateSize") or args.candidate_size),
            collect_instruction_ablation=False,
            primary_rerank_instruction=str(frozen_rag["instructionText"]),
            primary_rerank_channel=str(frozen_parameters["rerankChannel"]),
        )
    finally:
        await redis_service.close()
    report = _replay(collection_path, {"fresh_holdout"})
    atomic_write_json(run_root / "rag-v3-final-replay.json", report)
    selected_key = str(frozen["rag"]["selectedVariant"])
    selected_metrics = (report.get("variantMetrics") or {}).get(selected_key)
    if not selected_metrics:
        raise ValueError("fresh replay lacks the frozen selected configuration")
    gate = retrieval_gate(
        selected_metrics,
        collection["providerFacts"],
        len(collection["cases"]),
        dev_completeness=frozen.get("devCompleteness"),
        regression_guard=frozen.get("regressionGuard"),
    )
    atomic_write_json(
        finalization_path,
        {
            "schemaVersion": 1,
            "finalizedAt": datetime.now(timezone.utc).isoformat(),
            "freshHoldoutExecutedOnceByThisRun": True,
            "frozenConfigSha256": sha256_file(frozen_path),
            "freshDatasetSha256": sha256_file(FRESH_PATH),
            "selectedVariant": selected_key,
            "qualityGate": gate,
        },
    )
    return {
        "phase": "collect-final",
        "runId": args.run_id,
        "caseCount": len(collection["cases"]),
        "selectedMetrics": selected_metrics,
        "qualityGate": gate,
    }


def _provider_completeness(
    provider: dict[str, Any],
    case_count: int,
    *,
    expected_case_count: int,
) -> dict[str, Any]:
    embedding = provider.get("embedding") or {}
    rerank = provider.get("rerank") or {}
    expansion = provider.get("queryExpansion") or {}
    checks = {
        "caseCountComplete": case_count == expected_case_count,
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "embeddingProviderCalled": int(embedding.get("providerSuccesses") or 0) > 0,
        "rerankFallbackZero": int(rerank.get("fallbackCount") or 0) == 0,
        "rerankFailuresZero": int(rerank.get("providerFailures") or 0) == 0,
        "rerankProviderCalled": int(rerank.get("providerSuccesses") or 0) > 0,
        "queryExpansionFailuresZero": int(expansion.get("providerFailures") or 0) == 0,
    }
    return {
        "passed": all(checks.values()),
        "caseCount": case_count,
        "expectedCaseCount": expected_case_count,
        "checks": checks,
    }


def _regression_guard(
    selected: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, Any]:
    comparisons = {
        "recallAt5": (
            ((selected.get("metricCurves") or {}).get("5") or {}).get("recall"),
            ((baseline.get("metricCurves") or {}).get("5") or {}).get("recall"),
        ),
        "mrrAt10": (
            ((selected.get("metricCurves") or {}).get("10") or {}).get("mrr"),
            ((baseline.get("metricCurves") or {}).get("10") or {}).get("mrr"),
        ),
        "ndcgAt5": (
            ((selected.get("metricCurves") or {}).get("5") or {}).get("ndcg"),
            ((baseline.get("metricCurves") or {}).get("5") or {}).get("ndcg"),
        ),
        "canonicalCitationCoverage": (
            selected.get("canonicalCitationCoverage"),
            baseline.get("canonicalCitationCoverage"),
        ),
    }
    rows = {
        name: {
            "selected": float(values[0] or 0),
            "baseline": float(values[1] or 0),
            "delta": round(float(values[0] or 0) - float(values[1] or 0), 6),
            "passed": float(values[0] or 0) + 0.01 >= float(values[1] or 0),
        }
        for name, values in comparisons.items()
    }
    return {"passed": all(row["passed"] for row in rows.values()), "metrics": rows}


def retrieval_gate(
    metrics: dict[str, Any],
    provider: dict[str, Any],
    case_count: int,
    *,
    dev_completeness: dict[str, Any] | None = None,
    regression_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    curve3 = (metrics.get("metricCurves") or {}).get("3") or {}
    curve5 = (metrics.get("metricCurves") or {}).get("5") or {}
    curve10 = (metrics.get("metricCurves") or {}).get("10") or {}
    provider_gate = _provider_completeness(
        provider, case_count, expected_case_count=32
    )
    checks = {
        "caseCount": case_count == 32,
        "freshProviderComplete": provider_gate["passed"],
        "devProviderComplete": bool((dev_completeness or {}).get("passed")),
        "regressionGuard": bool((regression_guard or {}).get("passed")),
        "recallAt3": float(curve3.get("recall") or 0) >= 0.90,
        "recallAt5": float(curve5.get("recall") or 0) >= 0.95,
        "mrrAt10": float(curve10.get("mrr") or 0) >= 0.85,
        "ndcgAt5": float(curve5.get("ndcg") or 0) >= 0.85,
        "noAnswerAccuracy": float(metrics.get("noAnswerAccuracy") or 0) >= 0.90,
        "injectionRobustness": float(metrics.get("injectionRobustness") or 0) == 1.0,
        "canonicalCitationCorrectness": float(
            metrics.get("canonicalCitationCorrectness") or 0
        ) >= 0.90,
        "canonicalCitationCoverage": float(
            metrics.get("canonicalCitationCoverage") or 0
        ) >= 0.90,
    }
    return {
        "status": "PASSED" if all(checks.values()) else "FAILED_RETAINED",
        "passed": all(checks.values()),
        "checks": checks,
        "providerCompleteness": {
            "dev": dev_completeness,
            "fresh": provider_gate,
        },
        "regressionGuard": regression_guard,
    }


def _compact_metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {field: value for field, value in metrics.items() if field != "perCase"}
        for key, metrics in (report.get("variantMetrics") or {}).items()
    }


def _compact_deltas(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {field: value for field, value in value.items() if field != "perCase"}
        for key, value in (report.get("pairedDeltas") or {}).items()
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    required = [
        run_root / "rag-v3-dev-replay.json",
        run_root / "rag-v3-final-replay.json",
        run_root / "frozen-config.json",
        run_root / "finalization.json",
        run_root / "raw" / "rag-v3-dev-collection.json.gz",
        run_root / "raw" / "rag-v3-final-collection.json.gz",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete RAG v3 run: {missing}")
    dev = json.loads(required[0].read_text(encoding="utf-8"))
    final = json.loads(required[1].read_text(encoding="utf-8"))
    frozen = json.loads(required[2].read_text(encoding="utf-8"))
    finalization = json.loads(required[3].read_text(encoding="utf-8"))
    selected = str(frozen["rag"]["selectedVariant"])
    evidence_dir = EVIDENCE_ROOT / args.run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "gitCommit": frozen.get("gitCommit") or git_commit(REPO_ROOT),
        "workspaceSha256": frozen.get("workspaceSha256") or workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-live",
        "labelScope": "12-document canonical fact catalog; 48 public + 64 known regression + 32 one-time fresh holdout",
        "caseCounts": {"public": 48, "knownRegression": 64, "freshHoldout": 32, "total": 144},
        "selectedVariant": selected,
        "selectedDevMetrics": (dev.get("variantMetrics") or {}).get(selected),
        "selectedFreshMetrics": (final.get("variantMetrics") or {}).get(selected),
        "qualityGate": finalization["qualityGate"],
        "variantMetrics": {"dev": _compact_metrics(dev), "fresh": _compact_metrics(final)},
        "pairedDeltas": {"dev": _compact_deltas(dev), "fresh": _compact_deltas(final)},
        "confidenceIntervals": {
            "dev": dev.get("confidenceIntervals") or {},
            "fresh": final.get("confidenceIntervals") or {},
        },
        "stageLatency": {"dev": dev.get("stageLatency"), "fresh": final.get("stageLatency")},
        "providerFacts": {
            "dev": (dev.get("providerFacts") or {}).get("sourceCollectionProviderFacts"),
            "fresh": (final.get("providerFacts") or {}).get("sourceCollectionProviderFacts"),
        },
        "honestBoundaries": [
            "All 144 labels are SYNTHETIC and derived from the versioned catalog; the run is local-live, not real-user traffic.",
            "Fresh holdout was generated only after the dev configuration was frozen and was executed once by this run.",
            "Local P95/P99 are sample descriptions, not production SLOs; Provider cost is UNPRICED.",
            "No existing baseline was accepted or overwritten.",
        ],
    }
    atomic_write_json(evidence_dir / "summary.json", summary)
    source_sha = {str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in required}
    manifest = {
        "schemaVersion": 1,
        "suite": SUITE,
        "runId": args.run_id,
        "summaryPath": str((evidence_dir / "summary.json").relative_to(PROJECT_ROOT)),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "datasetSha256": combined_sha(
            [
                PUBLIC_PATH,
                PUBLIC_PATH.with_suffix(".lock.json"),
                KNOWN_PATH,
                KNOWN_PATH.with_suffix(".lock.json"),
                FRESH_PATH,
                FRESH_PATH.with_suffix(".lock.json"),
                GENERATION_PATH,
                GENERATION_PATH.with_suffix(".lock.json"),
                DEFAULT_CATALOG_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
        "sourceArtifacts": source_sha,
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    atomic_write_bytes(
        evidence_dir / "badcases.json",
        json.dumps(
            {
                split: [
                    {"caseId": row.get("caseId"), **row}
                    for row in ((metrics or {}).get("perCase") or [])
                    if not row.get("passed")
                ]
                for split, metrics in (
                    ("dev", (dev.get("variantMetrics") or {}).get(selected)),
                    ("fresh", (final.get("variantMetrics") or {}).get(selected)),
                )
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        + b"\n",
    )
    status = finalization["qualityGate"]["status"]
    atomic_write_bytes(
        evidence_dir / "report.md",
        (
            "# RAG retrieval live v3\n\n"
            f"- Run: `{args.run_id}`\n"
            "- Evidence: `SYNTHETIC + local-live`\n"
            "- Cases: 144 (48 public, 64 known regression, 32 fresh holdout)\n"
            f"- Frozen variant: `{selected}`\n"
            f"- Quality gate: `{status}`\n"
            "- Baseline: unchanged; cost: `UNPRICED`; local latency is not an SLO.\n"
        ).encode("utf-8"),
    )
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode())
    return {"phase": "package", "evidenceDir": str(evidence_dir), "manifest": manifest}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    for phase in ("collect-dev", "replay", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--candidate-size", type=int, default=20)
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "collect-dev":
        return await collect_dev(args)
    if args.phase == "replay":
        return replay_dev(args)
    if args.phase == "collect-final":
        return await collect_final(args)
    if args.phase == "package":
        return package(args)
    raise ValueError(f"unsupported phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(async_main(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
