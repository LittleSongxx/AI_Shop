"""Search relevance evaluation, in two layers.

Layer 1 - query understanding (default, always runnable):
    Asserts the normalization contract in search_taxonomy.yml deterministically.
    No Elasticsearch, no LLM, no product catalogue. This is what guards against a
    taxonomy edit silently breaking recall for a whole category.

Layer 2 - graded relevance (--graded, needs live services + labels):
    Runs the real hybrid retrieval path and scores Recall@K / MRR / NDCG@K against
    hand-labelled relevantProductIds. Skipped when nothing is labelled, which is
    the current state of this repo: generate_products.sql only produces
    placeholder titles like "商品-105-00001", so there is nothing meaningful to
    label. Load a real catalogue, then use --emit-template to bootstrap labels.

Usage:
    python benchmarks/run_search_relevance.py
    python benchmarks/run_search_relevance.py --graded
    python benchmarks/run_search_relevance.py --emit-template labels.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DATASET = Path(__file__).with_name("search_relevance_v1.jsonl")


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


def _ndcg(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    gains = [1.0 if pid in relevant else 0.0 for pid in ranked_ids[:k]]
    ideal = [1.0] * min(len(relevant), k)
    best = _dcg(ideal)
    return (_dcg(gains) / best) if best else 0.0


async def _retrieve(query: str, k: int) -> list[str]:
    """Run the same dual recall + RRF fusion the live search path uses."""
    from app.constants import PRODUCT_CANDIDATE_SIZE
    from app.rag.retriever import rag_retriever
    from app.rag.rrf import rrf_merge
    from app.services.product_search_query import normalize_product_search_query

    normalized = normalize_product_search_query(query) or query
    keyword_ids = await rag_retriever.search_product_keyword_ids(normalized, PRODUCT_CANDIDATE_SIZE)
    vector_ids = await rag_retriever.search_product_vector_ids(normalized, PRODUCT_CANDIDATE_SIZE)
    return rrf_merge(keyword_ids, vector_ids, k)


async def evaluate_graded_relevance(cases: list[dict], k: int) -> dict[str, Any]:
    labelled = [
        case
        for case in cases
        if isinstance(case.get("relevantProductIds"), list) and case["relevantProductIds"]
    ]
    if not labelled:
        return {
            "skipped": True,
            "reason": (
                "No case carries relevantProductIds. This repo's generate_products.sql "
                "only produces placeholder titles, so there is nothing meaningful to "
                "label. Load a real catalogue then run --emit-template."
            ),
        }

    recalls: list[float] = []
    reciprocal: list[float] = []
    ndcgs: list[float] = []
    misses: list[dict] = []
    for case in labelled:
        relevant = {str(pid) for pid in case["relevantProductIds"]}
        ranked = await _retrieve(case.get("query") or "", k)
        hit_positions = [rank for rank, pid in enumerate(ranked, start=1) if pid in relevant]
        recalls.append(len(set(ranked) & relevant) / len(relevant))
        reciprocal.append(1.0 / hit_positions[0] if hit_positions else 0.0)
        ndcgs.append(_ndcg(ranked, relevant, k))
        if not hit_positions:
            misses.append(
                {"id": case.get("id"), "query": case.get("query"), "returned": ranked[:5]}
            )

    count = len(labelled)
    return {
        "skipped": False,
        "labelledCases": count,
        "k": k,
        f"recallAt{k}": round(sum(recalls) / count, 4),
        "mrr": round(sum(reciprocal) / count, 4),
        f"ndcgAt{k}": round(sum(ndcgs) / count, 4),
        "zeroHitCases": misses,
    }


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
    if graded.get("skipped"):
        print(f"  skipped: {graded['reason']}")
        return
    for key, value in graded.items():
        if key in ("skipped", "zeroHitCases"):
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
        report["gradedRelevance"] = await evaluate_graded_relevance(cases, args.top_k)

    _print_report(report)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nreport written to {args.out}", file=sys.stderr)

    understanding = report["queryUnderstanding"]
    below_threshold = (
        understanding["keywordAccuracy"] < args.min_keyword_accuracy
        or understanding["termCoverage"] < args.min_term_coverage
    )
    if below_threshold and not args.no_fail:
        print(
            "\nFAIL: below threshold "
            f"(keyword >= {args.min_keyword_accuracy}, term >= {args.min_term_coverage})",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
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
    parser.add_argument("--out", help="write the full report as JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="always exit 0, for exploratory runs",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
