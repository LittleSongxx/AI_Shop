import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.product_search_pipeline import (
    ProductRuntimeConstraints,
    ProductSearchPipeline,
    build_product_query_plan,
    filter_products_by_runtime_constraints,
    filter_products_for_query_plan,
    merge_ranked_lists,
    product_search_evaluation_scope,
)


def test_query_plan_preserves_raw_query_and_adds_normalized_variant():
    plan = build_product_query_plan(
        "预算3000元、华为品牌、适合拍照的手机",
        {
            "category": "手机",
            "useCases": ["拍照影像"],
            "hardConstraints": {"budgetMax": 3000, "requiredBrands": ["华为"]},
            "softPreferences": {"features": ["长续航"]},
            "exclusions": {"brands": ["测试品牌"]},
        },
    )

    assert plan.raw_query == "预算3000元、华为品牌、适合拍照的手机"
    assert plan.retrieval_variants == ("预算3000元、华为品牌、适合拍照的手机", "手机")
    assert plan.constraints.budget_max == 3000
    assert plan.constraints.required_brands == ("华为",)
    assert "managed_taxonomy_additive_variant" in plan.normalization_rules


def test_runtime_constraints_do_not_use_dataset_gold_fields():
    plan = build_product_query_plan(
        "华为手机",
        {"hardConstraints": {"requiredBrands": ["华为"]}},
    )

    assert "relevanceGrades" not in plan.public()
    assert "constraints" not in plan.public()


def test_query_plan_upgrades_broad_mission_category_from_managed_raw_query():
    plan = build_product_query_plan(
        "预算1500元以内的咖啡机",
        {"category": "家电", "hardConstraints": {"budgetMax": 1500}},
    )

    assert plan.constraints.category == "咖啡机"
    assert plan.retrieval_variants == ("预算1500元以内的咖啡机", "咖啡机")
    assert "managed_taxonomy_runtime_category" in plan.normalization_rules


def test_runtime_filter_uses_only_verified_available_fields():
    products = [
        {"id": "ok", "brand": "华为", "price": 2999, "category": "手机"},
        {"id": "price", "brand": "华为", "price": 3999, "category": "手机"},
        {"id": "brand", "brand": "荣耀", "price": 2499, "category": "手机"},
        {"id": "unknown", "productName": "华为手机"},
    ]
    eligible, rejected = filter_products_by_runtime_constraints(
        products,
        ProductRuntimeConstraints(
            category="手机",
            budget_max=3000,
            required_brands=("华为",),
        ),
    )

    assert [row["id"] for row in eligible] == ["ok", "unknown"]
    assert {row["reason"] for row in rejected} == {"OVER_BUDGET", "BRAND_REQUIRED"}


def test_runtime_filter_resolves_required_brand_from_verified_product_name():
    constraints = ProductRuntimeConstraints(required_brands=("苹果",))
    products = [
        {
            "product_id": "apple",
            "product_name": "Apple/苹果 MacBook Pro",
            "price": 9999,
        },
        {
            "product_id": "other",
            "product_name": "无品牌笔记本电脑",
            "price": 4999,
        },
    ]

    eligible, rejected = filter_products_by_runtime_constraints(products, constraints)

    assert [row["product_id"] for row in eligible] == ["apple"]
    assert eligible[0]["brand"] == "苹果"
    assert rejected == [{"productId": "other", "reason": "BRAND_REQUIRED"}]


def test_unknown_catalog_request_drops_comparison_target_hits():
    plan = build_product_query_plan(
        "想买可当天送达的火星土壤样本，并能当作手机直接使用",
        {},
    )

    eligible, rejected = filter_products_for_query_plan(
        [{"id": "phone-1", "productName": "华为智能手机"}], plan
    )

    assert eligible == []
    assert rejected == [
        {
            "productId": "phone-1",
            "reason": "UNKNOWN_CATEGORY_SURFACE_MISMATCH",
        }
    ]


