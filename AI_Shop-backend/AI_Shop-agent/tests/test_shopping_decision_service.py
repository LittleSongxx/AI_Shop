from app.services.shopping_decision_service import ShoppingDecisionService


def test_hard_filter_resolves_brand_from_verified_product_name():
    mission = {
        "hardConstraints": {"requiredBrands": ["索尼"]},
        "softPreferences": {"brands": ["索尼"]},
        "exclusions": {"brands": []},
    }
    products = [
        {
            "product_id": "sony-headphones",
            "product_name": "索尼（SONY）头戴式无线降噪耳机",
            "status": 1,
            "in_stock": True,
            "estimated_payable": 1999,
            "offer_snapshot_id": "offer-1",
            "quote_expires_at": "2999-01-01T00:00:00+00:00",
        },
        {
            "product_id": "other-headphones",
            "product_name": "其他品牌无线耳机",
            "status": 1,
            "in_stock": True,
            "estimated_payable": 999,
            "offer_snapshot_id": "offer-2",
            "quote_expires_at": "2999-01-01T00:00:00+00:00",
        },
    ]

    eligible, rejected = ShoppingDecisionService()._hard_filter(products, mission)

    assert [row["product_id"] for row in eligible] == ["sony-headphones"]
    assert eligible[0]["brand"] == "索尼"
    assert rejected == [
        {"productId": "other-headphones", "reason": "BRAND_REQUIRED"}
    ]


def test_current_mission_hard_constraints_override_negative_profile_signal():
    mission = {
        "category": "耳机",
        "useCases": [],
        "hardConstraints": {
            "budgetMax": 2500,
            "requiredBrands": ["索尼"],
        },
        "softPreferences": {"brands": ["索尼"], "features": []},
        "exclusions": {"brands": []},
        "personalization": {
            "enabled": True,
            "implicitSignals": [
                {
                    "kind": "negativeProduct",
                    "value": "sony-headphones",
                    "effectiveWeight": 1.0,
                    "source": "SUPPORT_CONTACT",
                },
                {
                    "kind": "product",
                    "value": "other-headphones",
                    "effectiveWeight": 1.0,
                    "source": "REPEAT_PURCHASE",
                },
            ],
        },
    }
    products = [
        {
            "product_id": "sony-headphones",
            "product_name": "索尼无线降噪耳机",
            "status": "1",
            "in_stock": True,
            "estimated_payable": 1999,
            "offer_snapshot_id": "offer-1",
            "quote_expires_at": "2999-01-01T00:00:00+00:00",
        },
        {
            "product_id": "other-headphones",
            "product_name": "其他品牌无线耳机",
            "status": "1",
            "in_stock": True,
            "estimated_payable": 999,
            "offer_snapshot_id": "offer-2",
            "quote_expires_at": "2999-01-01T00:00:00+00:00",
        },
    ]

    eligible, rejected = ShoppingDecisionService()._hard_filter(products, mission)
    ranked = ShoppingDecisionService()._rank(eligible, mission)

    assert [row["product_id"] for row in ranked] == ["sony-headphones"]
    assert ranked[0]["ranking"]["explicitPreferenceScore"] == 0.85
    assert rejected == [
        {"productId": "other-headphones", "reason": "BRAND_REQUIRED"}
    ]
