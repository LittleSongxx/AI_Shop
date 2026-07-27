from __future__ import annotations

import re
from typing import Any

import structlog

from app.constants import CLARIFY_MAX_TEXT_LENGTH, PRODUCT_STATUS_ON_SALE
from app.services.redis_service import redis_service

logger = structlog.get_logger()

_AMOUNT = r"(\d+(?:\.\d+)?)\s*(万|千|k|K)?"
_RANGE_RE = re.compile(rf"{_AMOUNT}\s*(?:-|~|～|至|到)\s*{_AMOUNT}\s*(?:元|块)?")
_UPPER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*(?:不超过|不高于|最多|低于|小于)\s*{_AMOUNT}\s*(?:元|块)?"
)
_SUFFIX_UPPER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*{_AMOUNT}\s*(?:以内|以下|封顶|左右以内)"
)
_LOWER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*(?:至少|不低于|不少于|最低)\s*{_AMOUNT}\s*(?:元|块)?"
)
_PLAIN_BUDGET_RE = re.compile(rf"(?:预算|价格|价位)\s*{_AMOUNT}\s*(?:元|块)?")

_BRAND_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("红米", ("红米", "redmi")),
    ("苹果", ("苹果", "apple", "iphone", "ipad", "macbook")),
    ("华为", ("华为", "huawei")),
    ("小米", ("小米", "xiaomi")),
    ("荣耀", ("荣耀", "honor")),
    ("三星", ("三星", "samsung")),
    ("OPPO", ("oppo",)),
    ("vivo", ("vivo",)),
    ("联想", ("联想", "lenovo")),
    ("戴尔", ("戴尔", "dell")),
    ("惠普", ("惠普", "hp")),
    ("耐克", ("耐克", "nike")),
    ("阿迪达斯", ("阿迪达斯", "adidas")),
)

_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机", ("手机", "智能机")),
    ("笔记本电脑", ("笔记本", "笔记本电脑", " laptop ", "laptop")),
    ("电脑", ("电脑", "台式机", "主机")),
    ("平板", ("平板", "ipad")),
    ("零食", ("零食", "食品", "吃的")),
    ("家电", ("家电", "电器")),
    ("耳机", ("耳机", "降噪耳机")),
    ("相机", ("相机", "摄影")),
    ("玩具", ("玩具", "公仔")),
    ("乐器", ("乐器", "吉他", "钢琴")),
    ("服饰", ("服装", "衣服", "服饰")),
    ("鞋子", ("鞋子", "运动鞋", "跑鞋")),
    ("美妆", ("美妆", "护肤", "化妆品")),
)

_SCENARIO_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("办公", ("办公", "工作", "上班")),
    ("游戏", ("游戏", "电竞")),
    ("拍照", ("拍照", "摄影", "影像")),
    ("送礼", ("送礼", "礼物", "送人")),
    ("学生", ("学生", "上课")),
    ("老人", ("老人", "长辈")),
    ("儿童", ("儿童", "孩子", "小孩")),
    ("通勤", ("通勤",)),
    ("旅行", ("旅行", "出差")),
)

_FEATURE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("便携", ("便携", "轻薄", "小巧")),
    ("续航", ("续航", "电池耐用", "待机久")),
    ("性价比", ("性价比", "实惠", "划算")),
    ("大屏", ("大屏", "屏幕大")),
    ("高性能", ("高性能", "性能强", "配置高")),
)

_NEGATIVE_BRAND_WORDS = ("不要", "不想要", "排除", "不考虑", "不选", "别买", "避开")
_GENERIC_RECOMMEND_WORDS = ("推荐", "买什么", "选什么", "挑什么", "有什么好物", "找点")

# Low-consideration categories: the bare category is already a good enough query,
# so a budget/scenario question costs a turn and buys almost no ranking quality.
# Considered purchases (phone, laptop, camera, appliance...) are the opposite.
_CLARIFY_EXEMPT_CATEGORIES = ("零食",)
_ACCEPT_SUBSTITUTE_HINTS = (
    "可以替代",
    "接受替代",
    "接受其他品牌",
    "其他品牌也可以",
    "不限品牌",
    "同类都可以",
)
_REJECT_SUBSTITUTE_HINTS = ("不接受替代", "不要替代", "只要这个品牌", "必须是这个品牌", "只考虑这个品牌")


def empty_profile() -> dict[str, Any]:
    return {
        "version": 1,
        "category": None,
        "budgetMin": None,
        "budgetMax": None,
        "brands": [],
        "excludedBrands": [],
        "scenarios": [],
        "features": [],
        "acceptSubstitute": None,
    }


