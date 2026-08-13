from pathlib import Path

from benchmarks.mature_eval.rag_pipeline import (
    choose_rag_configuration,
    load_rag_sets,
    replay_rag_collection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _collection():
    faq_case = {
        "id": "faq-case",
        "query": "优惠券能用几张",
        "relevantRefs": [{"type": "faq", "questionId": "9002"}],
        "answerKeywords": ["一张", "优惠券"],
    }
    no_answer_case = {
        "id": "no-answer-case",
        "query": "是否支持比特币",
        "relevantRefs": [],
        "answerKeywords": [],
        "noAnswer": True,
        "injection": True,
    }
    faq_ref = {
        "type": "faq",
        "id": "faq_9002",
        "questionId": "9002",
        "source": "FAQ",
        "retrieval": "rerank",
        "score": 0.95,
        "snippet": "一个订单只能使用一张优惠券",
    }
    noise_ref = {
        "type": "knowledge_chunk",
        "id": "noise",
        "source": "noise.md",
        "heading": "noise",
        "retrieval": "rerank",
        "score": 0.4,
        "snippet": "无关内容",
    }
    return {
        "schemaVersion": 1,
        "providerFacts": {
            "embedding": {"providerRequests": 2},
            "rerank": {"providerRequests": 2},
        },
        "cases": [
            {
                "caseId": "faq-case",
                "split": "public",
                "case": faq_case,
                "exactFaq": [faq_ref],
                "bm25": [noise_ref, faq_ref],
                "vector": [faq_ref, noise_ref],
                "rrf": [faq_ref, noise_ref],
                "rerank": [faq_ref, noise_ref],
                "stageLatencyMs": {"bm25": 2, "vector": 8, "rerank": 12},
            },
            {
                "caseId": "no-answer-case",
                "split": "public",
                "case": no_answer_case,
                "exactFaq": [],
                "bm25": [],
                "vector": [],
                "rrf": [],
                "rerank": [],
                "stageLatencyMs": {"bm25": 1, "vector": 7, "rerank": 0},
            },
        ],
    }


def test_rag_sets_are_34_public_16_regression_14_fresh():
    sets = load_rag_sets(
        PROJECT_ROOT / "scripts" / "rag_golden.jsonl",
        PROJECT_ROOT / "benchmarks" / "datasets" / "rag_holdout_v1.jsonl",
        PROJECT_ROOT / "benchmarks" / "datasets" / "rag_fresh_holdout_v2.jsonl",
    )

    assert {key: len(value) for key, value in sets.items()} == {
        "public": 34,
        "regression": 16,
        "fresh_holdout": 14,
    }
    assert len({case["id"] for values in sets.values() for case in values}) == 64


def test_rag_replay_covers_all_retrieval_variants_and_has_zero_provider_calls():
    report = replay_rag_collection(
        _collection(),
        rerank_top_n_values=[1, 2],
        evidence_thresholds=[0.55, 0.75],
        k_values=[1, 3, 5, 10],
        split_filter={"public"},
    )

    assert report["providerFacts"]["embeddingRequests"] == 0
    assert report["providerFacts"]["rerankRequests"] == 0
    assert any(key.startswith("bm25:") for key in report["variantMetrics"])
    assert any(key.startswith("vector:") for key in report["variantMetrics"])
    assert any(key.startswith("rrf:") for key in report["variantMetrics"])
    assert any(key.startswith("rrf_rerank:") for key in report["variantMetrics"])
    assert any(key.startswith("production:") for key in report["variantMetrics"])
    assert report["pairedDeltas"]
    assert report["confidenceIntervals"]
    assert any(key.endswith(":mrr@10") for key in report["pairedDeltas"])
    production = report["variantMetrics"]["production:n1:t0.55"]
    assert production["metricCurves"]["1"]["recall"] == 1.0
    assert production["noAnswerAccuracy"] == 1.0
    assert production["injectionRobustness"] == 1.0


def test_rag_configuration_selection_uses_only_production_variants():
    report = replay_rag_collection(
        _collection(),
        variants=["production"],
        rerank_top_n_values=[1, 2],
        evidence_thresholds=[0.55, 0.75],
        k_values=[1, 5],
    )

    selected = choose_rag_configuration(report)

    assert selected["selectedVariant"].startswith("production:")
    assert selected["selectionData"] == "public + known regression only"
