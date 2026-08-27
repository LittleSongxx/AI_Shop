"""Structured, bounded shopping missions used by the customer Agent.

The mission is intentionally separate from the durable preference profile:
profile answers "what has this user explicitly preferred before?", while a
mission represents one short-lived purchase decision.  Only explicit signals
become hard constraints; inferred behaviour is never allowed to silently turn
into a price, brand, or suitability promise.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from app.config.settings import get_settings
from app.db.pool import acquire
from app.memory.session_memory_service import session_memory_service
from app.services.redis_service import redis_service
from app.services.shopping_profile_service import _has_signal, extract_profile

logger = structlog.get_logger()

MISSION_VERSION = 2
MAX_RECENT_CANDIDATES = 12
_DECLINE_CLARIFICATION_HINTS = (
    "不想再回答",
    "不想回答",
    "别再问",
    "不用再问",
    "不要再问",
    "直接推荐",
    "随便推荐",
)


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


def _unique(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    result: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _category_key(category: str | None) -> str:
    value = str(category or "").strip()
    if any(token in value for token in ("笔记本", "电脑", "主机")):
        return "computer"
    if any(token in value for token in ("手机", "平板")):
        return "mobile"
    if "耳机" in value:
        return "headphones"
    if any(token in value for token in ("音箱", "音响")):
        return "generic"
    if any(
        token in value
        for token in (
            "箱包",
            "背包",
            "书包",
            "双肩包",
            "手提包",
            "斜挎包",
            "行李箱",
            "拉杆箱",
        )
    ) or value == "包":
        return "bags"
    if any(token in value for token in ("服", "鞋", "裙", "外套")):
        return "apparel"
    if any(token in value for token in ("家电", "电器", "冰箱", "洗衣", "空调")):
        return "appliance"
    return "generic"


# Values are deliberately broad. The product catalogue can map them to concrete
# verified features, while a schema never claims that every category has a given
# attribute available.
_CATEGORY_SCHEMAS: dict[str, dict[str, Any]] = {
    "generic": {
        "required": (),
        "questions": {
            "category": ("你想选哪一类商品？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "computer": {
        "required": ("useCase", "budget"),
        "questions": {
            "useCase": ("这台电脑主要用于什么？", ["编程开发", "视频创作", "游戏娱乐", "日常办公"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "mobile": {
        "required": ("useCase", "budget"),
        "questions": {
            "useCase": ("你更看重拍照、游戏、续航还是日常使用？", ["拍照影像", "游戏娱乐", "长续航", "日常使用"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "headphones": {
        "required": ("useCase",),
        "questions": {
            "useCase": ("耳机主要用于通勤降噪、运动、游戏还是音乐？", ["通勤降噪", "运动", "游戏娱乐", "音乐欣赏"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "bags": {
        "required": ("useCase",),
        "questions": {
            "useCase": ("这个包主要用于什么？", ["上学通勤", "上班通勤", "旅行出差", "户外运动"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "apparel": {
        "required": ("useCase",),
        "questions": {
            "useCase": ("准备在什么场景穿着或使用？", ["日常通勤", "运动户外", "正式场合", "旅行休闲"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
    "appliance": {
        "required": ("useCase", "budget"),
        "questions": {
            "useCase": ("主要要解决什么使用需求？", ["小户型", "家庭日用", "节能静音", "高性能"]),
            "budget": ("你的最高预算是多少？", []),
        },
        "weights": {"useCase": 0.45, "feature": 0.20, "offer": 0.20, "explicit": 0.10, "diversity": 0.05},
    },
}

_CATEGORY_SCHEMA_SEED_VERSION = "agentic-commerce-v2"
_CATEGORY_SCHEMA_WEIGHT_KEYS = frozenset(
    {"useCase", "feature", "offer", "explicit", "diversity"}
)
_PUBLISHED_CATEGORY_SCHEMAS = deepcopy(_CATEGORY_SCHEMAS)
_PUBLISHED_CATEGORY_SCHEMA_VERSIONS = {
    key: _CATEGORY_SCHEMA_SEED_VERSION for key in _CATEGORY_SCHEMAS
}


def _validated_category_schema(schema_key: str, raw: Any) -> dict[str, Any]:
    if schema_key not in _CATEGORY_SCHEMAS or not isinstance(raw, dict):
        raise ValueError("CATEGORY_SCHEMA_INVALID")
    required_raw = raw.get("required") or []
    if not isinstance(required_raw, (list, tuple)):
        raise ValueError("CATEGORY_SCHEMA_REQUIRED_INVALID")
    required = tuple(_unique(required_raw))
    allowed_slots = frozenset({"category", "useCase", "budget", "feature", "brand", "portability"})
    if any(slot not in allowed_slots for slot in required):
        raise ValueError("CATEGORY_SCHEMA_SLOT_INVALID")

    questions_raw = raw.get("questions") or {}
    if not isinstance(questions_raw, dict):
        raise ValueError("CATEGORY_SCHEMA_QUESTIONS_INVALID")
    questions: dict[str, tuple[str, list[str]]] = {}
    for slot, question_spec in questions_raw.items():
        if slot not in allowed_slots or not isinstance(question_spec, (list, tuple)):
            raise ValueError("CATEGORY_SCHEMA_QUESTION_INVALID")
        if len(question_spec) != 2:
            raise ValueError("CATEGORY_SCHEMA_QUESTION_INVALID")
        question = str(question_spec[0] or "").strip()
        options_raw = question_spec[1]
        if not question or len(question) > 200 or not isinstance(options_raw, (list, tuple)):
            raise ValueError("CATEGORY_SCHEMA_QUESTION_INVALID")
        questions[slot] = (question, _unique(options_raw)[:8])
    if any(slot not in questions for slot in required):
        raise ValueError("CATEGORY_SCHEMA_REQUIRED_QUESTION_MISSING")

    weights_raw = raw.get("weights") or {}
    if not isinstance(weights_raw, dict) or set(weights_raw) != _CATEGORY_SCHEMA_WEIGHT_KEYS:
        raise ValueError("CATEGORY_SCHEMA_WEIGHTS_INVALID")
    weights = {key: float(weights_raw[key]) for key in _CATEGORY_SCHEMA_WEIGHT_KEYS}
    if any(value < 0 or value > 1 for value in weights.values()):
        raise ValueError("CATEGORY_SCHEMA_WEIGHTS_INVALID")
    if abs(sum(weights.values()) - 1.0) > 0.0001:
        raise ValueError("CATEGORY_SCHEMA_WEIGHTS_INVALID")
    return {"required": required, "questions": questions, "weights": weights}


async def initialize_category_need_schemas() -> dict[str, Any]:
    """Seed missing contracts and load the latest valid published version."""
    global _PUBLISHED_CATEGORY_SCHEMAS, _PUBLISHED_CATEGORY_SCHEMA_VERSIONS

    try:
        async with acquire() as cur:
            for schema_key, schema in _CATEGORY_SCHEMAS.items():
                await cur.execute(
                    """
                    INSERT INTO agent_category_need_schema
                        (schema_key, version, status, schema_json, created_by,
                         created_at, updated_at)
                    VALUES (%s, %s, 'PUBLISHED', %s, 'system:agentic-commerce-v2',
                            NOW(3), NOW(3)) AS incoming
                    ON DUPLICATE KEY UPDATE schema_key=incoming.schema_key
                    """,
                    (
                        schema_key,
                        _CATEGORY_SCHEMA_SEED_VERSION,
                        json.dumps(schema, ensure_ascii=False),
                    ),
                )
            await cur.execute(
                """
                SELECT schema_key, version, schema_json
                FROM agent_category_need_schema
                WHERE status='PUBLISHED'
                ORDER BY schema_key, updated_at DESC, version DESC
                """
            )
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning(
            "category_need_schema_load_failed",
            error=type(exc).__name__,
            fallback_version=_CATEGORY_SCHEMA_SEED_VERSION,
        )
        return {
            "status": "DEGRADED",
            "loaded": len(_PUBLISHED_CATEGORY_SCHEMAS),
            "fallback": True,
        }

    loaded: dict[str, dict[str, Any]] = {}
    versions: dict[str, str] = {}
    rejected: list[str] = []
    for row in rows or []:
        schema_key = str(row.get("schema_key") or "")
        if schema_key in loaded or schema_key not in _CATEGORY_SCHEMAS:
            continue
        payload = row.get("schema_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                rejected.append(schema_key)
                continue
        try:
            loaded[schema_key] = _validated_category_schema(schema_key, payload)
            versions[schema_key] = str(row.get("version") or "unknown")
        except (TypeError, ValueError):
            rejected.append(schema_key)

    for schema_key, fallback in _CATEGORY_SCHEMAS.items():
        if schema_key not in loaded:
            loaded[schema_key] = deepcopy(fallback)
            versions[schema_key] = _CATEGORY_SCHEMA_SEED_VERSION
    _PUBLISHED_CATEGORY_SCHEMAS = loaded
    _PUBLISHED_CATEGORY_SCHEMA_VERSIONS = versions
    if rejected:
        logger.warning("category_need_schema_rejected", schema_keys=sorted(set(rejected)))
    logger.info(
        "category_need_schema_loaded",
        loaded=len(loaded),
        rejected=len(set(rejected)),
    )
    return {
        "status": "SUCCEEDED" if not rejected else "DEGRADED",
        "loaded": len(loaded),
        "rejected": sorted(set(rejected)),
        "fallback": bool(rejected),
    }


def schema_for(category: str | None) -> dict[str, Any]:
    schema_key = _category_key(category)
    schema = deepcopy(_PUBLISHED_CATEGORY_SCHEMAS[schema_key])
    schema["schemaKey"] = schema_key
    schema["version"] = _PUBLISHED_CATEGORY_SCHEMA_VERSIONS[schema_key]
    return schema


def empty_shopping_mission(profile: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    profile = profile or {}
    now = _utc_now()
    mission = {
        "version": MISSION_VERSION,
        "missionId": f"shop_{uuid.uuid4().hex}",
        "status": "ACTIVE",
        "category": profile.get("category"),
        "useCases": _unique(profile.get("scenarios")),
        "hardConstraints": {
            "budgetMin": profile.get("budgetMin"),
            "budgetMax": profile.get("budgetMax"),
            "requiredBrands": (
                _unique(profile.get("brands"))
                if profile.get("acceptSubstitute") is False
                else []
            ),
            "availability": "ON_SALE",
        },
        "softPreferences": {
            "brands": _unique(profile.get("brands")),
            "features": _unique(profile.get("features")),
            "acceptSubstitute": profile.get("acceptSubstitute"),
        },
        "exclusions": {
            "brands": _unique(profile.get("excludedBrands")),
            "terms": _unique(profile.get("excludedTerms")),
        },
        "personalization": {
            "enabled": bool(profile.get("personalizationEnabled", True)),
            "implicitSignals": [
                {
                    "kind": str(signal.get("kind") or "")[:32],
                    "value": str(signal.get("value") or "")[:80],
                    "effectiveWeight": float(signal.get("effectiveWeight") or 0),
                    "source": str(signal.get("source") or "")[:32],
                }
                for signal in profile.get("implicitSignals") or []
                if isinstance(signal, dict)
                and float(signal.get("effectiveWeight") or 0) > 0
            ][:20],
        },
        "unknownSlots": [],
        "clarificationCount": 0,
        "sourceMessageIds": {},
        "candidateProducts": [],
        "updatedAt": _iso(now),
        "expiresAt": _iso(now + timedelta(hours=settings.shopping_mission_active_hours)),
    }
    mission["unknownSlots"] = _missing_slots(mission)
    return mission


def mission_is_active(mission: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not isinstance(mission, dict) or mission.get("status") != "ACTIVE":
        return False
    expires_at = _parse_time(mission.get("expiresAt"))
    return bool(expires_at and expires_at > (now or _utc_now()))


def _migrate_legacy_need(legacy: Any) -> dict[str, Any] | None:
    """Convert an active v1 memory record once, without reviving its service.

    The legacy record is only a historical in-memory representation.  After
    migration the caller removes it, so every subsequent decision uses the v2
    mission and its persisted database projection.
    """
    if not isinstance(legacy, dict):
        return None
    expires_at = _parse_time(legacy.get("activeUntil"))
    if expires_at is None or expires_at <= _utc_now():
        return None
    budget = legacy.get("budget") or {}
    soft = legacy.get("softPreferences") or {}
    exclusions = legacy.get("exclusions") or {}
    hard = legacy.get("hardConstraints") or {}
    mission = empty_shopping_mission({})
    mission.update(
        {
            "category": legacy.get("category"),
            "useCases": _unique(legacy.get("scenarios") or []),
            "hardConstraints": {
                "budgetMin": budget.get("min"),
                "budgetMax": budget.get("max"),
                "requiredBrands": _unique(hard.get("requiredBrands") or []),
                "availability": "ON_SALE",
            },
            "softPreferences": {
                "brands": _unique(soft.get("brands") or []),
                "features": _unique(soft.get("features") or []),
                "acceptSubstitute": soft.get("acceptSubstitute"),
            },
            "exclusions": {
                "brands": _unique(exclusions.get("brands") or []),
                "terms": _unique(exclusions.get("terms") or []),
            },
            "sourceMessageIds": {
                str(key): value
                for key, value in (legacy.get("sourceMessageIds") or {}).items()
                if isinstance(value, int) and value > 0
            },
            "candidateProducts": [
                {
                    "productId": str(item.get("productId") or ""),
                    "productName": item.get("productName"),
                    "sourceMessageId": item.get("sourceMessageId"),
                    "observedAt": item.get("observedAt"),
                }
                for item in legacy.get("candidateProducts") or []
                if isinstance(item, dict) and str(item.get("productId") or "").strip()
            ][:MAX_RECENT_CANDIDATES],
            "updatedAt": legacy.get("updatedAt") or _iso(_utc_now()),
            "expiresAt": _iso(expires_at),
        }
    )
    if not mission["hardConstraints"]["requiredBrands"] and soft.get("acceptSubstitute") is False:
        mission["hardConstraints"]["requiredBrands"] = _unique(soft.get("brands") or [])
    mission["unknownSlots"] = _missing_slots(mission)
    return mission


def _missing_slots(mission: dict[str, Any]) -> list[str]:
    if not mission.get("category"):
        return ["category"]
    schema = schema_for(str(mission.get("category") or ""))
    hard = mission.get("hardConstraints") or {}
    use_cases = mission.get("useCases") or []
    missing: list[str] = []
    for slot in schema.get("required") or ():
        if slot == "useCase" and not use_cases:
            missing.append(slot)
        elif slot == "budget" and hard.get("budgetMin") is None and hard.get("budgetMax") is None:
            missing.append(slot)
    return missing


def _set_source(mission: dict[str, Any], field: str, message_id: int) -> None:
    if message_id > 0:
        mission.setdefault("sourceMessageIds", {})[field] = message_id


def apply_explicit_turn(
    current: dict[str, Any] | None,
    *,
    profile: dict[str, Any] | None,
    user_text: str,
    message_id: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Merge only regex/structured explicit signals into the active mission."""
    timestamp = now or _utc_now()
    incoming = extract_profile(user_text)
    if not _has_signal(incoming) and not mission_is_active(current, now=timestamp):
        return None
    mission = deepcopy(current) if mission_is_active(current, now=timestamp) else empty_shopping_mission(profile)
    previous_category = str(mission.get("category") or "")
    incoming_category = str(incoming.get("category") or "")
    if incoming_category and previous_category and incoming_category != previous_category:
        # A new shelf is a new decision. Stable exclusions remain useful, but
        # old budget/use-case candidates must not silently constrain it.
        mission = empty_shopping_mission(profile)
        mission["category"] = incoming_category
        mission["sourceMessageIds"] = {"category": message_id} if message_id > 0 else {}

    if incoming_category:
        mission["category"] = incoming_category
        _set_source(mission, "category", message_id)

    hard = mission.setdefault("hardConstraints", {})
    if incoming.get("budgetMin") is not None:
        hard["budgetMin"] = incoming["budgetMin"]
        _set_source(mission, "budget", message_id)
    if incoming.get("budgetMax") is not None:
        hard["budgetMax"] = incoming["budgetMax"]
        _set_source(mission, "budget", message_id)
    hard["availability"] = "ON_SALE"

    if incoming.get("scenarios"):
        mission["useCases"] = _unique(incoming["scenarios"])
        _set_source(mission, "useCases", message_id)

    soft = mission.setdefault("softPreferences", {})
    if incoming.get("brands"):
        soft["brands"] = _unique(incoming["brands"])
        _set_source(mission, "brands", message_id)
    if incoming.get("features"):
        soft["features"] = _unique(incoming["features"])
        _set_source(mission, "features", message_id)
    if incoming.get("acceptSubstitute") is not None:
        soft["acceptSubstitute"] = bool(incoming["acceptSubstitute"])
        _set_source(mission, "acceptSubstitute", message_id)

    exclusions = mission.setdefault("exclusions", {"brands": [], "terms": []})
    if incoming.get("excludedBrands"):
        exclusions["brands"] = _unique(incoming["excludedBrands"])
        soft["brands"] = [brand for brand in soft.get("brands") or [] if brand not in exclusions["brands"]]
        _set_source(mission, "excludedBrands", message_id)
    if incoming.get("excludedTerms"):
        exclusions["terms"] = _unique(
            [*(exclusions.get("terms") or []), *incoming["excludedTerms"]]
        )
        _set_source(mission, "excludedTerms", message_id)

    # A brand named in the current turn is a hard constraint by default. It is
    # downgraded only when the same turn explicitly accepts substitutes; a
    # remembered profile brand remains a weak preference and never filters by
    # itself.
    if incoming.get("brands"):
        hard["requiredBrands"] = (
            []
            if incoming.get("acceptSubstitute") is True
            else _unique(incoming.get("brands"))
        )
    elif incoming.get("acceptSubstitute") is False:
        hard["requiredBrands"] = _unique(soft.get("brands"))
    elif incoming.get("acceptSubstitute") is True:
        hard["requiredBrands"] = []
    mission["unknownSlots"] = _missing_slots(mission)
    if any(marker in user_text for marker in _DECLINE_CLARIFICATION_HINTS):
        mission["clarificationDeclined"] = True
        mission["uncertaintyDisclosureRequired"] = bool(mission["unknownSlots"])
    mission["updatedAt"] = _iso(timestamp)
    mission["expiresAt"] = _iso(
        timestamp + timedelta(hours=get_settings().shopping_mission_active_hours)
    )
    mission["status"] = "ACTIVE"
    mission["version"] = MISSION_VERSION
    return mission


