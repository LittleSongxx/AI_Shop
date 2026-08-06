from datetime import datetime, timedelta, timezone

from app.domain.intent.types import IntentKind
from app.services.agent_service import _SHOPPING_MEMORY_INTENTS
from app.services.shopping_need_service import (
    apply_explicit_turn,
    effective_profile_from_need,
    next_clarification_question,
    recent_candidate_ids,
)
from app.services.shopping_profile_service import empty_profile


def test_only_shopping_intents_are_allowed_to_write_automatic_preferences():
    assert IntentKind.PRODUCT_SEARCH in _SHOPPING_MEMORY_INTENTS
    assert IntentKind.PRODUCT_CONSULT in _SHOPPING_MEMORY_INTENTS
    assert IntentKind.REFUND not in _SHOPPING_MEMORY_INTENTS
    assert IntentKind.QUERY_ORDER not in _SHOPPING_MEMORY_INTENTS
    assert IntentKind.COMPLAINT not in _SHOPPING_MEMORY_INTENTS


def test_shopping_need_asks_only_the_highest_value_missing_slot():
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    need = apply_explicit_turn(
        None,
        profile=empty_profile(),
        user_text="想买手机",
        message_id=101,
        now=now,
    )

    assert need is not None
    assert need["category"] == "手机"
    assert need["missingSlots"] == ["budget"]
    assert need["sourceMessageIds"]["category"] == 101
    assert next_clarification_question(
        {**empty_profile(), "category": "手机"}, user_text="想买手机"
    ) == "你的最高预算是多少？"


def test_current_turn_overrides_long_term_profile_and_category_switch_resets_task():
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    durable = {
        **empty_profile(),
        "category": "手机",
        "budgetMax": 3000,
        "brands": ["华为"],
    }
    phone_need = apply_explicit_turn(
        None,
        profile=durable,
        user_text="3000以内的手机",
        message_id=1,
        now=now,
    )
    assert phone_need is not None
    phone_need["candidateProducts"] = [
        {
            "productId": "p1",
            "sourceMessageId": 1,
            "observedAt": now.isoformat(),
        }
    ]

    laptop_need = apply_explicit_turn(
        phone_need,
        profile=durable,
        user_text="改看笔记本电脑",
        message_id=2,
        now=now + timedelta(minutes=5),
    )

    assert laptop_need is not None
    assert laptop_need["category"] == "笔记本电脑"
    assert laptop_need["budget"] == {"min": None, "max": None}
    assert laptop_need["candidateProducts"] == []
    effective = effective_profile_from_need(durable, laptop_need)
    assert effective["category"] == "笔记本电脑"
    assert effective["budgetMax"] is None


def test_recent_comparison_candidates_expire_with_the_shopping_session():
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    need = {
        "activeUntil": (now + timedelta(hours=1)).isoformat(),
        "candidateProducts": [
            {"productId": "p1", "observedAt": now.isoformat()},
            {
                "productId": "expired",
                "observedAt": (now - timedelta(hours=25)).isoformat(),
            },
        ],
    }

    assert recent_candidate_ids(need, now=now) == ["p1"]
    assert recent_candidate_ids(need, now=now + timedelta(hours=2)) == []
