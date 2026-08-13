from copy import deepcopy

import pytest

from benchmarks.mature_eval import search_pipeline
from benchmarks.mature_eval.search_pipeline import (
    choose_configuration,
    collect_cases,
    relevance_filter,
    replay_collection,
    rrf_merge_rankings,
)


def _products():
    return [
        {
            "id": "a",
            "productName": "主动降噪入耳式耳机",
            "productDesc": "通勤使用",
            "brand": "澄屿",
            "category": "earphone",
            "price": 99,
        },
        {
            "id": "b",
            "productName": "办公头戴式耳机",
            "productDesc": "舒适佩戴",
            "brand": "星序",
            "category": "earphone",
            "price": 199,
        },
        {
            "id": "c",
            "productName": "智能手机",
            "productDesc": "长续航",
            "brand": "青岚",
            "category": "phone",
            "price": 2999,
        },
    ]


def _collection():
    return {
        "schemaVersion": 1,
        "kind": "search-cold-collection",
        "providerFacts": {"embeddingRequests": 1, "rerankRequests": 1},
        "cases": [
            {
                "caseId": "q1",
                "query": "通勤降噪耳机",
                "normalizedQuery": "耳机",
                "split": "public",
                "constraints": {"category": "earphone", "priceMax": 200},
                "expectedNoResults": False,
                "relevanceGrades": {"a": 3, "b": 2, "c": 0},
                "labelScope": "full-catalog",
                "rawBm25": ["b", "a", "c"],
                "normalizedBm25": ["b", "a", "c"],
                "vector": ["a", "c", "b"],
                "rerank": [
                    {"productId": "a", "score": 0.95},
                    {"productId": "b", "score": 0.7},
                    {"productId": "c", "score": 0.1},
                ],
                "stageLatencyMs": {"embedding": 10, "bm25": 2, "rerank": 12},
            },
            {
                "caseId": "q2",
                "query": "一元以内的耳机",
                "normalizedQuery": "耳机",
                "split": "public",
                "constraints": {"category": "earphone", "priceMax": 1},
                "expectedNoResults": True,
                "relevanceGrades": {"a": 0, "b": 0, "c": 0},
                "labelScope": "full-catalog",
                "rawBm25": [],
                "normalizedBm25": [],
                "vector": [],
                "rerank": [],
                "stageLatencyMs": {"embedding": 8, "bm25": 1},
            },
        ],
    }


def test_rrf_parameter_changes_order_deterministically():
    first = rrf_merge_rankings(["a", "b"], ["b", "c"], rrf_k=10, limit=3)
    second = rrf_merge_rankings(["a", "b"], ["b", "c"], rrf_k=10, limit=3)

    assert first == second
    assert first[0] == "b"


def test_relevance_filter_uses_product_surface_text():
    products = {row["id"]: row for row in _products()}

    assert relevance_filter(["a", "b", "c"], products, "耳机") == ["a", "b"]


def test_relevance_filter_applies_structured_category_and_price_constraints():
    products = {row["id"]: row for row in _products()}

    assert relevance_filter(
        ["a", "b", "c"],
        products,
        "一元以内耳机",
        constraints={"category": "earphone", "priceMax": 1},
    ) == []


def test_replay_uses_zero_provider_calls_and_identical_collection():
    payload = _collection()
    before = deepcopy(payload)

    report = replay_collection(
        payload,
        products=_products(),
        variants=["rrf", "full_rerank"],
        candidate_counts=[3],
        rrf_k_values=[10, 60],
        rerank_top_n_values=[2],
        k_values=[1, 3, 5],
        split_filter={"public"},
    )

    assert payload == before
    assert report["providerFacts"]["embeddingRequests"] == 0
    assert report["providerFacts"]["rerankRequests"] == 0
    assert len(report["variantMetrics"]) == 4
    rerank_key = next(key for key in report["variantMetrics"] if key.startswith("full_rerank"))
    assert report["variantMetrics"][rerank_key]["metricCurves"]["1"]["recall"] == 0.5
    assert report["variantMetrics"][rerank_key]["noResultAccuracy"] == 1.0


def test_wands_replay_discards_unjudged_candidates_without_calling_them_irrelevant():
    payload = _collection()
    payload["cases"] = [payload["cases"][0]]
    payload["cases"][0]["labelScope"] = "judged-pool"
    payload["cases"][0]["relevanceGrades"] = {"a": 2, "b": 0}

    report = replay_collection(
        payload,
        products=_products(),
        variants=["vector"],
        candidate_counts=[3],
        rrf_k_values=[60],
        rerank_top_n_values=[3],
        dataset="wands",
    )

    row = next(iter(report["cases"].values()))[0]
    assert row["rankedIds"] == ["a", "b"]
    assert report["labelScope"] == "judged-pool"


def test_configuration_selection_is_lexicographic():
    replay = {
        "stageLatency": {"rerank": {"p95Ms": 50}},
        "variantMetrics": {
            "a": {"metricCurves": {"5": {"ndcg": 0.8, "recall": 1.0, "mrr": 1.0}}},
            "b": {"metricCurves": {"5": {"ndcg": 0.9, "recall": 0.8, "mrr": 0.8}}},
        },
    }

    assert choose_configuration(replay)["selectedVariant"] == "b"


@pytest.mark.asyncio
async def test_collect_rejects_rerank_fallback(monkeypatch, tmp_path):
    class FakeCollector:
        def __init__(self, *_args, **_kwargs):
            pass

        async def bm25(self, _query, _size, *, allowed_ids=None):
            return ["a", "b"], 1.0

        async def vector(self, _query, _size, *, allowed_ids=None):
            search_pipeline._provider_call("embedding")
            return ["a", "b"], [0.0] * 1024, {"embedding": 1.0, "vector": 1.0}

        async def rerank(self, *_args, **_kwargs):
            search_pipeline._provider_call("rerank")
            raise RuntimeError("rerank returned 1/2 valid candidates; fallback forbidden")

    monkeypatch.setattr(search_pipeline, "SearchCollector", FakeCollector)

    with pytest.raises(RuntimeError, match="fallback forbidden"):
        await collect_cases(
            cases=[
                {
                    "id": "q1",
                    "query": "耳机",
                    "split": "public",
                    "relevanceGrades": {"a": 3, "b": 0},
                }
            ],
            products=_products(),
            index="aishop_eval_fixture",
            output_path=tmp_path / "collection.json.gz",
            candidate_size=2,
        )
