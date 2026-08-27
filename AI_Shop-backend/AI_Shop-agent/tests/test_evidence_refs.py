from unittest.mock import AsyncMock

import pytest

from app.services import mcp_tools_service
from app.services.evidence_refs import (
    negative_lookup_ref,
    order_refs,
    pending_action_ref,
    product_no_result_ref,
    product_refs,
    product_search_constraint_ref,
)
from app.services.product_search_query import build_product_query_scope
from app.services.tool_invoke_result import ToolInvokeResult, parse_tool_wire
from app.utils.biz_payload import ACTION_LABELS, build_product_payload


def test_product_refs_are_authoritative_identity_bound_and_skip_missing_ids():
    refs = product_refs(
        [
            {
                "product_id": "P1",
                "product_name": "耳机",
                "estimated_payable": "199.00",
                "total_stock": 3,
            },
            {"product_name": "没有稳定 ID 的模型文本"},
        ],
        request_id="req-1",
        captured="2026-08-24T00:00:00.000Z",
    )
    assert len(refs) == 1
    assert refs[0]["productId"] == "P1"
    assert refs[0]["price"] == 199
    assert refs[0]["stock"] == 3
    assert refs[0]["requestId"] == "req-1"


def test_product_refs_include_structured_property_claims():
    refs = product_refs(
        [
            {
                "product_id": "P1",
                "product_name": "示例耳机",
                "brand": "索尼",
                "property_values": [{"propertyName": "颜色", "propertyValue": "黑色"}],
                "properties": [
                    {
                        "name": "降噪",
                        "propertyValues": [{"value": "支持主动降噪"}],
                    }
                ],
            }
        ],
        captured="2026-08-25T00:00:00.000Z",
    )

    claims = [
        item
        for item in refs[0]["claims"]
        if item.get("claimType") == "PRODUCT_PROPERTY"
    ]
    assert {(item["propertyName"], item["value"]) for item in claims} >= {
        ("品牌", "索尼"),
        ("颜色", "黑色"),
        ("降噪", "支持主动降噪"),
    }
    assert all(item["claimType"] == "PRODUCT_PROPERTY" for item in claims)
    assert all(item["subjectId"] == "P1" for item in claims)


def test_product_refs_claim_offer_coupon_expiry_and_ranking_card_fields():
    product = {
        "product_id": "P1",
        "product_name": "示例手机",
        "offer_snapshot_id": "offer-1",
        "sku_key": "sku-1",
        "base_price": 3999,
        "estimated_payable": 3799,
        "total_stock": 8,
        "status": 1,
        "in_stock": True,
        "coupon_status": "AVAILABLE",
        "coupon": {
            "couponName": "新客券",
            "estimatedDiscount": 200,
            "validEndTime": "2026-09-01T00:00:00Z",
        },
        "quote_expires_at": "2026-08-25T12:00:00Z",
        "ranking_decision_id": "ranking-1",
        "position": 1,
        "ranking": {"utilityScore": 0.87, "policyVersion": "shopping-decision/v2"},
    }

    cards, _ids = build_product_payload([product])
    assert "quoteExpiresAt" in cards and "utilityScore" in cards
    claims = product_refs([product], captured="2026-08-25T00:00:00.000Z")[0][
        "claims"
    ]
    paths = {claim["factPath"] for claim in claims if "factPath" in claim}
    assert paths >= {
        "product.productName",
        "offer.offerSnapshotId",
        "offer.skuKey",
        "offer.basePrice",
        "offer.estimatedPayable",
        "offer.couponStatus",
        "offer.coupon.couponName",
        "offer.coupon.estimatedDiscount",
        "offer.coupon.validEndTime",
        "offer.quoteExpiresAt",
        "ranking.rankingDecisionId",
        "ranking.position",
        "ranking.utilityScore",
        "ranking.policyVersion",
    }
    claims_by_path = {claim["factPath"]: claim for claim in claims if "factPath" in claim}
    assert claims_by_path["product.productName"]["sourceId"] == "P1"
    assert claims_by_path["offer.estimatedPayable"]["sourceId"] == "offer-1"
    assert claims_by_path["ranking.utilityScore"]["sourceId"] == "ranking-1"


