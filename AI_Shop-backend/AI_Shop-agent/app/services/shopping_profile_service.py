from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.constants import CLARIFY_MAX_TEXT_LENGTH, PRODUCT_STATUS_ON_SALE
from app.db.pool import acquire, transaction
from app.domain.category_terms import has_bare_bag_category
from app.observability.llm_metrics import invoke_llm_with_metrics
from app.services.redis_service import redis_service

logger = structlog.get_logger()

_AMOUNT = r"(\d+(?:\.\d+)?)\s*(万|千|k|K)?"
_RANGE_RE = re.compile(
    rf"(?:预算|价格|价位)\s*{_AMOUNT}\s*(?:-|~|～|至|到)\s*{_AMOUNT}\s*(?:元|块)?"
)
_BARE_CURRENCY_RANGE_RE = re.compile(
    rf"{_AMOUNT}\s*(?:-|~|～|至|到)\s*{_AMOUNT}\s*(?:元|块)"
)
_UPPER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*(?:不超过|不高于|最多|低于|小于)\s*{_AMOUNT}\s*(?:元|块)?"
)
_SUFFIX_UPPER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*{_AMOUNT}\s*(?:元|块)?\s*"
    r"(?:以内|以下|封顶|左右以内)"
)
_LOWER_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*(?:至少|不低于|不少于|最低)\s*{_AMOUNT}\s*(?:元|块)?"
)
_PLAIN_BUDGET_RE = re.compile(rf"(?:预算|价格|价位)\s*{_AMOUNT}\s*(?:元|块)?")
_APPROX_BUDGET_RE = re.compile(
    rf"(?:预算|价格|价位)?\s*(?:大约|大概|约)?\s*{_AMOUNT}\s*"
    r"(?:元|块)?\s*(?:左右|上下)"
)
_PREFIX_APPROX_BUDGET_RE = re.compile(
    rf"(?:预算|价格|价位)\s*(?:大约|大概|约)\s*{_AMOUNT}\s*(?:元|块)?"
)
_UPDATED_BUDGET_RE = re.compile(
    rf"(?:预算|价格|价位)\s*"
    rf"(?:提高|提升|增加|上调|调整|改成|改为|改|提到|放宽)\s*"
    rf"(?:到|至|为|成)?\s*{_AMOUNT}\s*(?:元|块)?"
)
_APPROX_BUDGET_TOLERANCE = 0.2

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
    ("索尼", ("索尼", "sony")),
    ("耐克", ("耐克", "nike")),
    ("阿迪达斯", ("阿迪达斯", "adidas")),
)

_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机", ("手机", "智能机")),
    ("笔记本电脑", ("笔记本", "笔记本电脑", " laptop ", "laptop")),
    ("电脑", ("电脑", "台式机", "主机")),
    ("平板", ("平板", "ipad")),
    ("零食", ("零食", "食品", "吃的")),
    ("车载充电器", ("车载充电器", "车充", "车载快充")),
    ("音箱", ("音箱", "音响", "蓝牙音箱", "桌面音箱")),
    ("家电", ("家电", "电器")),
    ("耳机", ("耳机", "降噪耳机")),
    ("箱包", ("箱包", "背包", "书包", "双肩包", "手提包", "斜挎包", "包包", "包")),
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
    ("儿童", ("儿童", "孩子", "小孩", "新生儿", "婴儿", "宝宝")),
    ("通勤", ("通勤",)),
    ("旅行", ("旅行", "出差")),
    ("编程开发", ("编程", "写代码", "程序员", "开发")),
    ("视频创作", ("视频剪辑", "剪视频", "视频创作", "做视频")),
    ("设计创作", ("设计", "渲染")),
    ("商务办公", ("商务", "企业办公", "公司办公")),
    ("户外运动", ("户外", "登山", "徒步", "露营")),
    ("上学通勤", ("上学", "书包")),
    ("上班通勤", ("上班通勤", "职场通勤")),
)
_EXPLICIT_USE_CASE_RE = re.compile(
    r"(?:主要)?(?:用于|用来|拿来)\s*([^\uff0c。；！？]{1,24}?)(?=\uff0c|。|；|！|？|适合|希望|$)"
)

