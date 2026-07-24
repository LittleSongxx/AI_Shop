from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _reference_ids(ref: dict[str, Any]) -> set[str]:
    values = {
        ref.get("id"),
        ref.get("documentId"),
        ref.get("chunkId"),
        ref.get("questionId"),
    }
    return {str(value) for value in values if value is not None and str(value)}


def _source_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs = result.get("source_refs") or result.get("sourceRefs") or []
    return [ref for ref in refs if isinstance(ref, dict)]


def evaluate_results(
    cases: Iterable[dict[str, Any]],
    results: Iterable[dict[str, Any]],
    top_k: int = 5,
) -> dict[str, Any]:
    """Calculate small, dependency-free RAG retrieval metrics."""
    case_list = list(cases)
    result_list = list(results)
    limit = max(1, int(top_k))
    if not case_list:
        return {
            "cases": 0,
            "retrievalCases": 0,
            "recallAtK": 0.0,
            "mrr": 0.0,
            "topKHitRate": 0.0,
            "answerCitationRate": 0.0,
        }

    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    top_k_hits = 0
    citation_hits = 0
    for index, case in enumerate(case_list):
        result = result_list[index] if index < len(result_list) else {}
        refs = _source_refs(result)
        expected = {str(value) for value in case.get("relevantIds") or []}
        if not expected:
            continue
        ranked_ids = [
            _reference_ids(ref)
            for ref in refs[:limit]
        ]
        retrieved_ids = set().union(*ranked_ids) if ranked_ids else set()
        recall_values.append(len(expected.intersection(retrieved_ids)) / len(expected))
        rank = next(
            (
                position
                for position, ref_ids in enumerate(ranked_ids, start=1)
                if expected.intersection(ref_ids)
            ),
            None,
        )
        if rank is not None:
            top_k_hits += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)

        keywords = [str(value).lower() for value in case.get("answerKeywords") or []]
        snippets = " ".join(str(ref.get("snippet") or "") for ref in refs).lower()
        if expected and refs and (not keywords or any(keyword in snippets for keyword in keywords)):
            citation_hits += 1

    count = len(case_list)
    retrieval_count = len(recall_values)
    if retrieval_count == 0:
        return {
            "cases": count,
            "retrievalCases": 0,
            "recallAtK": 0.0,
            "mrr": 0.0,
            "topKHitRate": 0.0,
            "answerCitationRate": 0.0,
        }
    return {
        "cases": count,
        "retrievalCases": retrieval_count,
        "recallAtK": round(sum(recall_values) / retrieval_count, 4),
        "mrr": round(sum(reciprocal_ranks) / retrieval_count, 4),
        "topKHitRate": round(top_k_hits / retrieval_count, 4),
        "answerCitationRate": round(citation_hits / retrieval_count, 4),
    }
