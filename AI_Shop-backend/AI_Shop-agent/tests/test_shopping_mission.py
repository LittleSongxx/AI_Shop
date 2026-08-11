import pytest

from app.services.shopping_mission_service import (
    _CATEGORY_SCHEMAS,
    _validated_category_schema,
    apply_explicit_turn,
    empty_shopping_mission,
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
