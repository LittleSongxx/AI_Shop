from pathlib import Path

from benchmarks.mature_eval.rag_v4_pipeline import (
    _merge_provider_facts,
    _refs_for_variant,
    load_rag_v4_sets,
    replay_rag_v4_collection,
    scoped_case_id,
)

DATASETS = Path(__file__).resolve().parents[1] / "benchmarks" / "datasets"


def test_rag_v4_allows_public_known_overlap_but_scopes_runtime_ids():
    sets = load_rag_v4_sets(
        DATASETS / "rag_v4_public.jsonl",
        DATASETS / "rag_v4_known_regression.jsonl",
        DATASETS / "rag_v4_fresh_holdout.jsonl",
    )
    assert len(sets["public"]) == 72
    assert len(sets["known_regression"]) == 144
    assert sets["public"][0]["id"] == sets["known_regression"][0]["id"]
    assert scoped_case_id(sets["public"][0]).startswith("public:")
    assert scoped_case_id(sets["known_regression"][0]).startswith("known_regression:")
    assert {row["id"] for row in sets["fresh_holdout"]}.isdisjoint(
        row["id"] for row in sets["known_regression"]
    )
    assert {row["query"] for row in sets["fresh_holdout"]}.isdisjoint(
        row["query"] for row in sets["known_regression"]
    )
    assert {row["id"] for row in sets["fresh_holdout"]}.isdisjoint(
        row["id"] for row in sets["public"]
    )
    assert {row["query"] for row in sets["fresh_holdout"]}.isdisjoint(
        row["query"] for row in sets["public"]
    )


def test_rag_v4_resume_merges_provider_facts_without_losing_old_calls():
    merged = _merge_provider_facts(
        {
            "embedding": {
                "requests": 3,
                "providerRequests": 3,
                "providerSuccesses": 3,
                "responseRecords": [{"id": "old"}],
            },
            "rerank": {
                "eligibleRequests": 2,
                "providerRequests": 2,
                "providerSuccesses": 2,
                "fallbackReasons": {},
                "responseRecords": [{"id": "old"}],
            },
            "queryExpansion": {
                "eligibleRequests": 1,
                "providerRequests": 1,
                "providerSuccesses": 1,
            },
        },
        {
            "embedding": {
                "requests": 2,
                "providerRequests": 2,
                "providerSuccesses": 2,
                "responseRecords": [{"id": "new"}],
            },
            "rerank": {
                "eligibleRequests": 1,
                "providerRequests": 1,
                "providerSuccesses": 1,
                "fallbackReasons": {},
                "responseRecords": [{"id": "new"}],
            },
            "queryExpansion": {},
        },
    )
    assert merged["embedding"]["requests"] == 5
    assert merged["embedding"]["providerSuccesses"] == 5
    assert len(merged["embedding"]["responseRecords"]) == 2
    assert merged["rerank"]["providerSuccesses"] == 3
    assert merged["queryExpansion"]["providerSuccesses"] == 1


def test_rag_v4_replay_reports_zero_provider_calls_and_multiple_k_curves():
    sets = load_rag_v4_sets(
        DATASETS / "rag_v4_public.jsonl",
        DATASETS / "rag_v4_known_regression.jsonl",
        DATASETS / "rag_v4_fresh_holdout.jsonl",
    )
    case = sets["public"][0]
    ref = {
        "type": "knowledge_chunk",
        "source": case["relevantRefs"][0]["source"],
        "heading": case["relevantRefs"][0]["heading"],
        "id": "synthetic-ref",
        "snippet": " ".join(case.get("answerKeywords") or []),
        "score": 0.9,
    }
    collection = {
        "kind": "rag-v4-cold-collection",
        "candidateSize": 20,
        "contextualMode": "context_prefix",
        "cases": [
            {
                "caseId": case["id"],
                "evaluationCaseId": scoped_case_id(case),
                "split": "public",
                "case": case,
                "exactFaq": [],
                "bm25": [ref],
                "vector": [ref],
                "rrf": [ref],
                "rerank": [dict(ref, retrieval="rerank")],
                "production": [dict(ref, retrieval="rerank")],
                "stageLatencyMs": {"total": 1.0},
            }
        ],
        "providerFacts": {"embedding": {}, "rerank": {}, "queryExpansion": {}},
    }
    report = replay_rag_v4_collection(collection)
    assert report["providerFacts"] == {
        "embeddingRequests": 0,
        "rerankRequests": 0,
        "queryExpansionRequests": 0,
    }
    production = [
        key for key in report["variantMetrics"] if key.startswith("production:")
    ]
    assert len(production) == 45
    assert set(report["metricCurves"][production[0]]) == {"1", "2", "3", "5", "10", "20"}


