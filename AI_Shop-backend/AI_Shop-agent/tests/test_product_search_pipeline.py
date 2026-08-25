import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.product_search_pipeline import (
    ProductQueryPlan,
    ProductRuntimeConstraints,
    ProductSearchPipeline,
    build_product_query_plan,
    filter_products_by_runtime_constraints,
    filter_products_for_query_plan,
    merge_ranked_lists,
    product_search_evaluation_scope,
)
from app.services.product_search_query import exact_model_tokens


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

    assert [row["id"] for row in eligible] == ["ok"]
    assert {row["reason"] for row in rejected} == {
        "OVER_BUDGET",
        "BRAND_REQUIRED",
        "BUDGET_UNVERIFIED",
    }


def test_runtime_filter_checks_category_even_when_exclusion_passes():
    eligible, rejected = filter_products_by_runtime_constraints(
        [
            {
                "id": "wrong-category",
                "productName": "帐篷",
                "category": "户外用品",
                "price": 300,
            },
            {
                "id": "right-category",
                "productName": "男士休闲外套",
                "category": "外套",
                "price": 300,
            },
        ],
        ProductRuntimeConstraints(category="外套", excluded_terms=("户外",)),
    )

    assert [row["id"] for row in eligible] == ["right-category"]
    assert rejected == [
        {"productId": "wrong-category", "reason": "CATEGORY_REQUIRED"}
    ]


def test_query_plan_carries_explicit_negative_style_and_phone_case_surface():
    plan = build_product_query_plan(
        "500元以内、不要户外款的男士外套",
        {
            "category": "外套",
            "hardConstraints": {"budgetMax": 500},
            "exclusions": {"terms": ["户外"]},
        },
    )
    assert plan.constraints.excluded_terms == ("户外",)

    case_plan = build_product_query_plan("手机壳有没有适配 iPhone 15", {})
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "case", "productName": "适用于 iPhone 15 的透明手机壳"},
            {"id": "phone", "productName": "Apple iPhone 15 手机"},
        ],
        case_plan,
    )
    assert [row["id"] for row in eligible] == ["case"]
    assert rejected == [{"productId": "phone", "reason": "MANAGED_CATEGORY_SURFACE_MISMATCH"}]


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


def test_runtime_filter_does_not_treat_compatibility_target_as_brand():
    constraints = ProductRuntimeConstraints(
        category="手机", required_brands=("苹果",), budget_max=7000
    )
    products = [
        {
            "product_id": "charger",
            "product_name": "CUKTECH车载充电器适用苹果17小米",
            "price": 99,
        }
    ]

    eligible, rejected = filter_products_by_runtime_constraints(products, constraints)

    assert eligible == []
    assert rejected == [{"productId": "charger", "reason": "BRAND_REQUIRED"}]


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


@pytest.mark.parametrize(
    ("query", "matching_name", "reason"),
    [
        ("雪饼不要旺旺牌", "芒果味休闲零食", "MANAGED_CATEGORY_SURFACE_MISMATCH"),
        ("厨下净水器不要COLMO", "家用空气净化器", "MANAGED_CATEGORY_SURFACE_MISMATCH"),
        ("入门民谣琴排除电箱款", "维修工具箱", "MANAGED_CATEGORY_SURFACE_MISMATCH"),
        ("火星量子无人机第九代", "可充大疆无人机的车载充电器", "MANAGED_CATEGORY_SURFACE_MISMATCH"),
        ("纯素食牛肉味零食", "原切牛肉干休闲零食", "UNVERIFIED_REQUIRED_ATTRIBUTE"),
    ],
)
def test_query_surface_contract_prevents_unrelated_candidate_backfill(
    query: str,
    matching_name: str,
    reason: str,
):
    plan = build_product_query_plan(query, {})

    eligible, rejected = filter_products_for_query_plan(
        [{"id": "unrelated", "productName": matching_name}], plan
    )

    assert eligible == []
    assert rejected == [{"productId": "unrelated", "reason": reason}]


@pytest.mark.parametrize(
    ("query", "excluded_term", "unrelated_name"),
    [
        ("雪饼不要旺旺牌", "旺旺", "芒果味休闲零食"),
        ("厨下净水器不要COLMO", "COLMO", "家用空气净化器"),
    ],
)
def test_specific_type_contract_stays_strict_with_exclusions(
    query: str, excluded_term: str, unrelated_name: str
):
    plan = build_product_query_plan(
        query,
        {"exclusions": {"terms": [excluded_term]}},
    )

    eligible, rejected = filter_products_for_query_plan(
        [{"id": "unrelated", "productName": unrelated_name}], plan
    )

    assert eligible == []
    assert rejected == [
        {
            "productId": "unrelated",
            "reason": "MANAGED_CATEGORY_SURFACE_MISMATCH",
        }
    ]


