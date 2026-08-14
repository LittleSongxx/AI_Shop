import pytest

from app.services.product_search_query import (
    filter_products_by_query_relevance,
    infer_product_category,
    match_terms_for_query,
    normalize_product_search_query,
    taxonomy_contract,
)
from app.services.product_service import derive_search_keyword, format_search_tool_message


def test_normalize_snack_utterance():
    assert taxonomy_contract() == "current"
    assert normalize_product_search_query("我要吃零食") == "零食"
    assert derive_search_keyword("我要吃零食", None) == "零食"


def test_unknown_category_is_preserved_for_generic_search():
    assert normalize_product_search_query("帮我找露营天幕") == "露营天幕"
    assert match_terms_for_query("帮我找露营天幕")[0] == "露营天幕"


@pytest.mark.parametrize(
    ("query", "category"),
    (
        ("预算2000元的空气炸锅", "空气炸锅"),
        ("适合新手的咖啡机", "咖啡机"),
        ("通勤用的双肩包", "双肩包"),
        ("买个智能手表", "智能手表"),
        ("程序员长时间用的办公椅", "办公椅"),
        ("蓝牙桌面音箱", "音箱"),
        ("百瓦多口车载充电器", "车载充电器"),
        ("台式电脑主机", "电脑"),
        ("新生儿衣服礼盒", "服饰"),
    ),
)
def test_infer_product_category_prefers_specific_catalog_leaf(query, category):
    assert infer_product_category(query) == category


def test_comparison_category_does_not_replace_unknown_requested_product():
    query = "想买可当天送达的火星土壤样本，并能当作手机直接使用"

    assert infer_product_category(query) is None


def test_ambiguous_all_in_one_alias_only_matches_the_exact_computer_query():
    assert normalize_product_search_query("我想买个一体机") == "电脑"
    assert normalize_product_search_query("蒸烤一体机") == "蒸烤一体机"
    assert infer_product_category("我想买个一体机") == "电脑"
    assert infer_product_category("蒸烤一体机") is None


def test_bag_mission_query_normalizes_to_catalog_search_term():
    query = "预算提高到1000元，请继续推荐适合上班通勤的包"

    assert normalize_product_search_query(query) == "包"
    assert derive_search_keyword(query, None) == "包"
    assert "旅行包" in match_terms_for_query(query)


def test_bag_topic_keeps_a_real_travel_bag_and_drops_unrelated_packages():
    products = [
        {"product_name": "Walker Shop 大容量手提单肩旅行包"},
        {"product_name": "SolidWorks 软件安装包正版激活服务"},
    ]

    assert filter_products_by_query_relevance(products, "上班通勤的包") == [
        products[0]
    ]


def test_filter_drops_unrelated_hybrid_hits():
    products = [
        {"product_name": "名创优品小猪B-BO趴姿公仔"},
        {"product_name": "任天堂港服NS充值卡"},
        {"product_name": "旺旺雪饼厚烧海苔零食大礼包"},
    ]
    kept = filter_products_by_query_relevance(products, "零食")
    assert len(kept) == 1
    assert "雪饼" in kept[0]["product_name"]


def test_filter_all_miss_means_empty():
    products = [
        {"product_name": "名创优品小猪B-BO趴姿公仔"},
        {"product_name": "WPS Office超级会员"},
    ]
    assert filter_products_by_query_relevance(products, "我要吃零食") == []
    assert "零食" in match_terms_for_query("我要吃零食")


def test_hot_sale_fallback_message_not_found():
    products = [{"product_name": "公仔"}, {"product_name": "点卡"}]
    msg = format_search_tool_message("我要吃零食", None, products, "hot_sale")
    assert "暂未找到" in msg
    assert "零食" in msg
    assert "另荐热销" in msg
    assert "找到 2 个商品" not in msg
