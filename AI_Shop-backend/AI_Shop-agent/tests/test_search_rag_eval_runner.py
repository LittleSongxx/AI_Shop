from benchmarks.run_search_rag_eval import (
    RAG_HOLDOUT_DATASET,
    SEARCH_HOLDOUT_DATASET,
    _deterministic_rag_cases,
    _deterministic_search_cases,
    validate_rag_holdout_contract,
    validate_search_holdout_contract,
)
from benchmarks.run_search_relevance import load_cases as load_search_cases
from scripts.eval_rag import load_cases as load_rag_cases


def test_search_holdout_is_hash_locked_and_query_contract_passes():
    contract = validate_search_holdout_contract()
    results, metrics = _deterministic_search_cases(
        load_search_cases(SEARCH_HOLDOUT_DATASET),
        run_id="test-search-rag",
        split="holdout",
    )

    assert len(contract["cases"]) == 15
    assert metrics["keywordAccuracy"] == 1.0
    assert metrics["termCoverage"] == 1.0
    assert all(result.status == "PASSED" for result in results)
    assert all(result.observations["retrievalQualityClaimed"] is False for result in results)


def test_rag_holdout_has_refusal_and_injection_cases_without_live_claims():
    contract = validate_rag_holdout_contract()
    cases = load_rag_cases(RAG_HOLDOUT_DATASET)
    results = _deterministic_rag_cases(
        cases,
        run_id="test-search-rag",
        split="holdout",
    )

    assert contract["caseCount"] == 16
    assert sum(bool(case.get("noAnswer")) for case in cases) == 3
    assert sum(bool(case.get("injection")) for case in cases) == 2
    assert len(results) == 16
    assert all(result.status == "PASSED" for result in results)
    assert all(result.observations["retrievalQualityClaimed"] is False for result in results)
