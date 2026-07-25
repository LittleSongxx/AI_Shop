from __future__ import annotations

import json
import re

from app.utils.biz_payload import is_order_cards_json, parse_product_search_message

_PRODUCT_PRICE = re.compile(r"(?:¥|[￥])[\d,]+(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*元")
_LISTING_LINE = re.compile(r".{2,80}[—\-–]\s*\d[\d,]*(?:\.\d+)?\s*元")
_MIN_NAME_LEN = 4

def is_product_search_result(raw: str | None) -> bool:
    _, products = parse_product_search_message(raw)
    return products is not None

def collect_known_product_names(
    tool_biz: dict | None,
    consult_card: dict | None,
    assistant_cards: str | None,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str | None) -> None:
        n = (name or "").strip()
        if len(n) < _MIN_NAME_LEN or n in seen:
            return
        seen.add(n)
        names.append(n)

    for n in (tool_biz or {}).get("productNames") or []:
        _add(str(n))
    if consult_card:
        _add(consult_card.get("productName") or consult_card.get("product_name"))
    if assistant_cards and assistant_cards.strip().startswith("["):
        try:
            parsed = json.loads(assistant_cards)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        _add(item.get("productName") or item.get("product_name"))
        except json.JSONDecodeError:
            pass
    return names

def name_mentioned_in_text(name: str, text: str) -> bool:
    name = (name or "").strip()
    if len(name) < _MIN_NAME_LEN:
        return False
    if name in text:
        return True
    core = re.sub(r"[（(【\[].*?[）)】\]]", "", name).strip()
    if len(core) >= _MIN_NAME_LEN and core in text:
        return True
    tokens = [tok for tok in re.split(r"[\s/|·\-—]+", core) if len(tok) >= 3]
    return any(tok in text for tok in tokens[:6])

def text_contains_product_info(text: str | None, known_names: list[str] | None = None) -> bool:

    t = (text or "").strip()
    if not t or t.startswith("{"):
        return False

    lines = [ln.strip() for ln in re.split(r"[\n\r]+", t) if ln.strip()]
    price_lines = [ln for ln in lines if _PRODUCT_PRICE.search(ln)]
    if len(price_lines) >= 2:
        return True
    if any(_LISTING_LINE.search(ln) for ln in lines):
        return True
    for name in known_names or []:
        if name_mentioned_in_text(name, t):
            return True
    if price_lines and any(len(ln) > 10 for ln in lines):
        return True
    return False

def build_consult_product_cards_json(consult_card: dict | None) -> str | None:

    if not consult_card or not consult_card.get("productId"):
        return None
    card = {
        "productId": str(consult_card["productId"]),
        "productName": consult_card.get("productName") or consult_card.get("product_name") or "",
        "cover": consult_card.get("cover"),
        "minPrice": consult_card.get("minPrice") or consult_card.get("min_price"),
    }
    return json.dumps([card], ensure_ascii=False)

def text_promises_product_cards(text: str | None) -> bool:

    t = (text or "").strip()
    if not t:
        return False
    has_place_ref = any(k in t for k in ("下方", "下面", "以下"))
    has_card_word = any(k in t for k in ("卡片", "推荐商品", "推荐结果", "推荐列表"))
    return has_place_ref and has_card_word

def should_force_product_cards(
    full_text: str | None,
    assistant: str | None,
    tool_biz: dict | None,
    consult_card: dict | None,
    assistant_cards: str | None,
    *,
    is_consult_turn: bool = False,
    tools_called: list[str] | None = None,
) -> bool:

    if is_consult_turn:
        return False
    called = tools_called or []
    if any(
        t in called
        for t in (
            "QUERY_ORDERS",
            "QUERY_LOGISTICS",
            "QUERY_COMMENT",
            "QUERY_USER_COUPONS",
            "PROPOSE_REFUND",
            "PROPOSE_CONFIRM_RECEIPT",
            "PROPOSE_PRODUCT_REVIEW",
            "PROPOSE_RECOMMENT",
            "GET_PRODUCT_DETAIL",
        )
    ):
        return False
    from app.utils.biz_payload import looks_like_aftersales_or_order_text

    if looks_like_aftersales_or_order_text(full_text) or looks_like_aftersales_or_order_text(assistant):
        return False
    if is_order_cards_json(assistant_cards):
        return False
    if is_product_search_result(assistant):
        return False
    known = collect_known_product_names(tool_biz, consult_card, assistant_cards)
    return text_contains_product_info(full_text, known)
