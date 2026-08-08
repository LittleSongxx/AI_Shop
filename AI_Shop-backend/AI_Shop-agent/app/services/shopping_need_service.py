from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.session_memory_service import session_memory_service
from app.services.redis_service import redis_service
from app.services.shopping_profile_service import _has_signal, extract_profile

SHOPPING_NEED_ACTIVE_HOURS = 24
RECENT_CANDIDATE_HOURS = 24
MAX_RECENT_CANDIDATES = 12
_LOW_CONSIDERATION_CATEGORIES = frozenset({"零食"})


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


def empty_shopping_need_state() -> dict[str, Any]:
    return {
        "version": 1,
        "category": None,
        "scenarios": [],
        "budget": {"min": None, "max": None},
        "hardConstraints": {
            "requiredBrands": [],
            "requiredTerms": [],
        },
        "softPreferences": {
            "brands": [],
            "features": [],
            "acceptSubstitute": None,
        },
        "exclusions": {"brands": [], "terms": []},
        "missingSlots": ["category"],
        "candidateProducts": [],
        "sourceMessageIds": {},
        "updatedAt": None,
        "activeUntil": None,
    }


def shopping_need_is_active(
    need: dict[str, Any] | None, *, now: datetime | None = None
) -> bool:
    if not isinstance(need, dict):
        return False
    active_until = _parse_time(need.get("activeUntil"))
    return bool(active_until and active_until > (now or _utc_now()))


def _seed_from_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    need = empty_shopping_need_state()
    if not isinstance(profile, dict):
        return need
    need["category"] = profile.get("category")
    need["scenarios"] = list(profile.get("scenarios") or [])
    need["budget"] = {
        "min": profile.get("budgetMin"),
        "max": profile.get("budgetMax"),
    }
    brands = list(profile.get("brands") or [])
    accept_substitute = profile.get("acceptSubstitute")
    need["softPreferences"] = {
        "brands": brands,
        "features": list(profile.get("features") or []),
        "acceptSubstitute": accept_substitute,
    }
    need["hardConstraints"] = {
        "requiredBrands": brands if accept_substitute is False else [],
        "requiredTerms": [],
    }
    need["exclusions"] = {
        "brands": list(profile.get("excludedBrands") or []),
        "terms": [],
    }
    need["missingSlots"] = _missing_slots(need)
    return need


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def _missing_slots(need: dict[str, Any]) -> list[str]:
    if not need.get("category"):
        return ["category"]
    budget = need.get("budget") or {}
    if (
        need.get("category") not in _LOW_CONSIDERATION_CATEGORIES
        and budget.get("min") is None
        and budget.get("max") is None
    ):
        return ["budget"]
    soft = need.get("softPreferences") or {}
    if not need.get("scenarios") and not soft.get("features"):
        return ["scenario"]
    return []


