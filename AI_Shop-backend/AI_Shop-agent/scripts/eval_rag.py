from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(json.loads(line))
    return cases


async def run(dataset: Path, top_k: int) -> dict:
    from app.rag.evaluation import evaluate_results
    from app.rag.retriever import rag_retriever

    cases = load_cases(dataset)
    results = [
        await rag_retriever.search_faq_with_trace(case.get("query") or "", top_k=top_k)
        for case in cases
    ]
    return evaluate_results(cases, results, top_k=top_k)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate live FAQ/knowledge retrieval.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("rag_golden.jsonl"),
        help="JSONL file containing query, relevantIds and answerKeywords",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-recall", type=float, default=0.80)
    parser.add_argument("--min-mrr", type=float, default=0.65)
    parser.add_argument("--min-no-answer", type=float, default=0.90)
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Print metrics without enforcing the quality gate.",
    )
    args = parser.parse_args()
    metrics = asyncio.run(run(args.dataset, args.top_k))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    passed = (
        metrics["recallAtK"] >= args.min_recall
        and metrics["mrr"] >= args.min_mrr
        and metrics["noAnswerAccuracy"] >= args.min_no_answer
    )
    if not args.no_fail and not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
