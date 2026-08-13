"""Prepare, collect, replay and package the mature Search/RAG evaluation."""

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

from app.config.settings import get_settings  # noqa: E402
from app.evaluation.artifacts import git_commit, workspace_sha256  # noqa: E402
from app.rag.embedding import embedding_evaluation_scope  # noqa: E402
from app.services.redis_service import redis_service  # noqa: E402
from benchmarks.mature_eval.chinese_dataset import (  # noqa: E402
    generate_dataset,
    validate_dataset,
)
from benchmarks.mature_eval.common import (  # noqa: E402
    atomic_write_json,
    combined_sha,
    read_gzip_json,
    sha256_file,
)
from benchmarks.mature_eval.indexing import (  # noqa: E402
    EvaluationIndexManager,
    index_document,
    product_embedding_text,
)
from benchmarks.mature_eval.rag_pipeline import (  # noqa: E402
    choose_rag_configuration,
    collect_rag_cases,
    load_rag_sets,
    replay_rag_collection,
)
from benchmarks.mature_eval.search_pipeline import (  # noqa: E402
    WANDS_SEARCH_VARIANTS,
    choose_configuration,
    collect_cases,
    replay_collection,
)
from benchmarks.mature_eval.wands import prepare_wands, validate_subset  # noqa: E402

SUITE = "search-rag-mature-v1"
DATASETS_ROOT = PROJECT_ROOT / "benchmarks" / "datasets" / "mature_v1"
RAW_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE / "raw"
RESULTS_ROOT = PROJECT_ROOT / "benchmarks" / "results" / SUITE
EVIDENCE_ROOT = PROJECT_ROOT / "benchmarks" / "evidence" / SUITE
CHINESE_DATASET = DATASETS_ROOT / "chinese-commerce-search-v1.json"
WANDS_TRACKED_ROOT = DATASETS_ROOT / "wands"
WANDS_RAW_ROOT = RAW_ROOT / "wands-source"
RAG_PUBLIC = PROJECT_ROOT / "scripts" / "rag_golden.jsonl"
RAG_REGRESSION = PROJECT_ROOT / "benchmarks" / "datasets" / "rag_holdout_v1.jsonl"
RAG_FRESH = PROJECT_ROOT / "benchmarks" / "datasets" / "rag_fresh_holdout_v2.jsonl"
DEFAULT_CHINESE_INDEX = "aishop_eval_chinese_v1"
DEFAULT_WANDS_INDEX = "aishop_eval_wands_v1"


def _run_id(value: str | None) -> str:
    return value or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_chinese() -> dict[str, Any]:
    payload = json.loads(CHINESE_DATASET.read_text(encoding="utf-8"))
    validate_dataset(payload)
    return payload


def _load_wands() -> dict[str, Any]:
    payload = json.loads((WANDS_TRACKED_ROOT / "selection.json").read_text(encoding="utf-8"))
    validate_subset(payload)
    return payload


