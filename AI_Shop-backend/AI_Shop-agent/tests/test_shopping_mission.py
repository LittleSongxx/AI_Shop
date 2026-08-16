from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.shopping_mission_service import (
    _CATEGORY_SCHEMAS,
    ShoppingMissionService,
    _validated_category_schema,
    apply_explicit_turn,
    empty_shopping_mission,
    next_clarification,
    schema_for,
)
from app.services.shopping_profile_service import empty_profile


def test_brand_named_in_current_turn_is_a_hard_constraint():
    mission = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="500元以内的耐克运动鞋",
        message_id=11,
    )

    assert mission is not None
    assert mission["hardConstraints"]["requiredBrands"] == ["耐克"]
    assert mission["softPreferences"]["brands"] == ["耐克"]


def test_remembered_brand_stays_a_weak_signal():
    profile = {**empty_profile(), "brands": ["华为"]}

    mission = empty_shopping_mission(profile)

    assert mission["softPreferences"]["brands"] == ["华为"]
    assert mission["hardConstraints"]["requiredBrands"] == []


def test_explicit_substitute_permission_downgrades_brand_constraint():
    mission = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="想买耐克运动鞋",
        message_id=11,
    )

    relaxed = apply_explicit_turn(
        mission,
        profile=empty_profile(),
        user_text="其他品牌也可以",
        message_id=12,
    )

    assert relaxed is not None
    assert relaxed["softPreferences"]["brands"] == ["耐克"]
    assert relaxed["softPreferences"]["acceptSubstitute"] is True
    assert relaxed["hardConstraints"]["requiredBrands"] == []


def test_category_schema_is_versioned_and_has_user_utility_weights():
    schema = schema_for("电脑")

    assert schema["schemaKey"] == "computer"
    assert schema["version"]
    assert set(schema["weights"]) == {"useCase", "feature", "offer", "explicit", "diversity"}
    assert sum(schema["weights"].values()) == pytest.approx(1.0)
    assert "useCase" in schema["required"]
    assert schema["questions"]["useCase"][1]


def test_speaker_category_is_not_misclassified_as_luggage():
    mission = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="蓝牙桌面音箱",
        message_id=12,
    )

    assert mission is not None
    assert mission["category"] == "音箱"
    assert mission["unknownSlots"] == []
    assert next_clarification(mission) is None


def test_clarification_uses_injected_clock_for_deterministic_evaluation():
    fixed_now = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
    mission = empty_shopping_mission(
        {
            **empty_profile(),
            "category": "箱包",
            "budgetMax": 500.0,
        }
    )
    mission["expiresAt"] = (fixed_now + timedelta(hours=1)).isoformat()

    clarification = next_clarification(mission, now=fixed_now)

    assert clarification is not None
    assert clarification["slot"] == "useCase"
    assert clarification["options"] == ["上学通勤", "上班通勤", "旅行出差", "户外运动"]


def test_category_schema_rejects_weights_that_do_not_sum_to_one():
    invalid = {
        **_CATEGORY_SCHEMAS["computer"],
        "weights": {"useCase": 1, "feature": 0, "offer": 0, "explicit": 0, "diversity": 0.1},
    }

    with pytest.raises(ValueError, match="CATEGORY_SCHEMA_WEIGHTS_INVALID"):
        _validated_category_schema("computer", invalid)


def test_explicit_bag_turn_replaces_an_active_headphones_mission():
    current = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="通勤用的降噪耳机，预算4500元",
        message_id=74,
    )
    assert current is not None
    current["candidateProducts"] = [{"productId": "old-headphones"}]
    profile = {
        **empty_profile(),
        "category": "箱包",
        "budgetMax": 500.0,
        "scenarios": ["办公", "通勤", "上班通勤"],
    }

    updated = apply_explicit_turn(
        current,
        profile=profile,
        user_text="我想买一个适合上班通勤的包，预算500元以内",
        message_id=75,
    )

    assert updated is not None
    assert updated["missionId"] != current["missionId"]
    assert updated["category"] == "箱包"
    assert updated["hardConstraints"]["budgetMax"] == 500.0
    assert updated["useCases"] == ["办公", "通勤", "上班通勤"]
    assert updated["candidateProducts"] == []


@pytest.mark.asyncio
async def test_mission_upsert_qualifies_existing_columns_for_mysql_84(monkeypatch):
    cur = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cur)
    cm.__aexit__ = AsyncMock(return_value=None)
    memory = MagicMock()
    memory.state = {}

    monkeypatch.setattr(
        "app.services.shopping_mission_service.session_memory_service.load",
        AsyncMock(return_value=memory),
    )
    monkeypatch.setattr(
        "app.services.shopping_mission_service.session_memory_service.save",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.shopping_mission_service.redis_service._client",
        MagicMock(),
    )

    mission = empty_shopping_mission(empty_profile())
    with patch("app.services.shopping_mission_service.acquire", return_value=cm):
        await ShoppingMissionService()._save("u1", mission, source_message_id=42)

    sql = cur.execute.await_args.args[0]
    assert "agent_shopping_mission.source_message_id" in sql
    assert "agent_shopping_mission.revision+1" in sql
