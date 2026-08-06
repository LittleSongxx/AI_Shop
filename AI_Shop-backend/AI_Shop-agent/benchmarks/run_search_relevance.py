"""Search relevance evaluation, in two layers.

Layer 1 - query understanding (default, always runnable):
    Asserts the normalization contract in search_taxonomy.yml deterministically.
    No Elasticsearch, no LLM, no product catalogue. This is what guards against a
    taxonomy edit silently breaking recall for a whole category.

Layer 2 - graded relevance (--graded, needs live services + locked labels):
    Runs the real hybrid retrieval path and scores Recall@K / MRR / NDCG@K against
    hand-labelled relevantProductIds from the reproducible 47-product mirror.
    A missing label set, catalog mismatch, missing ES index, or dead recall
    channel is a failure rather than a successful skip.

Usage:
    python benchmarks/run_search_relevance.py
    python benchmarks/run_search_relevance.py --graded
    python benchmarks/run_search_relevance.py --emit-template labels.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATASET = Path(__file__).with_name("search_relevance_v1.jsonl")
DEFAULT_LOCK = Path(__file__).with_name("search_relevance_v1.lock.json")
DEFAULT_CATALOG = PROJECT_ROOT.parent / "data" / "simlect_catalog" / "catalog.json"


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def evaluate_query_understanding(cases: list[dict]) -> dict[str, Any]:
    """Score normalization and term expansion. Pure functions, no I/O."""
    from app.services.product_search_query import (
        match_terms_for_query,
        normalize_product_search_query,
    )

    keyword_graded = keyword_passed = 0
    term_graded = term_passed = 0
    failures: list[dict] = []
    by_subset: dict[str, dict[str, int]] = {}

    for case in cases:
        subset = str(case.get("subset") or "default")
        bucket = by_subset.setdefault(subset, {"graded": 0, "passed": 0})
        query = case.get("query") or ""
        actual_keyword = normalize_product_search_query(query)
        actual_terms = match_terms_for_query(query)

        if "expectKeyword" in case:
            keyword_graded += 1
            bucket["graded"] += 1
            if actual_keyword == case["expectKeyword"]:
                keyword_passed += 1
                bucket["passed"] += 1
            else:
                failures.append(
                    {
                        "id": case.get("id"),
                        "subset": subset,
                        "query": query,
                        "field": "keyword",
                        "expected": case["expectKeyword"],
                        "actual": actual_keyword,
                        "note": case.get("note"),
                    }
                )

        expected_terms = case.get("expectTerms")
        if isinstance(expected_terms, list) and expected_terms:
            term_graded += 1
            lowered = {str(term).lower() for term in actual_terms}
            missing = [term for term in expected_terms if str(term).lower() not in lowered]
            if missing:
                failures.append(
                    {
                        "id": case.get("id"),
                        "subset": subset,
                        "query": query,
                        "field": "terms",
                        "missing": missing,
                        "actual": actual_terms,
                        "note": case.get("note"),
                    }
                )
            else:
                term_passed += 1

    return {
        "cases": len(cases),
        "keywordGraded": keyword_graded,
        "keywordAccuracy": round(keyword_passed / keyword_graded, 4) if keyword_graded else 0.0,
        "termGraded": term_graded,
        "termCoverage": round(term_passed / term_graded, 4) if term_graded else 0.0,
        "bySubset": {
            name: {
                **counts,
                "passRate": round(counts["passed"] / counts["graded"], 4)
                if counts["graded"]
                else 0.0,
            }
            for name, counts in sorted(by_subset.items())
        },
        "failures": failures,
    }


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def _ndcg(ranked_ids: list[str], grades: dict[str, float], k: int) -> float:
    gains = [float(grades.get(pid, 0.0)) for pid in ranked_ids[:k]]
    ideal = sorted((float(value) for value in grades.values()), reverse=True)[:k]
    best = _dcg(ideal)
    return (_dcg(gains) / best) if best else 0.0


async def _retrieve_channels(query: str, k: int) -> dict[str, list[str]]:
    """Run the same dual recall + RRF fusion the live search path uses."""
    from app.constants import PRODUCT_CANDIDATE_SIZE
    from app.rag.retriever import rag_retriever
    from app.rag.rrf import rrf_merge
    from app.services.product_search_query import normalize_product_search_query

    normalized = normalize_product_search_query(query) or query
    keyword_ids = await rag_retriever.search_product_keyword_ids(normalized, PRODUCT_CANDIDATE_SIZE)
    vector_ids = await rag_retriever.search_product_vector_ids(normalized, PRODUCT_CANDIDATE_SIZE)
    return {
        "keyword": keyword_ids,
        "vector": vector_ids,
        "fused": rrf_merge(keyword_ids, vector_ids, k),
    }


async def _retrieve(query: str, k: int) -> list[str]:
    return (await _retrieve_channels(query, k))["fused"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_graded_contract(
    cases: list[dict], dataset: Path, lock_path: Path, catalog_path: Path
) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if lock.get("schemaVersion") != 1:
        errors.append("unsupported search relevance lock schema")
    dataset_sha = _sha256(dataset)
    catalog_sha = _sha256(catalog_path)
    if dataset_sha != lock.get("datasetSha256"):
        errors.append("search dataset SHA does not match lock")
    if catalog_sha != lock.get("catalogSha256"):
        errors.append("catalog SHA does not match lock")
    if catalog.get("catalogVersion") != lock.get("catalogVersion"):
        errors.append("catalog version does not match lock")

    product_rows = catalog.get("products") or []
    product_ids = [
        str((detail.get("productInfo") or {}).get("productId") or "")
        for detail in product_rows
    ]
    products = {product_id for product_id in product_ids if product_id}
    if len(products) != len(product_ids):
        errors.append("catalog contains an empty or duplicate product ID")
    labelled = [case for case in cases if "relevantProductIds" in case]
    if len(labelled) != int(lock.get("labelledCases") or 0) or not labelled:
        errors.append("labelled case count does not match lock")
    labelled_ids = [str(case.get("id") or "") for case in labelled]
    if len(set(labelled_ids)) != len(labelled_ids) or "" in labelled_ids:
        errors.append("labelled cases contain an empty or duplicate ID")
    for case in labelled:
        relevant = case.get("relevantProductIds")
        grades = case.get("relevanceGrades")
        if not isinstance(relevant, list) or not relevant:
            errors.append(f"{case.get('id')} has no relevant products")
            continue
        if not isinstance(grades, dict) or set(map(str, relevant)) != set(map(str, grades)):
            errors.append(f"{case.get('id')} relevanceGrades do not match relevantProductIds")
        elif any(
            not isinstance(grade, (int, float)) or isinstance(grade, bool) or grade <= 0
            for grade in grades.values()
        ):
            errors.append(f"{case.get('id')} relevanceGrades must be positive numbers")
        missing = sorted(set(map(str, relevant)) - products)
        if missing:
            errors.append(f"{case.get('id')} references products absent from catalog: {missing}")
    if (
        len(products) != int(catalog.get("productCount") or 0)
        or len(products) != int(lock.get("productCount") or 0)
        or len(products) != 47
    ):
        errors.append(f"expected the locked 47-product catalog, got {len(products)}")
    thresholds = lock.get("thresholds") or {}
    expected_thresholds = {"recallAt10": 0.80, "mrr": 0.65, "ndcgAt10": 0.70}
    if thresholds != expected_thresholds:
        errors.append(f"quality thresholds do not match the frozen gate: {expected_thresholds}")
    if errors:
        raise ValueError("graded search contract invalid:\n- " + "\n- ".join(errors))
    return {
        "datasetSha256": dataset_sha,
        "catalogSha256": catalog_sha,
        "catalogVersion": catalog.get("catalogVersion"),
        "productCount": len(products),
        "labelledCases": len(labelled),
        "thresholds": thresholds,
    }


async def _require_live_product_index(minimum_count: int) -> int:
    from app.infra.http_client import get_client
    from app.rag.retriever import PRODUCT_INDEX, rag_retriever

    client = await get_client("es", timeout=10)
    response = await client.get(rag_retriever._es_url(f"/{PRODUCT_INDEX}/_count"), timeout=10)
    response.raise_for_status()
    count = int((response.json() or {}).get("count") or 0)
    if count < minimum_count:
        raise RuntimeError(
            f"Elasticsearch product index contains {count} documents; expected at least {minimum_count}"
        )
    return count


async def evaluate_graded_relevance(cases: list[dict], k: int) -> dict[str, Any]:
    labelled = [
        case
        for case in cases
        if isinstance(case.get("relevantProductIds"), list) and case["relevantProductIds"]
    ]
    if not labelled:
        raise ValueError("graded relevance requires non-empty relevantProductIds labels")

    recalls: list[float] = []
    reciprocal: list[float] = []
    ndcgs: list[float] = []
    misses: list[dict] = []
    per_case: list[dict] = []
    keyword_non_empty = vector_non_empty = 0
    for case in labelled:
        relevant = {str(pid) for pid in case["relevantProductIds"]}
        grades = {
            str(pid): float(grade)
            for pid, grade in (case.get("relevanceGrades") or {}).items()
        }
        channels = await _retrieve_channels(case.get("query") or "", k)
        ranked = channels["fused"]
        keyword_non_empty += int(bool(channels["keyword"]))
        vector_non_empty += int(bool(channels["vector"]))
        hit_positions = [rank for rank, pid in enumerate(ranked, start=1) if pid in relevant]
        recall = len(set(ranked) & relevant) / len(relevant)
        rr = 1.0 / hit_positions[0] if hit_positions else 0.0
        ndcg = _ndcg(ranked, grades, k)
        recalls.append(recall)
        reciprocal.append(rr)
        ndcgs.append(ndcg)
        per_case.append(
            {
                "id": case.get("id"),
                "query": case.get("query"),
                "relevantProductIds": sorted(relevant),
                "returned": ranked,
                "recall": round(recall, 4),
                "reciprocalRank": round(rr, 4),
                "ndcg": round(ndcg, 4),
                "keywordCandidates": len(channels["keyword"]),
                "vectorCandidates": len(channels["vector"]),
            }
        )
        if not hit_positions:
            misses.append(
                {"id": case.get("id"), "query": case.get("query"), "returned": ranked[:5]}
            )

    count = len(labelled)
    return {
        "labelledCases": count,
        "k": k,
        f"recallAt{k}": round(sum(recalls) / count, 4),
        "mrr": round(sum(reciprocal) / count, 4),
        f"ndcgAt{k}": round(sum(ndcgs) / count, 4),
        "keywordNonEmptyCases": keyword_non_empty,
        "vectorNonEmptyCases": vector_non_empty,
        "recallChannelsHealthy": keyword_non_empty > 0 and vector_non_empty > 0,
        "zeroHitCases": misses,
        "perCase": per_case,
    }


def graded_gate_failures(
    graded: dict[str, Any], *, k: int, min_recall: float, min_mrr: float, min_ndcg: float
) -> list[str]:
    failures: list[str] = []
    if not graded["recallChannelsHealthy"]:
        failures.append(
            "both keyword and vector recall channels must return candidates "
            f"(keyword={graded['keywordNonEmptyCases']}, vector={graded['vectorNonEmptyCases']})"
        )
    if graded[f"recallAt{k}"] < min_recall:
        failures.append(f"Recall@{k} {graded[f'recallAt{k}']} < {min_recall}")
    if graded["mrr"] < min_mrr:
        failures.append(f"MRR@{k} {graded['mrr']} < {min_mrr}")
    if graded[f"ndcgAt{k}"] < min_ndcg:
        failures.append(f"NDCG@{k} {graded[f'ndcgAt{k}']} < {min_ndcg}")
    return failures


async def emit_template(cases: list[dict], out_path: Path, k: int) -> None:
    """Record what the live pipeline returns today, for a human to label."""
    lines = []
    for case in cases:
        if not (case.get("query") or "").strip():
            continue
        ranked = await _retrieve(case["query"], k)
        lines.append(
            json.dumps(
                {
                    "id": case.get("id"),
                    "query": case["query"],
                    "candidateProductIds": ranked,
                    "relevantProductIds": [],
                    "_hint": "把 candidateProductIds 里相关的挪进 relevantProductIds",
                },
                ensure_ascii=False,
            )
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} labelling rows to {out_path}", file=sys.stderr)


def _print_report(report: dict[str, Any]) -> None:
    understanding = report["queryUnderstanding"]
    print("=== Layer 1: query understanding ===")
    print(f"  cases           : {understanding['cases']}")
    print(
        f"  keywordAccuracy : {understanding['keywordAccuracy']} "
        f"({understanding['keywordGraded']} graded)"
    )
    print(
        f"  termCoverage    : {understanding['termCoverage']} "
        f"({understanding['termGraded']} graded)"
    )
    print("  keyword accuracy by subset:")
    for subset, stats in sorted(understanding["bySubset"].items()):
        print(f"    {subset:<12} {stats['passed']}/{stats['graded']}")
    if understanding["failures"]:
        print(f"  failures ({len(understanding['failures'])}):")
        for failure in understanding["failures"]:
            detail = (
                f"missing {failure['missing']!r} from {failure['actual']!r}"
                if failure["field"] == "terms"
                else f"expected {failure['expected']!r} got {failure['actual']!r}"
            )
            print(f"    [{failure['id']}] {failure['query']!r} {failure['field']}: {detail}")
            if failure.get("note"):
                print(f"        note: {failure['note']}")

    graded = report.get("gradedRelevance")
    if not graded:
        return
    print("\n=== Layer 2: graded relevance ===")
    for key, value in graded.items():
        if key in ("zeroHitCases", "perCase"):
            continue
        print(f"  {key:<16}: {value}")
    if graded["zeroHitCases"]:
        print(f"  zero-hit cases ({len(graded['zeroHitCases'])}):")
        for miss in graded["zeroHitCases"]:
            print(f"    [{miss['id']}] {miss['query']!r} -> {miss['returned']}")


async def run(args: argparse.Namespace) -> int:
    cases = load_cases(args.dataset)
    if not cases:
        print(f"no cases in {args.dataset}", file=sys.stderr)
        return 1

    if args.emit_template:
        await emit_template(cases, Path(args.emit_template), args.top_k)
        return 0

    report: dict[str, Any] = {
        "dataset": str(args.dataset),
        "queryUnderstanding": evaluate_query_understanding(cases),
    }
    if args.graded:
        report["contract"] = validate_graded_contract(
            cases, args.dataset, args.lock, args.catalog
        )
        report["contract"]["liveEsProductCount"] = await _require_live_product_index(
            report["contract"]["productCount"]
        )
        report["gradedRelevance"] = await evaluate_graded_relevance(cases, args.top_k)

    _print_report(report)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nreport written to {args.out}", file=sys.stderr)

    understanding = report["queryUnderstanding"]
    gate_failures: list[str] = []
    if understanding["keywordAccuracy"] < args.min_keyword_accuracy:
        gate_failures.append(
            f"keyword accuracy {understanding['keywordAccuracy']} < {args.min_keyword_accuracy}"
        )
    if understanding["termCoverage"] < args.min_term_coverage:
        gate_failures.append(
            f"term coverage {understanding['termCoverage']} < {args.min_term_coverage}"
        )
    if args.graded:
        graded = report["gradedRelevance"]
        gate_failures.extend(
            graded_gate_failures(
                graded,
                k=args.top_k,
                min_recall=args.min_recall,
                min_mrr=args.min_mrr,
                min_ndcg=args.min_ndcg,
            )
        )
    if gate_failures and not args.no_fail:
        print("\nFAIL: quality gate failed", file=sys.stderr)
        for failure in gate_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--graded",
        action="store_true",
        help="also run layer 2 against live Elasticsearch (needs labels)",
    )
    parser.add_argument(
        "--emit-template",
        metavar="PATH",
        help="query live retrieval and write a labelling template, then exit",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-keyword-accuracy", type=float, default=1.0)
    parser.add_argument("--min-term-coverage", type=float, default=1.0)
    parser.add_argument("--min-recall", type=float, default=0.80)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--min-ndcg", type=float, default=0.70)
    parser.add_argument("--out", help="write the full report as JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="always exit 0, for exploratory runs",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
