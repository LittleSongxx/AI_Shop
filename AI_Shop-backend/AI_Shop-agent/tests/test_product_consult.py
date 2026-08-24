"""商品咨询上下文解析测试。"""

from app.domain.intent.rules import looks_like_new_product_search
from app.utils.product_consult import (
    is_product_consult_turn,
    normalize_consult_card,
    parse_consult_card,
    product_consult_clarification,
)


def test_normalize_consult_card_camel_case():
    card = normalize_consult_card(
        {"productId": "100", "productName": "车充", "minPrice": 99, "categoryId": "9"}
    )
    assert card == {
        "productId": "100",
        "productName": "车充",
        "minPrice": 99,
        "cover": None,
        "categoryId": "9",
    }

def test_normalize_consult_card_snake_case():
    card = normalize_consult_card(
        {"product_id": "101", "product_name": "公仔", "min_price": 33.9}
    )
    assert card["productId"] == "101"
    assert card["productName"] == "公仔"

def test_parse_consult_card_followup_without_embedded_card():
    card, text = parse_consult_card("有没有类似的")
    assert card is None
    assert text == "有没有类似的"

def test_is_product_consult_turn_with_message_card():
    msg_card = {"productId": "1", "productName": "FG800"}
    assert is_product_consult_turn("", msg_card, None)
    assert is_product_consult_turn("这款怎么样", msg_card, None)
    assert not is_product_consult_turn("有什么类似的推荐吗", msg_card, msg_card)

def test_is_product_consult_turn_followup_without_card():
    consult = {"productId": "1", "productName": "FG800"}
    assert is_product_consult_turn("介绍一下", None, consult)
    assert not is_product_consult_turn("有没有同款", None, consult)

def test_is_product_consult_turn_category_switch_from_computer():
    consult = {"productId": "1", "productName": "AOC荣光T260台式机"}
    assert not is_product_consult_turn("苹果和三星怎么选", None, consult)
    assert not is_product_consult_turn("苹果手机", None, consult)
    assert not is_product_consult_turn("我要买零食", None, consult)
    assert not is_product_consult_turn("预算500想买手机", None, consult)
    assert not is_product_consult_turn("还有什么电脑手机推荐", None, consult)
    assert is_product_consult_turn("这款内存多大", None, consult)

def test_is_product_consult_turn_new_card_overrides_consult():
    consult = {"productId": "1", "productName": "AOC台式机"}
    msg_card = {"productId": "2", "productName": "iPhone 17 Pro Max"}
    assert is_product_consult_turn("这个怎么样", msg_card, consult)

def test_is_product_consult_turn_general_channel_ignores_redis_consult():
    consult = {"productId": "1", "productName": "iPhone 17 Pro Max"}
    assert not is_product_consult_turn("这款内存多大", None, consult, from_product=False)
    assert not is_product_consult_turn("预算500想买手机", None, consult, from_product=False)
    assert not is_product_consult_turn("苹果和三星怎么选", None, consult, from_product=False)

def test_is_product_consult_turn_product_channel_keeps_followup():
    consult = {"productId": "1", "productName": "iPhone 17 Pro Max"}
    assert is_product_consult_turn("这款内存多大", None, consult, from_product=True)
    assert not is_product_consult_turn("预算500想买手机", None, consult, from_product=True)
    assert not is_product_consult_turn("吉他", None, consult, from_product=True)


def test_attribute_questions_do_not_become_fresh_product_searches():
    for text in (
        "这款耳机支持蓝牙 5.4 吗",
        "这副耳机有没有主动降噪",
        "耳机有主动降噪嘛",
        "这款手机续航怎么样",
        "手机壳有没有适配 iPhone 15",
    ):
        assert not looks_like_new_product_search(text)


def test_product_discovery_queries_still_use_search_path():
    for text in (
        "推荐主动降噪耳机",
        "预算 2000 买手机",
        "有没有适合学生的平板",
        "搜索蓝牙耳机",
    ):
        assert looks_like_new_product_search(text)


def test_product_consult_clarification_is_specific_to_requested_attribute():
    assert "蓝牙或版本" in product_consult_clarification("这款耳机支持蓝牙 5.4 吗")
    assert "主动降噪" in product_consult_clarification("耳机有主动降噪嘛")
    assert "续航" in product_consult_clarification("这款手机续航怎么样")
    assert "兼容性" in product_consult_clarification("手机壳适配 iPhone 15 吗")