def next_clarification(
    mission: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return one question selected by expected decision impact, or ``None``."""
    if not mission_is_active(mission, now=now):
        return None
    assert mission is not None
    if mission.get("clarificationDeclined") is True:
        return None
    if int(mission.get("clarificationCount") or 0) >= get_settings().shopping_mission_max_clarifications:
        return None
    missing = list(mission.get("unknownSlots") or _missing_slots(mission))
    if not missing:
        return None
    slot = missing[0]
    schema = schema_for(str(mission.get("category") or ""))
    question, options = schema.get("questions", {}).get(slot, (None, []))
    if not question:
        if slot == "category":
            question = "你想选哪一类商品？"
        elif slot == "budget":
            question = "你的最高预算是多少？"
        else:
            question = "你最看重哪一项条件？"
    return {
        "type": "SHOPPING_CLARIFICATION",
        "missionId": mission.get("missionId"),
        "slot": slot,
        "question": question,
        "options": options[:4],
        "reason": "该信息最能缩小当前候选范围",
    }


def mission_summary(mission: dict[str, Any] | None) -> str:
    if not mission_is_active(mission):
        return ""
    assert mission is not None
    hard = mission.get("hardConstraints") or {}
    soft = mission.get("softPreferences") or {}
    values = [
        f"品类:{mission.get('category')}" if mission.get("category") else "",
        "用途:" + ",".join(mission.get("useCases") or []) if mission.get("useCases") else "",
        (
            f"预算:{hard.get('budgetMin') or '*'}-{hard.get('budgetMax') or '*'}元"
            if hard.get("budgetMin") is not None or hard.get("budgetMax") is not None
            else ""
        ),
        "偏好:" + ",".join(_unique(soft.get("brands")) + _unique(soft.get("features")))
        if soft.get("brands") or soft.get("features")
        else "",
    ]
    return " | ".join(value for value in values if value)


class ShoppingMissionService:
    async def capture_user_turn(
        self,
        user_id: str,
        message_id: int,
        user_text: str,
        durable_profile: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = await self.load(user_id)
        updated = apply_explicit_turn(
            current,
            profile=durable_profile,
            user_text=user_text,
            message_id=message_id,
        )
        if updated is None:
            return None
        await self._save(user_id, updated, source_message_id=message_id)
        return updated

    async def load(self, user_id: str) -> dict[str, Any] | None:
        if not user_id:
            return None
        try:
            memory = await session_memory_service.load(user_id, redis_service.client)
            mission = memory.state.get("shoppingMission")
            if mission_is_active(mission):
                return mission
            migrated = _migrate_legacy_need(memory.state.get("shoppingNeed"))
            if migrated is not None:
                await self._save(user_id, migrated, source_message_id=None)
                logger.info("shopping_need_migrated_to_mission", user_id=user_id)
                return migrated
        except Exception as exc:
            logger.warning("shopping_mission_memory_read_failed", error=type(exc).__name__)
        try:
            async with acquire() as cur:
                await cur.execute(
                    "SELECT mission_json FROM agent_shopping_mission "
                    "WHERE user_id=%s AND status='ACTIVE' AND expires_at>NOW(3)",
                    (user_id,),
                )
                row = await cur.fetchone()
        except Exception as exc:
            logger.warning("shopping_mission_db_read_failed", user_id=user_id, error=type(exc).__name__)
            return None
        if not row:
            return None
        mission = row.get("mission_json")
        if isinstance(mission, str):
            try:
                mission = json.loads(mission)
            except json.JSONDecodeError:
                return None
        if not mission_is_active(mission):
            return None
        try:
            memory = await session_memory_service.load(user_id, redis_service.client)
            memory.state["shoppingMission"] = mission
            memory.state.pop("shoppingNeed", None)
            await session_memory_service.save(memory, redis_service.client)
        except Exception:
            pass
        return mission

    async def mark_clarification_presented(self, user_id: str, mission: dict[str, Any]) -> None:
        if not mission_is_active(mission):
            return
        updated = deepcopy(mission)
        updated["clarificationCount"] = min(
            get_settings().shopping_mission_max_clarifications,
            int(updated.get("clarificationCount") or 0) + 1,
        )
        updated["updatedAt"] = _iso(_utc_now())
        await self._save(user_id, updated, source_message_id=None)

    async def record_candidates(
        self,
        user_id: str,
        message_id: int,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        mission = await self.load(user_id)
        # A server-observed result list is itself sufficient to begin a
        # shopping mission.  Named-product consultations may not contain a
        # category/budget token, so waiting for an explicit profile signal
        # would leave the just-resolved candidates unavailable to the
        # comparison tool in the same turn.
        if mission is None:
            mission = empty_shopping_mission({})
        updated = deepcopy(mission)
        existing = {
            str(item.get("productId")): item
            for item in updated.get("candidateProducts") or []
            if isinstance(item, dict) and item.get("productId")
        }
        observed_at = _iso(_utc_now())
        ordered: list[dict[str, Any]] = []
        for raw in candidates:
            product_id = str(raw.get("productId") or raw.get("product_id") or "").strip()
            if not product_id:
                continue
            existing.pop(product_id, None)
            ordered.append(
                {
                    "productId": product_id,
                    "productName": raw.get("productName") or raw.get("product_name"),
                    "offerSnapshotId": (
                        raw.get("offerSnapshotId")
                        or raw.get("offer_snapshot_id")
                        or raw.get("snapshotId")
                    ),
                    "basePrice": raw.get("basePrice") or raw.get("base_price"),
                    "estimatedPayable": (
                        raw.get("estimatedPayable")
                        if raw.get("estimatedPayable") is not None
                        else raw.get("estimated_payable")
                    ),
                    "rankingDecisionId": (
                        raw.get("rankingDecisionId")
                        or raw.get("ranking_decision_id")
                    ),
                    "recommendation": (
                        deepcopy(raw.get("recommendation"))
                        if isinstance(raw.get("recommendation"), dict)
                        else None
                    ),
                    "sourceMessageId": message_id,
                    "observedAt": observed_at,
                }
            )
        ordered.extend(existing.values())
        updated["candidateProducts"] = ordered[:MAX_RECENT_CANDIDATES]
        updated.setdefault("sourceMessageIds", {})["candidates"] = message_id
        updated["updatedAt"] = observed_at
        await self._save(user_id, updated, source_message_id=message_id)
        return updated

    async def allowed_candidate_ids(self, user_id: str) -> list[str]:
        mission = await self.load(user_id)
        if not mission:
            return []
        return _unique(
            [item.get("productId") for item in mission.get("candidateProducts") or [] if isinstance(item, dict)]
        )

    async def rebase_profile(self, user_id: str, profile: dict[str, Any]) -> dict[str, Any] | None:
        current = await self.load(user_id)
        if current is None:
            return None
        updated = empty_shopping_mission(profile)
        updated["missionId"] = current.get("missionId") or updated["missionId"]
        updated["candidateProducts"] = list(current.get("candidateProducts") or [])[:MAX_RECENT_CANDIDATES]
        updated["clarificationCount"] = int(current.get("clarificationCount") or 0)
        await self._save(user_id, updated, source_message_id=None)
        return updated

    @staticmethod
    def specialist_context(mission: dict[str, Any] | None) -> dict[str, Any]:
        if not mission_is_active(mission):
            return {}
        assert mission is not None
        hard = mission.get("hardConstraints") or {}
        soft = mission.get("softPreferences") or {}
        exclusions = mission.get("exclusions") or {}
        return {
            "missionId": mission.get("missionId"),
            "category": mission.get("category"),
            "useCases": list(mission.get("useCases") or [])[:4],
            "hardConstraints": {
                "budgetMin": hard.get("budgetMin"),
                "budgetMax": hard.get("budgetMax"),
                "requiredBrands": _unique(hard.get("requiredBrands"))[:6],
                "availability": "ON_SALE",
            },
            "softPreferences": {
                "brands": _unique(soft.get("brands"))[:6],
                "features": _unique(soft.get("features"))[:8],
                "acceptSubstitute": soft.get("acceptSubstitute"),
            },
            "exclusions": {
                "brands": _unique(exclusions.get("brands"))[:6],
                "terms": _unique(exclusions.get("terms"))[:8],
            },
            "unknownSlots": list(mission.get("unknownSlots") or [])[:3],
            "schemaKey": _category_key(str(mission.get("category") or "")),
            "schemaVersion": schema_for(str(mission.get("category") or "")).get("version"),
        }

    async def _save(
        self,
        user_id: str,
        mission: dict[str, Any],
        *,
        source_message_id: int | None,
    ) -> None:
        expires_at = _parse_time(mission.get("expiresAt")) or (
            _utc_now() + timedelta(hours=get_settings().shopping_mission_active_hours)
        )
        try:
            async with acquire() as cur:
                await cur.execute(
                    """
                    INSERT INTO agent_shopping_mission
                        (user_id, mission_id, status, mission_json, source_message_id,
                         revision, expires_at, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,1,%s,NOW(3),NOW(3)) AS incoming
                    ON DUPLICATE KEY UPDATE
                        mission_id=incoming.mission_id, status=incoming.status,
                        mission_json=incoming.mission_json,
                        source_message_id=COALESCE(
                            incoming.source_message_id,
                            agent_shopping_mission.source_message_id
                        ),
                        revision=agent_shopping_mission.revision+1,
                        expires_at=incoming.expires_at, updated_at=NOW(3)
                    """,
                    (
                        user_id,
                        str(mission.get("missionId") or f"shop_{uuid.uuid4().hex}"),
                        str(mission.get("status") or "ACTIVE"),
                        json.dumps(mission, ensure_ascii=False),
                        source_message_id,
                        expires_at.replace(tzinfo=None),
                    ),
                )
        except Exception as exc:
            logger.warning("shopping_mission_db_write_failed", user_id=user_id, error=type(exc).__name__)
        try:
            memory = await session_memory_service.load(user_id, redis_service.client)
            memory.state["shoppingMission"] = mission
            # The old online state is intentionally cleared. Older messages stay
            # readable, but current recommendation decisions are v2 only.
            memory.state.pop("shoppingNeed", None)
            await session_memory_service.save(memory, redis_service.client)
        except Exception as exc:
            logger.warning("shopping_mission_memory_write_failed", user_id=user_id, error=type(exc).__name__)


shopping_mission_service = ShoppingMissionService()