async def _embed_products(
    products: list[dict[str, Any]],
    *,
    dataset: str,
    vector_path: Path,
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    from app.rag.embedding import embed_texts
    from benchmarks.mature_eval.common import write_gzip_json

    existing: dict[str, list[float]] = {}
    previous_facts: dict[str, Any] = {}
    if vector_path.is_file():
        payload = read_gzip_json(vector_path)
        if payload.get("dataset") != dataset or int(payload.get("dimensions") or 0) != 1024:
            raise ValueError("existing product-vector checkpoint does not match this dataset")
        existing = {
            str(key): [float(value) for value in vector]
            for key, vector in (payload.get("vectors") or {}).items()
        }
        previous_facts = dict(payload.get("providerFacts") or {})
    pending = [
        product
        for product in products
        if str(product.get("id") or product.get("product_id") or "") not in existing
    ]
    def merged_facts(current: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: int(previous_facts.get(key) or 0) + int(current.get(key) or 0)
            for key in (
                "requests",
                "cacheHits",
                "providerRequests",
                "providerSuccesses",
                "providerFailures",
                "breakerRejections",
            )
        }
        result["bypassCache"] = True
        result["responseRecords"] = [
            *(previous_facts.get("responseRecords") or []),
            *(current.get("responseRecords") or []),
        ]
        return result

    def checkpoint(facts: dict[str, Any]) -> None:
        write_gzip_json(
            vector_path,
            {
                "schemaVersion": 1,
                "dataset": dataset,
                "model": get_settings().embedding_model,
                "dimensions": 1024,
                "embeddingTextMaxChars": 6_000 if dataset == "wands" else None,
                "providerFacts": facts,
                "vectors": existing,
            },
        )

    with embedding_evaluation_scope(bypass_cache=True) as stats:
        for start in range(0, len(pending), 200):
            chunk = pending[start : start + 200]
            vectors = await embed_texts(
                [product_embedding_text(product, dataset=dataset) for product in chunk],
                # DashScope text-embedding-v4 accepts at most ten input strings
                # in one OpenAI-compatible request.
                batch_size=10,
            )
            failures: list[str] = []
            for product, vector in zip(chunk, vectors):
                product_id = str(product.get("id") or product.get("product_id") or "")
                if not vector or len(vector) != 1024:
                    failures.append(product_id)
                    continue
                existing[product_id] = [float(value) for value in vector]
            checkpoint(merged_facts(stats.snapshot()))
            if failures:
                raise RuntimeError(f"product embedding failed: {failures[:5]}")
        provider_facts = merged_facts(stats.snapshot())
    if provider_facts["cacheHits"] or provider_facts["providerFailures"]:
        raise RuntimeError("product embedding evidence is incomplete")
    if len(existing) != len(products):
        raise RuntimeError(f"product vector checkpoint has {len(existing)}/{len(products)} vectors")
    checkpoint(provider_facts)
    return existing, provider_facts


async def _index_dataset(
    *,
    products: list[dict[str, Any]],
    dataset: str,
    index_name: str,
    vector_path: Path,
) -> dict[str, Any]:
    settings = get_settings()
    manager = EvaluationIndexManager(
        index_name,
        es_hosts=settings.es_hosts,
        dimensions=settings.embedding_dimensions,
    )
    await manager.ensure()
    ids = [str(row.get("id") or row.get("product_id") or "") for row in products]
    existing_ids = await manager.existing_ids(ids)
    vectors, provider_facts = await _embed_products(
        products,
        dataset=dataset,
        vector_path=vector_path,
    )
    documents = [
        index_document(product, dataset=dataset, embedding=vectors[product_id])
        for product, product_id in zip(products, ids)
        if product_id not in existing_ids
    ]
    indexed = await manager.bulk_upsert(documents)
    count = await manager.count()
    if count < len(products):
        raise RuntimeError(f"evaluation index {index_name} has {count}/{len(products)} documents")
    return {
        "index": index_name,
        "expectedProducts": len(products),
        "existingProducts": len(existing_ids),
        "indexedProducts": indexed,
        "indexCount": count,
        "vectorFile": str(vector_path),
        "vectorSha256": sha256_file(vector_path),
        "providerFacts": provider_facts,
    }


async def prepare(args: argparse.Namespace) -> dict[str, Any]:
    DATASETS_ROOT.mkdir(parents=True, exist_ok=True)
    if not CHINESE_DATASET.is_file() or args.regenerate_chinese:
        await generate_dataset(
            CHINESE_DATASET,
            resume_dir=RAW_ROOT / "chinese-generation",
        )
    chinese = _load_chinese()
    wands = prepare_wands(WANDS_RAW_ROOT, WANDS_TRACKED_ROOT)
    return {
        "phase": "prepare",
        "chinese": validate_dataset(chinese),
        "wands": wands["lock"],
    }


async def collect_dev(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    chinese = _load_chinese()
    rag_sets = load_rag_sets(RAG_PUBLIC, RAG_REGRESSION, RAG_FRESH)
    await redis_service.ensure_connected()
    try:
        index = await _index_dataset(
            products=chinese["products"],
            dataset="chinese",
            index_name=args.chinese_index,
            vector_path=run_root / "raw" / "chinese-product-vectors.json.gz",
        )
        public_cases = [case for case in chinese["queries"] if case["split"] == "public"]
        collection = await collect_cases(
            cases=public_cases,
            products=chinese["products"],
            index=args.chinese_index,
            output_path=run_root / "raw" / "chinese-dev-collection.json.gz",
        )
        rag_dev_cases = [*rag_sets["public"], *rag_sets["regression"]]
        rag_collection = await collect_rag_cases(
            rag_dev_cases,
            output_path=run_root / "raw" / "rag-dev-collection.json.gz",
        )
    finally:
        await redis_service.close()
    return {
        "phase": "collect-dev",
        "index": index,
        "collectionCases": {
            "chinese": len(collection["cases"]),
            "ragDev": len(rag_collection["cases"]),
        },
        "providerFacts": {
            "chinese": collection["providerFacts"],
            "ragDev": rag_collection["providerFacts"],
        },
    }


def replay(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    chinese = _load_chinese()
    collection_path = run_root / "raw" / "chinese-dev-collection.json.gz"
    report = replay_collection(
        collection_path,
        products=chinese["products"],
        split_filter={"public"},
    )
    selected = choose_configuration(report)
    rag_report = replay_rag_collection(
        run_root / "raw" / "rag-dev-collection.json.gz",
        split_filter={"public", "regression"},
    )
    rag_selected = choose_rag_configuration(rag_report)
    atomic_write_json(run_root / "chinese-dev-replay.json", report)
    atomic_write_json(run_root / "rag-dev-replay.json", rag_report)
    atomic_write_json(
        run_root / "frozen-config.json",
        {"search": selected, "rag": rag_selected},
    )
    return {"phase": "replay", "search": selected, "rag": rag_selected}


async def collect_final(args: argparse.Namespace) -> dict[str, Any]:
    if not args.finalize_holdout:
        raise ValueError("collect-final requires explicit --finalize-holdout")
    run_root = RESULTS_ROOT / args.run_id
    frozen_path = run_root / "frozen-config.json"
    if not frozen_path.is_file():
        raise ValueError("freeze dev configuration with replay before final collection")
    chinese = _load_chinese()
    wands = _load_wands()
    rag_sets = load_rag_sets(RAG_PUBLIC, RAG_REGRESSION, RAG_FRESH)
    final_chinese = [case for case in chinese["queries"] if case["split"] != "public"]
    await redis_service.ensure_connected()
    try:
        chinese_index = await _index_dataset(
            products=chinese["products"],
            dataset="chinese",
            index_name=args.chinese_index,
            vector_path=run_root / "raw" / "chinese-product-vectors.json.gz",
        )
        wands_index = await _index_dataset(
            products=wands["products"],
            dataset="wands",
            index_name=args.wands_index,
            vector_path=run_root / "raw" / "wands-product-vectors.json.gz",
        )
        chinese_collection = await collect_cases(
            cases=final_chinese,
            products=chinese["products"],
            index=args.chinese_index,
            output_path=run_root / "raw" / "chinese-final-collection.json.gz",
        )
        wands_collection = await collect_cases(
            cases=wands["queries"],
            products=wands["products"],
            index=args.wands_index,
            output_path=run_root / "raw" / "wands-final-collection.json.gz",
        )
        rag_collection = await collect_rag_cases(
            rag_sets["fresh_holdout"],
            output_path=run_root / "raw" / "rag-final-collection.json.gz",
        )
    finally:
        await redis_service.close()
    chinese_report = replay_collection(
        chinese_collection,
        products=chinese["products"],
        split_filter={"fresh_holdout", "challenge"},
    )
    wands_report = replay_collection(
        wands_collection,
        products=wands["products"],
        dataset="wands",
        variants=WANDS_SEARCH_VARIANTS,
    )
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    if "search" not in frozen or "rag" not in frozen:
        raise ValueError("frozen configuration must include Search and RAG dev selections")
    rag_final = replay_rag_collection(
        rag_collection,
        split_filter={"fresh_holdout"},
    )
    atomic_write_json(run_root / "chinese-final-replay.json", chinese_report)
    atomic_write_json(run_root / "wands-final-replay.json", wands_report)
    atomic_write_json(run_root / "rag-final-replay.json", rag_final)
    atomic_write_json(
        run_root / "finalization.json",
        {
            "finalizedAt": datetime.now(timezone.utc).isoformat(),
            "finalizeHoldoutExplicit": True,
            "freshHoldoutExecutedOnceByThisRun": True,
        },
    )
    return {
        "phase": "collect-final",
        "indices": {"chinese": chinese_index, "wands": wands_index},
        "cases": {
            "chinese": len(chinese_collection["cases"]),
            "wands": len(wands_collection["cases"]),
            "rag": len(rag_collection["cases"]),
        },
        "ragSelected": frozen["rag"],
    }


def _badcases(report: dict[str, Any], *, metric: str = "recall", k: str = "5") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for variant, rows in (report.get("cases") or {}).items():
        for row in rows:
            ranking = row.get("metrics") or row.get("ranking") or {}
            value = (((ranking.get("metricsByK") or {}).get(k) or {}).get(metric))
            if value is not None and float(value) < 1.0:
                result.append(
                    {
                        "variant": variant,
                        "caseId": row.get("caseId"),
                        "metric": f"{metric}@{k}",
                        "value": value,
                    }
                )
    return result


def package(args: argparse.Namespace) -> dict[str, Any]:
    run_root = RESULTS_ROOT / args.run_id
    required = [
        run_root / "chinese-dev-replay.json",
        run_root / "chinese-final-replay.json",
        run_root / "wands-final-replay.json",
        run_root / "rag-dev-replay.json",
        run_root / "rag-final-replay.json",
        run_root / "frozen-config.json",
        run_root / "finalization.json",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"cannot package incomplete run; missing {missing}")
    reports = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in required}
    evidence_dir = EVIDENCE_ROOT / args.run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    compact = {
        "schemaVersion": "aishop-eval/v1",
        "suite": SUITE,
        "runId": args.run_id,
        "gitCommit": git_commit(REPO_ROOT),
        "workspaceSha256": workspace_sha256(REPO_ROOT),
        "evidenceSource": "SYNTHETIC",
        "executionMode": "local-live",
        "labelScope": {
            "chinese": "full-catalog structured labels",
            "wands": "judged-pool",
            "rag": "locked FAQ and knowledge references",
        },
        "metricCurves": {
            key: report.get("metricCurves")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("metricCurves")
        },
        "variantMetrics": {
            key: report.get("variantMetrics")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("variantMetrics")
        },
        "pairedDeltas": {
            key: report.get("pairedDeltas")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("pairedDeltas") is not None
        },
        "confidenceIntervals": {
            key: report.get("confidenceIntervals")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("confidenceIntervals") is not None
        },
        "stageLatency": {
            key: report.get("stageLatency")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("stageLatency")
        },
        "providerFacts": {
            key: report.get("providerFacts")
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("providerFacts")
        },
        "frozenConfiguration": reports["frozen-config"],
        "badcases": {
            key: _badcases(report)
            for key, report in reports.items()
            if isinstance(report, dict) and report.get("cases")
        },
        "honestBoundaries": [
            "Chinese products and queries are explicitly SYNTHETIC; labels are computed from structured constraints.",
            "WANDS metrics rank only each query's complete human-judged pool, not the full 42,994-product catalog.",
            "P95/P99 describe local samples and are not production SLOs.",
            "No baseline was accepted or overwritten by this runner.",
        ],
    }
    atomic_write_json(evidence_dir / "summary.json", compact)
    source_sha = {
        str(path.relative_to(PROJECT_ROOT)): sha256_file(path)
        for path in required
    }
    raw_files = sorted((run_root / "raw").glob("*.json.gz"))
    raw_sha = {str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in raw_files}
    manifest = {
        "schemaVersion": 1,
        "runId": args.run_id,
        "summaryPath": str((evidence_dir / "summary.json").relative_to(PROJECT_ROOT)),
        "summarySha256": sha256_file(evidence_dir / "summary.json"),
        "datasetSha256": combined_sha(
            [
                CHINESE_DATASET,
                CHINESE_DATASET.with_suffix(".lock.json"),
                WANDS_TRACKED_ROOT / "selection.json",
                WANDS_TRACKED_ROOT / "selection.lock.json",
                RAG_PUBLIC,
                RAG_REGRESSION,
                RAG_FRESH,
            ],
            relative_to=PROJECT_ROOT,
        ),
        "compactSources": source_sha,
        "rawArtifacts": raw_sha,
    }
    atomic_write_json(evidence_dir / "run-manifest.json", manifest)
    sums = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(evidence_dir.iterdir())
        if path.name != "SHA256SUMS"
    ]
    (evidence_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    report_lines = [
        "# Search/RAG mature evaluation",
        "",
        f"- Run: `{args.run_id}`",
        "- Evidence: `SYNTHETIC` + `local-live`",
        "- WANDS scope: complete `judged-pool` per query",
        "- Baseline: unchanged",
        "",
        "The machine-readable metrics, paired deltas, confidence intervals, badcases and Provider facts are in `summary.json`.",
    ]
    (evidence_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return {"phase": "package", "evidenceDir": str(evidence_dir), "manifest": manifest}


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
    return parser


async def async_main(args: argparse.Namespace) -> dict[str, Any]:
    if args.phase == "prepare":
        return await prepare(args)
    if args.phase == "collect-dev":
        return await collect_dev(args)
    if args.phase == "replay":
        return replay(args)
    if args.phase == "collect-final":
        return await collect_final(args)
    if args.phase == "package":
        return package(args)
    raise ValueError(f"unsupported phase: {args.phase}")


def main() -> None:
    args = build_parser().parse_args()
    if not getattr(args, "run_id", None):
        args.run_id = _run_id(None)
    result = asyncio.run(async_main(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