def test_product_card_and_ref_use_the_same_out_of_stock_and_ranking_whitelist():
    product = {
        "product_id": "P1",
        "product_name": "缺货示例",
        "status": 1,
        "in_stock": False,
        "ranking": {"utilityScore": 0.5, "privateDebugScore": 99},
    }

    cards, _ids = build_product_payload([product])
    assert '"availability": "OUT_OF_STOCK"' in cards
    assert "privateDebugScore" not in cards
    ref = product_refs([product], captured="2026-08-25T00:00:00.000Z")[0]
    assert ref["availability"] == "OUT_OF_STOCK"
    ranking_paths = {
        claim["factPath"]
        for claim in ref["claims"]
        if claim.get("claimType") == "RANKING_DECISION_FACT"
    }
    assert "ranking.utilityScore" in ranking_paths
    assert "ranking.privateDebugScore" not in ranking_paths


def test_pending_action_ref_proves_persisted_proposal_without_exposing_credentials():
    pending = {
        "token": "act_" + "a" * 32,
        "userId": "user-secret",
        "actionType": "CREATE_SUPPORT_CASE",
        "businessKey": "user-secret:CREATE_SUPPORT_CASE:case-1",
        "argsFingerprint": "b" * 64,
        "status": 0,
        "statusName": "PENDING",
        "createTime": 1_777_777_777_000,
    }

    ref = pending_action_ref(pending, captured="2026-08-25T00:00:00.000Z")

    assert ref is not None
    assert ref["type"] == "action_proposal"
    assert ref["proposalPersisted"] is True
    assert ref["requiresUserConfirmation"] is True
    assert ref["effectExecuted"] is False
    assert pending["token"] not in str(ref)
    assert pending["userId"] not in str(ref)


def test_confirmation_risk_tips_do_not_assert_unverified_irreversible_outcomes():
    rendered = " ".join(value[2] for value in ACTION_LABELS.values())

    assert "无法发起退款" not in rendered
    assert "无法撤销" not in rendered
    assert "不可修改" not in rendered


def test_product_search_constraint_ref_is_scoped_to_returned_candidates():
    ref = product_search_constraint_ref(
        {
            "excludedBrands": ["苹果"],
            "excludedTerms": ["户外"],
            "returnedCandidateCount": 1,
            "returnedCandidatesSatisfyExclusions": True,
        },
        request_id="req-constraints",
        captured="2026-08-25T00:00:00.000Z",
    )

    assert ref is not None
    assert ref["type"] == "product_search_constraint"
    assert ref["returnedCandidatesSatisfyExclusions"] is True
    assert ref["catalogAbsenceClaim"] is False


def test_product_search_constraint_ref_persists_required_qualifier_audit():
    ref = product_search_constraint_ref(
        {
            "requiredQualifierIds": ["android-operating-system"],
            "returnedCandidateCount": 1,
            "unverifiedRequiredQualifierProductIds": ["P1"],
            "unverifiedRequiredQualifierCandidateCount": 1,
            "returnedCandidatesSatisfyRequiredQualifiers": False,
        },
        request_id="req-android",
        captured="2026-08-25T00:00:00.000Z",
    )

    assert ref is not None
    assert ref["requiredQualifierIds"] == ["android-operating-system"]
    assert ref["unverifiedRequiredQualifierProductIds"] == ["P1"]
    assert ref["unverifiedRequiredQualifierCandidateCount"] == 1
    assert ref["returnedCandidatesSatisfyRequiredQualifiers"] is False
    assert ref["requiredQualifierEvidenceSource"] == "JAVA_PRODUCT_SNAPSHOT"


def test_negative_refs_distinguish_no_match_from_provider_uncertainty():
    ref = product_no_result_ref(
        "索尼耳机",
        result_source="constraint_miss",
        authoritative=True,
        request_id="req-2",
        captured="2026-08-24T00:00:00.000Z",
    )
    assert ref["matched"] is False
    assert ref["authoritative"] is True
    uncertain = negative_lookup_ref(
        "order",
        query={"orderId": "O1"},
        source="JAVA_ORDER_SERVICE",
        authoritative=False,
    )
    assert uncertain["authoritative"] is False


def test_no_result_ref_binds_bounded_query_scope_and_never_claims_catalog_absence():
    scope = build_product_query_scope("我想买索尼 WH-1000XM6，预算 2000 元")

    ref = product_no_result_ref(
        "索尼",
        result_source="constraint_miss",
        query_scope=scope,
        captured="2026-08-26T00:00:00.000Z",
    )

    assert ref["query"] == "索尼"
    assert ref["queryScope"]["requestedModels"] == ["WH-1000XM6"]
    assert ref["queryScope"]["modelTokens"] == ["wh1000xm6"]
    assert ref["catalogAbsenceClaim"] is False
    assert "我想买索尼" not in ref["queryScope"]