def _amount(value: str, unit: str | None) -> float:
    number = float(value)
    if unit in ("万",):
        return number * 10000
    if unit in ("千",):
        return number * 1000
    if unit in ("k", "K"):
        return number * 1000
    return number


def _budget_from_match(match: re.Match[str], first: int = 1) -> float:
    return _amount(match.group(first), match.group(first + 1))


def _parse_budget(text: str) -> tuple[float | None, float | None]:
    match = _RANGE_RE.search(text)
    if match:
        left = _budget_from_match(match, 1)
        right = _budget_from_match(match, 3)
        return (min(left, right), max(left, right))

    match = _UPPER_RE.search(text) or _SUFFIX_UPPER_RE.search(text)
    if match:
        return None, _budget_from_match(match)

    match = _LOWER_RE.search(text)
    if match:
        return _budget_from_match(match), None

    match = _PLAIN_BUDGET_RE.search(text)
    if match:
        return None, _budget_from_match(match)
    return None, None


def _brand_in_text(text: str, aliases: tuple[str, ...]) -> bool:
    return any(re.search(re.escape(alias), text, flags=re.IGNORECASE) for alias in aliases)


def _brand_is_excluded(text: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        escaped = re.escape(alias)
        negative_before = rf"(?:{'|'.join(map(re.escape, _NEGATIVE_BRAND_WORDS))})\s*(?:品牌)?\s*{escaped}"
        negative_after = rf"{escaped}\s*(?:{'|'.join(map(re.escape, _NEGATIVE_BRAND_WORDS))})"
        if re.search(negative_before, text, flags=re.IGNORECASE):
            return True
        if re.search(negative_after, text, flags=re.IGNORECASE):
            return True
    return False


def extract_profile(text: str | None) -> dict[str, Any]:
    value = (text or "").strip()
    profile = empty_profile()
    if not value:
        return profile

    budget_min, budget_max = _parse_budget(value)
    profile["budgetMin"] = budget_min
    profile["budgetMax"] = budget_max

    for canonical, aliases in _BRAND_ALIASES:
        if not _brand_in_text(value, aliases):
            continue
        if _brand_is_excluded(value, aliases):
            profile["excludedBrands"].append(canonical)
        else:
            profile["brands"].append(canonical)

    for canonical, aliases in _CATEGORY_HINTS:
        if any(alias.strip().lower() in value.lower() for alias in aliases):
            profile["category"] = canonical
            break

    profile["scenarios"] = [
        canonical
        for canonical, aliases in _SCENARIO_HINTS
        if any(alias in value for alias in aliases)
    ]
    profile["features"] = [
        canonical
        for canonical, aliases in _FEATURE_HINTS
        if any(alias in value for alias in aliases)
    ]
    if any(hint in value for hint in _ACCEPT_SUBSTITUTE_HINTS):
        profile["acceptSubstitute"] = True
    elif any(hint in value for hint in _REJECT_SUBSTITUTE_HINTS):
        profile["acceptSubstitute"] = False
    return profile


def _has_signal(profile: dict[str, Any]) -> bool:
    return bool(
        profile.get("category")
        or profile.get("budgetMin") is not None
        or profile.get("budgetMax") is not None
        or profile.get("brands")
        or profile.get("excludedBrands")
        or profile.get("scenarios")
        or profile.get("features")
        or profile.get("acceptSubstitute") is not None
    )


def _has_narrowing_signal(profile: dict[str, Any]) -> bool:
    """Signals that actually narrow a result set, so clarification adds nothing.

    A bare category is deliberately excluded: "买个笔记本" tells us the shelf but
    not the budget, scenario or brand, which is exactly the case where one
    clarifying question buys the most ranking quality.
    """
    return bool(
        profile.get("budgetMin") is not None
        or profile.get("budgetMax") is not None
        or profile.get("brands")
        or profile.get("excludedBrands")
        or profile.get("scenarios")
        or profile.get("features")
    )


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    result = list(existing or [])
    for item in incoming:
        if item and item not in result:
            result.append(item)
    return result


def merge_profiles(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    result = empty_profile()
    if isinstance(current, dict):
        result.update({key: current.get(key) for key in result if key in current})
    result["version"] = 1
    for key in ("scenarios", "features"):
        result[key] = _merge_unique(result.get(key) or [], incoming.get(key) or [])
    result["brands"] = list(result.get("brands") or [])
    result["excludedBrands"] = list(result.get("excludedBrands") or [])
    for brand in incoming.get("brands") or []:
        result["brands"] = _merge_unique(result["brands"], [brand])
        result["excludedBrands"] = [
            excluded for excluded in result["excludedBrands"] if excluded != brand
        ]
    for brand in incoming.get("excludedBrands") or []:
        result["excludedBrands"] = _merge_unique(result["excludedBrands"], [brand])
        result["brands"] = [
            preferred for preferred in result["brands"] if preferred != brand
        ]
    if incoming.get("category"):
        result["category"] = incoming["category"]
    if incoming.get("budgetMin") is not None:
        result["budgetMin"] = incoming["budgetMin"]
    if incoming.get("budgetMax") is not None:
        result["budgetMax"] = incoming["budgetMax"]
    if incoming.get("acceptSubstitute") is not None:
        result["acceptSubstitute"] = incoming["acceptSubstitute"]
    return result


class ShoppingProfileService:

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        if not user_id:
            return empty_profile()
        try:
            value = await redis_service.get_shopping_profile(user_id)
            return merge_profiles(value, empty_profile()) if value else empty_profile()
        except Exception as exc:
            logger.warning("shopping_profile_read_failed", user_id=user_id, error=str(exc))
            return empty_profile()

    async def update_profile(self, user_id: str, text: str | None) -> dict[str, Any]:
        incoming = extract_profile(text)
        if not user_id or not _has_signal(incoming):
            return await self.get_profile(user_id)
        try:
            current = await redis_service.get_shopping_profile(user_id)
            merged = merge_profiles(current, incoming)
            await redis_service.save_shopping_profile(user_id, merged)
            return merged
        except Exception as exc:
            # A Redis outage must never make an ordinary chat message fail.
            logger.warning("shopping_profile_write_failed", user_id=user_id, error=str(exc))
            return merge_profiles(None, incoming)

    @staticmethod
    def has_hard_constraints(profile: dict[str, Any] | None) -> bool:
        if not profile:
            return False
        return bool(
            profile.get("budgetMin") is not None
            or profile.get("budgetMax") is not None
            or profile.get("brands")
            or profile.get("excludedBrands")
        )

    @staticmethod
    def should_clarify(
        text: str | None,
        keyword: str | None,
        profile: dict[str, Any] | None,
        consult_product: dict | None,
    ) -> bool:
        """Decide whether one clarifying question beats guessing at the ranking.

        Widened from the original "no signal at all and <=24 chars" rule: a
        category-only request now qualifies, because category alone cannot rank
        a 10k-SKU shelf. Anything carrying a budget, brand, scenario or feature
        is left alone, as is any long request where the user has clearly already
        spelled out what they want.
        """
        if consult_product:
            return False
        # Remembered budget/brand/scenario already narrows the shelf, so asking again is noise.
        if _has_narrowing_signal(profile or {}):
            return False
        value = (text or keyword or "").strip()
        if not value or len(value) > CLARIFY_MAX_TEXT_LENGTH:
            return False
        extracted = extract_profile(value)
        if _has_narrowing_signal(extracted):
            return False
        # Category without any narrowing signal: the highest-value place to ask,
        # except where the category alone already pins the shelf well enough.
        category = str(extracted.get("category") or "")
        if category:
            return category not in _CLARIFY_EXEMPT_CATEGORIES
        if any(word in value for word in _GENERIC_RECOMMEND_WORDS):
            return True
        return value in {"商品", "东西", "好物", "产品", "随便看看"}

    @staticmethod
    def _product_text(product: dict[str, Any]) -> str:
        fields = [
            product.get("brand"),
            product.get("product_name"),
            product.get("productName"),
            product.get("product_desc"),
            product.get("productDesc"),
            product.get("description"),
        ]
        for key in ("property_values", "propertyValues", "properties"):
            rows = product.get(key) or []
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        fields.extend(
                            [
                                row.get("property_name") or row.get("propertyName"),
                                row.get("property_value") or row.get("propertyValue"),
                            ]
                        )
        return " ".join(str(item) for item in fields if item)

    @staticmethod
    def _product_price_range(product: dict[str, Any]) -> tuple[float | None, float | None]:
        def number(value: Any) -> float | None:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        minimum = number(product.get("min_price") or product.get("minPrice"))
        maximum = number(product.get("max_price") or product.get("maxPrice"))
        sku_prices: list[float] = []
        for sku in product.get("skus") or []:
            if isinstance(sku, dict):
                price = number(sku.get("price"))
                if price is not None:
                    sku_prices.append(price)
        if minimum is None and sku_prices:
            minimum = min(sku_prices)
        if maximum is None and sku_prices:
            maximum = max(sku_prices)
        if maximum is None:
            maximum = minimum
        return minimum, maximum

    def matches_product(self, product: dict[str, Any], profile: dict[str, Any] | None) -> bool:
        status = product.get("status")
        if status is not None and str(status) != str(PRODUCT_STATUS_ON_SALE):
            return False
        if self.is_known_out_of_stock(product):
            return False
        if not profile:
            return True

        minimum, maximum = self._product_price_range(product)
        budget_min = profile.get("budgetMin")
        budget_max = profile.get("budgetMax")
        if (budget_min is not None or budget_max is not None) and minimum is None:
            return False
        if budget_max is not None and minimum is not None and minimum > float(budget_max):
            return False
        if budget_min is not None and maximum is not None and maximum < float(budget_min):
            return False

        product_text = self._product_text(product).lower()
        for brand in profile.get("excludedBrands") or []:
            aliases = next(
                (aliases for canonical, aliases in _BRAND_ALIASES if canonical == brand),
                (brand,),
            )
            if _brand_in_text(product_text, aliases):
                return False
        preferred = profile.get("brands") or []
        if profile.get("acceptSubstitute") is True:
            preferred = []
        if preferred:
            if not any(
                _brand_in_text(
                    product_text,
                    next(
                        (aliases for canonical, aliases in _BRAND_ALIASES if canonical == brand),
                        (brand,),
                    ),
                )
                for brand in preferred
            ):
                return False
        return True

    @staticmethod
    def is_known_out_of_stock(product: dict[str, Any]) -> bool:
        in_stock = (
            product.get("in_stock")
            if product.get("in_stock") is not None
            else product.get("inStock")
        )
        if in_stock is False or str(in_stock).lower() in {"false", "0"}:
            return True
        total_stock = (
            product.get("total_stock")
            if product.get("total_stock") is not None
            else product.get("totalStock")
        )
        if total_stock is None:
            return False
        try:
            return float(total_stock) <= 0
        except (TypeError, ValueError):
            return False

    def filter_products(
        self,
        products: list[dict[str, Any]],
        profile: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not self.has_hard_constraints(profile):
            return products
        return [product for product in products if self.matches_product(product, profile)]

    @staticmethod
    def summary(profile: dict[str, Any] | None) -> str:
        if not profile:
            return ""
        parts: list[str] = []
        budget_min = profile.get("budgetMin")
        budget_max = profile.get("budgetMax")
        if budget_min is not None and budget_max is not None:
            parts.append(f"预算{budget_min:g}-{budget_max:g}元")
        elif budget_max is not None:
            parts.append(f"预算不超过{budget_max:g}元")
        elif budget_min is not None:
            parts.append(f"预算至少{budget_min:g}元")
        if profile.get("brands"):
            parts.append("偏好" + "、".join(profile["brands"][:3]))
        if profile.get("excludedBrands"):
            parts.append("排除" + "、".join(profile["excludedBrands"][:3]))
        if profile.get("category"):
            parts.append(f"类别{profile['category']}")
        if profile.get("scenarios"):
            parts.append("场景" + "、".join(profile["scenarios"][:2]))
        if profile.get("acceptSubstitute") is True:
            parts.append("可接受同类替代")
        elif profile.get("acceptSubstitute") is False:
            parts.append("不接受同类替代")
        return "、".join(parts)

    def recommend_reason(
        self,
        product: dict[str, Any],
        profile: dict[str, Any] | None,
        source: str,
    ) -> str:
        if not profile:
            return "根据你的搜索条件推荐"
        reasons: list[str] = []
        if profile.get("budgetMin") is not None or profile.get("budgetMax") is not None:
            reasons.append("符合预算")
        product_text = self._product_text(product).lower()
        matched_brands = [
            brand
            for brand in profile.get("brands") or []
            if _brand_in_text(
                product_text,
                next(
                    (aliases for canonical, aliases in _BRAND_ALIASES if canonical == brand),
                    (brand,),
                ),
            )
        ]
        if matched_brands:
            reasons.append(f"匹配{matched_brands[0]}偏好")
        if profile.get("scenarios"):
            reasons.append(f"适合{profile['scenarios'][0]}")
        if profile.get("features"):
            reasons.append(f"关注{profile['features'][0]}")
        if not reasons:
            if source == "browse":
                return "结合当前浏览与搜索结果推荐"
            if source == "similar_i2i":
                return "与你正在看的商品相似"
            return "根据搜索结果推荐"
        return "、".join(reasons)


shopping_profile_service = ShoppingProfileService()
