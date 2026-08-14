"""Prepare, collect, replay and package Search v2 live evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.artifacts import git_commit, workspace_sha256  # noqa: E402
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.mature_eval.chinese_dataset_v2 import (  # noqa: E402
    generate_dataset,
    validate_dataset,
)
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    combined_sha,
    read_gzip_json,
    sha256_bytes,
    sha256_file,
)
from benchmarks.mature_eval.product_service_runner import (  # noqa: E402
    run_product_service_cases,
)
from benchmarks.mature_eval.search_v2 import (  # noqa: E402
    WANDS_V2_VARIANTS,
    choose_v2_configuration,
    collect_v2_cases,
    replay_v2_collection,
)
from benchmarks.mature_eval.wands import (  # noqa: E402
    prepare_wands_full_catalog,
    validate_subset,
)
from benchmarks.run_search_rag_mature_eval import _index_dataset  # noqa: E402
from benchmarks.run_search_relevance import load_cases  # noqa: E402

SUITE = "search-mature-v2"
DATASET_ROOT = PROJECT_ROOT / "benchmarks" / "datasets" / "mature_v2"
RESULT_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / SUITE
CHINESE_PATH = DATASET_ROOT / "chinese-commerce-search-v2.json"
CHINESE_LOCK_PATH = CHINESE_PATH.with_suffix(".lock.json")
WANDS_LOCK_PATH = DATASET_ROOT / "wands" / "selection.lock.json"
WANDS_SOURCE_ROOT = (
    PROJECT_ROOT / "benchmarks" / "results" / "search-rag-mature-v1" / "raw" / "wands-source"
)
WANDS_SELECTION_PATH = RESULT_ROOT / "raw" / "wands" / "selection.json"
PUBLIC_SEARCH_PATH = PROJECT_ROOT / "benchmarks" / "search_relevance_v1.jsonl"
HOLDOUT_SEARCH_PATH = PROJECT_ROOT / "benchmarks" / "datasets" / "search_holdout_v1.jsonl"
DEFAULT_CHINESE_INDEX = "aishop_eval_chinese_v2"
DEFAULT_WANDS_INDEX = "aishop_eval_wands_full_v2"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _load_chinese() -> dict[str, Any]:
    payload = _load_json(CHINESE_PATH)
    validate_dataset(payload)
    lock = _load_json(CHINESE_LOCK_PATH)
    if sha256_bytes(canonical_json_bytes(payload)) != lock.get("datasetSha256"):
        raise ValueError("Chinese Search v2 dataset SHA does not match its lock")
    return payload


def _load_wands() -> dict[str, Any]:
    payload = _load_json(WANDS_SELECTION_PATH)
    validate_subset(
        payload,
        product_cap=50_000,
        expected_label_scope="full-catalog-incomplete-qrels",
    )
    lock = _load_json(WANDS_LOCK_PATH)
    if sha256_bytes(canonical_json_bytes(payload)) != lock.get("selectionSha256"):
        raise ValueError("WANDS full-catalog selection SHA does not match its lock")
    return payload


def _search_provider_completeness(
    collection: dict[str, Any], *, expected_case_count: int
) -> dict[str, Any]:
    """Validate that a cold Search collection really reached both Providers."""

    facts = collection.get("providerFacts") or {}
    embedding = facts.get("embedding") or {}
    responses = list(facts.get("responseFacts") or [])
    embedding_requests = int(facts.get("embeddingRequests") or 0)
    rerank_requests = int(facts.get("rerankRequests") or 0)
    checks = {
        "caseCountComplete": len(collection.get("cases") or [])
        == expected_case_count,
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "embeddingCallsComplete": embedding_requests > 0
        and int(embedding.get("requests") or 0) == embedding_requests
        and int(embedding.get("providerRequests") or 0) == embedding_requests
        and int(embedding.get("providerSuccesses") or 0) == embedding_requests,
        "rerankProviderCalled": rerank_requests > 0,
        "rerankResponsesComplete": len(responses) == rerank_requests
        and all(row.get("status") == "SUCCESS" for row in responses),
    }
    return {
        "passed": all(checks.values()),
        "expectedCaseCount": expected_case_count,
        "caseCount": len(collection.get("cases") or []),
        "checks": checks,
    }


def _runtime_provider_completeness(runtime: dict[str, Any]) -> dict[str, Any]:
    facts = runtime.get("providerFacts") or {}
    embedding = facts.get("embedding") or {}
    rerank = facts.get("rerank") or {}
    cases = list(runtime.get("cases") or [])
    checks = {
        "allCasesExecuted": len(cases) == 45
        and int(runtime.get("executedCount") or 0) == 45,
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "embeddingProviderCalled": int(embedding.get("providerSuccesses") or 0) > 0,
        "rerankFailuresZero": int(rerank.get("providerFailures") or 0) == 0,
        "rerankFallbackZero": int(rerank.get("fallbackCount") or 0) == 0,
        "rerankProviderCalled": int(rerank.get("providerSuccesses") or 0) > 0,
        "runtimeFallbackZero": all(
            not bool((row.get("runtimeTrace") or {}).get("fallback")) for row in cases
        ),
        "goldNeverUsed": all(row.get("goldUsedByRuntime") is False for row in cases),
    }
    return {"passed": all(checks.values()), "checks": checks}


def _runtime_cases() -> list[dict[str, Any]]:
    public = [
        {**row, "split": "public", "expectedNoResults": False}
        for row in load_cases(PUBLIC_SEARCH_PATH)
        if row.get("relevanceGrades")
    ]
    holdout = [
        {**row, "split": "holdout", "expectedNoResults": False}
        for row in load_cases(HOLDOUT_SEARCH_PATH)
        if row.get("relevanceGrades")
    ]
    if len(public) != 30 or len(holdout) != 15:
        raise ValueError(
            f"47-product runtime suite must contain 30 public + 15 holdout cases, got "
            f"{len(public)} + {len(holdout)}"
        )
    return [*public, *holdout]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    if args.regenerate_chinese or not CHINESE_PATH.is_file():
        generate_dataset(CHINESE_PATH)
    chinese = _load_chinese()
    wands = prepare_wands_full_catalog(
        WANDS_SOURCE_ROOT,
        WANDS_SELECTION_PATH,
        WANDS_LOCK_PATH,
    )
    return {
        "phase": "prepare",
        "chinese": validate_dataset(chinese),
        "wands": wands["lock"],
        "runtimeCases": len(_runtime_cases()),
    }


async def collect_dev(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULT_ROOT / args.run_id
    if (run_root / "frozen-config.json").exists():
        raise ValueError("Search v2 dev configuration is already frozen")
    chinese = _load_chinese()
    public = [row for row in chinese["queries"] if row["split"] == "public"]
    await redis_service.ensure_connected()
    try:
        index = await _index_dataset(
            products=chinese["products"],
            dataset="chinese-v2",
            index_name=args.chinese_index,
            vector_path=run_root / "raw" / "chinese-product-vectors.json.gz",
        )
        collection = await collect_v2_cases(
            cases=public,
            products=chinese["products"],
            index=args.chinese_index,
            output_path=run_root / "raw" / "chinese-dev-collection.json.gz",
            candidate_size=50,
            rerank_pool_size=100,
        )
    finally:
        await redis_service.close()
    return {
        "phase": "collect-dev",
        "index": index,
        "cases": len(collection["cases"]),
        "providerFacts": collection["providerFacts"],
    }


def replay(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULT_ROOT / args.run_id
    frozen_path = run_root / "frozen-config.json"
    replay_path = run_root / "chinese-dev-replay.json"
    if frozen_path.is_file():
        if not replay_path.is_file():
            raise ValueError("Search v2 frozen configuration is incomplete")
        frozen = _load_json(frozen_path)
        if frozen.get("runId") != args.run_id:
            raise ValueError("Search v2 frozen run identity mismatch")
        return {
            "phase": "replay",
            **dict(frozen.get("search") or {}),
            "reusedFrozenConfig": True,
        }
    chinese = _load_chinese()
    collection_path = run_root / "raw" / "chinese-dev-collection.json.gz"
    if not collection_path.is_file():
        raise ValueError("collect Search v2 dev cases before replay")
    collection = read_gzip_json(collection_path)
    report = replay_v2_collection(
        collection,
        products=chinese["products"],
        split_filter={"public"},
    )
    selected = choose_v2_configuration(report)
    atomic_write_json(replay_path, report)
    frozen = {
        "schemaVersion": 2,
        "suite": SUITE,
        "runId": args.run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "search": selected,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "oracleUsedForSelection": False,
        "providerCompleteness": _search_provider_completeness(
            collection, expected_case_count=120
        ),
        "datasetSha256": combined_sha(
            [CHINESE_PATH, CHINESE_LOCK_PATH], relative_to=PROJECT_ROOT
        ),
    }
    atomic_write_json(frozen_path, frozen)
    return {"phase": "replay", **selected}


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_root = RESULT_ROOT / args.run_id
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("run replay before finalizing Search v2 holdouts")
    finalization_path = run_root / "finalization.json"
    required_final = (
        run_root / "chinese-final-replay.json",
        run_root / "wands-final-replay.json",
        run_root / "raw" / "product-service-runtime.json.gz",
    )
    if finalization_path.is_file():
        if not all(path.is_file() for path in required_final):
            raise ValueError("Search v2 finalization is incomplete")
        finalization = _load_json(finalization_path)
        if finalization.get("frozenConfigSha256") != sha256_file(frozen_path):
            raise ValueError("Search v2 finalization frozen-config SHA mismatch")
        return {
            "phase": "collect-final",
            "cases": {"chinese": 120, "wands": 202, "productService": 45},
            "qualityEvidence": finalization.get("providerCompleteness"),
            "reusedFinalization": True,
        }
    chinese = _load_chinese()
    wands = _load_wands()
    final_chinese = [row for row in chinese["queries"] if row["split"] != "public"]
    await redis_service.ensure_connected()
    try:
        chinese_index = await _index_dataset(
            products=chinese["products"],
            dataset="chinese-v2",
            index_name=args.chinese_index,
            vector_path=run_root / "raw" / "chinese-product-vectors.json.gz",
        )
        wands_index = await _index_dataset(
            products=wands["products"],
            dataset="wands",
            index_name=args.wands_index,
            vector_path=run_root / "raw" / "wands-product-vectors.json.gz",
        )
        chinese_collection = await collect_v2_cases(
            cases=final_chinese,
            products=chinese["products"],
            index=args.chinese_index,
            output_path=run_root / "raw" / "chinese-final-collection.json.gz",
            candidate_size=50,
            rerank_pool_size=100,
        )
        wands_collection = await collect_v2_cases(
            cases=wands["queries"],
            products=wands["products"],
            index=args.wands_index,
            output_path=run_root / "raw" / "wands-final-collection.json.gz",
            candidate_size=200,
            rerank_pool_size=50,
            rerank_request_char_budget=48_000,
        )
        runtime = await run_product_service_cases(
            _runtime_cases(),
            output_path=run_root / "raw" / "product-service-runtime.json.gz",
            evaluate_authoritative_availability=True,
        )
    finally:
        await redis_service.close()
    chinese_report = replay_v2_collection(
        chinese_collection,
        products=chinese["products"],
        split_filter={"fresh_holdout", "challenge"},
    )
    wands_report = replay_v2_collection(
        wands_collection,
        products=wands["products"],
        variants=WANDS_V2_VARIANTS,
        candidate_counts=(200,),
        rrf_k_values=(60,),
        rerank_top_n_values=(50,),
    )
    atomic_write_json(run_root / "chinese-final-replay.json", chinese_report)
    atomic_write_json(run_root / "wands-final-replay.json", wands_report)
    final_provider = {
        "chinese": _search_provider_completeness(
            chinese_collection, expected_case_count=120
        ),
        "wands": _search_provider_completeness(
            wands_collection, expected_case_count=202
        ),
        "productService": _runtime_provider_completeness(runtime),
    }
    atomic_write_json(
        finalization_path,
        {
            "finalizedAt": datetime.now(timezone.utc).isoformat(),
            "finalizeHoldoutExplicit": True,
            "freshHoldoutExecutedOnceByThisRun": True,
            "runtimeCases": runtime["caseCount"],
            "frozenConfigSha256": sha256_file(frozen_path),
            "chineseDatasetSha256": sha256_file(CHINESE_PATH),
            "wandsSelectionSha256": _load_json(WANDS_LOCK_PATH)["selectionSha256"],
            "providerCompleteness": final_provider,
        },
    )
    return {
        "phase": "collect-final",
        "indices": {"chinese": chinese_index, "wands": wands_index},
        "cases": {
            "chinese": len(chinese_collection["cases"]),
            "wands": len(wands_collection["cases"]),
            "productService": runtime["caseCount"],
        },
        "providerCompleteness": final_provider,
    }


def _selected_rows(report: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list((report.get("cases") or {}).get(key) or [])


def _split_metrics(report: dict[str, Any], key: str, split: str) -> dict[str, Any]:
    from app.evaluation.ranking import aggregate_ranking_cases

    rows = [row for row in _selected_rows(report, key) if row.get("split") == split]
    return aggregate_ranking_cases([row["metrics"] for row in rows]) if rows else {}


def _badcases(report: dict[str, Any], key: str) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in _selected_rows(report, key):
        metrics = row["metrics"]
        if metrics.get("expectedNoResults"):
            if not metrics.get("noResultCorrect"):
                failures.append({"caseId": row["caseId"], "reason": "unexpected_results"})
            continue
        recall = float((metrics.get("metricsByK") or {}).get("3", {}).get("recall") or 0)
        if recall < 1:
            failures.append(
                {"caseId": row["caseId"], "reason": "recall@3", "value": recall}
            )
        if int(row.get("constraintViolationCount") or 0):
            failures.append(
                {
                    "caseId": row["caseId"],
                    "reason": "constraint_violation",
                    "value": row["constraintViolationCount"],
                }
            )
    return failures


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULT_ROOT / args.run_id
    required = [
        run_root / "chinese-dev-replay.json",
        run_root / "chinese-final-replay.json",
        run_root / "wands-final-replay.json",
        run_root / "frozen-config.json",
        run_root / "finalization.json",
        run_root / "raw" / "product-service-runtime.json.gz",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete Search v2 run: {missing}")
    dev = _load_json(required[0])
    final = _load_json(required[1])
    wands = _load_json(required[2])
    frozen = _load_json(required[3])
    finalization = _load_json(required[4])
    runtime = read_gzip_json(required[5])
    selected = str((frozen.get("search") or {}).get("selectedVariant") or "")
    wands_key = "full_rerank:c200:rrf60:n50"
    if selected not in (dev.get("variantMetrics") or {}) or wands_key not in (
        wands.get("variantMetrics") or {}
    ):
        raise ValueError("frozen Search v2 configuration is absent from replay")
    public_metrics = _split_metrics(dev, selected, "public")
    fresh_metrics = _split_metrics(final, selected, "fresh_holdout")
    challenge_metrics = _split_metrics(final, selected, "challenge")
    selected_final = (final.get("variantMetrics") or {})[selected]
    parser = dev.get("constraintParser") or {}
    runtime_curve = ((runtime.get("metrics") or {}).get("metricCurves") or {}).get(
        "10", {}
    )
    provider_evidence = {
        "dev": frozen.get("providerCompleteness"),
        **dict(finalization.get("providerCompleteness") or {}),
    }
    gates = {
        "allChineseCasesExecuted": dev.get("caseCount") == 120
        and final.get("caseCount") == 120,
        "runtimeConstraintPrecision": float(parser.get("precision") or 0) >= 0.98,
        "freshRecallAt3": float(
            (fresh_metrics.get("metricCurves") or {}).get("3", {}).get("recall") or 0
        )
        >= 0.85,
        "freshNdcgAt5": float(
            (fresh_metrics.get("metricCurves") or {}).get("5", {}).get("ndcg") or 0
        )
        >= 0.80,
        "challengeNoResultAccuracy": float(challenge_metrics.get("noResultAccuracy") or 0)
        >= 0.90,
        "constraintViolationZero": int(selected_final.get("constraintViolationCount") or 0)
        == 0,
        "productServiceExecuted": runtime.get("executedCount") == 45,
        "productServiceRecallAt10": float(runtime_curve.get("recall") or 0) >= 0.80,
        "productServiceMrrAt10": float(runtime_curve.get("mrr") or 0) >= 0.65,
        "productServiceNdcgAt10": float(runtime_curve.get("ndcg") or 0) >= 0.70,
        "wandsFullCatalogComplete": wands.get("caseCount") == 202
        and int((_load_json(WANDS_LOCK_PATH)).get("products") or 0) == 42_994,
        "providerEvidenceComplete": bool(provider_evidence)
        and all(
            bool((value or {}).get("passed")) for value in provider_evidence.values()
        ),
    }
    evidence_dir = EVIDENCE_ROOT / args.run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-live",
        "datasets": {
            "runtime47": {"products": 47, "publicQueries": 30, "holdoutQueries": 15},
            "chineseV2": _load_json(CHINESE_LOCK_PATH),
            "wandsFullCatalog": _load_json(WANDS_LOCK_PATH),
        },
        "frozenConfiguration": frozen,
        "splitMetrics": {
            "chinesePublic": public_metrics,
            "chineseFreshHoldout": fresh_metrics,
            "chineseChallenge": challenge_metrics,
            "wandsFullCatalog": (wands.get("variantMetrics") or {})[wands_key],
            "productServiceRuntime": runtime.get("metrics"),
        },
        "constraintParser": parser,
        "variantMetrics": {
            "chineseDev": dev.get("variantMetrics"),
            "chineseFinal": final.get("variantMetrics"),
            "wands": wands.get("variantMetrics"),
        },
        "pairedDeltas": {
            "chineseDev": dev.get("pairedDeltas"),
            "chineseFinal": final.get("pairedDeltas"),
            "wands": wands.get("pairedDeltas"),
        },
        "stageLatency": {
            "chineseDev": dev.get("stageLatency"),
            "chineseFinal": final.get("stageLatency"),
            "wands": wands.get("stageLatency"),
            "productService": runtime.get("stageLatency"),
        },
        "providerFacts": {
            "chineseDev": dev.get("providerFacts"),
            "chineseFinal": final.get("providerFacts"),
            "wands": wands.get("providerFacts"),
            "productService": runtime.get("providerFacts"),
        },
        "providerCompleteness": provider_evidence,
        "qualityGates": {**gates, "passed": all(gates.values())},
        "humanReviewStatus": "NOT_APPLICABLE",
        "badcases": {
            "chineseDev": _badcases(dev, selected),
            "chineseFinal": _badcases(final, selected),
        },
        "honestBoundaries": [
            "The Chinese catalog and queries are SYNTHETIC and deterministically labelled.",
            "WANDS uses a 42,994-product full-catalog ranking with incomplete qrels; unjudged results are not negatives.",
            "oracle_gold_filter is diagnostic-only and did not select the runtime configuration.",
            "Local latency percentiles are sample descriptions, not production SLOs.",
            "No baseline was accepted or overwritten.",
        ],
    }
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(evidence_dir / "badcases.json", summary["badcases"])
    raw_paths = sorted((run_root / "raw").glob("*.json.gz"))
    manifest = {
        "schemaVersion": 2,
        "runId": args.run_id,
        "summaryPath": str((evidence_dir / "summary.json").relative_to(PROJECT_ROOT)),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "datasetSha256": combined_sha(
            [CHINESE_PATH, CHINESE_LOCK_PATH, WANDS_LOCK_PATH], relative_to=PROJECT_ROOT
        ),
        "rawArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in raw_paths
        },
        "humanReviewStatus": "NOT_APPLICABLE",
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    report = [
        "# Search mature v2",
        "",
        f"- Run: `{args.run_id}`",
        "- Evidence: `SYNTHETIC + local-live`",
        f"- Quality gates: `{'PASS' if summary['qualityGates']['passed'] else 'FAILED_RETAINED'}`",
        "- Baseline: unchanged",
        "",
        "See `summary.json` for multi-K metrics, incomplete-qrel metrics, paired CI, Provider facts and latency.",
    ]
    atomic_write_bytes(evidence_dir / "report.md", ("\n".join(report) + "\n").encode())
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode())
    return {"phase": "package", "evidenceDir": str(evidence_dir), "qualityGates": gates}


def package_runtime_regression(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULT_ROOT / args.run_id
    raw_path = run_root / "raw" / "product-service-runtime.json.gz"
    if not raw_path.is_file():
        raise ValueError(f"runtime regression artifact is missing: {raw_path}")
    runtime = read_gzip_json(raw_path)
    catalog_curve = ((runtime.get("metrics") or {}).get("metricCurves") or {}).get(
        "10", {}
    )
    adjusted = runtime.get("availabilityAdjustedMetrics") or {}
    adjusted_curve = (adjusted.get("metricCurves") or {}).get("10", {})
    embedding = (runtime.get("providerFacts") or {}).get("embedding") or {}
    rerank = (runtime.get("providerFacts") or {}).get("rerank") or {}
    gates = {
        "allCasesExecuted": runtime.get("executedCount") == 45,
        "catalogRecallAt10": float(catalog_curve.get("recall") or 0) >= 0.80,
        "catalogMrrAt10": float(catalog_curve.get("mrr") or 0) >= 0.65,
        "catalogNdcgAt10": float(catalog_curve.get("ndcg") or 0) >= 0.70,
        "purchasableRecallAt10": float(adjusted_curve.get("recall") or 0) >= 0.80,
        "availabilityNoResultAccuracy": float(adjusted.get("noResultAccuracy") or 0)
        >= 0.90,
        "embeddingCacheHitsZero": int(embedding.get("cacheHits") or 0) == 0,
        "embeddingFailuresZero": int(embedding.get("providerFailures") or 0) == 0,
        "rerankFailuresZero": int(rerank.get("providerFailures") or 0) == 0,
        "rerankFallbackZero": int(rerank.get("fallbackCount") or 0) == 0,
        "runtimeFallbackZero": all(
            not bool((row.get("runtimeTrace") or {}).get("fallback"))
            for row in runtime.get("cases") or []
        ),
        "goldNeverUsedByRuntime": all(
            row.get("goldUsedByRuntime") is False for row in runtime.get("cases") or []
        ),
    }
    badcases = [
        {
            "caseId": row.get("caseId"),
            "route": row.get("route"),
            "catalogRecallAt10": ((row.get("metrics") or {}).get("metricsByK") or {})
            .get("10", {})
            .get("recall"),
            "unavailableRelevantIds": row.get("unavailableRelevantIds") or [],
            "availabilityNoResultCorrect": (
                row.get("availabilityAdjustedMetrics") or {}
            ).get("noResultCorrect"),
        }
        for row in runtime.get("cases") or []
        if float(
            (((row.get("metrics") or {}).get("metricsByK") or {}).get("10", {})).get(
                "recall"
            )
            or 0
        )
        < 1
        or (row.get("availabilityAdjustedMetrics") or {}).get("noResultCorrect") is False
    ]
    provider_facts = {
        "embedding": {key: value for key, value in embedding.items() if key != "responseRecords"},
        "rerank": {key: value for key, value in rerank.items() if key != "responseRecords"},
    }
    evidence_dir = EVIDENCE_ROOT / args.run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "evidenceKind": "POST_FIX_RUNTIME_REGRESSION",
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-live",
        "originalFinalRunId": args.original_final_run_id,
        "holdoutExposed": True,
        "freshEvidence": False,
        "caseCount": runtime.get("caseCount"),
        "routeCounts": runtime.get("routeCounts"),
        "catalogMetrics": runtime.get("metrics"),
        "availabilityAdjustedMetrics": adjusted,
        "availabilityFacts": runtime.get("availabilityFacts"),
        "stageLatency": runtime.get("stageLatency"),
        "providerFacts": provider_facts,
        "qualityGates": {**gates, "passed": all(gates.values())},
        "qualityGateState": (
            "PASSED_POST_FIX_REGRESSION" if all(gates.values()) else "FAILED_RETAINED"
        ),
        "badcases": badcases,
        "rawArtifactSha256": sha256_file(raw_path),
        "honestBoundaries": [
            "This regression reuses disclosed public/holdout cases and is not fresh holdout evidence.",
            "The original Search v2 final run remains FAILED_RETAINED and is not overwritten.",
            "Availability adjustment uses a Java-owned product snapshot only for scoring; gold labels never enter runtime retrieval.",
            "SYNTHETIC + local-live is not real-user or production-traffic evidence.",
            "No baseline was accepted or overwritten.",
        ],
    }
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(evidence_dir / "badcases.json", badcases)
    manifest = {
        "schemaVersion": 2,
        "runId": args.run_id,
        "evidenceKind": "POST_FIX_RUNTIME_REGRESSION",
        "summaryPath": str((evidence_dir / "summary.json").relative_to(PROJECT_ROOT)),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "datasetSha256": combined_sha(
            [PUBLIC_SEARCH_PATH, HOLDOUT_SEARCH_PATH], relative_to=PROJECT_ROOT
        ),
        "rawArtifacts": {
            str(raw_path.relative_to(PROJECT_ROOT)): sha256_file(raw_path),
        },
        "holdoutExposed": True,
        "freshEvidence": False,
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    report = [
        "# Search v2 post-fix runtime regression",
        "",
        f"- Run: `{args.run_id}`",
        f"- Original final: `{args.original_final_run_id}` (`FAILED_RETAINED`)",
        "- Evidence: `SYNTHETIC + local-live`",
        "- Holdout: exposed regression; not fresh evidence",
        f"- Quality gates: `{'PASS' if all(gates.values()) else 'FAILED_RETAINED'}`",
        "- Baseline: unchanged",
        "",
        "See `summary.json` for catalog and availability-adjusted metrics.",
    ]
    atomic_write_bytes(evidence_dir / "report.md", ("\n".join(report) + "\n").encode())
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    atomic_write_bytes(evidence_dir / "SHA256SUMS", ("\n".join(sums) + "\n").encode())
    return {
        "phase": "package-runtime-regression",
        "evidenceDir": str(evidence_dir),
        "qualityGates": gates,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--regenerate-chinese", action="store_true")
    for phase in ("collect-dev", "replay", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--chinese-index", default=DEFAULT_CHINESE_INDEX)
        child.add_argument("--wands-index", default=DEFAULT_WANDS_INDEX)
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
    runtime_package = subparsers.add_parser("package-runtime-regression")
    runtime_package.add_argument("--run-id", required=True)
    runtime_package.add_argument(
        "--original-final-run-id",
        default="search-v2-64aa86e-final-20260814",
    )
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return prepare(args)
    if args.phase == "collect-dev":
        return await collect_dev(args)
    if args.phase == "replay":
        return replay(args)
    if args.phase == "collect-final":
        return await collect_final(args)
    if args.phase == "package":
        return package(args)
    if args.phase == "package-runtime-regression":
        return package_runtime_regression(args)
    raise ValueError(f"unsupported Search v2 phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(async_main(args))
    except Exception as exc:
        run_id = getattr(args, "run_id", None)
        if run_id:
            failure_dir = RESULT_ROOT / run_id
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