def test_no_result_ref_drops_untrusted_query_scope_fields():
    ref = product_no_result_ref(
        "索尼",
        result_source="constraint_miss",
        query_scope={
            "schemaVersion": "product-query-scope/v1",
            "querySha256": "0" * 64,
            "queryChars": 4,
            "requestedModels": ["WH-1000XM6"],
            "modelTokens": ["wrong-token"],
            "budgetMin": None,
            "budgetMax": 2000,
            "mustTerms": [],
            "mustNotTerms": [],
            "comparisonTargets": [],
            "comparisonRequired": False,
            "untrustedAnswer": "平台无货",
        },
    )

    assert "queryScope" not in ref


def test_order_refs_keep_only_authoritative_order_identity():
    refs = order_refs(
        [{"order_id": "O1", "order_status": 2, "items": [{"order_item_id": "I1"}]}]
    )
    assert refs[0]["orderId"] == "O1"
    assert refs[0]["orderItemIds"] == ["I1"]


def test_tool_result_keeps_legacy_string_compatibility_and_wire_refs():
    result = ToolInvokeResult(
        content="订单不存在",
        source_refs=[{"type": "order", "matched": False}],
    )
    assert str(result) == "订单不存在"
    assert "不存在" in result
    parsed = parse_tool_wire(result.to_wire())
    assert parsed.content == result.content
    assert parsed.source_refs == result.source_refs


def test_tool_result_keeps_ref_only_negative_evidence_in_business_payload():
    result = ToolInvokeResult(
        content="没有找到订单",
        biz_type="query_order",
        source_refs=[{"type": "order", "matched": False, "authoritative": True}],
    )
    assert result.to_biz_dict() == {
        "productIds": [],
        "productNames": [],
        "orderIds": [],
        "sourceRefs": result.source_refs,
    }


def test_search_constraint_evidence_audits_returned_candidates_only():
    evidence = mcp_tools_service._search_constraint_evidence(
        [{"product_id": "P1", "product_name": "基础款外套"}],
        {"excludedTerms": ["户外款"], "excludedBrands": ["Acme"]},
    )
    assert evidence["returnedCandidatesSatisfyExclusions"] is True
    assert evidence["returnedCandidateCount"] == 1
    assert evidence["violatingReturnedProductIds"] == []
    assert evidence["catalogAbsenceClaim"] is False


def test_search_constraint_evidence_rejects_unverified_required_qualifier():
    evidence = mcp_tools_service._search_constraint_evidence(
        [{"product_id": "P1", "product_name": "三星智能手机"}],
        {},
        constraint_query="不要苹果，推荐安卓手机",
    )

    assert evidence["requiredQualifierIds"] == ["android-operating-system"]
    assert evidence["unverifiedRequiredQualifierProductIds"] == ["P1"]
    assert evidence["unverifiedRequiredQualifierCandidateCount"] == 1
    assert evidence["returnedCandidatesSatisfyRequiredQualifiers"] is False
    assert evidence["violatingReturnedProductIds"] == ["P1"]


def test_search_constraint_evidence_treats_empty_result_as_qualifier_compliant():
    evidence = mcp_tools_service._search_constraint_evidence(
        [],
        {},
        constraint_query="推荐安卓手机",
    )

    assert evidence["requiredQualifierIds"] == ["android-operating-system"]
    assert evidence["unverifiedRequiredQualifierCandidateCount"] == 0
    assert evidence["returnedCandidatesSatisfyRequiredQualifiers"] is True
    assert evidence["violatingReturnedProductIds"] == []


def test_search_constraint_evidence_accepts_snapshot_backed_required_qualifier():
    evidence = mcp_tools_service._search_constraint_evidence(
        [
            {
                "product_id": "P1",
                "product_name": "三星智能手机",
                "property_values": [
                    {"propertyName": "操作系统", "propertyValue": "Android 16"}
                ],
            }
        ],
        {},
        constraint_query="推荐安卓手机",
    )

    assert evidence["requiredQualifierIds"] == ["android-operating-system"]
    assert evidence["unverifiedRequiredQualifierProductIds"] == []
    assert evidence["returnedCandidatesSatisfyRequiredQualifiers"] is True
    assert evidence["violatingReturnedProductIds"] == []


@pytest.mark.asyncio
async def test_logistics_without_records_does_not_reference_uninitialized_latest_address(monkeypatch):
    monkeypatch.setattr(
        mcp_tools_service.java_internal_client,
        "get_logistics",
        AsyncMock(
            return_value={
                "logistics_company": "测试快递",
                "logistics_no": "T1",
                "logistics_status": 2,
                "record_list": [],
            }
        ),
    )
    result = await mcp_tools_service.query_logistics("U1", "O1")
    assert result.success is True
    assert result.source_refs[0]["recordCount"] == 0
    assert result.source_refs[0]["latestLocation"] is None