def test_exact_model_contract_rejects_nearby_but_different_model():
    plan = build_product_query_plan("WH-1000XM999 原装耳机", {})

    eligible, rejected = filter_products_for_query_plan(
        [{"id": "xm6", "productName": "索尼 WH-1000XM6 降噪耳机"}], plan
    )

    assert exact_model_tokens(plan.raw_query) == ("wh1000xm999",)
    assert eligible == []
    assert rejected == [{"productId": "xm6", "reason": "EXACT_MODEL_MISMATCH"}]


def test_comparison_model_tokens_match_alternative_products_independently():
    plan = build_product_query_plan("华硕破晓6X与AOC T260办公机怎么选", {})
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "asus", "productName": "华硕破晓6X 商用办公电脑"},
            {"id": "aoc", "productName": "AOC T260 办公整机"},
            {"id": "other", "productName": "无型号办公电脑"},
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["asus", "aoc"]
    assert rejected == [{"productId": "other", "reason": "EXACT_MODEL_MISMATCH"}]


def test_negative_model_query_keeps_requested_alternative():
    plan = build_product_query_plan("无线降噪耳机排除XM6保留十周年版", {})
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "xm6", "productName": "索尼 WH-1000XM6 降噪耳机"},
            {"id": "xm10", "productName": "索尼 WH-1000XX 十周年典藏版耳机"},
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["xm10"]
    assert not any(row["reason"] == "EXACT_MODEL_MISMATCH" for row in rejected)


def test_raw_query_hard_constraints_merge_with_mission_and_keep_string_fields_atomic():
    plan = build_product_query_plan(
        "100元以内旺旺雪饼和可乐零食",
        {
            "hardConstraints": {
                "budgetMin": 20,
                "budgetMax": 120,
                "mustTerms": "零食",
            },
            "exclusions": {"terms": "临期"},
        },
    )

    assert plan.constraints.budget_min == 20
    assert plan.constraints.budget_max == 100
    assert plan.constraints.must_terms == ("零食", "旺旺雪饼", "可乐")
    assert plan.constraints.must_not_terms == ("临期",)
    assert plan.constraints.comparison_targets == ("旺旺雪饼", "可乐")
    assert plan.constraints.comparison_required is True


def test_named_snack_comparison_requires_both_targets_and_budget_evidence():
    plan = build_product_query_plan("100元以内旺旺雪饼和可乐零食", {})
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "snow", "productName": "旺旺雪饼零食", "price": 25},
            {"id": "cola", "productName": "可乐零食组合装", "price": 39},
            {"id": "over", "productName": "可乐零食豪华装", "price": 129},
            {"id": "unknown", "productName": "旺旺雪饼礼盒"},
            {"id": "other", "productName": "薯片零食", "price": 10},
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["snow", "cola"]
    assert {row["reason"] for row in rejected} >= {
        "OVER_BUDGET",
        "BUDGET_UNVERIFIED",
    }
    assert any(row["productId"] == "other" for row in rejected)


def test_raw_negative_term_is_enforced_without_mission_parser_support():
    plan = build_product_query_plan("平价零食不要旺旺雪饼", {})
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "excluded", "productName": "旺旺雪饼零食", "price": 10},
            {"id": "kept", "productName": "芒果味休闲零食", "price": 12},
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["kept"]
    assert rejected == [{"productId": "excluded", "reason": "TERM_EXCLUDED"}]


def test_named_headphone_comparison_tracks_each_target_independently():
    plan = build_product_query_plan(
        "WH-1000XM6和十周年版降噪耳机如何比较", {}
    )
    eligible, _rejected = filter_products_for_query_plan(
        [
            {"id": "xm6", "productName": "Sony WH-1000XM6 无线降噪耳机"},
            {"id": "anniversary", "productName": "Sony 十周年典藏版降噪耳机"},
        ],
        plan,
    )

    from app.services.product_search_pipeline import comparison_target_coverage

    coverage, complete, reason = comparison_target_coverage(eligible, plan)
    assert coverage == {"wh1000xm6": 1, "十周年版": 1}
    assert complete is True
    assert reason is None


def test_runtime_surface_contract_keeps_verified_target_product():
    plan = build_product_query_plan("入门民谣琴排除电箱款", {})

    eligible, rejected = filter_products_for_query_plan(
        [{"id": "fg800", "productName": "YAMAHA FG800 原声民谣吉他"}], plan
    )

    assert [row["id"] for row in eligible] == ["fg800"]
    assert rejected == []


