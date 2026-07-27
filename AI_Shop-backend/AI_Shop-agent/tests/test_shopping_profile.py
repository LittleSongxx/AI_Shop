from app.constants import CLARIFY_MAX_TEXT_LENGTH
from app.services.shopping_profile_service import (
    ShoppingProfileService,
    empty_profile,
    extract_profile,
    merge_profiles,
)


def test_extract_profile_parses_budget_brand_category_and_preferences():
    profile = extract_profile("想买华为手机，预算3000到5000，办公续航，不要苹果")

    assert profile["category"] == "手机"
    assert profile["budgetMin"] == 3000
    assert profile["budgetMax"] == 5000
    assert profile["brands"] == ["华为"]
    assert profile["excludedBrands"] == ["苹果"]
    assert profile["scenarios"] == ["办公"]
    assert profile["features"] == ["续航"]


def test_extract_profile_supports_units_and_upper_budget():
    profile = extract_profile("推荐3k以内的轻薄笔记本")

    assert profile["budgetMin"] is None
    assert profile["budgetMax"] == 3000
    assert profile["category"] == "笔记本电脑"
    assert profile["features"] == ["便携"]


def test_merge_profile_keeps_previous_constraints():
    current = extract_profile("预算3000以内的华为手机")
    incoming = extract_profile("办公、续航，排除苹果")
    merged = merge_profiles(current, incoming)

    assert merged["budgetMax"] == 3000
    assert merged["brands"] == ["华为"]
    assert merged["excludedBrands"] == ["苹果"]
    assert merged["scenarios"] == ["办公"]
    assert merged["features"] == ["续航"]


def test_later_brand_statement_overrides_previous_preference_or_exclusion():
    excluded = merge_profiles(extract_profile("想买华为手机"), extract_profile("不要华为"))
    preferred = merge_profiles(extract_profile("不要苹果"), extract_profile("苹果手机也可以"))

    assert excluded["brands"] == []
    assert excluded["excludedBrands"] == ["华为"]
    assert preferred["brands"] == ["苹果"]
    assert preferred["excludedBrands"] == []


def test_generic_request_requires_clarification_without_profile():
    service = ShoppingProfileService()
    profile = empty_profile()

    assert service.should_clarify("推荐几个商品", "推荐几个商品", profile, None)
    # A considered purchase with only a category is worth one clarifying turn:
    # "手机" alone cannot rank a shelf, budget and scenario can.
    assert service.should_clarify("推荐手机", "推荐手机", profile, None)
    # Low-consideration categories stay direct — no budget interview for snacks.
    assert not service.should_clarify("我要吃零食", "零食", profile, None)


def test_narrowing_signal_suppresses_clarification():
    service = ShoppingProfileService()
    empty = empty_profile()

    # Budget, brand, scenario or feature in the request itself is enough to rank on.
    assert not service.should_clarify("3000以内的手机", "手机", empty, None)
    assert not service.should_clarify("推荐华为手机", "华为手机", empty, None)
    assert not service.should_clarify("办公用的笔记本", "笔记本", empty, None)
    assert not service.should_clarify("轻薄的笔记本", "笔记本", empty, None)

    # A remembered budget also suppresses it, so we never ask the same thing twice.
    remembered = extract_profile("预算3000以内")
    assert not service.should_clarify("推荐手机", "推荐手机", remembered, None)

    # A remembered category alone must NOT suppress it — that was the old bug.
    category_only = extract_profile("买个手机")
    assert service.should_clarify("推荐手机", "推荐手机", category_only, None)


def test_long_request_skips_clarification():
    service = ShoppingProfileService()
    profile = empty_profile()

    # Past the length gate the user has spelled out their own intent.
    long_request = (
        "我最近想换一台手机不过还没决定好到底要选哪个品牌和什么价位"
        "你先随便给我看看有哪些款式我再慢慢挑吧"
    )
    assert len(long_request) > CLARIFY_MAX_TEXT_LENGTH
    assert not service.should_clarify(long_request, "手机", profile, None)


def test_consult_context_skips_clarification():
    service = ShoppingProfileService()
    profile = empty_profile()

    # Looking at a product already supplies the context a question would ask for.
    assert not service.should_clarify(
        "推荐手机",
        "推荐手机",
        profile,
        {"productId": "1", "productName": "某品牌手机"},
    )


def test_filter_products_applies_budget_and_brand_constraints():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的华为手机，不要苹果")
    products = [
        {"product_id": "1", "product_name": "华为轻薄手机", "min_price": 2499, "max_price": 2999, "status": 1},
        {"product_id": "2", "product_name": "华为旗舰手机", "min_price": 3999, "max_price": 4999, "status": 1},
        {"product_id": "3", "product_name": "苹果手机", "min_price": 1999, "max_price": 2999, "status": 1},
    ]

    kept = service.filter_products(products, profile)

    assert [item["product_id"] for item in kept] == ["1"]
    assert "符合预算" in service.recommend_reason(kept[0], profile, "hybrid")


def test_accept_substitute_turns_brand_into_soft_preference():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的华为手机，其他品牌也可以")
    products = [
        {"product_id": "1", "product_name": "荣耀手机", "min_price": 1999, "status": 1},
        {"product_id": "2", "product_name": "华为手机", "min_price": 2499, "status": 1},
    ]

    assert profile["acceptSubstitute"] is True
    assert [item["product_id"] for item in service.filter_products(products, profile)] == ["1", "2"]


def test_filter_products_rejects_known_out_of_stock_but_keeps_unknown_stock():
    service = ShoppingProfileService()
    profile = extract_profile("预算3000以内的手机")
    products = [
        {"product_id": "1", "product_name": "手机A", "min_price": 1999, "total_stock": 0, "status": 1},
        {"product_id": "2", "product_name": "手机B", "min_price": 2499, "in_stock": False, "status": 1},
        {"product_id": "3", "product_name": "手机C", "min_price": 2599, "status": 1},
        {"product_id": "4", "product_name": "手机D", "min_price": 2799, "total_stock": 5, "status": 1},
    ]

    kept = service.filter_products(products, profile)

    assert [item["product_id"] for item in kept] == ["3", "4"]


def test_constraint_summary_is_user_readable():
    profile = extract_profile("预算3000以内的华为手机，办公")

    assert ShoppingProfileService.summary(profile) == "预算不超过3000元、偏好华为、类别手机、场景办公"