@pytest.mark.asyncio
async def test_search_tool_persists_constraint_audit_within_source_ref_bound(monkeypatch):
    products = [
        {"product_id": f"P{index}", "product_name": f"示例耳机 {index}"}
        for index in range(31)
    ]

    class Contract:
        def model_dump(self, **_kwargs):
            return {"status": "COMPLETED"}

    monkeypatch.setattr(
        "app.services.redis_service.redis_service.get_consult_product",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.redis_service.redis_service.is_consult_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.services.product_service.product_service.search_products",
        AsyncMock(return_value=("[]", {}, "product_search", products, "gateway")),
    )
    monkeypatch.setattr(
        "app.services.product_service.format_search_tool_message",
        lambda *_args, **_kwargs: "已返回受约束的候选。",
    )
    monkeypatch.setattr(
        "app.services.shopping_mission_service.shopping_mission_service.load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.shopping_profile_service.shopping_profile_service.get_effective_profile",
        AsyncMock(return_value={"excludedTerms": ["户外"]}),
    )
    monkeypatch.setattr(
        "app.services.recommendation_contract_service.build_response",
        lambda *_args, **_kwargs: Contract(),
    )

    result = await mcp_tools_service.tool_search_products(
        "U1",
        "降噪耳机",
        request_id="req-constraints",
        run_id="run-constraints",
    )

    assert len(result.source_refs) == 30
    assert result.source_refs[-1]["type"] == "product_search_constraint"
    assert result.source_refs[-1]["excludedTerms"] == ["户外"]
    assert result.source_refs[-1]["returnedCandidateCount"] == 31
    assert result.source_refs[-1]["catalogAbsenceClaim"] is False


@pytest.mark.asyncio
async def test_search_tool_uses_trusted_turn_for_hard_constraints(monkeypatch):
    search = AsyncMock(return_value=("[]", {}, "product_search", [], "gateway"))
    monkeypatch.setattr(
        "app.services.redis_service.redis_service.get_consult_product",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.redis_service.redis_service.is_consult_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.services.product_service.product_service.search_products", search
    )
    monkeypatch.setattr(
        "app.services.product_service.format_search_tool_message",
        lambda *_args, **_kwargs: "未找到商品。",
    )
    monkeypatch.setattr(
        "app.services.shopping_mission_service.shopping_mission_service.load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.shopping_profile_service.shopping_profile_service.get_effective_profile",
        AsyncMock(return_value={}),
    )
    await mcp_tools_service.tool_search_products(
        "U1",
        "手机",
        request_id="req-android",
        run_id="run-android",
        trusted_user_text="不要苹果，推荐安卓手机",
    )

    assert search.await_args.args[:2] == ("U1", "手机")
    assert search.await_args.kwargs["user_text"] == "不要苹果，推荐安卓手机"


@pytest.mark.asyncio
async def test_search_tool_projects_trusted_model_scope_into_message_and_evidence(monkeypatch):
    query = "我想买索尼 WH-1000XM6，预算 2000 元"
    search = AsyncMock(return_value=("[]", {}, "product_search", [], "constraint_miss"))
    captured: dict = {}
    captured_trace: dict = {}

    def formatter(*_args, **kwargs):
        captured.update(kwargs)
        return "【筛选结果】本次检索暂未返回同时满足你的条件（型号:WH-1000XM6），不能据此断言平台无货。"

    class Contract:
        def model_dump(self, **_kwargs):
            return {"status": "NO_RESULT"}

    def build_contract(*_args, **kwargs):
        captured_trace.update(kwargs.get("trace") or {})
        return Contract()

    monkeypatch.setattr(
        "app.services.redis_service.redis_service.get_consult_product",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.redis_service.redis_service.is_consult_active",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr("app.services.product_service.product_service.search_products", search)
    monkeypatch.setattr("app.services.product_service.format_search_tool_message", formatter)
    monkeypatch.setattr(
        "app.services.shopping_mission_service.shopping_mission_service.load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.shopping_profile_service.shopping_profile_service.get_effective_profile",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "app.services.recommendation_contract_service.build_response",
        build_contract,
    )

    result = await mcp_tools_service.tool_search_products(
        "U1",
        "索尼",
        request_id="req-model-scope",
        run_id="run-model-scope",
        trusted_user_text=query,
    )

    assert captured["constraint_query"] == query
    assert result.source_refs[0]["queryScope"]["requestedModels"] == ["WH-1000XM6"]
    assert result.source_refs[0]["catalogAbsenceClaim"] is False
    assert captured_trace["queryScope"]["modelTokens"] == [
        "wh1000xm6"
    ]
