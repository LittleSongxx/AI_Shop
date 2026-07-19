"""Normalize conversational search queries and filter irrelevant hybrid hits."""

from __future__ import annotations

import re

# Longest-first topic hints extracted from user chatter.
_TOPIC_HINTS = (
    "台式机",
    "笔记本",
    "尤克里里",
    "电子琴",
    "iphone",
    "零食",
    "小吃",
    "坚果",
    "糖果",
    "饼干",
    "手机",
    "苹果",
    "三星",
    "华为",
    "小米",
    "oppo",
    "vivo",
    "荣耀",
    "电脑",
    "台式",
    "平板",
    "主机",
    "显示器",
    "玩具",
    "玩偶",
    "模型",
    "积木",
    "乐高",
    "吉他",
    "乐器",
    "钢琴",
    "家电",
    "服饰",
    "衣服",
    "鞋子",
    "美妆",
    "护肤",
    "儿童",
)

# When user asks for a topic, accept related tokens in product titles.
_TOPIC_EXPAND: dict[str, tuple[str, ...]] = {
    "零食": (
        "零食",
        "小吃",
        "坚果",
        "糖果",
        "饼干",
        "薯片",
        "雪饼",
        "辣条",
        "巧克力",
        "膨化",
        "果干",
        "肉脯",
        "糕点",
        "锅巴",
        "虾条",
        "牛肉干",
        "瓜子",
        "旺旺",
        "奥利奥",
    ),
    "小吃": ("小吃", "零食", "糕点", "小吃货"),
    "手机": (
        "手机",
        "iphone",
        "苹果",
        "华为",
        "小米",
        "三星",
        "oppo",
        "vivo",
        "荣耀",
        "红米",
        "手机壳",
    ),
    "电脑": ("电脑", "台式", "笔记本", "主机", "显示器", "一体机"),
    "玩具": ("玩具", "玩偶", "公仔", "积木", "乐高", "模型", "毛绒"),
    "吉他": ("吉他", "尤克里里", "乐器", "民谣", "电吉他"),
}

_FILLERS = re.compile(
    r"(我想要|我要|想要|想买|帮我|给我|麻烦|请你|请|"
    r"有没有|能不能|可以吗|可以|推荐一下|推荐|"
    r"看看|买点|来点|吃点|搜一下|搜索一下|搜索|"
    r"找找|找|买|要|吃)"
)
_PUNCT = re.compile(r"[的了吗呢啊哦呀呗嘛～~，。！？、；：""''\s]+")


def normalize_product_search_query(text: str | None) -> str:
    """Turn「我要吃零食」into「零食」; keep concrete keywords otherwise."""
    t = (text or "").strip()
    if not t:
        return ""
    lower = t.lower()
    for hint in sorted(_TOPIC_HINTS, key=len, reverse=True):
        if hint.lower() in lower:
            return hint
    cleaned = _FILLERS.sub("", t)
    cleaned = _PUNCT.sub("", cleaned).strip()
    return cleaned or t


def match_terms_for_query(query: str | None) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        t = (term or "").strip().lower()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        terms.append(t)

    topic = normalize_product_search_query(q)
    _add(topic)
    _add(q)
    for key, expand in _TOPIC_EXPAND.items():
        if key in q or key in topic or key.lower() in q.lower():
            for e in expand:
                _add(e)
    return terms


def product_matches_query_terms(product: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    name = str(product.get("product_name") or product.get("productName") or "").lower()
    desc = str(
        product.get("product_desc")
        or product.get("productDesc")
        or product.get("description")
        or ""
    ).lower()
    hay = f"{name} {desc}"
    return any(term in hay for term in terms)


def filter_products_by_query_relevance(products: list[dict], query: str | None) -> list[dict]:
    """Drop hybrid hits that share no topic tokens with the user query."""
    if not products:
        return []
    terms = match_terms_for_query(query)
    if not terms:
        return list(products)
    return [p for p in products if product_matches_query_terms(p, terms)]