_FEATURE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("便携", ("便携", "轻薄", "小巧")),
    ("续航", ("续航", "电池耐用", "待机久")),
    ("性价比", ("性价比", "实惠", "划算")),
    ("大屏", ("大屏", "屏幕大")),
    ("高性能", ("高性能", "性能强", "配置高")),
    ("降噪", ("降噪", "anc")),
    ("折叠屏", ("折叠屏", "折叠手机")),
    ("头戴式", ("头戴式", "头戴耳机")),
    ("台式主机", ("台式机", "台式电脑", "电脑主机", "台式主机")),
    ("大功率快充", ("100w", "百瓦", "多口快充", "超级快充")),
    ("防水", ("防水",)),
    ("大容量", ("大容量", "能装", "收纳多")),
    ("轻量", ("轻量", "重量轻")),
)
_EXPLICIT_SPEC_PATTERNS = (
    re.compile(r"(?i)(\d+(?:\.\d+)?\s*(?:GB|TB|G)\s*(?:内存|运存|存储|硬盘))"),
    re.compile(r"(?i)((?:内存|运存|存储|硬盘)\s*\d+(?:\.\d+)?\s*(?:GB|TB|G))"),
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

_PROFILE_FIELDS = (
    "category",
    "budgetMin",
    "budgetMax",
    "brands",
    "excludedBrands",
    "scenarios",
    "features",
    "acceptSubstitute",
)
_PERSISTENT_EXPLICIT_FIELDS = frozenset(
    {"brands", "excludedBrands", "scenarios", "features", "acceptSubstitute"}
)
_SHORT_LIVED_FIELDS = frozenset(
    {"category", "budgetMin", "budgetMax", "acceptSubstitute"}
)
_LIST_FIELDS = frozenset(
    {"brands", "excludedBrands", "scenarios", "features"}
)
_CHAT_TTL_DAYS = {field: (30 if field in _SHORT_LIVED_FIELDS else 90) for field in _PROFILE_FIELDS}
_MANUAL_TTL_DAYS = 180


class ProfileRevisionConflict(Exception):
    def __init__(self, current: dict[str, Any]):
        super().__init__("购物偏好已被其他请求更新")
        self.current = current


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def empty_profile() -> dict[str, Any]:
    return {
        "version": 2,
        "revision": 0,
        "category": None,
        "budgetMin": None,
        "budgetMax": None,
        "brands": [],
        "excludedBrands": [],
        "scenarios": [],
        "features": [],
        "acceptSubstitute": None,
        "personalizationEnabled": True,
        "implicitSignals": [],
        "fieldMeta": {},
    }


def _field_has_value(profile: dict[str, Any], field: str) -> bool:
    value = profile.get(field)
    return bool(value) if field in _LIST_FIELDS else value is not None


def prune_expired_profile(
    profile: dict[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    normalized = merge_profiles(profile, empty_profile())
    metadata = dict(normalized.get("fieldMeta") or {})
    current = now or _utc_now()
    for field in _PROFILE_FIELDS:
        field_meta = metadata.get(field)
        if not isinstance(field_meta, dict):
            continue
        expires_at = _parse_time(field_meta.get("expiresAt"))
        if expires_at is None or expires_at > current:
            continue
        normalized[field] = [] if field in _LIST_FIELDS else None
        metadata.pop(field, None)
    normalized["fieldMeta"] = metadata
    normalized["personalizationEnabled"] = bool(
        normalized.get("personalizationEnabled", True)
    )
    signals: list[dict[str, Any]] = []
    for raw in normalized.get("implicitSignals") or []:
        if not isinstance(raw, dict):
            continue
        expires_at = _parse_time(raw.get("expiresAt"))
        if expires_at is not None and expires_at <= current:
            continue
        signal_id = str(raw.get("signalId") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        value = str(raw.get("value") or "").strip()
        if not signal_id or not kind or not value:
            continue
        try:
            strength = min(1.0, max(0.0, float(raw.get("strength") or raw.get("weight") or 0)))
        except (TypeError, ValueError):
            strength = 0.0
        observed_at = _parse_time(raw.get("observedAt")) or current
        age_days = max(0.0, (current - observed_at).total_seconds() / 86400)
        effective_weight = round(strength * max(0.0, 1.0 - age_days / 180.0), 4)
        if effective_weight <= 0:
            continue
        signal = {
            "signalId": signal_id[:64],
            "kind": kind[:32],
            "value": value[:80],
            "strength": round(strength, 4),
            "effectiveWeight": effective_weight,
            "count": min(10_000, max(1, int(raw.get("count") or 1))),
            "source": str(raw.get("source") or "OUTCOME")[:32],
            "observedAt": _iso(observed_at),
        }
        if expires_at is not None:
            signal["expiresAt"] = _iso(expires_at)
        signals.append(signal)
    normalized["implicitSignals"] = signals[:100]
    return normalized


def _stamp_fields(
    profile: dict[str, Any],
    incoming: dict[str, Any],
    *,
    source: str,
    source_message_id: int | None,
    ttl_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    result = deepcopy(profile)
    metadata = dict(result.get("fieldMeta") or {})
    current = now or _utc_now()
    for field in _PROFILE_FIELDS:
        if not _field_has_value(incoming, field):
            continue
        days = (
            None
            if field in _PERSISTENT_EXPLICIT_FIELDS
            and source in {"EXPLICIT_CHAT", "MANUAL"}
            else (_CHAT_TTL_DAYS[field] if ttl_days is None else ttl_days)
        )
        field_meta: dict[str, Any] = {
            "source": source,
            "updatedAt": _iso(current),
        }
        if days is not None:
            field_meta["expiresAt"] = _iso(current + timedelta(days=days))
        if source_message_id is not None:
            field_meta["sourceMessageId"] = source_message_id
        metadata[field] = field_meta
    result["fieldMeta"] = metadata
    return result


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
    match = _RANGE_RE.search(text) or _BARE_CURRENCY_RANGE_RE.search(text)
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

    match = _UPDATED_BUDGET_RE.search(text)
    if match:
        return None, _budget_from_match(match)

    match = _APPROX_BUDGET_RE.search(text) or _PREFIX_APPROX_BUDGET_RE.search(text)
    if match:
        target = _budget_from_match(match)
        return (
            round(target * (1 - _APPROX_BUDGET_TOLERANCE), 2),
            round(target * (1 + _APPROX_BUDGET_TOLERANCE), 2),
        )

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


def _category_alias_in_text(text: str, alias: str) -> bool:
    if alias != "包":
        return alias.strip().lower() in text.lower()
    return has_bare_bag_category(text)


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
        if any(_category_alias_in_text(value, alias) for alias in aliases):
            profile["category"] = canonical
            break

    explicit_use_case = _EXPLICIT_USE_CASE_RE.search(value)
    profile["scenarios"] = (
        [explicit_use_case.group(1).strip()]
        if explicit_use_case and explicit_use_case.group(1).strip()
        else [
            canonical
            for canonical, aliases in _SCENARIO_HINTS
            if any(alias.casefold() in value.casefold() for alias in aliases)
        ]
    )
    profile["features"] = [
        canonical
        for canonical, aliases in _FEATURE_HINTS
        if any(alias.casefold() in value.casefold() for alias in aliases)
    ]
    for pattern in _EXPLICIT_SPEC_PATTERNS:
        for match in pattern.finditer(value):
            spec = re.sub(r"\s+", " ", match.group(1)).strip()
            if spec and spec not in profile["features"]:
                profile["features"].append(spec)
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
    result["version"] = 2
    try:
        result["revision"] = max(0, int(result.get("revision") or 0))
    except (TypeError, ValueError):
        result["revision"] = 0
    result["fieldMeta"] = dict(result.get("fieldMeta") or {})
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
    incoming_meta = incoming.get("fieldMeta")
    if isinstance(incoming_meta, dict):
        result["fieldMeta"].update(incoming_meta)
    return result


class ShoppingProfileService:

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        """Read the profile from Redis, falling back to the durable MySQL row.

        Redis is only a cache here: its TTL is finite and it can be restarted,
        so a miss must not silently reset a user's remembered budget/brand
        constraints. MySQL is the source of truth and a hit backfills Redis.
        """
        if not user_id:
            return empty_profile()
        try:
            cached = await redis_service.get_shopping_profile(user_id)
            if cached:
                return prune_expired_profile(cached)
        except Exception as exc:
            logger.warning("shopping_profile_redis_read_failed", user_id=user_id, error=str(exc))

        stored = await self._load_from_db(user_id)
        if not stored:
            return empty_profile()
        merged = prune_expired_profile(stored)
        await self._save_redis(user_id, merged)
        return merged

    async def get_effective_profile(self, user_id: str) -> dict[str, Any]:
        durable = await self.get_profile(user_id)
        try:
            from app.services.shopping_mission_service import shopping_mission_service

            mission = await shopping_mission_service.load(user_id)
            if not isinstance(mission, dict):
                return durable
            hard = mission.get("hardConstraints") or {}
            soft = mission.get("softPreferences") or {}
            exclusions = mission.get("exclusions") or {}
            effective = deepcopy(durable)
            effective.update(
                {
                    "category": mission.get("category"),
                    "budgetMin": hard.get("budgetMin"),
                    "budgetMax": hard.get("budgetMax"),
                    "brands": list(soft.get("brands") or []),
                    "excludedBrands": list(exclusions.get("brands") or []),
                    "scenarios": list(mission.get("useCases") or []),
                    "features": list(soft.get("features") or []),
                    "acceptSubstitute": soft.get("acceptSubstitute"),
                    "personalizationEnabled": bool(
                        durable.get("personalizationEnabled", True)
                    ),
                    "implicitSignals": (
                        list(durable.get("implicitSignals") or [])
                        if durable.get("personalizationEnabled", True)
                        else []
                    ),
                }
            )
            return effective
        except Exception as exc:
            logger.warning(
                "shopping_mission_read_failed",
                user_id=user_id,
                error=type(exc).__name__,
            )
            return durable

    async def update_profile(
        self,
        user_id: str,
        text: str | None,
        *,
        source_message_id: int | None = None,
    ) -> dict[str, Any]:
        incoming = extract_profile(text)
        if not user_id or not _has_signal(incoming):
            return await self.get_profile(user_id)
        current = await self.get_profile(user_id)
        for _attempt in range(3):
            base = deepcopy(current)
            current_category = str(base.get("category") or "")
            incoming_category = str(incoming.get("category") or "")
            if (
                current_category
                and incoming_category
                and current_category != incoming_category
            ):
                base["budgetMin"] = None
                base["budgetMax"] = None
                base["scenarios"] = []
                base["features"] = []
                for field in ("budgetMin", "budgetMax", "scenarios", "features"):
                    base.setdefault("fieldMeta", {}).pop(field, None)
            incoming_min = incoming.get("budgetMin")
            incoming_max = incoming.get("budgetMax")
            if (
                incoming_max is not None
                and base.get("budgetMin") is not None
                and float(base["budgetMin"]) > float(incoming_max)
            ):
                base["budgetMin"] = None
                base.setdefault("fieldMeta", {}).pop("budgetMin", None)
            if (
                incoming_min is not None
                and base.get("budgetMax") is not None
                and float(base["budgetMax"]) < float(incoming_min)
            ):
                base["budgetMax"] = None
                base.setdefault("fieldMeta", {}).pop("budgetMax", None)
            merged = merge_profiles(base, incoming)
            merged = _stamp_fields(
                merged,
                incoming,
                source="EXPLICIT_CHAT",
                source_message_id=source_message_id,
            )
            merged["revision"] = int(base.get("revision") or 0) + 1
            persisted = await self._save_db(user_id, merged)
            if persisted is not False:
                # ``None`` means MySQL was unavailable. The hot chat path remains
                # fail-open by caching the explicit signal; a later read/write can
                # reconcile it without failing this user turn.
                await self._save_redis(user_id, merged)
                return merged
            latest = await self._load_from_db(user_id)
            if latest is None:
                await self._save_redis(user_id, merged)
                return merged
            current = prune_expired_profile(latest)

        # Sustained contention is not allowed to delay the chat response. Return
        # the authoritative latest row; the user's current turn still lives in
        # ShoppingNeedState and therefore remains effective for this session.
        await self._save_redis(user_id, current)
        return current

    async def async_enrich_profile(self, user_id: str, text: str | None) -> None:
        """Retained for compatibility; model inference never mutates long-term memory."""
        _ = (user_id, text)

    async def _llm_extract_profile(self, text: str) -> dict[str, Any] | None:
        """Call the LLM to extract a structured shopping profile from natural language.

        Uses the memory LLM (non-streaming, may be a cheaper/faster model) with
        an 8-second hard timeout so a slow call never blocks the background task
        queue for long.
        """
        import asyncio as _asyncio

        from langchain_core.messages import HumanMessage

        from app.services.llm_factory import create_memory_llm

        prompt = (
            "你是电商购物意图分析助手。从用户文本提取购物偏好，仅返回JSON，不含任何其他文字。\n\n"
            f"用户文本：{text[:500]}\n\n"
            "提取字段（无明确信号保持 null 或空列表）：\n"
            "  category      — 商品类别，如手机、笔记本电脑、耳机\n"
            "  budgetMin     — 最低预算（纯数字，单位元，无则 null）\n"
            "  budgetMax     — 最高预算（纯数字，单位元，无则 null）\n"
            "  brands        — 偏好品牌列表，如 [\"苹果\",\"华为\"]\n"
            "  excludedBrands— 排除品牌列表\n"
            "  scenarios     — 使用场景列表，如 [\"办公\",\"游戏\",\"送礼\"]\n"
            "  features      — 功能偏好列表，如 [\"便携\",\"续航\",\"性价比\"]\n"
            "  acceptSubstitute — 是否接受替代品牌：true/false/null\n\n"
            "示例输出：\n"
            '{"category":"手机","budgetMin":null,"budgetMax":3000,"brands":["苹果"],'
            '"excludedBrands":[],"scenarios":["学生"],"features":["性价比"],'
            '"acceptSubstitute":true}'
        )
        try:
            llm = create_memory_llm()
            response = await _asyncio.wait_for(
                invoke_llm_with_metrics(llm, [HumanMessage(content=prompt)]),
                timeout=8.0,
            )
            raw = (response.content or "").strip()
            # Strip optional markdown code fences.
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:].lstrip()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return None
            result = empty_profile()
            cat = parsed.get("category")
            result["category"] = str(cat).strip() if cat else None
            for key in ("budgetMin", "budgetMax"):
                val = parsed.get(key)
                try:
                    result[key] = float(val) if val is not None else None
                except (TypeError, ValueError):
                    result[key] = None
            for key in ("brands", "excludedBrands", "scenarios", "features"):
                raw_list = parsed.get(key) or []
                result[key] = [str(x).strip() for x in raw_list if x][:10]
            sub = parsed.get("acceptSubstitute")
            if isinstance(sub, bool):
                result["acceptSubstitute"] = sub
            return result
        except Exception as exc:
            logger.warning("profile_llm_extract_failed", error=str(exc))
            return None

    async def _save_redis(self, user_id: str, profile: dict[str, Any]) -> None:
        try:
            await redis_service.save_shopping_profile(user_id, profile)
        except Exception as exc:
            logger.warning("shopping_profile_redis_write_failed", user_id=user_id, error=str(exc))

    async def _save_db(
        self, user_id: str, profile: dict[str, Any]
    ) -> bool | None:
        try:
            async with acquire() as cur:
                next_revision = int(profile.get("revision") or 1)
                expected_revision = max(0, next_revision - 1)
                await cur.execute(
                    """INSERT INTO agent_shopping_profile
                           (user_id, profile_json, revision, updated_at)
                       VALUES (%s, %s, %s, NOW()) AS incoming
                       ON DUPLICATE KEY UPDATE
                         profile_json=IF(
                           agent_shopping_profile.revision=%s,
                           incoming.profile_json,
                           agent_shopping_profile.profile_json
                         ),
                         updated_at=IF(
                           agent_shopping_profile.revision=%s,
                           incoming.updated_at,
                           agent_shopping_profile.updated_at
                         ),
                         revision=IF(
                           agent_shopping_profile.revision=%s,
                           incoming.revision,
                           agent_shopping_profile.revision
                         )""",
                    (
                        user_id,
                        json.dumps(profile, ensure_ascii=False),
                        next_revision,
                        expected_revision,
                        expected_revision,
                        expected_revision,
                    ),
                )
                return bool(cur.rowcount)
        except Exception as exc:
            logger.warning("shopping_profile_db_write_failed", user_id=user_id, error=str(exc))
            return None

    async def _load_from_db(self, user_id: str) -> dict[str, Any] | None:
        try:
            async with acquire() as cur:
                await cur.execute(
                    "SELECT profile_json, revision "
                    "FROM agent_shopping_profile WHERE user_id=%s",
                    (user_id,),
                )
                row = await cur.fetchone()
        except Exception as exc:
            logger.warning("shopping_profile_db_read_failed", user_id=user_id, error=str(exc))
            return None
        if not row:
            return None
        stored = row.get("profile_json")
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except json.JSONDecodeError:
                logger.warning("shopping_profile_db_corrupt", user_id=user_id)
                return None
        if not isinstance(stored, dict):
            return None
        stored["revision"] = int(row.get("revision") or stored.get("revision") or 0)
        return stored

    async def manual_update(
        self,
        user_id: str,
        updates: dict[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        patch = self._normalize_manual_patch(updates)
        if not patch:
            current = await self.get_profile(user_id)
            if int(current.get("revision") or 0) != expected_revision:
                raise ProfileRevisionConflict(current)
            return current
        updated = await self._manual_write(
            user_id,
            expected_revision=expected_revision,
            patch=patch,
            clear=False,
        )
        await self._save_redis(user_id, updated)
        await self._rebase_session_mission(user_id, updated)
        return updated

    async def clear_profile(
        self, user_id: str, expected_revision: int
    ) -> dict[str, Any]:
        updated = await self._manual_write(
            user_id,
            expected_revision=expected_revision,
            patch={},
            clear=True,
        )
        await self._save_redis(user_id, updated)
        await self._rebase_session_mission(user_id, updated)
        return updated

    async def set_personalization(
        self, user_id: str, enabled: bool, expected_revision: int
    ) -> dict[str, Any]:
        updated = await self._manual_write(
            user_id,
            expected_revision=expected_revision,
            patch={"personalizationEnabled": bool(enabled)},
            clear=False,
        )
        await self._save_redis(user_id, updated)
        await self._rebase_session_mission(user_id, updated)
        return updated

    async def record_implicit_signal(
        self,
        user_id: str,
        *,
        kind: str,
        value: str,
        source: str,
        strength: float,
    ) -> dict[str, Any] | None:
        """Upsert one weak behavioural signal with a 180-day linear decay."""
        normalized_kind = str(kind or "").strip()[:32]
        normalized_value = str(value or "").strip()[:80]
        if not user_id or not normalized_kind or not normalized_value:
            return None
        try:
            normalized_strength = min(1.0, max(0.0, float(strength)))
        except (TypeError, ValueError):
            return None
        if normalized_strength <= 0:
            return None

        async with transaction() as cur:
            await cur.execute(
                "SELECT profile_json, revision FROM agent_shopping_profile "
                "WHERE user_id=%s FOR UPDATE",
                (user_id,),
            )
            current = self._profile_from_row(await cur.fetchone())
            current = prune_expired_profile(current)
            if not current.get("personalizationEnabled", True):
                return current
            now = _utc_now()
            signals = list(current.get("implicitSignals") or [])
            existing = next(
                (
                    item
                    for item in signals
                    if item.get("kind") == normalized_kind
                    and item.get("value") == normalized_value
                ),
                None,
            )
            if existing is None:
                existing = {
                    "signalId": f"sig_{uuid.uuid4().hex}",
                    "kind": normalized_kind,
                    "value": normalized_value,
                    "strength": normalized_strength,
                    "count": 1,
                }
                signals.append(existing)
            else:
                existing["strength"] = min(
                    1.0,
                    float(existing.get("strength") or 0)
                    + normalized_strength * 0.35,
                )
                existing["count"] = min(10_000, int(existing.get("count") or 0) + 1)
            existing["source"] = str(source or "OUTCOME")[:32]
            existing["observedAt"] = _iso(now)
            existing["expiresAt"] = _iso(now + timedelta(days=180))
            current["implicitSignals"] = signals[:100]
            current["revision"] = int(current.get("revision") or 0) + 1
            await cur.execute(
                "INSERT INTO agent_shopping_profile "
                "(user_id, profile_json, revision, updated_at) VALUES (%s,%s,%s,NOW()) AS incoming "
                "ON DUPLICATE KEY UPDATE profile_json=incoming.profile_json, "
                "revision=incoming.revision, updated_at=NOW()",
                (
                    user_id,
                    json.dumps(current, ensure_ascii=False),
                    current["revision"],
                ),
            )
        updated = prune_expired_profile(current)
        await self._save_redis(user_id, updated)
        return updated

    async def delete_implicit_signal(
        self, user_id: str, signal_id: str, expected_revision: int
    ) -> dict[str, Any]:
        return await self._mutate_implicit_signals(
            user_id,
            expected_revision,
            lambda signals: [
                signal
                for signal in signals
                if str(signal.get("signalId") or "") != str(signal_id or "")
            ],
        )

    async def clear_implicit_signals(
        self, user_id: str, expected_revision: int
    ) -> dict[str, Any]:
        return await self._mutate_implicit_signals(
            user_id, expected_revision, lambda _signals: []
        )

    async def _mutate_implicit_signals(
        self,
        user_id: str,
        expected_revision: int,
        transform: Any,
    ) -> dict[str, Any]:
        async with transaction() as cur:
            await cur.execute(
                "SELECT profile_json, revision FROM agent_shopping_profile "
                "WHERE user_id=%s FOR UPDATE",
                (user_id,),
            )
            current = self._profile_from_row(await cur.fetchone())
            current_revision = int(current.get("revision") or 0)
            if current_revision != expected_revision:
                raise ProfileRevisionConflict(prune_expired_profile(current))
            current["implicitSignals"] = transform(
                list(prune_expired_profile(current).get("implicitSignals") or [])
            )
            current["revision"] = current_revision + 1
            await cur.execute(
                "INSERT INTO agent_shopping_profile "
                "(user_id, profile_json, revision, updated_at) VALUES (%s,%s,%s,NOW()) AS incoming "
                "ON DUPLICATE KEY UPDATE profile_json=incoming.profile_json, "
                "revision=incoming.revision, updated_at=NOW()",
                (
                    user_id,
                    json.dumps(current, ensure_ascii=False),
                    current["revision"],
                ),
            )
        updated = prune_expired_profile(current)
        await self._save_redis(user_id, updated)
        return updated

    @staticmethod
    async def _rebase_session_mission(
        user_id: str, profile: dict[str, Any]
    ) -> None:
        try:
            from app.services.shopping_mission_service import shopping_mission_service

            await shopping_mission_service.rebase_profile(user_id, profile)
        except Exception as exc:
            logger.warning(
                "shopping_mission_rebase_failed",
                user_id=user_id,
                error=type(exc).__name__,
            )

    async def _manual_write(
        self,
        user_id: str,
        *,
        expected_revision: int,
        patch: dict[str, Any],
        clear: bool,
    ) -> dict[str, Any]:
        if expected_revision < 0:
            raise ValueError("expectedRevision 不能小于 0")
        async with transaction() as cur:
            await cur.execute(
                "SELECT profile_json, revision FROM agent_shopping_profile "
                "WHERE user_id=%s FOR UPDATE",
                (user_id,),
            )
            row = await cur.fetchone()
            current = self._profile_from_row(row)
            current_revision = int(current.get("revision") or 0)
            if current_revision != expected_revision:
                raise ProfileRevisionConflict(prune_expired_profile(current))

            next_revision = current_revision + 1
            if clear:
                updated = empty_profile()
                updated["personalizationEnabled"] = bool(
                    current.get("personalizationEnabled", True)
                )
                updated["revision"] = next_revision
            else:
                updated = prune_expired_profile(current)
                metadata = dict(updated.get("fieldMeta") or {})
                stamp_input = empty_profile()
                for field, value in patch.items():
                    updated[field] = deepcopy(value)
                    if _field_has_value(updated, field):
                        stamp_input[field] = deepcopy(value)
                    else:
                        metadata.pop(field, None)
                updated["fieldMeta"] = metadata
                self._validate_profile_values(updated)
                updated = _stamp_fields(
                    updated,
                    stamp_input,
                    source="MANUAL",
                    source_message_id=None,
                    ttl_days=_MANUAL_TTL_DAYS,
                )
                updated["revision"] = next_revision

            payload = json.dumps(updated, ensure_ascii=False)
            if row:
                await cur.execute(
                    "UPDATE agent_shopping_profile "
                    "SET profile_json=%s, revision=%s, updated_at=NOW() "
                    "WHERE user_id=%s",
                    (payload, next_revision, user_id),
                )
            else:
                await cur.execute(
                    "INSERT INTO agent_shopping_profile "
                    "(user_id, profile_json, revision, updated_at) "
                    "VALUES (%s, %s, %s, NOW())",
                    (user_id, payload, next_revision),
                )
        return updated

    @staticmethod
    def _profile_from_row(row: dict | None) -> dict[str, Any]:
        if not row:
            return empty_profile()
        stored = row.get("profile_json")
        if isinstance(stored, str):
            try:
                stored = json.loads(stored)
            except json.JSONDecodeError:
                stored = {}
        profile = merge_profiles(stored if isinstance(stored, dict) else {}, empty_profile())
        profile["revision"] = int(row.get("revision") or profile.get("revision") or 0)
        return profile

    @staticmethod
    def _normalize_manual_patch(updates: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(updates, dict):
            raise ValueError("profile 必须是对象")
        result: dict[str, Any] = {}
        if "personalizationEnabled" in updates:
            enabled = updates["personalizationEnabled"]
            if not isinstance(enabled, bool):
                raise ValueError("personalizationEnabled 必须是布尔值")
            result["personalizationEnabled"] = enabled
        for field in _PROFILE_FIELDS:
            if field not in updates:
                continue
            value = updates[field]
            if field in _LIST_FIELDS:
                if value is None:
                    result[field] = []
                    continue
                if not isinstance(value, list):
                    raise ValueError(f"{field} 必须是数组")
                result[field] = [
                    str(item).strip()
                    for item in value
                    if str(item or "").strip()
                ][:10]
            elif field in {"budgetMin", "budgetMax"}:
                if value is None:
                    result[field] = None
                    continue
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{field} 必须是非负数字") from exc
                if number < 0:
                    raise ValueError(f"{field} 必须是非负数字")
                result[field] = number
            elif field == "acceptSubstitute":
                if value is not None and not isinstance(value, bool):
                    raise ValueError("acceptSubstitute 必须是布尔值或 null")
                result[field] = value
            else:
                normalized = str(value).strip()[:80] if value is not None else ""
                result[field] = normalized or None
        budget_min = result.get("budgetMin")
        budget_max = result.get("budgetMax")
        if budget_min is not None and budget_max is not None and budget_min > budget_max:
            raise ValueError("budgetMin 不能大于 budgetMax")
        preferred = set(result.get("brands") or [])
        excluded = set(result.get("excludedBrands") or [])
        if preferred.intersection(excluded):
            raise ValueError("偏好品牌与排除品牌不能重复")
        return result

    @staticmethod
    def _validate_profile_values(profile: dict[str, Any]) -> None:
        budget_min = profile.get("budgetMin")
        budget_max = profile.get("budgetMax")
        if budget_min is not None and budget_max is not None:
            if float(budget_min) > float(budget_max):
                raise ValueError("budgetMin 不能大于 budgetMax")
        preferred = set(profile.get("brands") or [])
        excluded = set(profile.get("excludedBrands") or [])
        if preferred.intersection(excluded):
            raise ValueError("偏好品牌与排除品牌不能重复")

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

        if not self.matches_budget(product, profile):
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

    def matches_budget(
        self,
        product: dict[str, Any],
        profile: dict[str, Any] | None,
    ) -> bool:
        if not profile:
            return True
        budget_min = profile.get("budgetMin")
        budget_max = profile.get("budgetMax")
        if budget_min is None and budget_max is None:
            return True
        minimum, maximum = self._product_price_range(product)
        if minimum is None:
            return False
        if budget_max is not None and minimum > float(budget_max):
            return False
        if budget_min is not None and maximum is not None and maximum < float(budget_min):
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
        if profile.get("features"):
            parts.append("关注" + "、".join(profile["features"][:3]))
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
        if (
            profile.get("budgetMin") is not None
            or profile.get("budgetMax") is not None
        ) and self.matches_budget(product, profile):
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

    def resolve_known_brand(
        self, product: dict[str, Any], profile: dict[str, Any] | None
    ) -> str | None:
        existing = str(product.get("brand") or "").strip()
        if existing:
            for canonical, aliases in _BRAND_ALIASES:
                if canonical.casefold() == existing.casefold() or _brand_in_text(
                    existing, aliases
                ):
                    return canonical
            return existing
        product_text = self._product_text(product).lower()
        preferred = list((profile or {}).get("brands") or [])
        excluded = list((profile or {}).get("excludedBrands") or [])
        for brand in [*preferred, *excluded]:
            aliases = next(
                (
                    aliases
                    for canonical, aliases in _BRAND_ALIASES
                    if canonical == brand
                ),
                (brand,),
            )
            if _brand_in_text(product_text, aliases):
                return str(brand)
        return None


shopping_profile_service = ShoppingProfileService()
