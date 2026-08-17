"""Collect, freeze, finalize and package the production-aligned RAG v4 evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
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
from app.rag.canonical_facts import (  # noqa: E402
    LEGACY_V1_CATALOG_PATH,
    canonical_fact_catalog_scope,
)
from app.rag.fact_metadata import LEGACY_V1_FACT_METADATA_PATH  # noqa: E402
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
from benchmarks.mature_eval.rag_context_index import (  # noqa: E402
    prepare_context_index,
)
from benchmarks.mature_eval.rag_v4_pipeline import (  # noqa: E402
    choose_rag_v4_configuration,
    collect_rag_v4_cases,
    load_rag_v4_sets,
    replay_rag_v4_collection,
)

SUITE = "rag-retrieval-live-v4"
V4_CATALOG_PATH = LEGACY_V1_CATALOG_PATH
V4_FACT_METADATA_PATH = LEGACY_V1_FACT_METADATA_PATH
DATASETS_ROOT = PROJECT_ROOT / "benchmarks" / "datasets"
PUBLIC_PATH = DATASETS_ROOT / "rag_v4_public.jsonl"
KNOWN_PATH = DATASETS_ROOT / "rag_v4_known_regression.jsonl"
FRESH_PATH = DATASETS_ROOT / "rag_v4_fresh_holdout.jsonl"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / SUITE
SELECTED_PATTERN = re.compile(
    r"production:n(?P<top_n>\d+):t(?P<threshold>\d+(?:\.\d+)?):"
    r"m(?P<margin>off|\d+(?:\.\d+)?)"
)
TARGETED_POSTFIX_CASE_IDS = (
    "rag-v4-fresh-extra-001",
    "rag-v4-fresh-extra-016",
    "rag-v4-fresh-extra-020",
    "rag-v4-fresh-extra-021",
    "rag-v4-fresh-extra-024",
    "rag-v4-fresh-extra-030",
    "rag-v4-fresh-extra-032",
)
TARGETED_LABEL_LIMIT_CASE_IDS = frozenset(
    {"rag-v4-fresh-extra-021", "rag-v4-fresh-extra-024"}
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _verify_lock(dataset: Path, expected_count: int, split: str) -> dict[str, Any]:
    lock_path = dataset.with_suffix(".lock.json")
    lock = _load_json(lock_path)
    if sha256_file(dataset) != lock.get("datasetSha256"):
        raise ValueError(f"RAG v4 dataset SHA mismatch: {dataset.name}")
    if int(lock.get("caseCount") or 0) != expected_count or lock.get("split") != split:
        raise ValueError(f"RAG v4 dataset lock contract changed: {dataset.name}")
    return lock


def prepare(_args: argparse.Namespace) -> dict[str, Any]:
    sets = load_rag_v4_sets(PUBLIC_PATH, KNOWN_PATH, FRESH_PATH)
    locks = {
        "public": _verify_lock(PUBLIC_PATH, 72, "public"),
        "knownRegression": _verify_lock(KNOWN_PATH, 144, "known_regression"),
        "freshHoldout": _verify_lock(FRESH_PATH, 48, "fresh_holdout"),
    }
    fact_metadata = _load_json(V4_FACT_METADATA_PATH)
    if fact_metadata.get("canonicalCatalogSha256") != sha256_file(
        V4_CATALOG_PATH
    ):
        raise ValueError("fact metadata is not bound to the active canonical catalog")
    return {
        "phase": "prepare",
        "suite": SUITE,
        "caseCounts": {name: len(rows) for name, rows in sets.items()},
        "totalEvaluationObservations": sum(len(rows) for rows in sets.values()),
        "locks": locks,
        "policy": runtime_rag_policy().public(),
        "factCount": len(fact_metadata.get("facts") or []),
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


def parse_selected_variant(value: str) -> dict[str, Any]:
    match = SELECTED_PATTERN.fullmatch(str(value or ""))
    if not match:
        raise ValueError(f"invalid frozen RAG v4 variant: {value!r}")
    raw_margin = match.group("margin")
    return {
        "rerankTopN": int(match.group("top_n")),
        "evidenceThreshold": float(match.group("threshold")),
        "topScoreMargin": None if raw_margin == "off" else float(raw_margin),
    }


def _policy_from_selection(selected: str) -> RagRetrievalPolicy:
    parameters = parse_selected_variant(selected)
    return RagRetrievalPolicy(
        **{
            **runtime_rag_policy().__dict__,
            "rerank_top_n": parameters["rerankTopN"],
            "evidence_threshold": parameters["evidenceThreshold"],
            "top_score_margin": parameters["topScoreMargin"],
        }
    ).validate()


def _policy_variant_key(policy: RagRetrievalPolicy) -> str:
    margin = (
        "off"
        if policy.top_score_margin is None
        else f"{policy.top_score_margin:.2f}"
    )
    return (
        f"production:n{policy.rerank_top_n}:"
        f"t{policy.evidence_threshold:.2f}:m{margin}"
    )


async def collect_dev(args: argparse.Namespace) -> dict[str, Any]:
    sets = load_rag_v4_sets(PUBLIC_PATH, KNOWN_PATH, FRESH_PATH)
    cases = [*sets["public"], *sets["known_regression"]]
    run_root = RESULTS_ROOT / args.run_id
    if (run_root / "frozen-config.json").exists():
        raise ValueError("RAG v4 dev configuration is already frozen")
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_v4_cases(
            cases,
            output_path=run_root / "raw" / "rag-v4-dev-collection.json.gz",
            candidate_size=args.candidate_size,
            contextual_mode=args.contextual_mode,
            knowledge_index=args.knowledge_index,
        )
    finally:
        await redis_service.close()
    return {
        "phase": "collect-dev",
        "runId": args.run_id,
        "caseCount": len(collection.get("cases") or []),
        "providerFacts": collection.get("providerFacts"),
        "contextualMode": args.contextual_mode,
    }


def _split_replay(collection: Path, splits: set[str]) -> dict[str, Any]:
    return replay_rag_v4_collection(collection, split_filter=splits)


def _metric_value(metrics: Mapping[str, Any], k: str, name: str) -> float:
    return float(((metrics.get("metricCurves") or {}).get(k) or {}).get(name) or 0)


def _regression_guard(
    selected: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    pairs = {
        "recallAt5": (_metric_value(selected, "5", "recall"), _metric_value(baseline, "5", "recall")),
        "mrrAt10": (_metric_value(selected, "10", "mrr"), _metric_value(baseline, "10", "mrr")),
        "ndcgAt5": (_metric_value(selected, "5", "ndcg"), _metric_value(baseline, "5", "ndcg")),
        "canonicalCitationCoverage": (
            float(selected.get("canonicalCitationCoverage") or 0),
            float(baseline.get("canonicalCitationCoverage") or 0),
        ),
    }
    rows = {
        name: {
            "selected": candidate,
            "baseline": reference,
            "delta": round(candidate - reference, 6),
            "passed": candidate + 0.01 >= reference,
        }
        for name, (candidate, reference) in pairs.items()
    }
    return {"passed": all(row["passed"] for row in rows.values()), "metrics": rows}


def replay(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    raw = run_root / "raw" / "rag-v4-dev-collection.json.gz"
    if not raw.is_file():
        raise ValueError("collect RAG v4 dev cases before replay")
    frozen_path = run_root / "frozen-config.json"
    replay_path = run_root / "rag-v4-dev-replay.json"
    if frozen_path.is_file():
        frozen = _load_json(frozen_path)
        if not replay_path.is_file() or frozen.get("runId") != args.run_id:
            raise ValueError("RAG v4 frozen configuration is incomplete")
        return {"phase": "replay", "runId": args.run_id, "selected": frozen["rag"], "reusedFrozenConfig": True}
    report = _split_replay(raw, {"public", "known_regression"})
    public_report = _split_replay(raw, {"public"})
    regression_report = _split_replay(raw, {"known_regression"})
    selected = choose_rag_v4_configuration(report)
    selected_key = str(selected["selectedVariant"])
    selected["parameters"] = parse_selected_variant(selected_key)
    runtime_policy = runtime_rag_policy()
    baseline_key = _policy_variant_key(runtime_policy)
    regression_metrics = regression_report.get("variantMetrics") or {}
    if baseline_key not in regression_metrics or selected_key not in regression_metrics:
        raise ValueError("RAG v4 replay is missing selected or runtime baseline metrics")
    guard = _regression_guard(
        regression_metrics[selected_key], regression_metrics[baseline_key]
    )
    report["splitReports"] = {
        "public": public_report,
        "knownRegression": regression_report,
    }
    report["productionBaseline"] = {
        "variant": baseline_key,
        "metrics": regression_metrics[baseline_key],
    }
    atomic_write_json(replay_path, report)
    collection = read_gzip_json(raw)
    frozen = {
        "schemaVersion": 4,
        "suite": SUITE,
        "runId": args.run_id,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "selectionData": "72 public/dev + 144 known-regression observations; no fresh holdout",
        "experimentalOverride": selected_key != baseline_key,
        "rag": selected,
        "runtimeBaseline": baseline_key,
        "regressionGuard": guard,
        "candidateSize": int(collection.get("candidateSize") or args.candidate_size),
        "contextualMode": collection.get("contextualMode"),
        "knowledgeIndex": collection.get("knowledgeIndex"),
        "providerCompleteness": provider_completeness(
            collection.get("providerFacts") or {},
            len(collection.get("cases") or []),
            expected_case_count=216,
        ),
        "datasetSha256": combined_sha(
            [
                PUBLIC_PATH,
                PUBLIC_PATH.with_suffix(".lock.json"),
                KNOWN_PATH,
                KNOWN_PATH.with_suffix(".lock.json"),
                V4_CATALOG_PATH,
                V4_FACT_METADATA_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
    }
    atomic_write_json(frozen_path, frozen)
    return {"phase": "replay", "runId": args.run_id, "selected": selected, "regressionGuard": guard}


def provider_completeness(
    provider: Mapping[str, Any], case_count: int, *, expected_case_count: int
) -> dict[str, Any]:
    embedding = provider.get("embedding") or {}
    rerank = provider.get("rerank") or {}
    expansion = provider.get("queryExpansion") or {}
    checks = {
        "caseCountComplete": case_count == expected_case_count,
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "embeddingProviderCalled": int(embedding.get("providerSuccesses") or 0) > 0,
        "embeddingCallsComplete": int(embedding.get("requests") or 0)
        == int(embedding.get("providerRequests") or 0)
        == int(embedding.get("providerSuccesses") or 0),
        "rerankFallbackZero": int(rerank.get("fallbackCount") or 0) == 0,
        "rerankFailuresZero": int(rerank.get("providerFailures") or 0) == 0,
        "rerankProviderCalled": int(rerank.get("providerSuccesses") or 0) > 0,
        "rerankCallsComplete": int(rerank.get("eligibleRequests") or 0)
        == int(rerank.get("providerRequests") or 0)
        == int(rerank.get("providerSuccesses") or 0),
        "queryExpansionFailuresZero": int(expansion.get("providerFailures") or 0) == 0,
        "queryExpansionCallsComplete": int(expansion.get("eligibleRequests") or 0)
        == int(expansion.get("providerRequests") or 0)
        == int(expansion.get("providerSuccesses") or 0),
    }
    return {
        "passed": all(checks.values()),
        "caseCount": case_count,
        "expectedCaseCount": expected_case_count,
        "checks": checks,
    }


def retrieval_gate(
    metrics: Mapping[str, Any],
    provider: Mapping[str, Any],
    case_count: int,
    *,
    dev_completeness: Mapping[str, Any] | None,
    regression_guard: Mapping[str, Any] | None,
) -> dict[str, Any]:
    provider_gate = provider_completeness(
        provider, case_count, expected_case_count=48
    )
    checks = {
        "all264CasesExecuted": case_count == 48
        and int((dev_completeness or {}).get("caseCount") or 0) == 216,
        "freshProviderComplete": provider_gate["passed"],
        "devProviderComplete": bool((dev_completeness or {}).get("passed")),
        "regressionGuard": bool((regression_guard or {}).get("passed")),
        "freshRecallAt3": _metric_value(metrics, "3", "recall") >= 0.90,
        "freshRecallAt5": _metric_value(metrics, "5", "recall") >= 0.95,
        "freshMrrAt10": _metric_value(metrics, "10", "mrr") >= 0.85,
        "freshNdcgAt5": _metric_value(metrics, "5", "ndcg") >= 0.85,
        "freshNoAnswerAccuracy": float(metrics.get("noAnswerAccuracy") or 0) >= 0.90,
        "freshInjectionRobustness": float(metrics.get("injectionRobustness") or 0) == 1.0,
        "canonicalCitationCorrectness": float(metrics.get("canonicalCitationCorrectness") or 0) >= 0.90,
        "canonicalCitationCoverage": float(metrics.get("canonicalCitationCoverage") or 0) >= 0.90,
    }
    passed = all(checks.values())
    return {
        "status": "PASSED" if passed else "FAILED_RETAINED",
        "passed": passed,
        "checks": checks,
        "providerCompleteness": {"dev": dev_completeness, "fresh": provider_gate},
        "regressionGuard": regression_guard,
    }


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_root = RESULTS_ROOT / args.run_id
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("run replay before finalizing the RAG v4 holdout")
    finalization_path = run_root / "finalization.json"
    collection_path = run_root / "raw" / "rag-v4-final-collection.json.gz"
    replay_path = run_root / "rag-v4-final-replay.json"
    if finalization_path.is_file():
        if not collection_path.is_file() or not replay_path.is_file():
            raise ValueError("RAG v4 finalization is incomplete")
        finalization = _load_json(finalization_path)
        if finalization.get("frozenConfigSha256") != sha256_file(frozen_path):
            raise ValueError("RAG v4 finalization frozen-config SHA mismatch")
        return {
            "phase": "collect-final",
            "runId": args.run_id,
            "caseCount": 48,
            "qualityGate": finalization["qualityGate"],
            "reusedFinalization": True,
        }
    frozen = _load_json(frozen_path)
    sets = load_rag_v4_sets(PUBLIC_PATH, KNOWN_PATH, FRESH_PATH)
    selected_key = str(frozen["rag"]["selectedVariant"])
    selected_policy = _policy_from_selection(selected_key)
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_v4_cases(
            sets["fresh_holdout"],
            output_path=collection_path,
            candidate_size=int(frozen.get("candidateSize") or args.candidate_size),
            policy=selected_policy,
            contextual_mode=str(frozen.get("contextualMode") or "context_prefix"),
            knowledge_index=frozen.get("knowledgeIndex"),
        )
    finally:
        await redis_service.close()
    report = replay_rag_v4_collection(collection, split_filter={"fresh_holdout"})
    atomic_write_json(replay_path, report)
    selected_metrics = (report.get("variantMetrics") or {}).get(selected_key)
    if not selected_metrics:
        raise ValueError("fresh replay lacks the frozen RAG v4 configuration")
    gate = retrieval_gate(
        selected_metrics,
        collection.get("providerFacts") or {},
        len(collection.get("cases") or []),
        dev_completeness=frozen.get("providerCompleteness"),
        regression_guard=frozen.get("regressionGuard"),
    )
    finalization = {
        "schemaVersion": 4,
        "finalizedAt": datetime.now(timezone.utc).isoformat(),
        "freshHoldoutExecutedOnceByThisRun": True,
        "frozenConfigSha256": sha256_file(frozen_path),
        "freshDatasetSha256": sha256_file(FRESH_PATH),
        "selectedVariant": selected_key,
        "qualityGate": gate,
    }
    atomic_write_json(finalization_path, finalization)
    return {
        "phase": "collect-final",
        "runId": args.run_id,
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


def _quality_only_gate(
    fresh_metrics: Mapping[str, Any],
    regression_guard: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate quality after replay without pretending Provider calls occurred."""

    checks = {
        "freshRecallAt3": _metric_value(fresh_metrics, "3", "recall") >= 0.90,
        "freshRecallAt5": _metric_value(fresh_metrics, "5", "recall") >= 0.95,
        "freshMrrAt10": _metric_value(fresh_metrics, "10", "mrr") >= 0.85,
        "freshNdcgAt5": _metric_value(fresh_metrics, "5", "ndcg") >= 0.85,
        "freshNoAnswerAccuracy": float(fresh_metrics.get("noAnswerAccuracy") or 0)
        >= 0.90,
        "freshInjectionRobustness": float(
            fresh_metrics.get("injectionRobustness") or 0
        )
        == 1.0,
        "canonicalCitationCorrectness": float(
            fresh_metrics.get("canonicalCitationCorrectness") or 0
        )
        >= 0.90,
        "canonicalCitationCoverage": float(
            fresh_metrics.get("canonicalCitationCoverage") or 0
        )
        >= 0.90,
        "knownRegressionGuard": bool(regression_guard.get("passed")),
    }
    return {
        "status": "PASSED" if all(checks.values()) else "FAILED_RETAINED",
        "passed": all(checks.values()),
        "checks": checks,
        "scope": "QUALITY_ONLY_POST_FIX_OFFLINE_REPLAY",
    }


