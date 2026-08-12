from benchmarks.run_search_rag_eval import (
    RAG_HOLDOUT_DATASET,
    SEARCH_HOLDOUT_DATASET,
    _deterministic_rag_cases,
    _deterministic_search_cases,
    _labelled_search_cases,
    _live_rag_cases,
    validate_rag_holdout_contract,
    validate_search_holdout_contract,
)
from benchmarks.run_search_relevance import DEFAULT_DATASET as PUBLIC_SEARCH_DATASET
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


def test_live_search_uses_only_the_30_labelled_public_cases():
    public_cases = load_search_cases(PUBLIC_SEARCH_DATASET)

    labelled = _labelled_search_cases(public_cases)

    assert len(public_cases) == 127
    assert len(labelled) == 30
    assert all(case.get("relevantProductIds") for case in labelled)


def test_live_rag_rejects_silent_rrf_fallback():
    cases = [{"id": "fallback", "subset": "knowledge", "priority": "P0"}]
    metrics = {
        "perCase": [
            {
                "passed": True,
                "retrievedRefs": [
                    {"id": "chunk-1", "retrieval": "rrf", "score": 0.03}
                ],
            }
        ]
    }

    result = _live_rag_cases(cases, metrics, run_id="test", split="holdout")[0]

    assert result.status == "FAILED"
    assert result.task_success is False
    assert result.observations["rerankFallback"] is True
    assert result.observations["retrievalModes"] == ["rrf"]


def test_live_rag_allows_exact_faq_and_no_candidate_cases():
    cases = [
        {"id": "faq", "subset": "faq", "priority": "P0"},
        {"id": "none", "subset": "no_answer", "priority": "P0"},
    ]
    metrics = {
        "perCase": [
            {
                "passed": True,
                "retrievedRefs": [
                    {"id": "faq_9001", "retrieval": "exact_faq", "score": 1.0}
                ],
            },
            {"passed": True, "retrievedRefs": []},
        ]
    }

    results = _live_rag_cases(cases, metrics, run_id="test", split="holdout")

    assert [result.status for result in results] == ["PASSED", "PASSED"]
    assert all(result.observations["rerankFallback"] is False for result in results)
