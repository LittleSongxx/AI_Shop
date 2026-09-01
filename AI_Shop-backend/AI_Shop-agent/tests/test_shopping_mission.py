from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.shopping_mission_service import (
    _CATEGORY_SCHEMAS,
    ShoppingMissionService,
    _validated_category_schema,
    apply_explicit_turn,
    empty_shopping_mission,
    mission_summary,
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


def test_complete_same_category_request_does_not_inherit_the_previous_mission():
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
        "brands": ["华为"],
        "personalizationEnabled": False,
        "excludedBrands": ["苹果"],
        "excludedTerms": ["入耳式"],
        "implicitSignals": [{"kind": "product", "value": "p1", "effectiveWeight": 1}],
    }
    updated = apply_explicit_turn(
        current,
        profile=profile,
        user_text="我想买个3000以下的降噪耳机",
        message_id=75,
    )

    assert updated is not None
    assert updated["missionId"] != current["missionId"]
    assert updated["category"] == "耳机"
    assert updated["useCases"] == []
    assert updated["hardConstraints"]["budgetMax"] == 3000.0
    assert updated["softPreferences"]["features"] == ["降噪"]
    assert updated["softPreferences"]["brands"] == ["华为"]
    assert updated["hardConstraints"]["requiredBrands"] == []
    assert updated["candidateProducts"] == []
    assert updated["exclusions"] == {"brands": ["苹果"], "terms": ["入耳式"]}
    assert updated["personalization"]["enabled"] is False
    assert updated["personalization"]["implicitSignals"] == [
        {"kind": "product", "value": "p1", "effectiveWeight": 1.0, "source": ""}
    ]


def test_hot_sale_and_wps_start_clean_missions_but_budget_revision_continues():
    current = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="通勤用的降噪耳机，预算3000元",
        message_id=74,
    )
    assert current is not None

    continued = apply_explicit_turn(
        current,
        profile=empty_profile(),
        user_text="预算提高到4000元，继续推荐",
        message_id=75,
    )
    assert continued is not None
    assert continued["missionId"] == current["missionId"]
    assert continued["category"] == "耳机"
    assert continued["hardConstraints"]["budgetMax"] == 4000.0

    brand_refinement = apply_explicit_turn(
        current,
        profile=empty_profile(),
        user_text="按这个预算推荐索尼耳机",
        message_id=75,
    )
    assert brand_refinement is not None
    assert brand_refinement["missionId"] == current["missionId"]
    assert brand_refinement["hardConstraints"]["budgetMax"] == 3000.0
    assert brand_refinement["softPreferences"]["features"] == ["降噪"]
    assert brand_refinement["hardConstraints"]["requiredBrands"] == ["索尼"]

    hot_sale = apply_explicit_turn(
        continued,
        profile=empty_profile(),
        user_text="帮我推荐热销商品",
        message_id=76,
    )
    assert hot_sale is not None
    assert hot_sale["missionId"] != current["missionId"]
    assert hot_sale["category"] is None
    assert hot_sale["hardConstraints"]["budgetMax"] is None
    assert hot_sale["softPreferences"]["features"] == []
    hot_sale_second_apply = apply_explicit_turn(
        hot_sale,
        profile=empty_profile(),
        user_text="帮我推荐热销商品",
        message_id=0,
    )
    assert hot_sale_second_apply is not None
    assert hot_sale_second_apply["missionId"] == hot_sale["missionId"]

    wps = apply_explicit_turn(
        current,
        profile=empty_profile(),
        user_text="有WPS会员吗",
        message_id=77,
    )
    assert wps is not None
    assert wps["missionId"] != current["missionId"]
    assert wps["category"] == "会员服务"
    assert wps["hardConstraints"]["budgetMax"] is None
    wps_second_apply = apply_explicit_turn(
        wps,
        profile=empty_profile(),
        user_text="有WPS会员吗",
        message_id=0,
    )
    assert wps_second_apply is not None
    assert wps_second_apply["missionId"] == wps["missionId"]

    coffee = apply_explicit_turn(
        current,
        profile=empty_profile(),
        user_text="有没有咖啡机",
        message_id=78,
    )
    assert coffee is not None
    assert coffee["missionId"] != current["missionId"]
    assert coffee["category"] == "咖啡机"
    assert coffee["hardConstraints"]["budgetMax"] is None
    assert coffee["softPreferences"]["features"] == []

    unknown_topic = apply_explicit_turn(
        current,
        profile=empty_profile(),
        user_text="有露营天幕吗",
        message_id=79,
    )
    assert unknown_topic is not None
    assert unknown_topic["missionId"] != current["missionId"]
    assert unknown_topic["category"] is None
    assert unknown_topic["hardConstraints"]["budgetMax"] is None
    assert unknown_topic["softPreferences"]["features"] == []


def test_mission_summary_formats_one_sided_budgets_for_people():
    mission = empty_shopping_mission(
        {**empty_profile(), "category": "耳机", "budgetMax": 3000.0}
    )

    assert mission_summary(mission) == "品类:耳机 | 预算:3000元以内"

    mission["hardConstraints"].update(
        {"budgetMin": 123456.78, "budgetMax": 200000.5}
    )
    assert mission_summary(mission) == (
        "品类:耳机 | 预算:123456.78-200000.5元"
    )


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


@pytest.mark.asyncio
async def test_worker_refinement_reuses_same_turn_but_persists_a_new_topic(monkeypatch):
    service = ShoppingMissionService()
    current = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="我想买个3000元以内的降噪耳机",
        message_id=74,
    )
    assert current is not None
    monkeypatch.setattr(service, "load", AsyncMock(return_value=current))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save", save)

    same = await service.capture_user_turn(
        "u1", 0, "我想买个3000元以内的降噪耳机", empty_profile()
    )
    assert same is current
    save.assert_not_awaited()

    coffee = await service.capture_user_turn(
        "u1", 0, "有没有咖啡机", empty_profile()
    )
    assert coffee is not None
    assert coffee["category"] == "咖啡机"
    save.assert_awaited_once()
    assert save.await_args.kwargs["source_message_id"] is None


@pytest.mark.asyncio
async def test_server_observed_candidates_start_a_mission_when_profile_signals_are_absent(
    monkeypatch,
):
    service = ShoppingMissionService()
    monkeypatch.setattr(service, "load", AsyncMock(return_value=None))
    save = AsyncMock()
    monkeypatch.setattr(service, "_save", save)

    result = await service.record_candidates(
        "u1",
        91,
        [
            {"productId": "p-xm6", "productName": "WH-1000XM6"},
            {"productId": "p-anniversary", "productName": "十周年版"},
        ],
    )

    assert result is not None
    assert result["status"] == "ACTIVE"
    assert [row["productId"] for row in result["candidateProducts"]] == [
        "p-xm6",
        "p-anniversary",
    ]
    assert result["sourceMessageIds"]["candidates"] == 91
    save.assert_awaited_once_with("u1", result, source_message_id=91)