def _metric_delta(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    pairs = {
        "recallAt3": (
            _metric_value(before, "3", "recall"),
            _metric_value(after, "3", "recall"),
        ),
        "recallAt5": (
            _metric_value(before, "5", "recall"),
            _metric_value(after, "5", "recall"),
        ),
        "mrrAt10": (
            _metric_value(before, "10", "mrr"),
            _metric_value(after, "10", "mrr"),
        ),
        "ndcgAt5": (
            _metric_value(before, "5", "ndcg"),
            _metric_value(after, "5", "ndcg"),
        ),
        "canonicalCitationCorrectness": (
            float(before.get("canonicalCitationCorrectness") or 0),
            float(after.get("canonicalCitationCorrectness") or 0),
        ),
        "canonicalCitationCoverage": (
            float(before.get("canonicalCitationCoverage") or 0),
            float(after.get("canonicalCitationCoverage") or 0),
        ),
        "noAnswerAccuracy": (
            float(before.get("noAnswerAccuracy") or 0),
            float(after.get("noAnswerAccuracy") or 0),
        ),
        "injectionRobustness": (
            float(before.get("injectionRobustness") or 0),
            float(after.get("injectionRobustness") or 0),
        ),
    }
    return {
        name: {
            "before": round(old, 6),
            "after": round(new, 6),
            "delta": round(new - old, 6),
        }
        for name, (old, new) in pairs.items()
    }


def postfix_replay(args: argparse.Namespace) -> dict[str, Any]:
    """Replay retained live candidates after fixes, with no Provider calls."""

    if args.run_id == args.source_run_id:
        raise ValueError("post-fix replay run-id must differ from source-run-id")
    source_root = RESULTS_ROOT / args.source_run_id
    source_dev_raw = source_root / "raw" / "rag-v4-dev-collection.json.gz"
    source_fresh_raw = source_root / "raw" / "rag-v4-final-collection.json.gz"
    source_frozen_path = source_root / "frozen-config.json"
    source_dev_replay_path = source_root / "rag-v4-dev-replay.json"
    source_fresh_replay_path = source_root / "rag-v4-final-replay.json"
    source_finalization_path = source_root / "finalization.json"
    required = (
        source_dev_raw,
        source_fresh_raw,
        source_frozen_path,
        source_dev_replay_path,
        source_fresh_replay_path,
        source_finalization_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"post-fix replay source is incomplete: {missing}")

    result_root = RESULTS_ROOT / args.run_id
    evidence_dir = EVIDENCE_ROOT / args.run_id
    if (result_root.exists() and any(result_root.iterdir())) or (
        evidence_dir.exists() and any(evidence_dir.iterdir())
    ):
        raise ValueError("post-fix replay run-id already has retained artifacts")

    frozen = _load_json(source_frozen_path)
    selected = str((frozen.get("rag") or {}).get("selectedVariant") or "")
    parameters = parse_selected_variant(selected)
    replay_kwargs = {
        "variants": ("production",),
        "top_n_values": (parameters["rerankTopN"],),
        "thresholds": (parameters["evidenceThreshold"],),
        "margins": (parameters["topScoreMargin"],),
    }
    dev = replay_rag_v4_collection(
        source_dev_raw,
        split_filter={"public", "known_regression"},
        **replay_kwargs,
    )
    known = replay_rag_v4_collection(
        source_dev_raw,
        split_filter={"known_regression"},
        **replay_kwargs,
    )
    fresh = replay_rag_v4_collection(
        source_fresh_raw,
        split_filter={"fresh_holdout"},
        **replay_kwargs,
    )
    dev_metrics = (dev.get("variantMetrics") or {}).get(selected)
    known_metrics = (known.get("variantMetrics") or {}).get(selected)
    fresh_metrics = (fresh.get("variantMetrics") or {}).get(selected)
    if not dev_metrics or not known_metrics or not fresh_metrics:
        raise ValueError("post-fix replay is missing the frozen production variant")

    source_dev = _load_json(source_dev_replay_path)
    source_fresh = _load_json(source_fresh_replay_path)
    source_known_metrics = (
        (((source_dev.get("splitReports") or {}).get("knownRegression") or {}).get("variantMetrics") or {})
        .get(selected)
    )
    source_fresh_metrics = (source_fresh.get("variantMetrics") or {}).get(selected)
    if not source_known_metrics or not source_fresh_metrics:
        raise ValueError("source replay is missing comparable production metrics")
    guard = _regression_guard(known_metrics, source_known_metrics)
    quality_gate = _quality_only_gate(fresh_metrics, guard)
    deltas = _metric_delta(source_fresh_metrics, fresh_metrics)

    result_root.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(result_root / "postfix-dev-replay.json", dev)
    atomic_write_json(result_root / "postfix-known-regression-replay.json", known)
    atomic_write_json(result_root / "postfix-fresh-replay.json", fresh)
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "sourceRunId": args.source_run_id,
        "status": "POST_FIX_OFFLINE_REPLAY",
        "qualityGate": quality_gate,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-offline-replay",
        "holdoutExposed": True,
        "freshEvidence": False,
        "caseCounts": {
            "public": 72,
            "knownRegression": 144,
            "freshHoldout": 48,
            "total": 264,
        },
        "selectedVariant": selected,
        "policy": _policy_from_selection(selected).public(),
        "evidenceSelectionStrategy": "subquestion_bounded_v1",
        "providerFacts": {
            "embeddingRequests": 0,
            "rerankRequests": 0,
            "queryExpansionRequests": 0,
            "llmRequests": 0,
            "candidateSource": "retained local-live Provider output from sourceRunId",
        },
        "selectedDevMetrics": {
            key: value for key, value in dev_metrics.items() if key != "perCase"
        },
        "selectedKnownRegressionMetrics": {
            key: value for key, value in known_metrics.items() if key != "perCase"
        },
        "selectedFreshMetrics": {
            key: value for key, value in fresh_metrics.items() if key != "perCase"
        },
        "freshMetricDeltasAgainstFormalRun": deltas,
        "knownRegressionGuard": guard,
        "humanReviewStatus": "NOT_APPLICABLE",
        "honestBoundaries": [
            "This is a post-fix offline replay over an already opened holdout; it is not fresh E3 evidence.",
            "No Provider was called; the changed Chinese rerank instruction is not measured by this replay.",
            "The formal source run remains FAILED_RETAINED and is not overwritten.",
            "All labels are SYNTHETIC; baseline remains unchanged.",
        ],
    }
    atomic_write_json(result_root / "summary.json", summary)
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(
        evidence_dir / "badcases.json",
        {
            "dev": _badcases(dev_metrics),
            "knownRegression": _badcases(known_metrics),
            "fresh": _badcases(fresh_metrics),
        },
    )
    manifest = {
        "schemaVersion": 4,
        "suite": SUITE,
        "runId": args.run_id,
        "sourceRunId": args.source_run_id,
        "status": "POST_FIX_OFFLINE_REPLAY",
        "holdoutExposed": True,
        "freshEvidence": False,
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "sourceArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in required
        },
        "resultArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in sorted(result_root.iterdir())
        },
        "providerRequests": 0,
        "humanReviewStatus": "NOT_APPLICABLE",
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    fresh_curve = fresh_metrics.get("metricCurves") or {}
    atomic_write_bytes(
        evidence_dir / "report.md",
        (
            "# RAG retrieval v4 post-fix offline replay\n\n"
            f"- Run: `{args.run_id}`\n"
            f"- Source: `{args.source_run_id}`\n"
            "- Status: `POST_FIX_OFFLINE_REPLAY`; `holdoutExposed=true`; `freshEvidence=false`\n"
            f"- Fresh Recall@3/5: `{(fresh_curve.get('3') or {}).get('recall')}` / `{(fresh_curve.get('5') or {}).get('recall')}`\n"
            f"- Fresh MRR@10: `{(fresh_curve.get('10') or {}).get('mrr')}`; NDCG@5: `{(fresh_curve.get('5') or {}).get('ndcg')}`\n"
            f"- Canonical correctness/coverage: `{fresh_metrics.get('canonicalCitationCorrectness')}` / `{fresh_metrics.get('canonicalCitationCoverage')}`\n"
            f"- Quality-only gate: `{quality_gate['status']}`; Provider requests: `0`\n"
            "- The source formal run remains unchanged; this is not new holdout evidence.\n"
        ).encode("utf-8"),
    )
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(
        evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8")
    )
    return {
        "phase": "postfix-replay",
        "runId": args.run_id,
        "sourceRunId": args.source_run_id,
        "qualityGate": quality_gate,
        "freshMetricDeltas": deltas,
        "evidenceDir": str(evidence_dir),
    }


