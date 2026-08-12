from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import PurePath
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


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _source_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return PurePath(text).name.casefold() if text else ""


def _matches_expected(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_type = _normalized_text(expected.get("type"))
    if expected_type == "faq":
        return _normalized_text(expected.get("questionId")) == _normalized_text(
            actual.get("questionId")
        )
    if expected_type in {"knowledge", "knowledge_chunk"}:
        return (
            _source_name(expected.get("source")) == _source_name(actual.get("source"))
            and _normalized_text(expected.get("heading"))
            == _normalized_text(actual.get("heading"))
        )
    expected_ids = _reference_ids(expected)
    return bool(expected_ids.intersection(_reference_ids(actual)))


def _expected_refs(case: dict[str, Any]) -> list[dict[str, Any]]:
    refs = case.get("relevantRefs")
    if isinstance(refs, list):
        return [ref for ref in refs if isinstance(ref, dict)]
    return [{"id": value} for value in case.get("relevantIds") or []]


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def _expected_grade(expected: dict[str, Any]) -> float:
    value = expected.get("grade", expected.get("relevance", 1.0))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return max(0.0, float(value))


def _ndcg_at_k(
    expected_refs: list[dict[str, Any]],
    actual_refs: list[dict[str, Any]],
    limit: int,
) -> float:
    remaining = set(range(len(expected_refs)))
    gains: list[float] = []
    for actual in actual_refs[:limit]:
        matches = [
            index
            for index in remaining
            if _matches_expected(expected_refs[index], actual)
        ]
        if not matches:
            gains.append(0.0)
            continue
        selected = max(matches, key=lambda index: _expected_grade(expected_refs[index]))
        gains.append(_expected_grade(expected_refs[selected]))
        remaining.remove(selected)
    ideal = sorted((_expected_grade(expected) for expected in expected_refs), reverse=True)[
        :limit
    ]
    ideal_dcg = _dcg(ideal)
    return _dcg(gains) / ideal_dcg if ideal_dcg else 0.0


def _citation_supports_answer(
    actual: dict[str, Any],
    expected_refs: list[dict[str, Any]],
    keywords: list[str],
) -> bool:
    if not any(_matches_expected(expected, actual) for expected in expected_refs):
        return False
    snippet = _normalized_text(actual.get("snippet"))
    return not keywords or any(keyword in snippet for keyword in keywords)


def placeholder_references(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    placeholders: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        for ref in case.get("relevantIds") or []:
            value = str(ref)
            if value.startswith("faq_") and not value[4:].isdigit():
                placeholders.append({"case": index, "query": case.get("query"), "ref": value})
        for ref in case.get("relevantRefs") or []:
            if not isinstance(ref, dict):
                placeholders.append({"case": index, "query": case.get("query"), "ref": ref})
                continue
            ref_type = _normalized_text(ref.get("type"))
            valid = (
                ref_type == "faq" and str(ref.get("questionId") or "").isdigit()
            ) or (
                ref_type in {"knowledge", "knowledge_chunk"}
                and bool(_source_name(ref.get("source")))
                and bool(_normalized_text(ref.get("heading")))
            )
            if not valid:
                placeholders.append({"case": index, "query": case.get("query"), "ref": ref})
    return placeholders


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
            "noAnswerCases": 0,
            "recallAtK": 0.0,
            "mrr": 0.0,
            "ndcgAtK": 0.0,
            "topKHitRate": 0.0,
            "answerCitationRate": 0.0,
            "citationCorrectness": 0.0,
            "citationCoverage": 0.0,
            "noAnswerAccuracy": 0.0,
            "noAnswerPrecision": 0.0,
            "noAnswerRecall": 0.0,
            "noAnswerF1": 0.0,
            "injectionCases": 0,
            "injectionRobustness": 0.0,
            "placeholderRefs": [],
            "perCase": [],
        }

    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    top_k_hits = 0
    citation_hits = 0
    valid_citation_count = returned_citation_count = 0
    citation_coverage_values: list[float] = []
    injection_cases = injection_robust = 0
    no_answer_cases = no_answer_hits = 0
    no_answer_tp = no_answer_fp = no_answer_fn = 0
    per_case: list[dict[str, Any]] = []
    for index, case in enumerate(case_list):
        result = result_list[index] if index < len(result_list) else {}
        refs = _source_refs(result)
        expected_refs = _expected_refs(case)
        expected_no_answer = bool(case.get("noAnswer", not expected_refs))
        trace = result.get("trace") if isinstance(result, dict) else {}
        predicted_no_answer = not refs or (
            isinstance(trace, dict) and trace.get("hit") is False
        )
        latency_ms = trace.get("latencyMs") if isinstance(trace, dict) else None
        matched_positions = [
            position
            for position, actual in enumerate(refs[:limit], start=1)
            if any(_matches_expected(expected, actual) for expected in expected_refs)
        ]
        if expected_no_answer:
            no_answer_cases += 1
            if predicted_no_answer:
                no_answer_hits += 1
                no_answer_tp += 1
            else:
                no_answer_fn += 1
            robust = predicted_no_answer
            if case.get("injection"):
                injection_cases += 1
                injection_robust += int(robust)
            per_case.append(
                {
                    "index": index,
                    "query": case.get("query"),
                    "expectedNoAnswer": True,
                    "predictedNoAnswer": predicted_no_answer,
                    "passed": predicted_no_answer,
                    "injectionRobust": robust if case.get("injection") else None,
                    "latencyMs": latency_ms,
                    "retrievedRefs": refs[:limit],
                }
            )
            continue
        if predicted_no_answer:
            no_answer_fp += 1
        if not expected_refs:
            per_case.append(
                {
                    "index": index,
                    "query": case.get("query"),
                    "expectedNoAnswer": False,
                    "predictedNoAnswer": predicted_no_answer,
                    "passed": False,
                    "error": "answerable case has no relevantRefs",
                    "retrievedRefs": refs[:limit],
                }
            )
            continue

        matched_expected = sum(
            1
            for expected in expected_refs
            if any(_matches_expected(expected, actual) for actual in refs[:limit])
        )
        recall = matched_expected / len(expected_refs)
        recall_values.append(recall)
        rank = matched_positions[0] if matched_positions else None
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        ndcg = _ndcg_at_k(expected_refs, refs, limit)
        ndcg_values.append(ndcg)
        if rank:
            top_k_hits += 1

        keywords = [_normalized_text(value) for value in case.get("answerKeywords") or []]
        returned_citation_count += len(refs[:limit])
        valid_refs = [
            ref
            for ref in refs[:limit]
            if _citation_supports_answer(ref, expected_refs, keywords)
        ]
        valid_citation_count += len(valid_refs)
        covered_expected = sum(
            1
            for expected in expected_refs
            if any(
                _matches_expected(expected, actual)
                and _citation_supports_answer(actual, expected_refs, keywords)
                for actual in refs[:limit]
            )
        )
        citation_coverage = covered_expected / len(expected_refs)
        citation_coverage_values.append(citation_coverage)
        cited = bool(valid_refs)
        if cited:
            citation_hits += 1
        robust = recall == 1.0 and citation_coverage == 1.0
        if case.get("injection"):
            injection_cases += 1
            injection_robust += int(robust)
        per_case.append(
            {
                "index": index,
                "query": case.get("query"),
                "expectedNoAnswer": False,
                "predictedNoAnswer": predicted_no_answer,
                "recallAtK": round(recall, 4),
                "reciprocalRank": round(1.0 / rank, 4) if rank else 0.0,
                "ndcgAtK": round(ndcg, 4),
                "citation": cited,
                "citationCorrectness": round(len(valid_refs) / len(refs[:limit]), 4)
                if refs[:limit]
                else 0.0,
                "citationCoverage": round(citation_coverage, 4),
                "injectionRobust": robust if case.get("injection") else None,
                "latencyMs": latency_ms,
                "passed": recall == 1.0 and cited,
                "expectedRefs": expected_refs,
                "retrievedRefs": refs[:limit],
            }
        )

    count = len(case_list)
    retrieval_count = len(recall_values)
    precision = no_answer_tp / (no_answer_tp + no_answer_fp) if no_answer_tp + no_answer_fp else 0.0
    no_answer_recall = no_answer_tp / (no_answer_tp + no_answer_fn) if no_answer_tp + no_answer_fn else 0.0
    f1 = (
        2 * precision * no_answer_recall / (precision + no_answer_recall)
        if precision + no_answer_recall
        else 0.0
    )
    return {
        "cases": count,
        "retrievalCases": retrieval_count,
        "noAnswerCases": no_answer_cases,
        "recallAtK": round(sum(recall_values) / retrieval_count, 4) if retrieval_count else 0.0,
        "mrr": round(sum(reciprocal_ranks) / retrieval_count, 4) if retrieval_count else 0.0,
        "ndcgAtK": round(sum(ndcg_values) / retrieval_count, 4)
        if retrieval_count
        else 0.0,
        "topKHitRate": round(top_k_hits / retrieval_count, 4) if retrieval_count else 0.0,
        "answerCitationRate": round(citation_hits / retrieval_count, 4) if retrieval_count else 0.0,
        "citationCorrectness": round(valid_citation_count / returned_citation_count, 4)
        if returned_citation_count
        else 0.0,
        "citationCoverage": round(sum(citation_coverage_values) / retrieval_count, 4)
        if retrieval_count
        else 0.0,
        "noAnswerAccuracy": round(no_answer_hits / no_answer_cases, 4)
        if no_answer_cases
        else 0.0,
        "noAnswerPrecision": round(precision, 4),
        "noAnswerRecall": round(no_answer_recall, 4),
        "noAnswerF1": round(f1, 4),
        "injectionCases": injection_cases,
        "injectionRobustness": round(injection_robust / injection_cases, 4)
        if injection_cases
        else 0.0,
        "placeholderRefs": placeholder_references(case_list),
        "perCase": per_case,
    }
