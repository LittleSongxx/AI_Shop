from unittest.mock import AsyncMock

import pytest

from app.services import mcp_tools_service
from app.services.evidence_refs import (
    negative_lookup_ref,
    order_refs,
    product_no_result_ref,
    product_refs,
)
from app.services.tool_invoke_result import ToolInvokeResult, parse_tool_wire


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