async def targeted_regression(args: argparse.Namespace) -> dict[str, Any]:
    """Re-run the seven exposed retrieval badcases against live Providers."""

    result_root = RESULTS_ROOT / args.run_id
    evidence_dir = EVIDENCE_ROOT / args.run_id
    if (result_root.exists() and any(result_root.iterdir())) or (
        evidence_dir.exists() and any(evidence_dir.iterdir())
    ):
        raise ValueError("targeted regression run-id already has retained artifacts")
    sets = load_rag_v4_sets(PUBLIC_PATH, KNOWN_PATH, FRESH_PATH)
    by_id = {str(row.get("id")): row for row in sets["fresh_holdout"]}
    missing = [case_id for case_id in TARGETED_POSTFIX_CASE_IDS if case_id not in by_id]
    if missing:
        raise ValueError(f"targeted RAG v4 cases are missing: {missing}")
    cases = [by_id[case_id] for case_id in TARGETED_POSTFIX_CASE_IDS]
    raw_path = result_root / "raw" / "targeted-collection.json.gz"
    policy = runtime_rag_policy()
    await redis_service.ensure_connected()
    try:
        collection = await collect_rag_v4_cases(
            cases,
            output_path=raw_path,
            candidate_size=args.candidate_size,
            policy=policy,
            contextual_mode=args.contextual_mode,
            knowledge_index=args.knowledge_index,
        )
    finally:
        await redis_service.close()
    selected = _policy_variant_key(policy)
    report = replay_rag_v4_collection(
        collection,
        variants=("production",),
        top_n_values=(policy.rerank_top_n,),
        thresholds=(policy.evidence_threshold,),
        margins=(policy.top_score_margin,),
        split_filter={"fresh_holdout"},
    )
    metrics = (report.get("variantMetrics") or {}).get(selected)
    if not metrics:
        raise ValueError("targeted regression is missing the runtime production variant")
    rows = list(metrics.get("perCase") or [])
    rows_by_id = {str(row.get("caseId")): row for row in rows}
    targeted_fix_ids = set(TARGETED_POSTFIX_CASE_IDS) - set(
        TARGETED_LABEL_LIMIT_CASE_IDS
    )
    fix_checks = {
        case_id: bool((rows_by_id.get(case_id) or {}).get("passed"))
        for case_id in sorted(targeted_fix_ids)
    }
    label_limit_checks = {
        case_id: bool((rows_by_id.get(case_id) or {}).get("predictedNoAnswer"))
        for case_id in sorted(TARGETED_LABEL_LIMIT_CASE_IDS)
    }
    provider_gate = provider_completeness(
        collection.get("providerFacts") or {},
        len(collection.get("cases") or []),
        expected_case_count=len(TARGETED_POSTFIX_CASE_IDS),
    )
    checks = {
        "allSevenCasesExecuted": len(rows) == len(TARGETED_POSTFIX_CASE_IDS),
        "providerComplete": bool(provider_gate.get("passed")),
        "fiveFixTargetsPassed": all(fix_checks.values()),
        "twoUnsupportedLabelsRemainInsufficient": all(label_limit_checks.values()),
    }
    validation = {
        "status": (
            "PASSED_TARGETED_WITH_KNOWN_LABEL_LIMITS"
            if all(checks.values())
            else "FAILED_RETAINED"
        ),
        "passed": all(checks.values()),
        "checks": checks,
        "fixCases": fix_checks,
        "knownLabelLimitCases": label_limit_checks,
        "providerCompleteness": provider_gate,
    }
    result_root.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(result_root / "targeted-replay.json", report)
    source_root = RESULTS_ROOT / args.source_run_id
    source_paths = [
        source_root / "raw" / "rag-v4-final-collection.json.gz",
        source_root / "rag-v4-final-replay.json",
        source_root / "finalization.json",
    ]
    if any(not path.is_file() for path in source_paths):
        raise ValueError("targeted regression source formal run is incomplete")
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "sourceRunId": args.source_run_id,
        "status": "POST_FIX_TARGETED_REGRESSION",
        "validation": validation,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-live-targeted",
        "holdoutExposed": True,
        "freshEvidence": False,
        "caseCount": len(rows),
        "caseIds": list(TARGETED_POSTFIX_CASE_IDS),
        "knownLabelLimitCaseIds": sorted(TARGETED_LABEL_LIMIT_CASE_IDS),
        "selectedVariant": selected,
        "policy": policy.public(),
        "metrics": {key: value for key, value in metrics.items() if key != "perCase"},
        "cases": rows,
        "providerFacts": collection.get("providerFacts") or {},
        "humanReviewStatus": "NOT_APPLICABLE",
        "honestBoundaries": [
            "This targeted regression reuses seven exposed fresh cases and is not fresh E3 evidence.",
            "Two cases intentionally remain INSUFFICIENT because their labels exceed published knowledge.",
            "The formal 264-case source result remains FAILED_RETAINED and unchanged.",
            "All labels are SYNTHETIC; local latency is not an SLO; cost is UNPRICED.",
        ],
    }
    atomic_write_json(result_root / "summary.json", summary)
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(
        evidence_dir / "badcases.json",
        {
            "failedAgainstFrozenLabels": [row for row in rows if not row.get("passed")],
            "knownLabelLimitCaseIds": sorted(TARGETED_LABEL_LIMIT_CASE_IDS),
        },
    )
    manifest = {
        "schemaVersion": 4,
        "suite": SUITE,
        "runId": args.run_id,
        "sourceRunId": args.source_run_id,
        "status": "POST_FIX_TARGETED_REGRESSION",
        "holdoutExposed": True,
        "freshEvidence": False,
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "rawCollectionSha256": sha256_file(raw_path),
        "sourceArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in source_paths
        },
        "resultArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in sorted(result_root.iterdir())
            if path.is_file()
        },
        "humanReviewStatus": "NOT_APPLICABLE",
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    atomic_write_bytes(
        evidence_dir / "report.md",
        (
            "# RAG retrieval v4 post-fix targeted regression\n\n"
            f"- Run: `{args.run_id}`\n"
            f"- Source: `{args.source_run_id}`\n"
            "- Cases: 7 exposed badcases; `holdoutExposed=true`; `freshEvidence=false`\n"
            f"- Validation: `{validation['status']}`\n"
            f"- Fix targets passed: `{sum(fix_checks.values())}/5`\n"
            f"- Known label limits stayed insufficient: `{sum(label_limit_checks.values())}/2`\n"
            f"- Provider completeness: `{provider_gate['passed']}`\n"
        ).encode("utf-8"),
    )
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(
        evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8")
    )
    return {
        "phase": "targeted-regression",
        "runId": args.run_id,
        "validation": validation,
        "evidenceDir": str(evidence_dir),
    }


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    required = [
        run_root / "rag-v4-dev-replay.json",
        run_root / "rag-v4-final-replay.json",
        run_root / "frozen-config.json",
        run_root / "finalization.json",
        run_root / "raw" / "rag-v4-dev-collection.json.gz",
        run_root / "raw" / "rag-v4-final-collection.json.gz",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete RAG v4 run: {missing}")
    dev = _load_json(required[0])
    final = _load_json(required[1])
    frozen = _load_json(required[2])
    finalization = _load_json(required[3])
    selected = str(frozen["rag"]["selectedVariant"])
    dev_metrics = (dev.get("variantMetrics") or {}).get(selected)
    fresh_metrics = (final.get("variantMetrics") or {}).get(selected)
    if not dev_metrics or not fresh_metrics:
        raise ValueError("RAG v4 package is missing frozen metrics")
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
        "caseCounts": {"public": 72, "knownRegression": 144, "freshHoldout": 48, "total": 264},
        "labelScope": "12-document canonical fact catalog with versioned fact metadata and required claims",
        "selectedVariant": selected,
        "policy": _policy_from_selection(selected).public(),
        "selectedDevMetrics": {key: value for key, value in dev_metrics.items() if key != "perCase"},
        "selectedFreshMetrics": {key: value for key, value in fresh_metrics.items() if key != "perCase"},
        "qualityGate": finalization["qualityGate"],
        "variantMetrics": {"dev": _compact_metrics(dev), "fresh": _compact_metrics(final)},
        "pairedDeltas": {"dev": dev.get("pairedDeltas"), "fresh": final.get("pairedDeltas")},
        "confidenceIntervals": {"dev": dev.get("confidenceIntervals"), "fresh": final.get("confidenceIntervals")},
        "stageLatency": {"dev": dev.get("stageLatency"), "fresh": final.get("stageLatency")},
        "providerFacts": {
            "dev": (read_gzip_json(required[4]).get("providerFacts") or {}),
            "fresh": (read_gzip_json(required[5]).get("providerFacts") or {}),
        },
        "humanReviewStatus": "NOT_APPLICABLE",
        "honestBoundaries": [
            "All labels are SYNTHETIC; local-live is not real-user or production traffic.",
            "The 48-case fresh holdout was executed after the 216-observation dev configuration was frozen.",
            "Faithfulness remains an automatic citation-grounded proxy until human review is completed.",
            "Local latency percentiles are sample descriptions, not SLOs; Provider cost is UNPRICED.",
            "No baseline was accepted or overwritten.",
        ],
    }
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(
        evidence_dir / "badcases.json",
        {"dev": _badcases(dev_metrics), "fresh": _badcases(fresh_metrics)},
    )
    manifest = {
        "schemaVersion": 4,
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
                V4_CATALOG_PATH,
                V4_FACT_METADATA_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
        "sourceArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in required
        },
        "humanReviewStatus": "NOT_APPLICABLE",
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    atomic_write_bytes(
        evidence_dir / "report.md",
        (
            "# RAG retrieval live v4\n\n"
            f"- Run: `{args.run_id}`\n"
            "- Cases: 264 (72 public/dev, 144 known regression, 48 one-time fresh)\n"
            f"- Frozen variant: `{selected}`\n"
            f"- Quality gate: `{finalization['qualityGate']['status']}`\n"
            "- Evidence: `SYNTHETIC + local-live`; cost: `UNPRICED`; baseline unchanged.\n"
        ).encode("utf-8"),
    )
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(
        evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8")
    )
    return {"phase": "package", "evidenceDir": str(evidence_dir), "manifest": manifest}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    subparsers.add_parser("prepare")
    context_parser = subparsers.add_parser(
        "prepare-context", help="copy published knowledge into two isolated eval indices"
    )
    context_parser.add_argument("--source-index", required=True)
    context_parser.add_argument("--original-index", default="aishop_eval_rag_original_v4")
    context_parser.add_argument("--context-index", default="aishop_eval_rag_context_v4")
    context_parser.add_argument("--limit", type=int, default=500)
    for phase in ("collect-dev", "replay", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--candidate-size", type=int, default=20)
        child.add_argument(
            "--contextual-mode",
            choices=("original", "context_prefix"),
            default="context_prefix",
        )
        child.add_argument(
            "--knowledge-index",
            default=None,
            help="optional isolated aishop_eval_ knowledge index",
        )
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
        if phase in {"collect-dev", "collect-final"}:
            child.add_argument(
                "--release-version",
                type=int,
                required=True,
                help="immutable Java knowledge release containing catalog v1",
            )
    postfix_parser = subparsers.add_parser(
        "postfix-replay",
        help="replay retained live candidates after fixes without Provider calls",
    )
    postfix_parser.add_argument("--source-run-id", required=True)
    postfix_parser.add_argument("--run-id", required=True)
    targeted_parser = subparsers.add_parser(
        "targeted-regression",
        help="live post-fix regression of the seven exposed retrieval badcases",
    )
    targeted_parser.add_argument("--source-run-id", required=True)
    targeted_parser.add_argument("--run-id", required=True)
    targeted_parser.add_argument("--candidate-size", type=int, default=20)
    targeted_parser.add_argument(
        "--contextual-mode",
        choices=("original", "context_prefix"),
        default="context_prefix",
    )
    targeted_parser.add_argument(
        "--knowledge-index", default="aishop_eval_rag_context_v4"
    )
    targeted_parser.add_argument(
        "--release-version",
        type=int,
        required=True,
        help="immutable Java knowledge release containing catalog v1",
    )
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return prepare(args)
    if args.phase == "prepare-context":
        return await prepare_context(args)
    if args.phase == "collect-dev":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_dev(args)
    if args.phase == "replay":
        return replay(args)
    if args.phase == "collect-final":
        with evaluation_knowledge_release_scope(args.release_version):
            return await collect_final(args)
    if args.phase == "package":
        return package(args)
    if args.phase == "postfix-replay":
        return postfix_replay(args)
    if args.phase == "targeted-regression":
        with evaluation_knowledge_release_scope(args.release_version):
            return await targeted_regression(args)
    raise ValueError(f"unsupported RAG v4 phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        with canonical_fact_catalog_scope(V4_CATALOG_PATH):
            result = asyncio.run(async_main(args))
    except Exception as exc:
        run_id = getattr(args, "run_id", None)
        if run_id:
            failure_dir = RESULTS_ROOT / run_id
            failure_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                failure_dir / "failure.json",
                {
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
