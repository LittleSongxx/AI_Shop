"""Pure-function tests for the offline product enrichment script."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from enrich_products import (  # noqa: E402
    build_enriched_text,
    is_placeholder_name,
    parse_enrichment,
)


def test_placeholder_names_are_detected():
    # Generated test data carries no product semantics; enriching it makes the
    # LLM invent specifications that then get embedded as false matches.
    assert is_placeholder_name("商品-105-00001")
    assert is_placeholder_name("商品_105_00001")
    assert is_placeholder_name("")
    assert is_placeholder_name("   ")

    assert not is_placeholder_name("小米 Redmi Buds 蓝牙耳机")
    assert not is_placeholder_name("美的空气炸锅 5L")
    # A real title that merely starts with 商品 must not be swept up.
    assert not is_placeholder_name("商品级降噪耳机")


def test_parse_enrichment_tolerates_code_fences_and_prose():
    fenced = """```json
    {"synonyms":["蓝牙耳机","无线耳机"],"attributes":["降噪"],"scenarios":["通勤"],"audiences":["学生"]}
    ```"""
    parsed = parse_enrichment(fenced)
    assert parsed["synonyms"] == ["蓝牙耳机", "无线耳机"]
    assert parsed["attributes"] == ["降噪"]
    assert parsed["scenarios"] == ["通勤"]
    assert parsed["audiences"] == ["学生"]

    leading_prose = '好的，结果如下：{"synonyms":["耳麦"],"attributes":[],"scenarios":[],"audiences":[]}'
    assert parse_enrichment(leading_prose)["synonyms"] == ["耳麦"]


def test_parse_enrichment_rejects_unusable_payloads():
    assert parse_enrichment("") == {}
    assert parse_enrichment("抱歉，我无法完成") == {}
    assert parse_enrichment("{not valid json") == {}
    assert parse_enrichment("[1,2,3]") == {}


def test_parse_enrichment_drops_prose_entries_and_duplicates():
    raw = (
        '{"synonyms":["蓝牙耳机","蓝牙耳机","这是一个非常长的描述性句子不应该作为标签"],'
        '"attributes":[],"scenarios":[],"audiences":[]}'
    )
    assert parse_enrichment(raw)["synonyms"] == ["蓝牙耳机"]


def test_parse_enrichment_caps_list_lengths():
    many = ",".join(f'"词{i}"' for i in range(20))
    parsed = parse_enrichment(
        f'{{"synonyms":[{many}],"attributes":[],"scenarios":[{many}],"audiences":[{many}]}}'
    )
    assert len(parsed["synonyms"]) == 8
    assert len(parsed["scenarios"]) == 5
    assert len(parsed["audiences"]) == 4


def test_build_enriched_text_includes_only_present_sections():
    product = {"name": "蓝牙耳机", "desc": "", "props": "无"}
    text = build_enriched_text(product, {"synonyms": ["无线耳机"]})

    assert "商品名称：蓝牙耳机" in text
    assert "别称：无线耳机" in text
    # Empty description and placeholder props must not emit empty headings.
    assert "商品描述" not in text
    assert "规格" not in text
    assert "适用场景" not in text


def test_build_enriched_text_composes_all_sections():
    product = {
        "name": "美的空气炸锅",
        "desc": "5L 大容量，无油低脂",
        "props": "颜色:白色，功率:1500W",
    }
    enrichment = {
        "synonyms": ["空气炸锅", "无油炸锅"],
        "attributes": ["5L", "大容量"],
        "scenarios": ["厨房", "家用"],
        "audiences": ["家庭"],
    }
    text = build_enriched_text(product, enrichment)

    for expected in (
        "商品名称：美的空气炸锅",
        "商品描述：5L 大容量，无油低脂",
        "规格：颜色:白色，功率:1500W",
        "别称：空气炸锅、无油炸锅",
        "属性：5L、大容量",
        "适用场景：厨房、家用",
        "适用人群：家庭",
    ):
        assert expected in text


def test_build_enriched_text_truncates_long_descriptions():
    product = {"name": "X", "desc": "描" * 600, "props": "无"}
    text = build_enriched_text(product, {})
    assert len(text) < 400
