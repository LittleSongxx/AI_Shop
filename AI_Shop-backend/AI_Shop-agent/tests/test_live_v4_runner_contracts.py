from dataclasses import replace

from app.rag.policy import runtime_rag_policy
from benchmarks.mature_eval.search_v2 import replay_v2_collection
from benchmarks.run_rag_v4_eval import (
    _policy_from_selection,
    _policy_variant_key,
    provider_completeness,
)
from benchmarks.run_search_v2_eval import _search_provider_completeness


def test_rag_policy_variant_round_trip_supports_disabled_margin():
    policy = replace(runtime_rag_policy(), top_score_margin=None).validate()
    key = _policy_variant_key(policy)

    assert key.endswith(":moff")
    assert _policy_from_selection(key).top_score_margin is None


def test_rag_provider_completeness_rejects_silent_call_loss():
    provider = {
        "embedding": {
            "requests": 4,
            "providerRequests": 3,
            "providerSuccesses": 3,
            "providerFailures": 0,
            "cacheHits": 0,
        },
        "rerank": {
            "eligibleRequests": 2,
            "providerRequests": 2,
            "providerSuccesses": 2,
            "providerFailures": 0,
            "fallbackCount": 0,
        },
        "queryExpansion": {
            "eligibleRequests": 1,
            "providerRequests": 0,
            "providerSuccesses": 0,
            "providerFailures": 0,
        },
    }

    result = provider_completeness(provider, 48, expected_case_count=48)

    assert result["passed"] is False
    assert result["checks"]["embeddingCallsComplete"] is False
    assert result["checks"]["queryExpansionCallsComplete"] is False


def test_search_provider_completeness_requires_every_cold_response():
    collection = {
        "cases": [{"caseId": "a"}, {"caseId": "b"}],
        "providerFacts": {
            "embeddingRequests": 2,
            "rerankRequests": 2,
            "responseFacts": [{"status": "SUCCESS"}],
            "embedding": {
                "requests": 2,
                "providerRequests": 2,
                "providerSuccesses": 2,
                "providerFailures": 0,
                "cacheHits": 0,
            },
        },
    }

    result = _search_provider_completeness(collection, expected_case_count=2)

    assert result["passed"] is False
    assert result["checks"]["embeddingCallsComplete"] is True
    assert result["checks"]["rerankResponsesComplete"] is False


def test_chinese_complete_labels_never_use_wands_incomplete_qrel_metrics():
    collection = {
        "cases": [
            {
                "caseId": "zh-complete",
                "split": "public",
                "query": "华为手机",
                "labelScope": "full-catalog-complete-labels",
                "expectedNoResults": False,
                "relevanceGrades": {"p1": 3, "p2": 0},
                "queryPlan": {
                    "rawQuery": "华为手机",
                    "retrievalVariants": ["华为手机"],
                    "runtimeConstraints": {},
                },
                "bm25ByVariant": {"华为手机": ["p1", "p2"]},
                "vectorByVariant": {"华为手机": ["p1", "p2"]},
                "rerankCandidatePool": ["p1", "p2"],
                "rerank": [
                    {"productId": "p1", "score": 0.9},
                    {"productId": "p2", "score": 0.1},
                ],
            }
        ]
    }
    products = [
        {"id": "p1", "productName": "华为手机"},
        {"id": "p2", "productName": "其他手机"},
    ]

    report = replay_v2_collection(
        collection,
        products=products,
        variants=("raw_bm25",),
        candidate_counts=(2,),
        k_values=(1, 2),
    )

    metrics = next(iter(report["variantMetrics"].values()))
    assert report["labelScope"] == "full-catalog-complete-labels"
    assert metrics["metricCurves"]["1"]["recall"] == 1.0
    assert "knownRelevantRecall" not in metrics["metricCurves"]["1"]