def test_negative_category_alias_does_not_create_positive_surface_contract():
    plan = build_product_query_plan(
        "平价零食不要旺旺雪饼", {"exclusions": {"terms": ["旺旺"]}}
    )
    eligible, rejected = filter_products_for_query_plan(
        [
            {"id": "cola", "productName": "可乐整箱汽水", "price": 20},
            {"id": "wangwang", "productName": "旺旺雪饼零食", "price": 10},
        ],
        plan,
    )

    assert [row["id"] for row in eligible] == ["cola"]
    assert {row["reason"] for row in rejected} == {"TERM_EXCLUDED"}


def test_category_without_readable_snapshot_fields_is_not_rejected_by_title_absence():
    plan = build_product_query_plan(
        "平价零食不要旺旺雪饼", {"exclusions": {"terms": ["旺旺"]}}
    )
    eligible, rejected = filter_products_for_query_plan(
        [{"id": "alt", "productName": "芒果味休闲小吃", "price": 20}], plan
    )

    assert [row["id"] for row in eligible] == ["alt"]
    assert rejected == []


def test_runtime_exclusion_uses_explicit_sku_variant_evidence_for_mixed_title():
    product = {
        "id": "fg800",
        "productName": "YAMAHA FG800 原声弹唱电箱入门吉他",
        "property_values": [
            {
                "property_value_id": "series",
                "property_name": "系列品",
                "property_value": "FG800经典入门单板",
            },
            {
                "property_value_id": "acoustic",
                "property_name": "规格",
                "property_value": "FG800 41英寸原声款",
            },
        ],
        "skus": [
            {
                "property_value_id_hash": "sku-acoustic",
                "property_value_ids": "series-acoustic",
            }
        ],
    }

    eligible, rejected = filter_products_by_runtime_constraints(
        [product], ProductRuntimeConstraints(excluded_terms=("电箱",))
    )

    assert rejected == []
    assert eligible[0]["constraint_allowed_sku_keys"] == ["sku-acoustic"]
    assert eligible[0]["constraint_evidence_contracts"] == [
        "acoustic-guitar-not-electric-box"
    ]


def test_runtime_exclusion_rejects_electric_sku_and_never_uses_absence_as_proof():
    electric = {
        "id": "electric",
        "productName": "YAMAHA 民谣电箱吉他",
        "property_values": [
            {
                "property_value_id": "electric-option",
                "property_name": "规格",
                "property_value": "41英寸电箱款",
            }
        ],
        "skus": [
            {
                "property_value_id_hash": "sku-electric",
                "property_value_ids": "electric-option",
            }
        ],
    }
    generic = {
        "id": "in-ear",
        "productName": "无线入耳式耳机",
        "property_values": [
            {
                "property_value_id": "black",
                "property_name": "颜色",
                "property_value": "黑色",
            }
        ],
        "skus": [
            {"property_value_id_hash": "sku-black", "property_value_ids": "black"}
        ],
    }

    electric_eligible, electric_rejected = filter_products_by_runtime_constraints(
        [electric], ProductRuntimeConstraints(excluded_terms=("电箱",))
    )
    generic_eligible, generic_rejected = filter_products_by_runtime_constraints(
        [generic], ProductRuntimeConstraints(excluded_terms=("入耳",))
    )

    assert electric_eligible == []
    assert electric_rejected == [{"productId": "electric", "reason": "TERM_EXCLUDED"}]
    assert generic_eligible == []
    assert generic_rejected == [{"productId": "in-ear", "reason": "TERM_EXCLUDED"}]


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
async def test_pipeline_fails_closed_when_rerank_drops_one_comparison_target():
    async def recall(_query: str, _limit: int) -> list[str]:
        return ["snow", "cola"]

    async def load(_ids: list[str]) -> list[dict]:
        return [
            {"product_id": "snow", "product_name": "旺旺雪饼零食", "price": 25},
            {"product_id": "cola", "product_name": "可乐零食组合装", "price": 39},
        ]

    async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
        return [{**products[0], "_search_rerank_source": "rerank"}]

    result = await ProductSearchPipeline().search(
        build_product_query_plan("100元以内旺旺雪饼和可乐零食", {}),
        candidate_size=4,
        result_size=2,
        keyword_search=recall,
        vector_search=recall,
        load_products=load,
        rerank=rerank,
    )

    assert result.products == []
    assert result.trace.result_source == "comparison_incomplete"
    assert result.trace.comparison_coverage == {"旺旺雪饼": 1, "可乐": 0}
    assert result.trace.comparison_complete is False
    assert result.trace.incomplete_reason == "MISSING_COMPARISON_TARGETS"