def apply_explicit_turn(
    current: dict[str, Any] | None,
    *,
    profile: dict[str, Any] | None,
    user_text: str,
    message_id: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Apply only deterministic, explicitly stated shopping signals."""
    timestamp = now or _utc_now()
    incoming = extract_profile(user_text)
    if not _has_signal(incoming) and not shopping_need_is_active(current, now=timestamp):
        return None

    if shopping_need_is_active(current, now=timestamp):
        need = deepcopy(current)
    else:
        need = _seed_from_profile(profile)

    previous_category = str(need.get("category") or "")
    incoming_category = str(incoming.get("category") or "")
    if incoming_category and previous_category and incoming_category != previous_category:
        # A new shelf starts a new concrete shopping task. Stable brand exclusions
        # may remain useful, but old budget, scenario, feature and candidates must
        # not silently constrain the new category.
        need["budget"] = {"min": None, "max": None}
        need["scenarios"] = []
        soft = need.setdefault("softPreferences", {})
        soft["features"] = []
        need["candidateProducts"] = []
        sources = need.setdefault("sourceMessageIds", {})
        for key in ("budget", "scenarios", "features", "candidates"):
            sources.pop(key, None)

    sources = need.setdefault("sourceMessageIds", {})
    if incoming_category:
        need["category"] = incoming_category
        sources["category"] = message_id

    budget = need.setdefault("budget", {"min": None, "max": None})
    if incoming.get("budgetMin") is not None:
        budget["min"] = incoming["budgetMin"]
        sources["budget"] = message_id
    if incoming.get("budgetMax") is not None:
        budget["max"] = incoming["budgetMax"]
        sources["budget"] = message_id

    if incoming.get("scenarios"):
        need["scenarios"] = _unique(list(incoming["scenarios"]))
        sources["scenarios"] = message_id

    soft = need.setdefault("softPreferences", {})
    if incoming.get("brands"):
        soft["brands"] = _unique(list(incoming["brands"]))
        sources["brands"] = message_id
    if incoming.get("features"):
        soft["features"] = _unique(list(incoming["features"]))
        sources["features"] = message_id
    if incoming.get("acceptSubstitute") is not None:
        soft["acceptSubstitute"] = incoming["acceptSubstitute"]
        sources["acceptSubstitute"] = message_id

    exclusions = need.setdefault("exclusions", {})
    if incoming.get("excludedBrands"):
        exclusions["brands"] = _unique(list(incoming["excludedBrands"]))
        soft["brands"] = [
            brand
            for brand in soft.get("brands") or []
            if brand not in exclusions["brands"]
        ]
        sources["excludedBrands"] = message_id

    accept_substitute = soft.get("acceptSubstitute")
    need["hardConstraints"] = {
        "requiredBrands": (
            list(soft.get("brands") or []) if accept_substitute is False else []
        ),
        "requiredTerms": [],
    }
    need["missingSlots"] = _missing_slots(need)
    need["updatedAt"] = _iso(timestamp)
    need["activeUntil"] = _iso(
        timestamp + timedelta(hours=SHOPPING_NEED_ACTIVE_HOURS)
    )
    return need


def effective_profile_from_need(
    durable_profile: dict[str, Any], need: dict[str, Any] | None
) -> dict[str, Any]:
    result = deepcopy(durable_profile)
    # Callers load or gate the active need before merging. Keeping a second
    # wall-clock check here made deterministic category switches expire while
    # they were still being processed and restored stale durable preferences.
    if not isinstance(need, dict):
        return result
    assert need is not None
    budget = need.get("budget") or {}
    soft = need.get("softPreferences") or {}
    exclusions = need.get("exclusions") or {}
    result.update(
        {
            "category": need.get("category"),
            "budgetMin": budget.get("min"),
            "budgetMax": budget.get("max"),
            "brands": list(soft.get("brands") or []),
            "excludedBrands": list(exclusions.get("brands") or []),
            "scenarios": list(need.get("scenarios") or []),
            "features": list(soft.get("features") or []),
            "acceptSubstitute": soft.get("acceptSubstitute"),
        }
    )
    return result


def next_clarification_question(
    profile: dict[str, Any] | None, *, user_text: str = ""
) -> str:
    need = _seed_from_profile(profile)
    incoming = extract_profile(user_text)
    if _has_signal(incoming):
        need = apply_explicit_turn(
            need,
            profile=profile,
            user_text=user_text,
            message_id=0,
        ) or need
    missing = list(need.get("missingSlots") or [])
    slot = missing[0] if missing else ""
    if slot == "category":
        return "你想选哪一类商品？"
    if slot == "budget":
        return "你的最高预算是多少？"
    if slot == "scenario":
        return "主要使用场景是什么？"
    return "你最看重哪一项条件？"


def recent_candidate_ids(
    need: dict[str, Any] | None, *, now: datetime | None = None
) -> list[str]:
    if not shopping_need_is_active(need, now=now):
        return []
    threshold = (now or _utc_now()) - timedelta(hours=RECENT_CANDIDATE_HOURS)
    result: list[str] = []
    for candidate in (need or {}).get("candidateProducts") or []:
        if not isinstance(candidate, dict):
            continue
        observed_at = _parse_time(candidate.get("observedAt"))
        product_id = str(candidate.get("productId") or "").strip()
        if product_id and observed_at and observed_at >= threshold:
            result.append(product_id)
    return _unique(result)


class ShoppingNeedService:
    async def capture_user_turn(
        self,
        user_id: str,
        message_id: int,
        user_text: str,
        durable_profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        memory = await session_memory_service.load(user_id, redis_service.client)
        updated = apply_explicit_turn(
            memory.state.get("shoppingNeed"),
            profile=durable_profile,
            user_text=user_text,
            message_id=message_id,
        )
        if updated is None:
            return None
        memory.state["shoppingNeed"] = updated
        await session_memory_service.save(memory, redis_service.client)
        return updated

    async def record_candidates(
        self,
        user_id: str,
        message_id: int,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        memory = await session_memory_service.load(user_id, redis_service.client)
        need = memory.state.get("shoppingNeed")
        if not shopping_need_is_active(need):
            need = empty_shopping_need_state()
        else:
            need = deepcopy(need)
        timestamp = _iso(_utc_now())
        existing = {
            str(item.get("productId")): item
            for item in need.get("candidateProducts") or []
            if isinstance(item, dict) and item.get("productId")
        }
        ordered: list[dict[str, Any]] = []
        for raw in candidates:
            product_id = str(raw.get("productId") or raw.get("product_id") or "").strip()
            if not product_id:
                continue
            item = {
                "productId": product_id,
                "productName": raw.get("productName") or raw.get("product_name"),
                "minPrice": raw.get("minPrice") or raw.get("min_price"),
                "sourceMessageId": message_id,
                "observedAt": timestamp,
            }
            existing.pop(product_id, None)
            ordered.append(item)
        ordered.extend(existing.values())
        need["candidateProducts"] = ordered[:MAX_RECENT_CANDIDATES]
        need.setdefault("sourceMessageIds", {})["candidates"] = message_id
        need["updatedAt"] = timestamp
        need["activeUntil"] = _iso(
            _utc_now() + timedelta(hours=SHOPPING_NEED_ACTIVE_HOURS)
        )
        memory.state["shoppingNeed"] = need
        await session_memory_service.save(memory, redis_service.client)
        return need

    async def load(self, user_id: str) -> dict[str, Any] | None:
        memory = await session_memory_service.load(user_id, redis_service.client)
        need = memory.state.get("shoppingNeed")
        return need if shopping_need_is_active(need) else None

    async def allowed_candidate_ids(self, user_id: str) -> list[str]:
        return recent_candidate_ids(await self.load(user_id))

    async def rebase_profile(
        self, user_id: str, profile: dict[str, Any]
    ) -> dict[str, Any]:
        memory = await session_memory_service.load(user_id, redis_service.client)
        previous = memory.state.get("shoppingNeed") or {}
        need = _seed_from_profile(profile)
        need["candidateProducts"] = list(
            previous.get("candidateProducts") or []
        )[:MAX_RECENT_CANDIDATES]
        now = _utc_now()
        need["updatedAt"] = _iso(now)
        need["activeUntil"] = _iso(
            now + timedelta(hours=SHOPPING_NEED_ACTIVE_HOURS)
        )
        memory.state["shoppingNeed"] = need
        await session_memory_service.save(memory, redis_service.client)
        return need


shopping_need_service = ShoppingNeedService()
