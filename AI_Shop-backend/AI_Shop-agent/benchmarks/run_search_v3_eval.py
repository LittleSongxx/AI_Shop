"""Run the immutable, one-shot Search v3 quality evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.artifacts import git_commit, workspace_sha256  # noqa: E402
from app.evaluation.ranking import aggregate_ranking_cases  # noqa: E402
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_bytes,
    atomic_write_json,
    combined_sha,
    read_gzip_json,
    sha256_file,
)
from benchmarks.mature_eval.product_service_runner import (  # noqa: E402
    run_product_service_cases,
)
from benchmarks.mature_eval.search_v2 import (  # noqa: E402
    choose_v2_configuration,
    collect_v2_cases,
    replay_v2_collection,
)
from benchmarks.mature_eval.search_v3_dataset import (  # noqa: E402
    FRESH_CHALLENGE_LOCK_PATH,
    FRESH_CHALLENGE_PATH,
    KNOWN_RUNTIME_LOCK_PATH,
    KNOWN_RUNTIME_PATH,
    MANDATORY_DYNAMIC_CATEGORY_ID,
    MANDATORY_NO_RESULT_ID,
    PUBLIC_RUNTIME_LOCK_PATH,
    PUBLIC_RUNTIME_PATH,
    RUNTIME_HOLDOUT_LOCK_PATH,
    RUNTIME_HOLDOUT_PATH,
    SUITE_LOCK_PATH,
    V2_LOCK_PATH,
    V2_PATH,
    validate_search_v3_files,
)
from benchmarks.run_search_rag_mature_eval import _index_dataset  # noqa: E402
from benchmarks.run_search_relevance import load_cases  # noqa: E402

SUITE = "search-v3"
RUN_ID_RE = re.compile(r"search-v3-[0-9a-f]{7,40}-[0-9]{8}(?:-[a-z0-9-]+)?")
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / SUITE
FRESH_EXECUTION_LOCK = RESULTS_ROOT / "_fresh-execution-lock.json"
DEFAULT_INDEX = "aishop_eval_search_v3"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not RUN_ID_RE.fullmatch(value):
        raise ValueError(
            "Search v3 run-id must match "
            "search-v3-<7..40 lowercase git hex>-<yyyymmdd>[-qualifier]"
        )
    return value


def _v2_payload() -> dict[str, Any]:
    return _json(V2_PATH)


def _v3_payload() -> dict[str, Any]:
    return _json(FRESH_CHALLENGE_PATH)


def _known_runtime_cases() -> list[dict[str, Any]]:
    public = [
        {**row, "split": "known_product_service"}
        for row in load_cases(PUBLIC_RUNTIME_PATH)
        if row.get("relevanceGrades")
    ]
    holdout = [
        {**row, "split": "known_product_service"}
        for row in load_cases(KNOWN_RUNTIME_PATH)
        if row.get("relevanceGrades")
    ]
    if len(public) != 30 or len(holdout) != 15:
        raise ValueError(
            f"Search v3 known ProductService cases changed: {len(public)} + {len(holdout)}"
        )
    return [*public, *holdout]


def _fresh_runtime_cases() -> list[dict[str, Any]]:
    rows = load_cases(RUNTIME_HOLDOUT_PATH)
    if len(rows) != 30:
        raise ValueError("Search v3 runtime holdout must contain 30 cases")
    return rows


def _search_provider_completeness(
    collection: Mapping[str, Any], *, expected_case_count: int
) -> dict[str, Any]:
    facts = collection.get("providerFacts") or {}
    embedding = facts.get("embedding") or {}
    responses = list(facts.get("responseFacts") or [])
    embedding_requests = int(facts.get("embeddingRequests") or 0)
    rerank_requests = int(facts.get("rerankRequests") or 0)
    checks = {
        "caseCountComplete": len(collection.get("cases") or []) == expected_case_count,
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


def _runtime_provider_completeness(
    runtime: Mapping[str, Any], *, expected_case_count: int
) -> dict[str, Any]:
    facts = runtime.get("providerFacts") or {}
    embedding = facts.get("embedding") or {}
    rerank = facts.get("rerank") or {}
    cases = list(runtime.get("cases") or [])
    checks = {
        "allCasesExecuted": len(cases) == expected_case_count
        and int(runtime.get("executedCount") or 0) == expected_case_count,
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


def prepare(_args: argparse.Namespace) -> dict[str, Any]:
    validation = validate_search_v3_files()
    return {
        "phase": "prepare",
        "suite": SUITE,
        "caseCounts": validation["suiteLock"]["caseCounts"],
        "mandatoryCases": validation["suiteLock"]["mandatoryCases"],
        "suiteLockSha256": sha256_file(SUITE_LOCK_PATH),
        "freshExecutionState": (
            _json(FRESH_EXECUTION_LOCK) if FRESH_EXECUTION_LOCK.is_file() else "NOT_EXECUTED"
        ),
    }


async def collect_known(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_search_v3_files()
    run_root = RESULTS_ROOT / run_id
    frozen_path = run_root / "frozen-config.json"
    replay_path = run_root / "known-chinese-replay.json"
    runtime_path = run_root / "raw" / "known-product-service.json.gz"
    if frozen_path.is_file():
        if not replay_path.is_file() or not runtime_path.is_file():
            raise ValueError("Search v3 known collection is incomplete")
        frozen = _json(frozen_path)
        if frozen.get("runId") != run_id:
            raise ValueError("Search v3 frozen run identity mismatch")
        return {"phase": "collect-known", "runId": run_id, "reused": True}

    v2 = _v2_payload()
    raw_path = run_root / "raw" / "known-chinese-collection.json.gz"
    await redis_service.ensure_connected()
    try:
        index_result = await _index_dataset(
            products=v2["products"],
            dataset="search-v3-known",
            index_name=args.index,
            vector_path=run_root / "raw" / "search-v3-product-vectors.json.gz",
        )
        collection = await collect_v2_cases(
            cases=v2["queries"],
            products=v2["products"],
            index=args.index,
            output_path=raw_path,
            candidate_size=50,
            rerank_pool_size=100,
        )
        runtime = await run_product_service_cases(
            _known_runtime_cases(), output_path=runtime_path
        )
    finally:
        await redis_service.close()
    replay = replay_v2_collection(collection, products=v2["products"])
    selected = choose_v2_configuration(replay)
    atomic_write_json(replay_path, replay)
    frozen = {
        "schemaVersion": 3,
        "suite": SUITE,
        "runId": run_id,
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "selectionData": "240 disclosed Search v2 cases + 45 disclosed ProductService cases; no v3 fresh data",
        "search": selected,
        "index": args.index,
        "oracleUsedForSelection": False,
        "providerCompleteness": {
            "knownChinese": _search_provider_completeness(
                collection, expected_case_count=240
            ),
            "knownProductService": _runtime_provider_completeness(
                runtime, expected_case_count=45
            ),
        },
        "datasetSha256": combined_sha(
            [
                V2_PATH,
                V2_LOCK_PATH,
                PUBLIC_RUNTIME_PATH,
                PUBLIC_RUNTIME_LOCK_PATH,
                KNOWN_RUNTIME_PATH,
                KNOWN_RUNTIME_LOCK_PATH,
                SUITE_LOCK_PATH,
            ],
            relative_to=REPO_ROOT,
        ),
    }
    atomic_write_json(frozen_path, frozen)
    return {
        "phase": "collect-known",
        "runId": run_id,
        "index": index_result,
        "selected": selected,
        "providerCompleteness": frozen["providerCompleteness"],
    }


def _claim_fresh_execution(run_id: str) -> dict[str, Any]:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    dataset_sha = combined_sha(
        [
            FRESH_CHALLENGE_PATH,
            FRESH_CHALLENGE_LOCK_PATH,
            RUNTIME_HOLDOUT_PATH,
            RUNTIME_HOLDOUT_LOCK_PATH,
        ],
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
            FRESH_EXECUTION_LOCK,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError:
        existing = _json(FRESH_EXECUTION_LOCK)
        if existing.get("runId") != run_id or existing.get("datasetSha256") != dataset_sha:
            raise ValueError(
                "Search v3 fresh data was already claimed by another retained run; "
                "create Search v4 with a new holdout"
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
    validate_search_v3_files()
    run_root = RESULTS_ROOT / run_id
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("collect Search v3 known cases and freeze configuration first")
    finalization_path = run_root / "finalization.json"
    replay_path = run_root / "fresh-challenge-replay.json"
    collection_path = run_root / "raw" / "fresh-challenge-collection.json.gz"
    runtime_path = run_root / "raw" / "runtime-holdout.json.gz"
    if finalization_path.is_file():
        if not replay_path.is_file() or not collection_path.is_file() or not runtime_path.is_file():
            raise ValueError("Search v3 finalization is incomplete")
        finalization = _json(finalization_path)
        if finalization.get("frozenConfigSha256") != sha256_file(frozen_path):
            raise ValueError("Search v3 finalization frozen-config SHA mismatch")
        return {"phase": "collect-final", "runId": run_id, "reused": True}

    claim = _claim_fresh_execution(run_id)
    frozen = _json(frozen_path)
    payload = _v3_payload()
    await redis_service.ensure_connected()
    try:
        index_result = await _index_dataset(
            products=payload["products"],
            dataset="search-v3-fresh",
            index_name=str(frozen.get("index") or args.index),
            vector_path=run_root / "raw" / "search-v3-product-vectors.json.gz",
        )
        collection = await collect_v2_cases(
            cases=payload["queries"],
            products=payload["products"],
            index=str(frozen.get("index") or args.index),
            output_path=collection_path,
            candidate_size=50,
            rerank_pool_size=100,
        )
        runtime = await run_product_service_cases(
            _fresh_runtime_cases(), output_path=runtime_path
        )
    finally:
        await redis_service.close()
    replay = replay_v2_collection(collection, products=payload["products"])
    selected = str((frozen.get("search") or {}).get("selectedVariant") or "")
    if selected not in (replay.get("variantMetrics") or {}):
        raise ValueError("Search v3 fresh replay lacks the frozen configuration")
    atomic_write_json(replay_path, replay)
    provider = {
        "freshChallenge": _search_provider_completeness(
            collection, expected_case_count=120
        ),
        "runtimeHoldout": _runtime_provider_completeness(
            runtime, expected_case_count=30
        ),
    }
    finalization = {
        "schemaVersion": 3,
        "suite": SUITE,
        "runId": run_id,
        "finalizedAt": datetime.now(timezone.utc).isoformat(),
        "freshHoldoutExecutedOnceByThisRun": True,
        "executionClaim": claim,
        "frozenConfigSha256": sha256_file(frozen_path),
        "freshDatasetSha256": sha256_file(FRESH_CHALLENGE_PATH),
        "runtimeDatasetSha256": sha256_file(RUNTIME_HOLDOUT_PATH),
        "selectedVariant": selected,
        "providerCompleteness": provider,
    }
    atomic_write_json(finalization_path, finalization)
    return {
        "phase": "collect-final",
        "runId": run_id,
        "index": index_result,
        "providerCompleteness": provider,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return aggregate_ranking_cases([dict(row.get("metrics") or {}) for row in rows])


def _metric(metrics: Mapping[str, Any], k: str, name: str) -> float:
    return float(((metrics.get("metricCurves") or {}).get(k) or {}).get(name) or 0)


def _runtime_badcases(runtime: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "caseId": row.get("caseId"),
            "status": row.get("status"),
            "route": row.get("route"),
            "recallAt10": (((row.get("metrics") or {}).get("metricsByK") or {}).get("10") or {}).get("recall"),
            "expectedNoResults": (row.get("metrics") or {}).get("expectedNoResults"),
            "noResultCorrect": (row.get("metrics") or {}).get("noResultCorrect"),
        }
        for row in runtime.get("cases") or []
        if row.get("status") != "EXECUTED"
        or float(((((row.get("metrics") or {}).get("metricsByK") or {}).get("10") or {}).get("recall")) or 0) < 1
        or (row.get("metrics") or {}).get("noResultCorrect") is False
    ]


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_id = _validate_run_id(args.run_id)
    validate_search_v3_files()
    run_root = RESULTS_ROOT / run_id
    required = {
        "frozen": run_root / "frozen-config.json",
        "knownReplay": run_root / "known-chinese-replay.json",
        "knownRuntime": run_root / "raw" / "known-product-service.json.gz",
        "finalization": run_root / "finalization.json",
        "freshReplay": run_root / "fresh-challenge-replay.json",
        "freshRuntime": run_root / "raw" / "runtime-holdout.json.gz",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete Search v3 run: {missing}")
    frozen = _json(required["frozen"])
    known_replay = _json(required["knownReplay"])
    known_runtime = read_gzip_json(required["knownRuntime"])
    finalization = _json(required["finalization"])
    fresh_replay = _json(required["freshReplay"])
    fresh_runtime = read_gzip_json(required["freshRuntime"])
    selected = str((frozen.get("search") or {}).get("selectedVariant") or "")
    known_rows = list((known_replay.get("cases") or {}).get(selected) or [])
    final_rows = list((fresh_replay.get("cases") or {}).get(selected) or [])
    if len(known_rows) != 240 or len(final_rows) != 120:
        raise ValueError("Search v3 selected replay case count changed")
    fresh_rows = [row for row in final_rows if row.get("split") == "fresh_holdout"]
    challenge_positive = [
        row
        for row in final_rows
        if row.get("split") == "challenge"
        and not bool((row.get("metrics") or {}).get("expectedNoResults"))
    ]
    challenge_no_result = [
        row
        for row in final_rows
        if row.get("split") == "challenge"
        and bool((row.get("metrics") or {}).get("expectedNoResults"))
    ]
    fresh_metrics = _aggregate(fresh_rows)
    challenge_positive_metrics = _aggregate(challenge_positive)
    challenge_no_result_metrics = _aggregate(challenge_no_result)
    runtime_curve = ((fresh_runtime.get("metrics") or {}).get("metricCurves") or {}).get("10") or {}
    all_provider = {
        **dict(frozen.get("providerCompleteness") or {}),
        **dict(finalization.get("providerCompleteness") or {}),
    }
    mandatory_ids = {MANDATORY_NO_RESULT_ID, MANDATORY_DYNAMIC_CATEGORY_ID}
    executed_ids = {str(row.get("caseId") or "") for row in final_rows}.union(
        str(row.get("caseId") or "") for row in fresh_runtime.get("cases") or []
    )
    constraint_violations = sum(
        int(row.get("constraintViolationCount") or 0) for row in final_rows
    )
    checks = {
        "knownChineseExecuted": len(known_rows) == 240,
        "knownProductServiceExecuted": int(known_runtime.get("executedCount") or 0) == 45,
        "freshExecuted": len(fresh_rows) == 80,
        "challengeExecuted": len(challenge_positive) == 20 and len(challenge_no_result) == 20,
        "runtimeHoldoutExecuted": int(fresh_runtime.get("executedCount") or 0) == 30,
        "mandatoryCasesExecuted": mandatory_ids.issubset(executed_ids),
        "freshRecallAt3": _metric(fresh_metrics, "3", "recall") >= 0.85,
        "freshNdcgAt5": _metric(fresh_metrics, "5", "ndcg") >= 0.80,
        "challengePositiveRecallAt3": _metric(challenge_positive_metrics, "3", "recall") >= 0.85,
        "challengeNoResultAccuracy": float(challenge_no_result_metrics.get("noResultAccuracy") or 0) >= 0.90,
        "runtimeRecallAt10": float(runtime_curve.get("recall") or 0) >= 0.80,
        "runtimeMrrAt10": float(runtime_curve.get("mrr") or 0) >= 0.65,
        "runtimeNdcgAt10": float(runtime_curve.get("ndcg") or 0) >= 0.70,
        "constraintViolationsZero": constraint_violations == 0,
        "providerCompleteness": len(all_provider) == 4
        and all(bool((value or {}).get("passed")) for value in all_provider.values()),
    }
    badcases = {
        "freshChallenge": [
            {
                "caseId": row.get("caseId"),
                "split": row.get("split"),
                "queryType": row.get("queryType"),
                "recallAt3": ((((row.get("metrics") or {}).get("metricsByK") or {}).get("3") or {}).get("recall")),
                "noResultCorrect": (row.get("metrics") or {}).get("noResultCorrect"),
                "constraintViolationCount": row.get("constraintViolationCount"),
            }
            for row in final_rows
            if (
                bool((row.get("metrics") or {}).get("expectedNoResults"))
                and (row.get("metrics") or {}).get("noResultCorrect") is not True
            )
            or (
                not bool((row.get("metrics") or {}).get("expectedNoResults"))
                and float((((row.get("metrics") or {}).get("metricsByK") or {}).get("3") or {}).get("recall") or 0) < 1
            )
            or int(row.get("constraintViolationCount") or 0) > 0
        ],
        "runtimeHoldout": _runtime_badcases(fresh_runtime),
    }
    summary = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC+local-live",
        "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
        "datasets": _json(SUITE_LOCK_PATH),
        "frozenConfiguration": frozen,
        "metrics": {
            "knownChinese": (known_replay.get("variantMetrics") or {}).get(selected),
            "knownProductService": known_runtime.get("metrics"),
            "fresh": fresh_metrics,
            "challengePositive": challenge_positive_metrics,
            "challengeNoResult": challenge_no_result_metrics,
            "runtimeHoldout": fresh_runtime.get("metrics"),
        },
        "providerCompleteness": all_provider,
        "constraintViolationCount": constraint_violations,
        "qualityGates": {
            **checks,
            "passed": all(checks.values()),
            "status": "PASSED" if all(checks.values()) else "FAILED_RETAINED",
        },
        "badcases": badcases,
        "honestBoundaries": [
            "The 600-product ranking set is deterministic SYNTHETIC data.",
            "The 30-case ProductService holdout is developer-labelled against the locked 47-product local mirror.",
            "The run is local-live, not production traffic, and local latency is not a production SLO.",
            "Fresh data is one-shot; a failed result remains FAILED_RETAINED.",
            "Gold labels never enter runtime retrieval or reranking.",
        ],
    }
    evidence_dir = EVIDENCE_ROOT / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(evidence_dir / "summary.json", summary)
    atomic_write_json(evidence_dir / "badcases.json", badcases)
    manifest = {
        "schemaVersion": 3,
        "suite": SUITE,
        "runId": run_id,
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "badcasesSha256": sha256_file(evidence_dir / "badcases.json"),
        "suiteLockSha256": sha256_file(SUITE_LOCK_PATH),
        "freshExecutionLockSha256": sha256_file(FRESH_EXECUTION_LOCK),
        "localArtifacts": {
            str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
            for path in required.values()
        },
        "status": summary["qualityGates"]["status"],
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    report = [
        "# Search v3",
        "",
        f"- Run: `{run_id}`",
        f"- Quality gate: `{summary['qualityGates']['status']}`",
        f"- Fresh Recall@3 / NDCG@5: `{_metric(fresh_metrics, '3', 'recall')}` / `{_metric(fresh_metrics, '5', 'ndcg')}`",
        f"- Challenge positive Recall@3: `{_metric(challenge_positive_metrics, '3', 'recall')}`",
        f"- Challenge no-result accuracy: `{challenge_no_result_metrics.get('noResultAccuracy')}`",
        f"- Runtime Recall@10 / MRR@10 / NDCG@10: `{runtime_curve.get('recall')}` / `{runtime_curve.get('mrr')}` / `{runtime_curve.get('ndcg')}`",
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
        "qualityGates": summary["qualityGates"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    subparsers.add_parser("prepare")
    for phase in ("collect-known", "collect-final", "package"):
        child = subparsers.add_parser(phase)
        child.add_argument("--run-id", required=True)
        child.add_argument("--index", default=DEFAULT_INDEX)
        if phase == "collect-final":
            child.add_argument("--finalize-holdout", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return prepare(args)
    if args.phase == "collect-known":
        return await collect_known(args)
    if args.phase == "collect-final":
        return await collect_final(args)
    if args.phase == "package":
        return package(args)
    raise ValueError(f"unsupported Search v3 phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(async_main(args))
    except Exception as exc:
        run_id = getattr(args, "run_id", None)
        if run_id and RUN_ID_RE.fullmatch(str(run_id)):
            failure_dir = RESULTS_ROOT / str(run_id)
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