@pytest.mark.asyncio
async def test_pipeline_rejects_product_injected_only_by_reranker():
    async def recall(_query: str, _limit: int) -> list[str]:
        return ["p1"]

    result = await ProductSearchPipeline().search(
        build_product_query_plan("手机", {}),
        candidate_size=4,
        result_size=2,
        keyword_search=recall,
        vector_search=recall,
        load_products=AsyncMock(
            return_value=[
                {"product_id": "p1", "product_name": "手机", "categoryName": "手机"}
            ]
        ),
        rerank=AsyncMock(
            return_value=[
                {
                    "product_id": "injected",
                    "product_name": "手机",
                    "categoryName": "手机",
                    "_search_rerank_source": "rerank",
                }
            ]
        ),
    )

    assert result.products == []
    assert result.trace.rejection_counts["RERANK_UNKNOWN_PRODUCT"] == 1


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


@pytest.mark.asyncio
async def test_pipeline_keeps_successful_recall_when_one_provider_fails():
    async def keyword(_query: str, _limit: int) -> list[str]:
        return ["p1"]

    async def vector(_query: str, _limit: int) -> list[str]:
        raise RuntimeError("vector unavailable")

    async def load(_ids: list[str]) -> list[dict]:
        return [{"product_id": "p1", "product_name": "手机", "categoryName": "手机"}]

    async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
        for product in products:
            product["_search_rerank_source"] = "rerank"
        return products

    result = await ProductSearchPipeline().search(
        ProductQueryPlan(
            raw_query="手机",
            retrieval_variants=("手机",),
            constraints=ProductRuntimeConstraints(category="手机"),
        ),
        candidate_size=5,
        result_size=2,
        keyword_search=keyword,
        vector_search=vector,
        load_products=load,
        rerank=rerank,
    )

    assert [row["product_id"] for row in result.products] == ["p1"]
    assert result.trace.partial_failure is True
    assert result.trace.provider_failures["vector"] == 1
    assert result.trace.provider_calls == {"bm25": 1, "embeddingVector": 1, "rerank": 1}


@pytest.mark.asyncio
async def test_pipeline_times_out_slow_recall_and_keeps_fast_result():
    async def recall(query: str, _limit: int) -> list[str]:
        if query == "slow":
            await asyncio.sleep(1)
        return ["p1"]

    async def load(_ids: list[str]) -> list[dict]:
        return [{"product_id": "p1", "product_name": "手机", "categoryName": "手机"}]

    async def rerank(_query: str, products: list[dict], _limit: int) -> list[dict]:
        return products

    result = await ProductSearchPipeline().search(
        ProductQueryPlan(
            raw_query="slow",
            retrieval_variants=("fast", "slow"),
            constraints=ProductRuntimeConstraints(),
        ),
        candidate_size=5,
        result_size=2,
        keyword_search=recall,
        vector_search=recall,
        load_products=load,
        rerank=rerank,
        # The budget is end-to-end; provider-level timeout leaves enough time
        # for the fast provider's snapshot load and rerank.
        deadline_seconds=0.2,
        provider_timeout_seconds=0.05,
    )

    assert result.products
    assert result.trace.partial_failure is True
    assert result.trace.cancelled_count == 0
    assert result.trace.deadline_exceeded is False
    assert sum(result.trace.provider_timeouts.values()) >= 1


@pytest.mark.asyncio
async def test_pipeline_fails_closed_when_snapshot_load_exhausts_end_to_end_budget():
    async def recall(_query: str, _limit: int) -> list[str]:
        return ["p1"]

    async def load(_ids: list[str]) -> list[dict]:
        await asyncio.sleep(1)
        return [{"product_id": "p1", "categoryName": "手机"}]

    result = await ProductSearchPipeline().search(
        ProductQueryPlan(
            raw_query="手机",
            retrieval_variants=("手机",),
            constraints=ProductRuntimeConstraints(),
        ),
        candidate_size=5,
        result_size=2,
        keyword_search=recall,
        vector_search=recall,
        load_products=load,
        rerank=AsyncMock(return_value=[]),
        deadline_seconds=0.05,
        provider_timeout_seconds=0.5,
    )

    assert result.products == []
    assert result.ranked_ids == ["p1"]
    assert result.trace.result_source == "snapshot_unavailable"
    assert result.trace.stage_timeouts["snapshotLoad"] == 1
    assert result.trace.deadline_exceeded is True
