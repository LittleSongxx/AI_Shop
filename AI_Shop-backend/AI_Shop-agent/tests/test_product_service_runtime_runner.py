import pytest

from app.services.product_search_pipeline import (
    ProductSearchPipeline,
    build_product_query_plan,
)
from benchmarks.mature_eval.common import read_gzip_json
from benchmarks.mature_eval.product_service_runner import run_product_service_cases


class FakeProductService:
    async def search_products(self, **_kwargs):
        async def recall(_query: str, _limit: int) -> list[str]:
            return ["p1", "p2"]

        async def load(_ids: list[str]) -> list[dict]:
            return [
                {"product_id": "p1", "product_name": "华为手机", "brand": "华为"},
                {"product_id": "p2", "product_name": "其他手机", "brand": "其他"},
            ]

        async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
            for product in products:
                product["_search_rerank_source"] = "rerank"
            return products

        result = await ProductSearchPipeline().search(
            build_product_query_plan(
                "华为手机",
                {"category": "手机", "hardConstraints": {"requiredBrands": ["华为"]}},
            ),
            candidate_size=10,
            result_size=5,
            keyword_search=recall,
            vector_search=recall,
            load_products=load,
            rerank=rerank,
        )
        return "[]", None, "shopping_decision_v2", result.products, "hybrid"


@pytest.mark.asyncio
async def test_runtime_runner_calls_product_service_and_persists_trace(tmp_path):
    output = tmp_path / "runtime.json.gz"
    result = await run_product_service_cases(
        [
            {
                "id": "runtime-1",
                "query": "华为手机",
                "split": "public",
                "relevanceGrades": {"p1": 3},
            }
        ],
        output_path=output,
        service=FakeProductService(),
        authoritative_availability={"p1": True},
    )

    assert result["executedCount"] == 1
    assert result["cases"][0]["rankedIds"] == ["p1"]
    assert result["cases"][0]["runtimeTrace"]["queryPlan"]["rawQuery"] == "华为手机"
    assert result["cases"][0]["goldUsedByRuntime"] is False
    assert result["availabilityAdjustedMetrics"]["metricCurves"]["10"]["recall"] == 1
    assert read_gzip_json(output)["caseCount"] == 1


@pytest.mark.asyncio
async def test_runtime_runner_separates_unavailable_gold_from_catalog_recall(tmp_path):
    output = tmp_path / "runtime-unavailable.json.gz"
    result = await run_product_service_cases(
        [
            {
                "id": "runtime-unavailable",
                "query": "华为手机",
                "split": "public",
                "relevanceGrades": {"p1": 3},
            }
        ],
        output_path=output,
        service=FakeProductService(),
        authoritative_availability={"p1": False},
    )

    case = result["cases"][0]
    assert case["metrics"]["metricsByK"]["10"]["recall"] == 1
    assert case["availabilityAdjustedMetrics"]["expectedNoResults"] is True
    assert case["availabilityAdjustedMetrics"]["noResultCorrect"] is False
    assert case["unavailableRelevantIds"] == ["p1"]
