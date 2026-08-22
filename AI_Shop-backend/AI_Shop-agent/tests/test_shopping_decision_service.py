from app.services.shopping_decision_service import ShoppingDecisionService, _category_matches


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


def _offer_product(product_id: str, *, category: str = "耳机", name: str | None = None) -> dict:
    return {
        "product_id": product_id,
        "product_name": name or f"{category}商品{product_id}",
        "categoryName": category,
        "status": "1",
        "in_stock": True,
        "estimated_payable": 999,
        "offer_snapshot_id": f"offer-{product_id}",
        "quote_expires_at": "2999-01-01T00:00:00+00:00",
    }


def test_hard_filter_applies_excluded_terms_and_category_to_every_recall_source():
    mission = {
        "category": "耳机",
        "hardConstraints": {},
        "softPreferences": {},
        "exclusions": {"terms": ["入耳"], "brands": []},
    }
    eligible, rejected = ShoppingDecisionService()._hard_filter(
        [
            _offer_product("over-term", name="无线入耳式耳机"),
            _offer_product("over-category", category="手机", name="无线手机"),
            _offer_product("ok", name="头戴式无线耳机"),
        ],
        mission,
    )

    assert [row["product_id"] for row in eligible] == ["ok"]
    assert rejected == [
        {"productId": "over-term", "reason": "TERM_EXCLUDED"},
        {"productId": "over-category", "reason": "CATEGORY_REQUIRED"},
    ]


def test_hard_filter_checks_exclusion_against_selected_sku_evidence():
    mission = {
        "category": "乐器",
        "hardConstraints": {},
        "softPreferences": {},
        "exclusions": {"terms": ["电箱"], "brands": []},
    }
    acoustic = _offer_product(
        "acoustic", category="乐器", name="YAMAHA 原声弹唱电箱入门吉他"
    )
    acoustic.update(
        {
            "sku_key": "sku-acoustic",
            "property_values": [
                {
                    "property_value_id": "1001",
                    "property_name": "规格",
                    "property_value": "41英寸原声款",
                }
            ],
            "skus": [
                {
                    "property_value_id_hash": "sku-acoustic",
                    "property_value_ids": "1001",
                }
            ],
        }
    )
    electric = _offer_product(
        "electric", category="乐器", name="YAMAHA 民谣电箱吉他"
    )
    electric.update(
        {
            "sku_key": "sku-electric",
            "property_values": [
                {
                    "property_value_id": "1002",
                    "property_name": "规格",
                    "property_value": "41英寸电箱款",
                }
            ],
            "skus": [
                {
                    "property_value_id_hash": "sku-electric",
                    "property_value_ids": "1002",
                }
            ],
        }
    )

    eligible, rejected = ShoppingDecisionService()._hard_filter(
        [acoustic, electric], mission
    )

    assert [row["product_id"] for row in eligible] == ["acoustic"]
    assert rejected == [{"productId": "electric", "reason": "TERM_EXCLUDED"}]


def test_broad_instrument_category_accepts_leaf_product_title_when_id_is_numeric():
    product = {
        "product_id": "guitar-1",
        "product_name": "雅马哈 FG800 初学者民谣吉他",
        "category_id": "20028",
    }

    assert _category_matches(product, "乐器") is True
    assert _category_matches({"product_name": "无线降噪耳机", "category_id": "20002"}, "乐器") is False


def test_rank_reports_real_slate_diversity_instead_of_recall_rank_prior():
    mission = {
        "category": "耳机",
        "hardConstraints": {},
        "softPreferences": {},
        "exclusions": {},
    }
    ranked = ShoppingDecisionService()._rank(
        [
            _offer_product("a", name="头戴耳机 A"),
            _offer_product("b", name="头戴耳机 B"),
            _offer_product("c", category="音箱", name="桌面音箱 C"),
        ],
        mission,
    )

    assert ranked[0]["ranking"]["diversityScore"] == 1.0
    assert ranked[1]["product_id"] == "c"
    assert ranked[1]["ranking"]["diversityScore"] == 1.0
    assert ranked[-1]["ranking"]["diversityScore"] == 0.0