def test_production_replay_uses_locked_rerank_candidates_and_fact_hint_floor():
    row = {
        "query": "AI能直接退款吗",
        "queryPlan": {"factHints": ["ai.capability_and_confirmation"]},
        "production": [],
        "rerank": [
            {
                "id": "direct",
                "score": 0.62,
                "factIds": ["ai.capability_and_confirmation"],
            }
        ],
    }

    refs = _refs_for_variant(
        row,
        variant="production",
        top_n=6,
        threshold=0.70,
        margin=0.10,
    )

    assert [ref["id"] for ref in refs] == ["direct"]


def test_production_replay_never_restores_quarantined_candidate():
    row = {
        "query": "售后资格由规则引擎还是 RAG 决定",
        "queryPlan": {"factHints": ["aftersales.rule_engine_authoritative"]},
        "quarantinedCandidateIds": ["poisoned"],
        "rerank": [
            {
                "id": "poisoned",
                "score": 0.95,
                "factIds": ["aftersales.rule_engine_authoritative"],
            }
        ],
    }

    assert _refs_for_variant(
        row,
        variant="production",
        top_n=6,
        threshold=0.70,
        margin=0.10,
    ) == []


def test_production_replay_limits_single_question_to_one_evidence_item():
    row = {
        "query": "购物车价格是最终成交价吗",
        "queryPlan": {
            "subquestions": ["购物车价格是最终成交价吗"],
            "factHints": ["cart.price_snapshot_not_guarantee"],
        },
        "rerank": [
            {
                "id": "direct",
                "score": 0.90,
                "factIds": ["cart.price_snapshot_not_guarantee"],
            },
            {
                "id": "related",
                "score": 0.89,
                "factIds": ["cart.checkout_revalidation"],
            },
        ],
    }

    refs = _refs_for_variant(
        row,
        variant="production",
        top_n=6,
        threshold=0.70,
        margin=0.10,
    )

    assert [ref["id"] for ref in refs] == ["direct"]


def test_production_replay_keeps_one_evidence_item_per_subquestion():
    row = {
        "query": "购物车价格是否保证，同时说明结算校验",
        "queryPlan": {
            "subquestions": ["购物车价格是否保证", "结算时会校验什么"],
            "factHints": ["cart.price_snapshot_not_guarantee"],
        },
        "rerank": [
            {
                "id": "price",
                "score": 0.90,
                "factIds": ["cart.price_snapshot_not_guarantee"],
            },
            {
                "id": "checkout",
                "score": 0.88,
                "factIds": ["cart.checkout_revalidation"],
            },
            {
                "id": "unrelated",
                "score": 0.87,
                "factIds": ["cart.failure_release"],
            },
        ],
    }

    refs = _refs_for_variant(
        row,
        variant="production",
        top_n=6,
        threshold=0.70,
        margin=0.10,
    )

    assert [ref["id"] for ref in refs] == ["price", "checkout"]


def test_production_replay_keeps_close_second_candidate_without_fact_hint():
    row = {
        "query": "知识检索不足时怎么办",
        "queryPlan": {"subquestions": ["知识检索不足时怎么办"]},
        "rerank": [
            {
                "id": "related",
                "score": 0.91,
                "factIds": ["ai.recommendation.evidence_boundary"],
            },
            {
                "id": "direct",
                "score": 0.88,
                "factIds": ["rag.retrieval_and_abstention"],
            },
        ],
    }

    refs = _refs_for_variant(
        row,
        variant="production",
        top_n=6,
        threshold=0.70,
        margin=0.10,
    )

    assert [ref["id"] for ref in refs] == ["related", "direct"]
