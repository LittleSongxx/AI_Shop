"""Collect, freeze and package the one-shot RAG v5 retrieval evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.artifacts import git_commit, workspace_sha256  # noqa: E402
from app.rag.canonical_facts import canonical_fact_catalog_scope  # noqa: E402
from app.rag.policy import RagRetrievalPolicy, runtime_rag_policy  # noqa: E402
from app.rag.retriever import evaluation_knowledge_release_scope  # noqa: E402
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    combined_sha,
    read_gzip_json,
    sha256_file,
)
from benchmarks.mature_eval.rag_context_index import prepare_context_index  # noqa: E402
from benchmarks.mature_eval.rag_v4_pipeline import (  # noqa: E402
    choose_rag_v4_configuration,
    collect_rag_v4_cases,
    replay_rag_v4_collection,
)
from benchmarks.mature_eval.rag_v5_dataset import (  # noqa: E402
    CATALOG_PATH,
    FACT_METADATA_PATH,
    RETRIEVAL_FRESH_PATH,
    RETRIEVAL_KNOWN_PATH,
    SUITE_LOCK_PATH,
    validate_rag_v5_files,
)
from benchmarks.run_rag_v4_eval import provider_completeness  # noqa: E402
from scripts.eval_rag import load_cases  # noqa: E402

SUITE = "rag-v5-retrieval"
RUN_ID_RE = re.compile(r"rag-v5-[0-9a-f]{7,40}-[0-9]{8}(?:-[a-z0-9-]+)?")
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / "rag-v5"
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / "rag-v5"
FRESH_EXECUTION_LOCK = RESULTS_ROOT / "_retrieval-fresh-execution-lock.json"
SELECTED_PATTERN = re.compile(
    r"production:n(?P<top_n>\d+):t(?P<threshold>\d+(?:\.\d+)?):"
    r"m(?P<margin>off|\d+(?:\.\d+)?)"
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not RUN_ID_RE.fullmatch(value):
        raise ValueError(
            "RAG v5 run-id must match rag-v5-<7..40 lowercase git hex>-<yyyymmdd>[-qualifier]"
        )
    return value


def _run_root(run_id: str) -> Path:
    return RESULTS_ROOT / run_id / "retrieval"


def _known_cases() -> list[dict[str, Any]]:
    rows = load_cases(RETRIEVAL_KNOWN_PATH)
    if len(rows) != 264:
        raise ValueError("RAG v5 known retrieval set must contain 264 cases")
    return rows


def _fresh_cases() -> list[dict[str, Any]]:
    rows = load_cases(RETRIEVAL_FRESH_PATH)
    if len(rows) != 48:
        raise ValueError("RAG v5 fresh retrieval set must contain 48 cases")
    return rows


def _metric(metrics: Mapping[str, Any], k: str, name: str) -> float:
    return float(((metrics.get("metricCurves") or {}).get(k) or {}).get(name) or 0)


def _policy_variant_key(policy: RagRetrievalPolicy) -> str:
    margin = "off" if policy.top_score_margin is None else f"{policy.top_score_margin:.2f}"
    return (
        f"production:n{policy.rerank_top_n}:"
        f"t{policy.evidence_threshold:.2f}:m{margin}"
    )


def _policy_from_selection(selected: str) -> RagRetrievalPolicy:
    match = SELECTED_PATTERN.fullmatch(str(selected or ""))
    if not match:
        raise ValueError(f"invalid frozen RAG v5 variant: {selected!r}")
    raw_margin = match.group("margin")
    return RagRetrievalPolicy(
        **{
            **runtime_rag_policy().__dict__,
            "rerank_top_n": int(match.group("top_n")),
            "evidence_threshold": float(match.group("threshold")),
            "top_score_margin": None if raw_margin == "off" else float(raw_margin),
        }
    ).validate()


def known_regression_guard(
    selected: Mapping[str, Any], baseline: Mapping[str, Any], *, max_drop: float = 0.05
) -> dict[str, Any]:
    pairs = {
        "recallAt3": (_metric(selected, "3", "recall"), _metric(baseline, "3", "recall")),
        "recallAt5": (_metric(selected, "5", "recall"), _metric(baseline, "5", "recall")),
        "mrrAt10": (_metric(selected, "10", "mrr"), _metric(baseline, "10", "mrr")),
        "ndcgAt5": (_metric(selected, "5", "ndcg"), _metric(baseline, "5", "ndcg")),
        "canonicalCorrectness": (
            float(selected.get("canonicalCitationCorrectness") or 0),
            float(baseline.get("canonicalCitationCorrectness") or 0),
        ),
        "canonicalCoverage": (
            float(selected.get("canonicalCitationCoverage") or 0),
            float(baseline.get("canonicalCitationCoverage") or 0),
        ),
    }
    rows = {
        name: {
            "selected": round(candidate, 6),
            "baseline": round(reference, 6),
            "delta": round(candidate - reference, 6),
            "maximumAllowedDrop": max_drop,
            "passed": candidate + max_drop >= reference,
        }
        for name, (candidate, reference) in pairs.items()
    }
    return {
        "passed": all(row["passed"] for row in rows.values()),
        "baselineScope": "same 264 known observations under frozen runtime policy",
        "metrics": rows,
    }


def retrieval_gate(
    fresh_metrics: Mapping[str, Any],
    fresh_provider: Mapping[str, Any],
    *,
    known_provider: Mapping[str, Any],
    regression_guard: Mapping[str, Any],
) -> dict[str, Any]:
    checks = {
        "allKnownCasesExecuted": int(known_provider.get("caseCount") or 0) == 264,
        "allFreshCasesExecuted": int(fresh_provider.get("caseCount") or 0) == 48,
        "knownProviderComplete": bool(known_provider.get("passed")),
        "freshProviderComplete": bool(fresh_provider.get("passed")),
        "knownRegressionMaxDrop": bool(regression_guard.get("passed")),
        "freshRecallAt3": _metric(fresh_metrics, "3", "recall") >= 0.90,
        "freshRecallAt5": _metric(fresh_metrics, "5", "recall") >= 0.95,
        "freshMrrAt10": _metric(fresh_metrics, "10", "mrr") >= 0.85,
        "freshNdcgAt5": _metric(fresh_metrics, "5", "ndcg") >= 0.85,
        "freshNoAnswerAccuracy": float(fresh_metrics.get("noAnswerAccuracy") or 0) >= 0.90,
        "freshInjectionAccuracy": float(fresh_metrics.get("injectionRobustness") or 0) == 1.0,
        "canonicalCorrectness": float(fresh_metrics.get("canonicalCitationCorrectness") or 0) >= 0.90,
        "canonicalCoverage": float(fresh_metrics.get("canonicalCitationCoverage") or 0) >= 0.90,
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "status": "PASSED" if passed else "FAILED_RETAINED",
        "checks": checks,
        "providerCompleteness": {"known": known_provider, "fresh": fresh_provider},
        "regressionGuard": regression_guard,
    }


def prepare(_args: argparse.Namespace) -> dict[str, Any]:
    validation = validate_rag_v5_files()
    return {
        "phase": "prepare",
        "suite": SUITE,
        "caseCounts": validation["suiteLock"]["caseCounts"],
        "catalogSha256": sha256_file(CATALOG_PATH),
        "factMetadataSha256": sha256_file(FACT_METADATA_PATH),
        "freshExecutionState": (
            _json(FRESH_EXECUTION_LOCK) if FRESH_EXECUTION_LOCK.is_file() else "NOT_EXECUTED"
        ),
    }


async def prepare_context(args: argparse.Namespace) -> dict[str, Any]:
    results = []
    for mode, target in (
        ("original", args.original_index),
        ("context_prefix", args.context_index),
    ):
        results.append(
            await prepare_context_index(
                source_index=args.source_index,
                target_index=target,
                mode=mode,
                limit=args.limit,
            )
        )
    return {"phase": "prepare-context", "indices": results}


async def collect_known(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    run_root = _run_root(run_id)
    frozen_path = run_root / "frozen-config.json"
    replay_path = run_root / "known-replay.json"
    raw_path = run_root / "raw" / "known-collection.json.gz"
    if frozen_path.is_file():
        if not replay_path.is_file() or not raw_path.is_file():
            raise ValueError("RAG v5 known collection is incomplete")
        frozen = _json(frozen_path)
        if frozen.get("runId") != run_id:
            raise ValueError("RAG v5 frozen run identity mismatch")
        return {"phase": "collect-known", "runId": run_id, "reused": True}
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_v4_cases(
            _known_cases(),
            output_path=raw_path,
            candidate_size=args.candidate_size,
            contextual_mode=args.contextual_mode,
            knowledge_index=args.knowledge_index,
        )
    finally:
        await redis_service.close()
    replay = replay_rag_v4_collection(collection, split_filter={"known_regression"})
    selected = choose_rag_v4_configuration(replay)
    selected_key = str(selected["selectedVariant"])
    baseline_key = _policy_variant_key(runtime_rag_policy())
    metrics = replay.get("variantMetrics") or {}
    if selected_key not in metrics or baseline_key not in metrics:
        raise ValueError("RAG v5 replay lacks selected or runtime baseline metrics")
    guard = known_regression_guard(metrics[selected_key], metrics[baseline_key])
    selected["parameters"] = {
        "rerankTopN": _policy_from_selection(selected_key).rerank_top_n,
        "evidenceThreshold": _policy_from_selection(selected_key).evidence_threshold,
        "topScoreMargin": _policy_from_selection(selected_key).top_score_margin,
    }
    atomic_write_json(replay_path, replay)
    provider = provider_completeness(
        collection.get("providerFacts") or {},
        len(collection.get("cases") or []),
        expected_case_count=264,
    )
    frozen = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "selectionData": "264 known RAG v4 observations; no RAG v5 fresh case",
        "knowledge": {
            "catalog": str(CATALOG_PATH.relative_to(REPO_ROOT)),
            "catalogSha256": sha256_file(CATALOG_PATH),
            "factMetadataSha256": sha256_file(FACT_METADATA_PATH),
            "releaseVersion": args.release_version,
        },
        "rag": selected,
        "runtimeBaseline": baseline_key,
        "knownRegressionGuard": guard,
        "candidateSize": int(collection.get("candidateSize") or args.candidate_size),
        "contextualMode": collection.get("contextualMode"),
        "knowledgeIndex": collection.get("knowledgeIndex"),
        "providerCompleteness": provider,
        "datasetSha256": combined_sha(
            [
                RETRIEVAL_KNOWN_PATH,
                RETRIEVAL_KNOWN_PATH.with_suffix(".lock.json"),
                CATALOG_PATH,
                FACT_METADATA_PATH,
                SUITE_LOCK_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
    }
    atomic_write_json(frozen_path, frozen)
    return {
        "phase": "collect-known",
        "runId": run_id,
        "selected": selected,
        "regressionGuard": guard,
        "providerCompleteness": provider,
    }


def _claim_fresh_execution(run_id: str) -> dict[str, Any]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    dataset_sha = combined_sha(
        [RETRIEVAL_FRESH_PATH, RETRIEVAL_FRESH_PATH.with_suffix(".lock.json")],
        relative_to=REPO_ROOT,
    )
    claim = {
        "schemaVersion": 1,
        "suite": SUITE,
        "runId": run_id,
        "datasetSha256": dataset_sha,
        "claimedAt": datetime.now(timezone.utc).isoformat(),
        "policy": "ONE_SHOT_FAIL_RETAINED",
    }
    try:
        descriptor = os.open(
            FRESH_EXECUTION_LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
        )
    except FileExistsError:
        existing = _json(FRESH_EXECUTION_LOCK)
        if existing.get("runId") != run_id or existing.get("datasetSha256") != dataset_sha:
            raise ValueError(
                "RAG v5 retrieval fresh data was already claimed by another retained run; "
                "create RAG v6 with a new holdout"
            ) from None
        return existing
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(claim, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return claim


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    run_root = _run_root(run_id)
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("collect and freeze RAG v5 known cases first")
    finalization_path = run_root / "finalization.json"
    raw_path = run_root / "raw" / "fresh-collection.json.gz"
    replay_path = run_root / "fresh-replay.json"
    if finalization_path.is_file():
        if not raw_path.is_file() or not replay_path.is_file():
            raise ValueError("RAG v5 retrieval finalization is incomplete")
        finalization = _json(finalization_path)
        if finalization.get("frozenConfigSha256") != sha256_file(frozen_path):
            raise ValueError("RAG v5 retrieval frozen-config SHA mismatch")
        return {"phase": "collect-final", "runId": run_id, "reused": True}
    frozen = _json(frozen_path)
    frozen_release = int(((frozen.get("knowledge") or {}).get("releaseVersion")) or 0)
    if args.release_version != frozen_release:
        raise ValueError("RAG v5 fresh collection must use the frozen knowledge release")
    claim = _claim_fresh_execution(run_id)
    selected_key = str((frozen.get("rag") or {}).get("selectedVariant") or "")
    policy = _policy_from_selection(selected_key)
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_v4_cases(
            _fresh_cases(),
            output_path=raw_path,
            candidate_size=int(frozen.get("candidateSize") or args.candidate_size),
            policy=policy,
            contextual_mode=str(frozen.get("contextualMode") or "context_prefix"),
            knowledge_index=frozen.get("knowledgeIndex"),
        )
    finally:
        await redis_service.close()
    replay = replay_rag_v4_collection(collection, split_filter={"fresh_holdout"})
    metrics = (replay.get("variantMetrics") or {}).get(selected_key)
    if not metrics:
        raise ValueError("RAG v5 fresh replay lacks the frozen configuration")
    atomic_write_json(replay_path, replay)
    fresh_provider = provider_completeness(
        collection.get("providerFacts") or {},
        len(collection.get("cases") or []),
        expected_case_count=48,
    )
    gate = retrieval_gate(
        metrics,
        fresh_provider,
        known_provider=frozen.get("providerCompleteness") or {},
        regression_guard=frozen.get("knownRegressionGuard") or {},
    )
    finalization = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "finalizedAt": datetime.now(timezone.utc).isoformat(),
        "freshHoldoutExecutedOnceByThisRun": True,
        "executionClaim": claim,
        "frozenConfigSha256": sha256_file(frozen_path),
        "freshDatasetSha256": sha256_file(RETRIEVAL_FRESH_PATH),
        "selectedVariant": selected_key,
        "qualityGate": gate,
    }
    atomic_write_json(finalization_path, finalization)
    return {
        "phase": "collect-final",
        "runId": run_id,
        "caseCount": len(collection.get("cases") or []),
        "qualityGate": gate,
    }


def _compact_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: {field: value for field, value in metrics.items() if field != "perCase"}
        for key, metrics in (report.get("variantMetrics") or {}).items()
    }


def _badcases(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            key: row.get(key)
            for key in (
                "caseId",
                "query",
                "expectedNoAnswer",
                "predictedNoAnswer",
                "recallAtK",
                "canonicalCitationCorrectness",
                "canonicalCitationCoverage",
                "injectionRobust",
                "error",
            )
            if row.get(key) is not None
        }
        for row in metrics.get("perCase") or []
        if not row.get("passed")
    ]


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_rag_v5_files()
    run_root = _run_root(run_id)
    required = {
        "frozen": run_root / "frozen-config.json",
        "knownReplay": run_root / "known-replay.json",
        "knownRaw": run_root / "raw" / "known-collection.json.gz",
        "finalization": run_root / "finalization.json",
        "freshReplay": run_root / "fresh-replay.json",
        "freshRaw": run_root / "raw" / "fresh-collection.json.gz",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete RAG v5 retrieval run: {missing}")
    frozen = _json(required["frozen"])
    known_replay = _json(required["knownReplay"])
    finalization = _json(required["finalization"])
    fresh_replay = _json(required["freshReplay"])
    fresh_raw = read_gzip_json(required["freshRaw"])
    selected = str((frozen.get("rag") or {}).get("selectedVariant") or "")
    known_metrics = (known_replay.get("variantMetrics") or {}).get(selected)
    fresh_metrics = (fresh_replay.get("variantMetrics") or {}).get(selected)
    if not known_metrics or not fresh_metrics:
        raise ValueError("RAG v5 package lacks frozen selected metrics")
    gate = finalization.get("qualityGate") or {}
    badcases = _badcases(fresh_metrics)
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC+local-live",
        "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
        "knowledge": frozen.get("knowledge"),
        "frozenConfiguration": frozen,
        "knownMetrics": {
            field: value for field, value in known_metrics.items() if field != "perCase"
        },
        "freshMetrics": {
            field: value for field, value in fresh_metrics.items() if field != "perCase"
        },
        "allVariantMetrics": {
            "known": _compact_metrics(known_replay),
            "fresh": _compact_metrics(fresh_replay),
        },
        "providerFacts": {
            "known": read_gzip_json(required["knownRaw"]).get("providerFacts"),
            "fresh": fresh_raw.get("providerFacts"),
        },
        "qualityGate": gate,
        "badcases": badcases,
        "honestBoundaries": [
            "All cases are SYNTHETIC evaluation prompts over a local knowledge release.",
            "Provider completeness requires cold embedding, rerank and query-expansion evidence with no fallback.",
            "Known regression compares the selected configuration with the frozen runtime policy on the same 264 observations.",
            "The run is local-live, not production traffic, and local latency is not a production SLO.",
            "A failed one-shot fresh result remains FAILED_RETAINED.",
        ],
    }
    evidence_dir = EVIDENCE_ROOT / run_id / "retrieval"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(evidence_dir / "badcases.json", badcases)
    manifest = {
        "schemaVersion": 5,
        "suite": SUITE,
        "runId": run_id,
        "status": gate.get("status"),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "badcasesSha256": sha256_file(evidence_dir / "badcases.json"),
        "suiteLockSha256": sha256_file(SUITE_LOCK_PATH),
        "freshExecutionLockSha256": sha256_file(FRESH_EXECUTION_LOCK),
        "localArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in required.values()
        },
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    report = [
        "# RAG v5 retrieval",
        "",
        f"- Run: `{run_id}`",
        f"- Quality gate: `{gate.get('status')}`",
        f"- Fresh Recall@3 / Recall@5: `{_metric(fresh_metrics, '3', 'recall')}` / `{_metric(fresh_metrics, '5', 'recall')}`",
        f"- Fresh MRR@10 / NDCG@5: `{_metric(fresh_metrics, '10', 'mrr')}` / `{_metric(fresh_metrics, '5', 'ndcg')}`",
        f"- No-answer / injection: `{fresh_metrics.get('noAnswerAccuracy')}` / `{fresh_metrics.get('injectionRobustness')}`",
        "- Evidence: `SYNTHETIC + local-live`; no production-effect claim.",
    ]
    atomic_write_bytes(evidence_dir / "report.md", ("\n".join(report) + "\n").encode("utf-8"))
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8"))
    return {
        "phase": "package",
        "runId": run_id,
        "evidenceDir": str(evidence_dir),
        "qualityGate": gate,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    subparsers.add_parser("prepare")
    context_parser = subparsers.add_parser("prepare-context")
    context_parser.add_argument("--source-index", required=True)
    context_parser.add_argument("--original-index", default="aishop_eval_rag_original_v5")
    context_parser.add_argument("--context-index", default="aishop_eval_rag_context_v5")
    context_parser.add_argument("--limit", type=int, default=500)
    for phase in ("collect-known", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--candidate-size", type=int, default=20)
        child.add_argument(
            "--contextual-mode",
            choices=("original", "context_prefix"),
            default="context_prefix",
        )
        child.add_argument("--knowledge-index", default="aishop_eval_rag_context_v5")
        if phase in {"collect-known", "collect-final"}:
            child.add_argument(
                "--release-version",
                type=int,
                required=True,
                help="immutable Java knowledge release containing catalog v2",
            )
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return prepare(args)
    if args.phase == "prepare-context":
        return await prepare_context(args)
    if args.phase == "collect-known":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_known(args)
    if args.phase == "collect-final":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_final(args)
    if args.phase == "package":
        return package(args)
    raise ValueError(f"unsupported RAG v5 retrieval phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        with canonical_fact_catalog_scope(CATALOG_PATH):
            result = asyncio.run(async_main(args))
    except Exception as exc:
        run_id = getattr(args, "run_id", None)
        if run_id and RUN_ID_RE.fullmatch(str(run_id)):
            failure_dir = _run_root(str(run_id))
            failure_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                failure_dir / "failure.json",
                {
                    "schemaVersion": 1,
                    "suite": SUITE,
                    "runId": run_id,
                    "phase": args.phase,
                    "status": "FAILED_RETAINED",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                    "recordedAt": datetime.now(timezone.utc).isoformat(),
                },
            )
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