def test_dynamic_catalog_category_is_not_blocked_by_managed_taxonomy_coverage():
    plan = build_product_query_plan("给爸妈用的便携筋膜枪", {})

    eligible, rejected = filter_products_for_query_plan(
        [
            {
                "id": "massage-1",
                "productName": "匹克专业级迷你便携式筋膜枪",
                "price": 399,
            }
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["massage-1"]
    assert rejected == []


def test_managed_category_surface_guard_blocks_cross_category_substitutes():
    plan = build_product_query_plan("蓝牙桌面音箱", {"category": "音箱"})

    eligible, rejected = filter_products_for_query_plan(
        [
            {
                "id": "speaker",
                "productName": "哈曼卡顿蓝牙桌面音箱",
                "price": 2054,
            },
            {
                "id": "headphones",
                "productName": "索尼无线蓝牙降噪耳机",
                "price": 1999,
            },
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["speaker"]
    assert rejected == [
        {
            "productId": "headphones",
            "reason": "MANAGED_CATEGORY_SURFACE_MISMATCH",
        }
    ]


def test_comparison_suffix_cannot_leak_mission_category_into_query_plan():
    query = "想买可当天送达的火星土壤样本，并能当作零食直接使用"
    plan = build_product_query_plan(query, {"category": "零食"})

    assert plan.constraints.category is None
    assert plan.retrieval_variants[0] == query
    assert "comparison_target_excluded_from_requested_category" in plan.normalization_rules


def test_multi_list_rrf_rewards_cross_retriever_agreement():
    assert merge_ranked_lists([["a", "b"], ["b", "c"], ["b"]], 3)[0] == "b"


@pytest.mark.asyncio
async def test_pipeline_runs_all_retrieval_variants_concurrently():
    active = 0
    peak = 0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def search(query: str, _limit: int) -> list[str]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if peak == 4:
            entered.set()
        await release.wait()
        active -= 1
        return ["p1", "p2"] if "手机" in query else ["p2", "p1"]

    async def run_pipeline():
        return await ProductSearchPipeline().search(
            build_product_query_plan(
                "预算3000元适合拍照的手机",
                {"category": "手机", "hardConstraints": {"budgetMax": 3000}},
            ),
            candidate_size=8,
            result_size=2,
            keyword_search=search,
            vector_search=search,
            load_products=AsyncMock(
                return_value=[
                    {"product_id": "p1", "product_name": "拍照手机", "price": 2999},
                    {"product_id": "p2", "product_name": "续航手机", "price": 1999},
                ]
            ),
            rerank=AsyncMock(
                return_value=[
                    {
                        "product_id": "p1",
                        "product_name": "拍照手机",
                        "price": 2999,
                        "_search_rerank_source": "rerank",
                    }
                ]
            ),
        )

    task = asyncio.create_task(run_pipeline())
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()
    result = await task

    assert peak == 4
    assert result.trace.fallback is False
    assert result.trace.query_plan["rawQuery"] == "预算3000元适合拍照的手机"
    assert result.products[0]["product_id"] == "p1"
    assert result.trace.provider_calls == {"bm25": 2, "embeddingVector": 2, "rerank": 1}


@pytest.mark.asyncio
async def test_evaluation_scope_captures_runtime_trace_without_changing_result():
    async def recall(_query: str, _limit: int) -> list[str]:
        return ["p1"]

    async def load(_ids: list[str]) -> list[dict]:
        return [{"product_id": "p1", "product_name": "手机"}]

    async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
        products[0]["_search_rerank_source"] = "rerank"
        return products

    with product_search_evaluation_scope() as capture:
        result = await ProductSearchPipeline().search(
            build_product_query_plan("手机", {"category": "手机"}),
            candidate_size=3,
            result_size=1,
            keyword_search=recall,
            vector_search=recall,
            load_products=load,
            rerank=rerank,
        )

    assert result.products[0]["product_id"] == "p1"
    assert capture.traces == [result.trace]
    assert capture.traces[0].result_source == "rerank"


@pytest.mark.asyncio
async def test_pipeline_does_not_require_full_natural_query_in_product_text():
    async def recall(_query: str, _limit: int) -> list[str]:
        return ["coffee-1"]

    async def load(_ids: list[str]) -> list[dict]:
        return [
            {
                "product_id": "coffee-1",
                "product_name": "华为全自动咖啡机",
                "categoryName": "咖啡机",
                "brand": "华为",
                "price": 999,
            }
        ]

    async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
        products[0]["_search_rerank_source"] = "rerank"
        return products

    result = await ProductSearchPipeline().search(
        build_product_query_plan(
            "预算1000元以内，想买华为品牌的咖啡机，用于家庭早餐",
            {
                "category": "咖啡机",
                "hardConstraints": {
                    "budgetMax": 1000,
                    "requiredBrands": ["华为"],
                },
            },
        ),
        candidate_size=5,
        result_size=3,
        keyword_search=recall,
        vector_search=recall,
        load_products=load,
        rerank=rerank,
    )

    assert [row["product_id"] for row in result.products] == ["coffee-1"]
