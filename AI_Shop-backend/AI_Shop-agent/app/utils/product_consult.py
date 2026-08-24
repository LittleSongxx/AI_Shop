import json

CONSULT_PREFIX = "<<<PRODUCT_CONSULT>>>"
CONSULT_SUFFIX = "<<<END_CARD>>>"

def parse_consult_card(message: str) -> tuple[dict | None, str]:

    if not message or CONSULT_PREFIX not in message:
        return None, message or ""
    start = message.index(CONSULT_PREFIX) + len(CONSULT_PREFIX)
    end = message.find(CONSULT_SUFFIX, start)
    if end < 0:
        return None, message
    json_part = message[start:end].strip()
    user_text = message[end + len(CONSULT_SUFFIX):].strip()
    try:
        card = json.loads(json_part)
        return card, user_text
    except json.JSONDecodeError:
        return None, user_text or message

def build_consult_card_message(card: dict, user_text: str = "") -> str:

    return f"{CONSULT_PREFIX}{json.dumps(card, ensure_ascii=False)}{CONSULT_SUFFIX}{user_text}"

def normalize_consult_card(card: dict | None) -> dict | None:

    if not card:
        return None
    pid = card.get("productId") or card.get("product_id")
    if not pid:
        return None
    return {
        "productId": str(pid),
        "productName": card.get("productName") or card.get("product_name"),
        "minPrice": card.get("minPrice") or card.get("min_price"),
        "cover": card.get("cover"),
        "categoryId": card.get("categoryId") or card.get("category_id"),
    }

def is_product_consult_turn(
    user_text: str | None,
    message_card: dict | None = None,
    consult_card: dict | None = None,
    from_product: bool | None = None,
) -> bool:

    from app.domain.intent.rules import (
        HUMAN_HINTS,
        looks_like_category_switch,
        looks_like_consult_followup,
        looks_like_consult_question,
        looks_like_new_product_search,
        looks_like_same_product_comparison,
    )
    from app.services.product_service import is_similar_or_recommend_request

    if normalize_consult_card(message_card):
        consult_name = (normalize_consult_card(message_card) or {}).get("productName")
    else:
        consult = normalize_consult_card(consult_card)
        consult_name = (consult or {}).get("productName")

    if any(h in (user_text or "") for h in HUMAN_HINTS):
        # 转人工永远优先于商品咨询：用户在咨询中要求转人工，
        # 不能被 PRODUCT_CONSULT 分支吃掉。
        return False
    if looks_like_same_product_comparison(user_text):
        # No selected IDs means the specialist must clarify, not widen the
        # request into an arbitrary product shelf.
        return True
    if is_similar_or_recommend_request(user_text):
        return False
    if looks_like_category_switch(user_text, consult_name):
        return False
    if looks_like_new_product_search(user_text):
        return False
    if normalize_consult_card(message_card):
        return True
    if from_product is False:
        return False

    consult = normalize_consult_card(consult_card)
    if consult:

        return looks_like_consult_followup(user_text)
    if from_product is True:
        # consult-007：客户端从商品页进来但快照缺失（只传了 fromProduct
        # 没传商品 ID / 快照过期）时，规格追问仍按咨询处理——否则每一句
        # 都落到 CHAT 并建议转人工。类别切换/新搜索在上面已被排除。
        # 无卡时没有商品上下文，只认带明确咨询/规格标记的问法
        # （"谢谢""嗯"这类寒暄不进咨询分支）。
        return looks_like_consult_question(user_text)
    return False


def product_consult_clarification(user_text: str | None) -> str:
    """Ask for the minimum identity needed to answer an attribute question."""

    text = str(user_text or "")
    if any(marker in text for marker in ("蓝牙", "版本")):
        return "要核对蓝牙或版本规格，请提供具体商品品牌/型号，或发送商品卡片。"
    if any(marker in text for marker in ("主动降噪", "降噪")):
        return "要核对是否支持主动降噪，请提供具体耳机品牌/型号，或发送商品卡片。"
    if "续航" in text:
        return "要判断续航表现，请提供具体手机品牌/型号，或发送商品卡片。"
    if any(marker in text for marker in ("适配", "兼容")):
        return "要核对兼容性，请提供具体商品型号和手机型号，或发送商品卡片。"
    return "请提供具体商品名称、型号或商品卡片，我才能核对该商品的规格与兼容性。"

async def resolve_consult_card(
    user_id: str,
    message_card: dict | None = None,
    memory_state: dict | None = None,
    from_product: bool | None = None,
) -> dict | None:

    normalized = normalize_consult_card(message_card)
    if normalized:
        return normalized
    if from_product is False:
        return None

    from app.services.redis_service import redis_service

    cached = await redis_service.get_consult_product(user_id)
    normalized = normalize_consult_card(cached)
    if normalized:
        return normalized

    if memory_state:
        normalized = normalize_consult_card(memory_state.get("consultProduct"))
        if normalized:
            return normalized
    return None
