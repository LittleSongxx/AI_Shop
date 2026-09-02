import pytest

from app.constants import ORDER_STATUS_NAMES
from app.rag.fact_metadata import get_fact_metadata_catalog
from app.rag.prompt_builder import (
    build_grounding_prompt,
    canonical_claim_clauses,
    deterministic_explicit_fact_fallback,
    deterministic_grounding_policy_fallback,
    deterministic_policy_evidence_fallback,
    grounding_repair_reason,
)
from app.services.evidence_refs import (
    action_capability_ref,
    after_sales_eligibility_ref,
    order_card_fields_with_claims,
    order_refs,
    product_refs,
)
from app.services.response_verifier import response_verifier


def _order_evidence(
    order_id: str = "SM1",
    *,
    status: int = 2,
    status_name: str = "已发货",
) -> list[dict]:
    return order_refs(
        [
            {
                "order_id": order_id,
                "order_status": status,
                "order_status_name": status_name,
                "order_time": "2026-08-25 00:00:00",
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )


def _detailed_order_evidence(order_id: str = "O1") -> list[dict]:
    return order_refs(
        [
            {
                "order_id": order_id,
                "order_status": 2,
                "order_status_name": "已发货",
                "amount": 128.5,
                "pay_scene": "微信支付",
                "items": [
                    {
                        "order_id": order_id,
                        "order_item_id": "I1",
                        "product_name": "示例耳机",
                        "buy_count": 2,
                        "item_amount": 128.5,
                    }
                ],
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )


def _action_evidence(
    decision: str,
    *,
    action: str = "CANCEL_ORDER",
    order_id: str = "SM1",
    order_item_id: str | None = None,
) -> dict:
    ref = action_capability_ref(
        {
            "decision": decision,
            "action": action,
            "orderId": order_id,
            "orderItemId": order_item_id,
            "capabilityVersion": "order-action-capability/v1",
            "evaluatedAt": "2026-08-25T00:00:00+00:00",
        }
    )
    assert ref is not None
    return ref


def _after_sales_evidence(
    decision: str,
    *,
    action: str = "REFUND",
    order_id: str = "SM1",
    order_item_id: str = "SMITEM1",
) -> dict:
    ref = after_sales_eligibility_ref(
        {
            "decision": decision,
            "decisionId": f"after-sales-{action.lower()}-{decision.lower()}",
            "action": action,
            "orderId": order_id,
            "orderItemId": order_item_id,
            "policyId": "refund-policy",
            "policyVersion": "v1",
            "evaluatedAt": "2026-08-25T00:00:00+00:00",
        }
    )
    assert ref is not None
    return ref


def test_dynamic_order_fact_requires_a_tool_or_resolved_order_reference():
    blocked = response_verifier.verify(
        assistant="订单已发货",
        biz_type="query_order",
        tools_called=[],
        source_refs=None,
        has_pending_action=False,
    )
    tool_only = response_verifier.verify(
        assistant="订单已发货",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs=None,
        has_pending_action=False,
    )

    assert blocked.passed is False
    assert blocked.issues[0].code == "DYNAMIC_FACT_WITHOUT_TOOL"
    assert tool_only.passed is False
    assert any(
        issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in tool_only.issues
    )


def test_dynamic_facts_never_accept_tool_names_without_business_authority():
    cases = (
        ("订单 O1 当前已发货。", "query_order", "QUERY_ORDERS"),
        ("订单 O1 的物流状态为已签收。", "query_logistics", "QUERY_LOGISTICS"),
        ("订单 O1 的退款状态为处理中。", "query_refund_status", "QUERY_REFUND_STATUS"),
        ("商品库存剩余 5 件。", "product_search", "SEARCH_PRODUCTS"),
        ("商品价格为 99.00 元。", "product_search", "SEARCH_PRODUCTS"),
        ("优惠券状态为可用。", "query_user_coupons", "QUERY_USER_COUPONS"),
        ("工单 CASE1 状态为处理中。", "support_case_detail", "QUERY_SUPPORT_CASES"),
    )

    for assistant, biz_type, tool in cases:
        for source_refs in (None, {"businessSources": []}):
            result = response_verifier.verify(
                assistant=assistant,
                biz_type=biz_type,
                tools_called=[tool],
                source_refs=source_refs,
                has_pending_action=False,
            )
            assert result.passed is False, (assistant, source_refs)
            assert any(
                issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM"
                for issue in result.issues
            )


def test_dynamic_facts_accept_only_matching_java_business_refs_and_values():
    product = product_refs(
        [
            {
                "product_id": "P1",
                "product_name": "示例商品",
                "min_price": 99,
                "total_stock": 5,
                "status": 1,
                "in_stock": True,
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )
    cases = (
        (
            "订单 O1 当前已发货。",
            "query_order",
            "QUERY_ORDERS",
            _detailed_order_evidence(),
        ),
        (
            "订单 O1 的物流状态为已签收。",
            "query_logistics",
            "QUERY_LOGISTICS",
            [
                {
                    "type": "logistics",
                    "id": "O1",
                    "orderId": "O1",
                    "matched": True,
                    "authoritative": True,
                    "status": "已签收",
                    "source": "JAVA_LOGISTICS_SERVICE",
                }
            ],
        ),
        (
            "订单 O1 的退款状态为处理中，退款金额为 32.50 元。",
            "query_refund_status",
            "QUERY_REFUND_STATUS",
            [
                {
                    "type": "refund",
                    "id": "R1",
                    "orderId": "O1",
                    "matched": True,
                    "authoritative": True,
                    "refundStatus": "处理中",
                    "refundAmount": 32.5,
                    "source": "JAVA_REFUND_SERVICE",
                }
            ],
        ),
        ("商品库存剩余 5 件。", "product_search", "SEARCH_PRODUCTS", product),
        ("商品价格为 99.00 元。", "product_search", "SEARCH_PRODUCTS", product),
        (
            "优惠券状态为可用。",
            "query_user_coupons",
            "QUERY_USER_COUPONS",
            [
                {
                    "type": "coupon",
                    "id": "C1",
                    "matched": True,
                    "authoritative": True,
                    "status": 0,
                    "source": "JAVA_COUPON_SERVICE",
                }
            ],
        ),
        (
            "工单 CASE1 状态为处理中。",
            "support_case_detail",
            "QUERY_SUPPORT_CASES",
            [
                {
                    "type": "support_case",
                    "id": "CASE1",
                    "matched": True,
                    "authoritative": True,
                    "status": "处理中",
                    "source": "JAVA_SUPPORT_CASE_SERVICE",
                }
            ],
        ),
    )

    for assistant, biz_type, tool, refs in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type=biz_type,
            tools_called=[tool],
            source_refs={"businessSources": refs},
            order_resolution="RESOLVED" if biz_type == "query_order" else None,
            has_pending_action=False,
        )
        assert result.passed is True, (assistant, result.issues)


def test_dynamic_fact_rejects_matched_false_wrong_source_and_wrong_value():
    base = {
        "type": "logistics",
        "id": "O1",
        "orderId": "O1",
        "matched": True,
        "authoritative": True,
        "status": "已签收",
        "source": "JAVA_LOGISTICS_SERVICE",
    }
    for ref in (
        {**base, "matched": False},
        {**base, "source": "RAG"},
        {**base, "status": "运输中"},
        {**base, "authoritative": False},
    ):
        result = response_verifier.verify(
            assistant="订单 O1 的物流状态为已签收。",
            biz_type="query_logistics",
            tools_called=["QUERY_LOGISTICS"],
            source_refs={"businessSources": [ref]},
            has_pending_action=False,
        )
        assert result.passed is False, ref


def test_catalog_no_result_disclaimer_is_not_an_inventory_assertion():
    result = response_verifier.verify(
        assistant=(
            "本次检索暂未返回同时满足条件的商品，"
            "不能据此断言平台无货。"
        ),
        biz_type="shopping_decision_v2",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={
            "businessSources": [
                {
                    "type": "product",
                    "source": "JAVA_GATEWAY",
                    "matched": False,
                    "authoritative": True,
                }
            ]
        },
        has_pending_action=False,
    )

    assert result.passed is True


def test_every_java_order_status_and_common_negative_alias_need_an_order_ref():
    for status in (*ORDER_STATUS_NAMES.values(), "尚未发货", "未付款"):
        result = response_verifier.verify(
            assistant=f"订单 O1 当前{status}。",
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, status


def test_unlisted_dynamic_statuses_and_cross_object_refs_fail_closed():
    cases = (
        (
            "订单 O1 的物流已取消。",
            "query_logistics",
            "QUERY_LOGISTICS",
            {
                "type": "logistics",
                "id": "O1",
                "orderId": "O1",
                "status": "已签收",
                "source": "JAVA_LOGISTICS_SERVICE",
            },
        ),
        (
            "订单 O1 的退款待审核。",
            "query_refund_status",
            "QUERY_REFUND_STATUS",
            {
                "type": "refund",
                "id": "R1",
                "orderId": "O1",
                "refundStatus": "处理中",
                "source": "JAVA_REFUND_SERVICE",
            },
        ),
        (
            "优惠券冻结。",
            "query_user_coupons",
            "QUERY_USER_COUPONS",
            {
                "type": "coupon",
                "id": "C1",
                "status": 0,
                "source": "JAVA_COUPON_SERVICE",
            },
        ),
        (
            "工单 CASE2 已驳回。",
            "support_case_detail",
            "QUERY_SUPPORT_CASES",
            {
                "type": "support_case",
                "id": "CASE1",
                "caseNo": "CASE1",
                "status": "处理中",
                "source": "JAVA_SUPPORT_CASE_SERVICE",
            },
        ),
    )
    for assistant, biz_type, tool, ref in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type=biz_type,
            tools_called=[tool],
            source_refs={"businessSources": [ref]},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    product = product_refs(
        [{"product_id": "P1", "min_price": 99, "status": 1}],
        captured="2026-08-25T00:00:00+00:00",
    )
    wrong_product = response_verifier.verify(
        assistant="商品 P2 价格为 99 元。",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={"businessSources": product},
        has_pending_action=False,
    )
    assert wrong_product.passed is False


def test_explicit_unknown_status_values_are_compared_not_just_detected():
    cases = (
        (
            "物流状态为延误。",
            {"type": "logistics", "id": "O1", "orderId": "O1", "status": "已签收", "source": "JAVA_LOGISTICS_SERVICE"},
        ),
        (
            "退款状态为审核拒绝。",
            {"type": "refund", "id": "R1", "orderId": "O1", "refundStatus": "COMPLETED", "source": "JAVA_REFUND_SERVICE"},
        ),
        (
            "优惠券状态为暂停。",
            {"type": "coupon", "id": "C1", "status": 0, "source": "JAVA_COUPON_SERVICE"},
        ),
        (
            "工单 CASE1 状态为搁置。",
            {"type": "support_case", "id": "CASE1", "caseNo": "CASE1", "status": "处理中", "source": "JAVA_SUPPORT_CASE_SERVICE"},
        ),
        (
            "评价状态为隐藏。",
            {"type": "comment", "id": "O1", "orderId": "O1", "commentStatus": "已评价", "source": "JAVA_COMMENT_SERVICE"},
        ),
    )
    for assistant, ref in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=["QUERY_LOGISTICS"],
            source_refs={"businessSources": [ref]},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

def test_connected_dynamic_statuses_compare_every_value():
    cases = (
        (
            "优惠券 C1 可用且已过期。",
            {"type": "coupon", "id": "C1", "status": 0, "source": "JAVA_COUPON_SERVICE"},
        ),
        (
            "工单 CASE1 处理中且已关闭。",
            {"type": "support_case", "id": "CASE1", "caseNo": "CASE1", "status": "处理中", "source": "JAVA_SUPPORT_CASE_SERVICE"},
        ),
        (
            "订单 O1 的退款处理中且成功。",
            {"type": "refund", "id": "R1", "orderId": "O1", "refundStatus": "STOCK_PENDING", "source": "JAVA_REFUND_SERVICE"},
        ),
        (
            "订单 O1 的评价已评价且已追评。",
            {"type": "comment", "id": "O1", "orderId": "O1", "commentStatus": "已评价", "source": "JAVA_COMMENT_SERVICE"},
        ),
        (
            "订单 O1 的物流已签收且运输中。",
            {"type": "logistics", "id": "O1", "orderId": "O1", "status": "已签收", "source": "JAVA_LOGISTICS_SERVICE"},
        ),
    )
    for assistant, ref in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": [ref]},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for connector in (
        "以及",
        "和",
        "并",
        "及",
        "与",
        "、",
        "同时",
        "又",
        "或",
        "或者",
        "也",
        "随后",
        "然后",
    ):
        for assistant, ref in cases:
            rewritten = assistant.replace("且", connector)
            result = response_verifier.verify(
                assistant=rewritten,
                biz_type="agent",
                tools_called=[],
                source_refs={"businessSources": [ref]},
                has_pending_action=False,
            )
            assert result.passed is False, (connector, rewritten)

    negated_cases = (
        ("优惠券 C1 可用{separator}可用。", cases[0][1]),
        ("工单 CASE1 处理中{separator}处理中。", cases[1][1]),
        ("订单 O1 的退款处理中{separator}处理中。", cases[2][1]),
        ("订单 O1 的评价已评价{separator}已评价。", cases[3][1]),
        ("订单 O1 的物流已签收{separator}已签收。", cases[4][1]),
    )
    for separator in ("而非", "并非"):
        for template, ref in negated_cases:
            assistant = template.format(separator=separator)
            result = response_verifier.verify(
                assistant=assistant,
                biz_type="agent",
                tools_called=[],
                source_refs={"businessSources": [ref]},
                has_pending_action=False,
            )
            assert result.passed is False, assistant

    consistent = response_verifier.verify(
        assistant="优惠券 C1 可用而非已过期。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": [cases[0][1]]},
        has_pending_action=False,
    )
    assert consistent.passed is True


def test_price_coupon_and_payment_values_bind_the_named_field_and_object():
    product = product_refs(
        [
            {
                "product_id": "P1",
                "product_name": "AirPods",
                "min_price": 99,
                "max_price": 199,
                "estimated_payable": 89,
                "status": 1,
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )
    for assistant in ("商品 P1 到手价为 99 元。", "商品 P1 最高价为 99 元。"):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="product_search",
            tools_called=["SEARCH_PRODUCTS"],
            source_refs={"businessSources": product},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    named_product = response_verifier.verify(
        assistant="商品 AirPods 价格为 89 元。",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={"businessSources": product},
        has_pending_action=False,
    )
    assert named_product.passed is True

    coupon = {
        "type": "coupon",
        "id": "A",
        "couponId": "A",
        "status": 0,
        "validEndTime": "2026-09-01 00:00:00",
        "source": "JAVA_COUPON_SERVICE",
    }
    for assistant in (
        "券B可用。",
        "券A到期时间为 2099-01-01。",
        "券A面额为 20 元。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="query_user_coupons",
            tools_called=["QUERY_USER_COUPONS"],
            source_refs={"businessSources": [coupon]},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for connector in ("且", "并且", "同时", "而且", "又"):
        result = response_verifier.verify(
            assistant=f"订单 O1 可以取消{connector}确认收货。",
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [_action_evidence("ALLOWED", order_id="O1")]
            },
            has_pending_action=False,
        )
        assert result.passed is False, connector

    payment = response_verifier.verify(
        assistant="订单 O1 使用微信支付和支付宝支付。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs={"businessSources": _detailed_order_evidence()},
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert payment.passed is False
    for status in (0, 1):
        unavailable = product_refs(
            [
                {
                    "product_id": "P1",
                    "total_stock": 5,
                    "status": status,
                    "in_stock": False,
                }
            ],
            captured="2026-08-25T00:00:00+00:00",
        )
        conflicting_stock = response_verifier.verify(
            assistant="商品 P1 库存有货。",
            biz_type="product_search",
            tools_called=["SEARCH_PRODUCTS"],
            source_refs={"businessSources": unavailable},
            has_pending_action=False,
        )
        assert conflicting_stock.passed is False, status


def test_real_refund_status_shape_and_additional_tool_only_facts():
    completed = response_verifier.verify(
        assistant="订单 O1 的退款已完成。",
        biz_type="query_refund_status",
        tools_called=["QUERY_REFUND_STATUS"],
        source_refs={
            "businessSources": [
                {
                    "type": "refund",
                    "id": "R1",
                    "orderId": "O1",
                    "refundStatus": "COMPLETED",
                    "source": "JAVA_REFUND_SERVICE",
                }
            ]
        },
        has_pending_action=False,
    )
    assert completed.passed is True

    for assistant, biz_type, tool in (
        ("订单 O1 下单时间为 2099-01-01。", "query_order", "QUERY_ORDERS"),
        ("优惠券面额为 20 元。", "query_user_coupons", "QUERY_USER_COUPONS"),
        ("快递是顺丰。", "query_logistics", "QUERY_LOGISTICS"),
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type=biz_type,
            tools_called=[tool],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, assistant


def test_resolved_dynamic_snapshot_does_not_require_policy_citation():
    result = response_verifier.verify(
        assistant=(
            "当前订单状态为“已付款,待发货”，商家尚未发货；"
            "如需催发货或进一步核查，可以回复“转人工”继续处理。"
        ),
        biz_type="query_order",
        tools_called=[],
        source_refs={
            "ragSources": [],
            "businessSources": _order_evidence(
                "O1", status=1, status_name="已付款,待发货"
            ),
        },
        rag_source_refs=[],
        order_resolution="RESOLVED",
        has_pending_action=False,
        policy_evidence_required=False,
    )
    assert result.passed is True


def test_no_eligible_status_and_denied_action_require_separate_evidence():
    business_sources = [
        *_order_evidence("O1", status=1, status_name="已付款,待发货"),
        _action_evidence("DENIED", order_id="O1"),
    ]
    result = response_verifier.verify(
        assistant="订单当前状态为‘已付款,待发货’，当前不能取消。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "ragSources": [],
            "businessSources": business_sources,
        },
        rag_source_refs=[],
        order_resolution="NO_ELIGIBLE",
        has_pending_action=False,
    )
    assert result.passed is True

    blocked = response_verifier.verify(
        assistant="请确认取消订单。",
        biz_type="action_confirm",
        tools_called=["PROPOSE_CANCEL_ORDER"],
        source_refs={
            "ragSources": [],
            "businessSources": business_sources,
        },
        rag_source_refs=[],
        order_resolution="NO_ELIGIBLE",
        has_pending_action=False,
    )
    assert blocked.passed is False
    assert blocked.issues[0].code == "WRITE_WITHOUT_PENDING_ACTION"


def test_plain_order_ref_without_dynamic_claim_is_not_authority():
    result = response_verifier.verify(
        assistant="订单 O1 当前已发货。",
        biz_type="query_order",
        tools_called=[],
        source_refs={
            "businessSources": [
                {
                    "type": "order",
                    "source": "JAVA_ORDER_SERVICE",
                    "orderId": "O1",
                    "matched": True,
                }
            ]
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert result.passed is False
    assert result.issues[0].code == "DYNAMIC_FACT_WITHOUT_TOOL"


def test_order_id_claim_does_not_authorize_an_unclaimed_status():
    result = response_verifier.verify(
        assistant="订单 O1 当前已发货。",
        biz_type="query_order",
        tools_called=[],
        source_refs={
            "businessSources": order_refs(
                [{"order_id": "O1"}],
                captured="2026-08-25T00:00:00+00:00",
            )
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert result.passed is False
    assert any(
        issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in result.issues
    )


def test_order_product_assertion_must_match_an_item_claim():
    evidence = order_refs(
        [
            {
                "order_id": "O1",
                "order_status": 2,
                "items": [
                    {
                        "order_item_id": "I1",
                        "order_id": "O1",
                        "product_name": "索尼 WH-1000XM6",
                        "property_info": "黑色",
                    }
                ],
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )
    blocked = response_verifier.verify(
        assistant="订单 O1 买的是苹果手机。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": evidence},
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    grounded = response_verifier.verify(
        assistant="订单 O1 买的是索尼 WH-1000XM6。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": evidence},
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert blocked.passed is False
    assert any(
        issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in blocked.issues
    )
    assert grounded.passed is True


def test_order_status_amount_quantity_and_payment_compare_claim_values():
    sources = {"businessSources": _detailed_order_evidence()}
    cases = (
        ("订单 O1 当前已发货。", "订单 O1 当前已完成。"),
        ("订单 O1 实付金额为 128.50 元。", "订单 O1 实付金额为 99.00 元。"),
        (
            "订单 O1 的订单项 I1 商品数量为 2 件。",
            "订单 O1 的订单项 I1 商品数量为 3 件。",
        ),
        ("订单 O1 的支付方式为微信支付。", "订单 O1 的支付方式为支付宝。"),
    )

    for matching, opposite in cases:
        accepted = response_verifier.verify(
            assistant=matching,
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs=sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        rejected = response_verifier.verify(
            assistant=opposite,
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs=sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert accepted.passed is True, (matching, accepted.issues)
        assert rejected.passed is False, opposite
        assert any(
            issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM"
            for issue in rejected.issues
        )

    natural_payment = response_verifier.verify(
        assistant="订单 O1 使用支付宝支付。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs=sources,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert natural_payment.passed is False


def test_order_dynamic_facts_check_every_clause_and_only_its_object():
    sources = {"businessSources": _detailed_order_evidence("O1")}
    for assistant in (
        "订单 O1 当前已发货；订单 O1 当前已完成。",
        "已核验订单 O1；订单 O2 当前已发货。",
        "订单 O1 当前已发货且订单 O2 当前已完成。",
        "订单 O1 当前已发货，随后已完成。",
        "订单 O1 当前已发货，O2 也已发货。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs=sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False, assistant

        assert any(
            issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in result.issues
        )

    refunded = response_verifier.verify(
        assistant="订单 O1 当前已退款，随后交易取消。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs={
            "businessSources": _order_evidence(
                "O1", status=6, status_name="已退款,交易关闭"
            )
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert refunded.passed is False


def test_order_total_and_item_values_cannot_cross_objects():
    evidence = order_refs(
        [
            {
                "order_id": "O1",
                "order_status": 2,
                "order_status_name": "已发货",
                "amount": 100,
                "items": [
                    {
                        "order_id": "O1",
                        "order_item_id": "I1",
                        "item_amount": 50,
                        "buy_count": 2,
                    },
                    {
                        "order_id": "O1",
                        "order_item_id": "I2",
                        "item_amount": 25,
                        "buy_count": 3,
                    },
                ],
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )
    for assistant in (
        "订单 O1 实付金额为 50 元。",
        "订单 O1 的订单项 I2 商品数量为 2 件。",
        "订单 O1 的订单项商品数量为 2 件。",
        "订单 O1 的订单项 I1 数量为 2 件，I2 数量为 2 件。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs={"businessSources": evidence},
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False, assistant


def test_later_clause_cannot_negate_the_verified_value():
    order_sources = {"businessSources": _detailed_order_evidence()}
    for assistant in (
        "订单 O1 实付金额为 128.50 元；但其实不是 128.50 元。",
        "订单 O1 的订单项 I1 数量为 2 件；但其实不是 2 件。",
        "订单 O1 支付方式为微信支付；但其实不是微信支付。",
        "订单 O1 实付金额为 128.50 元而非 128.50 元。",
        "订单 O1 的订单项 I1 数量为 2 件而非 2 件。",
        "订单 O1 支付方式为微信支付而非微信支付。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="query_order",
            tools_called=["QUERY_ORDERS"],
            source_refs=order_sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    consistent = response_verifier.verify(
        assistant="订单 O1 实付金额为 128.50 元，而非 99 元。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs=order_sources,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert consistent.passed is True

    product = product_refs(
        [{"product_id": "P1", "estimated_payable": 89, "status": 1}],
        captured="2026-08-25T00:00:00+00:00",
    )
    contradicted_price = response_verifier.verify(
        assistant="商品 P1 到手价为 89 元；但其实不是 89 元。",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={"businessSources": product},
        has_pending_action=False,
    )
    assert contradicted_price.passed is False

    coupon = {
        "type": "coupon",
        "id": "A",
        "couponId": "A",
        "status": 0,
        "discountAmount": 20,
        "validEndTime": "2026-09-01 00:00:00",
        "source": "JAVA_COUPON_SERVICE",
    }
    for assistant in (
        "券A面额为 20 元；但其实不是 20 元。",
        "券A到期时间为 2026-09-01；但其实不是 2026-09-01。",
        "券A可用；但其实不是可用。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="query_user_coupons",
            tools_called=["QUERY_USER_COUPONS"],
            source_refs={"businessSources": [coupon]},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    support = response_verifier.verify(
        assistant="工单 CASE1 处理中；但其实不是处理中。",
        biz_type="support_case_detail",
        tools_called=["QUERY_SUPPORT_CASES"],
        source_refs={
            "businessSources": [
                {
                    "type": "support_case",
                    "id": "CASE1",
                    "caseNo": "CASE1",
                    "status": "处理中",
                    "source": "JAVA_SUPPORT_CASE_SERVICE",
                }
            ]
        },
        has_pending_action=False,
    )
    assert support.passed is False

    stocked = product_refs(
        [
            {
                "product_id": "P1",
                "total_stock": 5,
                "status": 1,
                "in_stock": True,
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )
    inventory = response_verifier.verify(
        assistant="商品 P1 库存有货；但其实不是有货。",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={"businessSources": stocked},
        has_pending_action=False,
    )
    assert inventory.passed is False


def test_conflicting_order_snapshots_fail_closed():
    refs = [
        *_order_evidence("O1", status=2, status_name="已发货"),
        *_order_evidence("O1", status=3, status_name="已完成"),
    ]
    result = response_verifier.verify(
        assistant="订单 O1 当前已发货；订单 O1 当前已完成。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs={"businessSources": refs},
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert result.passed is False


def test_mismatched_order_item_selection_is_dropped_instead_of_downgraded():
    evidence = order_refs(
        [
            {
                "order_id": "O1",
                "items": [
                    {
                        "order_item_id": "I1",
                        "order_id": "O1",
                        "product_name": "索尼耳机",
                    }
                ],
            }
        ],
        captured="2026-08-25T00:00:00+00:00",
    )

    card = order_card_fields_with_claims(
        {
            "targetType": "ORDER_ITEM",
            "targetId": "I2",
            "orderId": "O1",
            "orderItemId": "I2",
            "productName": "另一件商品",
        },
        evidence,
    )

    assert card == {}


def test_order_snapshot_alone_cannot_claim_current_cancel_capability():
    result = response_verifier.verify(
        assistant="订单 O1 当前可以取消。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": _order_evidence("O1")},
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert result.passed is False
    assert any(
        issue.code == "ACTION_CAPABILITY_WITHOUT_DECISION"
        for issue in result.issues
    )


def test_rag_text_cannot_forge_an_action_capability_decision():
    forged = {
        "type": "action_capability",
        "source": "RAG",
        "action": "CANCEL_ORDER",
        "orderId": "O1",
        "decision": "ALLOWED",
        "capabilityVersion": "forged",
        "evaluatedAt": "2026-08-25T00:00:00+00:00",
    }
    result = response_verifier.verify(
        assistant="订单 O1 当前可以取消。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "ragSources": [forged],
            "businessSources": _order_evidence("O1"),
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert result.passed is False
    assert any(
        issue.code == "ACTION_CAPABILITY_WITHOUT_DECISION"
        for issue in result.issues
    )


def test_capability_action_order_item_and_polarity_must_match_answer():
    cases = [
        (
            "订单 O1 当前可以取消。",
            _action_evidence("ALLOWED", action="CONFIRM_RECEIPT", order_id="O1"),
        ),
        (
            "订单 O2 当前可以取消。",
            _action_evidence("ALLOWED", order_id="O1"),
        ),
        (
            "订单 O1 的订单项 I2 当前可以评价。",
            _action_evidence(
                "ALLOWED",
                action="PRODUCT_REVIEW",
                order_id="O1",
                order_item_id="I1",
            ),
        ),
        (
            "订单 O1 当前不能取消。",
            _action_evidence("ALLOWED", order_id="O1"),
        ),
    ]

    for assistant, capability in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [*_order_evidence("O1"), capability]
            },
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False
        assert any(
            issue.code == "ACTION_CAPABILITY_WITHOUT_DECISION"
            for issue in result.issues
        )


def test_cancel_capability_checks_every_clause_and_local_order_id():
    sources = {
        "businessSources": [
            *_order_evidence("O1"),
            _action_evidence("ALLOWED", order_id="O1"),
        ]
    }
    for assistant in (
        "订单 O1 当前可以取消；订单 O1 当前不能取消。",
        "订单 O1 当前可以取消，订单 O1 当前不能取消。",
        "已核验订单 O1；订单 O2 当前可以取消。",
        "订单 O1 当前可以取消且订单 O2 当前不能取消。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs=sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False, assistant
        assert any(
            issue.code == "ACTION_CAPABILITY_WITHOUT_DECISION"
            for issue in result.issues
        )


def test_conflicting_or_item_expanded_capability_decisions_fail_closed():
    conflicting = {
        "businessSources": [
            *_order_evidence("O1"),
            _action_evidence("ALLOWED", order_id="O1"),
            _action_evidence("DENIED", order_id="O1"),
        ]
    }
    result = response_verifier.verify(
        assistant="订单 O1 当前可以取消；订单 O1 当前不能取消。",
        biz_type="agent",
        tools_called=[],
        source_refs=conflicting,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert result.passed is False

    item_only = response_verifier.verify(
        assistant="订单 O1 当前可以评价。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [
                *_order_evidence("O1"),
                _action_evidence(
                    "ALLOWED",
                    action="PRODUCT_REVIEW",
                    order_id="O1",
                    order_item_id="I1",
                ),
            ]
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert item_only.passed is False


def test_capability_clause_polarity_and_id_variants_fail_closed():
    cases = (
        (
            "订单 O1 不能取消但可以取消。",
            [_action_evidence("DENIED", order_id="O1")],
        ),
        (
            "订单 O1 不能取消但可以确认收货。",
            [
                _action_evidence("DENIED", order_id="O1"),
                _action_evidence(
                    "DENIED", action="CONFIRM_RECEIPT", order_id="O1"
                ),
            ],
        ),
        (
            "订单为 O2 当前可以取消。",
            [_action_evidence("ALLOWED", order_id="O1")],
        ),
        (
            "订单 ID O2 当前可以取消。",
            [_action_evidence("ALLOWED", order_id="O1")],
        ),
        (
            "订单项 ID I1 当前可以评价。",
            [
                _action_evidence(
                    "ALLOWED",
                    action="PRODUCT_REVIEW",
                    order_id="O1",
                    order_item_id="I1",
                ),
                _action_evidence(
                    "ALLOWED",
                    action="PRODUCT_REVIEW",
                    order_id="O2",
                    order_item_id="I1",
                ),
            ],
        ),
        (
            "订单 O1 和 O2 当前可以取消。",
            [_action_evidence("ALLOWED", order_id="O1")],
        ),
        (
            "订单 O1 当前可以取消，O2 也可以取消。",
            [_action_evidence("ALLOWED", order_id="O1")],
        ),
        (
            "订单 O1 当前可以取消，O2也可以取消。",
            [_action_evidence("ALLOWED", order_id="O1")],
        ),
        (
            "订单项 I1 和 I2 当前可以评价。",
            [
                _action_evidence(
                    "ALLOWED",
                    action="PRODUCT_REVIEW",
                    order_id="O1",
                    order_item_id="I1",
                )
            ],
        ),
        (
            "订单项 I1 当前可以评价，I2也可以评价。",
            [
                _action_evidence(
                    "ALLOWED",
                    action="PRODUCT_REVIEW",
                    order_id="O1",
                    order_item_id="I1",
                )
            ],
        ),
    )
    for assistant, refs in cases:
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": refs},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for assistant in (
        "订单 O1 无法取消。",
        "订单 O1 不支持取消。",
        "订单 O1 能取消。",
        "订单 O1 禁止取消。",
        "订单 O1 不得取消。",
        "订单 O1 当前具备取消资格。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for assistant in (
        "订单 O1 需要人工复核才可以取消。",
        "订单 O1 并非可以取消。",
        "订单 O1 暂时无法取得资格决定但可以取消。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [_action_evidence("ALLOWED", order_id="O1")]
            },
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for assistant in (
        "订单 O1 不能取消或可以取消。",
        "订单 O1 既不能取消也可以取消。",
        "订单 O1 可以取消或者不能取消。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [_action_evidence("DENIED", order_id="O1")]
            },
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    unavailable_then_denied = response_verifier.verify(
        assistant="订单 O1 暂时无法取得资格决定；当前不能取消。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [_action_evidence("DENIED", order_id="O1")]
        },
        has_pending_action=False,
    )
    assert unavailable_then_denied.passed is False


def test_cancel_capability_synonyms_and_outer_negation_require_decision():
    for assistant in (
        "订单 O1 不具备取消资格。",
        "订单 O1 没有取消资格。",
        "订单 O1 不是可以取消。",
        "订单 O1 并不是可以取消。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [_action_evidence("ALLOWED", order_id="O1")]
            },
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    double_negative = response_verifier.verify(
        assistant="订单 O1 不是不能取消。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [_action_evidence("DENIED", order_id="O1")]
        },
        has_pending_action=False,
    )
    assert double_negative.passed is False

    for assistant in (
        "订单 O1 支持取消。",
        "订单 O1 有权取消。",
        "订单 O1 无权取消。",
        "订单 O1 有取消资格。",
        "订单 O1 没资格取消。",
        "订单 O1 符合取消条件。",
        "订单 O1 不符合取消条件。",
        "订单 O1 满足取消条件。",
        "订单 O1 不满足取消条件。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for assistant in (
        "平台支持取消订单。",
        "平台允许取消订单。",
        "平台禁止取消订单。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    rag_ref = {"id": "policy-cancel", "source": "policy"}
    case_specific = response_verifier.verify(
        assistant="您的订单可以取消。[1]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs={"ragSources": [rag_ref], "businessSources": []},
        rag_source_refs=[rag_ref],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    assert case_specific.passed is False


def test_after_sales_checks_every_clause_object_polarity_and_action():
    sources = {
        "businessSources": [
            *_order_evidence("O1"),
            _after_sales_evidence(
                "ELIGIBLE", order_id="O1", order_item_id="I1"
            ),
        ]
    }
    for assistant in (
        "订单 O1 的订单项 I1 当前可以退款；订单 O1 的订单项 I1 当前不能退款。",
        "已核验订单 O1；订单 O2 的订单项 I2 当前可以退款。",
        "订单 O1 的订单项 I1 当前可以退货。",
        "订单 O1 的订单项 I1 当前可以换货。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs=sources,
            order_resolution="RESOLVED",
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    return_sources = {
        "businessSources": [
            *_order_evidence("O1"),
            _after_sales_evidence(
                "ELIGIBLE",
                action="RETURN",
                order_id="O1",
                order_item_id="I1",
            ),
        ]
    }
    accepted = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 当前可以退货。",
        biz_type="agent",
        tools_called=[],
        source_refs=return_sources,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert accepted.passed is True


def test_after_sales_requires_each_named_action_and_rejects_conflicts():
    return_only = {
        "businessSources": [
            *_order_evidence("O1"),
            _after_sales_evidence(
                "ELIGIBLE",
                action="RETURN",
                order_id="O1",
                order_item_id="I1",
            ),
        ]
    }
    combined = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 当前可以退款或退货。",
        biz_type="agent",
        tools_called=[],
        source_refs=return_only,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert combined.passed is False

    conflicting = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 当前可以退款；随后又不能退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [
                *_order_evidence("O1"),
                _after_sales_evidence(
                    "ELIGIBLE", order_id="O1", order_item_id="I1"
                ),
                _after_sales_evidence(
                    "INELIGIBLE", order_id="O1", order_item_id="I1"
                ),
            ]
        },
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    assert conflicting.passed is False

    denied = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 不能退款但可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [
                _after_sales_evidence(
                    "INELIGIBLE", order_id="O1", order_item_id="I1"
                )
            ]
        },
        has_pending_action=False,
    )
    unsupported = response_verifier.verify(
        assistant="订单 O1 不支持退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": []},
        has_pending_action=False,
    )
    assert denied.passed is False
    assert unsupported.passed is False

    connected = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 不能退款或可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [
                _after_sales_evidence(
                    "INELIGIBLE", order_id="O1", order_item_id="I1"
                )
            ]
        },
        has_pending_action=False,
    )
    assert connected.passed is False

    for connector in ("且", "并且", "同时", "而且", "又"):
        result = response_verifier.verify(
            assistant=f"订单 O1 的订单项 I1 可以退款{connector}退货。",
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [
                    _after_sales_evidence(
                        "ELIGIBLE", order_id="O1", order_item_id="I1"
                    )
                ]
            },
            has_pending_action=False,
        )
        assert result.passed is False, connector

    for assistant in (
        "订单 O1 的订单项 I1 需要证据才可以退款。",
        "订单 O1 的订单项 I1 并非可以退款。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [
                    _after_sales_evidence(
                        "ELIGIBLE", order_id="O1", order_item_id="I1"
                    )
                ]
            },
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    for assistant in (
        "订单 O1 的订单项 I1 能退款。",
        "订单 O1 的订单项 I1 允许退款。",
        "订单 O1 的订单项 I1 禁止退款。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"businessSources": []},
            has_pending_action=False,
        )
        assert result.passed is False, assistant


def test_after_sales_synonyms_cannot_borrow_rag_or_opposite_decision():
    rag_ref = {"id": "policy-1", "source": "policy", "snippet": "售后规则"}
    for assistant in (
        "订单 O1 的订单项 I1 具备退款资格。[1]",
        "订单 O1 的订单项 I1 没有退款资格。[1]",
        "订单 O1 的订单项 I1 支持退款。[1]",
        "订单 O1 的订单项 I1 有资格退款。[1]",
        "订单 O1 的订单项 I1 不满足退款条件。[1]",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=["SEARCH_KNOWLEDGE"],
            source_refs={"ragSources": [rag_ref], "businessSources": []},
            rag_source_refs=[rag_ref],
            has_pending_action=False,
            rag_citation_required=True,
            rag_evidence_state="SUPPORTED",
        )
        assert result.passed is False, assistant

    for assistant in (
        "订单 O1 的订单项 I1 不是可以退款。",
        "订单 O1 的订单项 I1 没有退款资格。",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={
                "businessSources": [
                    _after_sales_evidence(
                        "ELIGIBLE", order_id="O1", order_item_id="I1"
                    )
                ]
            },
            has_pending_action=False,
        )
        assert result.passed is False, assistant

    double_negative = response_verifier.verify(
        assistant="订单 O1 的订单项 I1 不是不能退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={
            "businessSources": [
                _after_sales_evidence(
                    "INELIGIBLE", order_id="O1", order_item_id="I1"
                )
            ]
        },
        has_pending_action=False,
    )
    assert double_negative.passed is False

    for assistant in (
        "您的订单支持退款。[1]",
        "此订单支持退款。[1]",
        "本次支持退款。[1]",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=["SEARCH_KNOWLEDGE"],
            source_refs={"ragSources": [rag_ref], "businessSources": []},
            rag_source_refs=[rag_ref],
            has_pending_action=False,
            rag_citation_required=True,
            rag_evidence_state="SUPPORTED",
        )
        assert result.passed is False, assistant

    no_source = response_verifier.verify(
        assistant="平台支持退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": []},
        has_pending_action=False,
    )
    grounded = response_verifier.verify(
        assistant="平台支持退款。[1]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs={"ragSources": [rag_ref], "businessSources": []},
        rag_source_refs=[rag_ref],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    assert no_source.passed is False
    assert grounded.passed is True


def test_after_sales_decision_supports_only_matching_order_not_general_policy():
    sources = {
        "businessSources": [
            *_order_evidence(),
            _after_sales_evidence("ELIGIBLE"),
        ]
    }
    bounded = response_verifier.verify(
        assistant="订单 SM1 的订单项 SMITEM1 当前可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs=sources,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )
    general = response_verifier.verify(
        assistant="平台规定七天内可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs=sources,
        order_resolution="RESOLVED",
        has_pending_action=False,
    )

    assert bounded.passed is True
    assert general.passed is False
    assert any(issue.code == "POLICY_WITHOUT_CITATION" for issue in general.issues)


def test_after_sales_ref_requires_versioned_policy_metadata():
    assert (
        after_sales_eligibility_ref(
            {
                "decision": "ELIGIBLE",
                "decisionId": "d1",
                "action": "REFUND",
                "orderId": "O1",
                "orderItemId": "I1",
            }
        )
        is None
    )


def test_write_tool_requires_a_server_verified_pending_action():
    result = response_verifier.verify(
        assistant="请确认退款",
        biz_type="action_confirm",
        tools_called=["PROPOSE_REFUND"],
        source_refs=None,
        has_pending_action=False,
    )

    assert result.passed is False
    assert result.action == "HANDOFF"
    assert result.issues[0].code == "WRITE_WITHOUT_PENDING_ACTION"


def test_policy_claim_requires_published_source_reference():
    blocked = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
    )
    grounded = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货",
        biz_type="agent",
        tools_called=[],
        source_refs={"sources": [{"documentId": "7", "version": 2}]},
        has_pending_action=False,
    )

    assert blocked.issues[0].code == "POLICY_WITHOUT_CITATION"
    assert grounded.passed is True


def test_business_snapshot_cannot_satisfy_rag_policy_citation_gate():
    result = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货。",
        biz_type="agent",
        tools_called=["QUERY_ORDERS"],
        source_refs={
            "ragSources": [],
            "businessSources": [
                {"type": "order", "orderId": "O1", "orderStatusName": "已发货"}
            ],
            "sources": [
                {"type": "order", "orderId": "O1", "orderStatusName": "已发货"}
            ],
        },
        rag_source_refs=[],
        has_pending_action=False,
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_evidence_fallback_is_conservative_and_cited():
    refs = [
        {
            "id": "cancel-policy",
            "factIds": ["order.cancel.by_fulfillment_state"],
            "snippet": (
                "待付款订单可以直接取消；"
                "订单进入发货流程后，是否可以取消取决于当前履约状态；"
                "已经发货的订单通常需要按售后流程处理。"
            ),
        }
    ]
    fallback = deterministic_policy_evidence_fallback(
        "我要取消订单 SM1",
        intent="CANCEL_ORDER",
        evidence_state="SUPPORTED",
        source_refs=refs,
    )

    assert fallback is not None
    assert fallback["citation"] == 1
    assert fallback["answer"].count("[1]") == 3
    checked = response_verifier.verify(
        assistant=fallback["answer"],
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    assert checked.passed is True


def test_coupon_policy_fallback_answers_stacking_question_from_faq():
    refs = [
        {
            "id": "faq9002",
            "factIds": ["coupon.single_per_order_and_revalidate"],
            "snippet": (
                "一个订单可以使用几张优惠券？相似问题：优惠券能叠加吗；"
                "答案：当前一个订单只能选择一张用户优惠券，"
                "提交订单时会重新校验有效期、门槛和归属。"
            ),
        }
    ]
    fallback = deterministic_policy_evidence_fallback(
        "优惠券能否叠加？请说明当前规则并展示依据。",
        intent="agent",
        evidence_state="SUPPORTED",
        source_refs=refs,
    )

    assert fallback is not None
    assert fallback["event"] == "RAG_COUPON_POLICY_DETERMINISTIC_FALLBACK"
    assert fallback["answer"].count("[1]") == 2
    checked = response_verifier.verify(
        assistant=fallback["answer"],
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    assert checked.passed is True


def test_policy_evidence_fallback_does_not_invent_without_matching_source():
    assert deterministic_policy_evidence_fallback(
        "我要取消订单 SM1",
        intent="CANCEL_ORDER",
        evidence_state="SUPPORTED",
        source_refs=[{"id": "unrelated", "snippet": "优惠券规则"}],
    ) is None

    assert deterministic_policy_evidence_fallback(
        "我要取消订单 SM1",
        intent="CANCEL_ORDER",
        evidence_state="SUPPORTED",
        source_refs=[
            {
                "id": "opposite-cancel",
                "factIds": ["order.cancel.by_fulfillment_state"],
                "snippet": (
                    "“待付款订单可以直接取消”这一说法并不成立；"
                    "所有订单均不得取消。"
                ),
            }
        ],
    ) is None


def test_policy_evidence_gate_rejects_uncited_answer_without_keyword_match():
    result = response_verifier.verify(
        assistant="这种情况可以办理。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[],
        has_pending_action=False,
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.action == "DEGRADE"
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_evidence_gate_preserves_grounded_facts_when_answer_abstains():
    assistant = (
        "订单 SM1 当前已发货。"
        "未找到可引用的售后政策证据，因此无法确认是否符合退款条件。"
    )

    result = response_verifier.verify(
        assistant=assistant,
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": _order_evidence()},
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is True
    assert result.action == "PASS"
    assert result.assistant == assistant


def test_policy_evidence_gate_rejects_uncited_eligibility_claim():
    result = response_verifier.verify(
        assistant="该订单符合退款条件，可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_abstention_cannot_mask_an_uncited_eligibility_claim():
    result = response_verifier.verify(
        assistant="该订单可以退款，但我无法确认具体售后资格。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
    )

    assert result.passed is False
    assert result.issues[0].code == "POLICY_WITHOUT_CITATION"


def test_policy_violation_uses_a_separately_verified_safe_fallback():
    fallback = (
        "订单 SM1 当前已发货。"
        "未找到可引用的售后政策证据，因此无法确认具体售后资格。"
    )

    result = response_verifier.verify(
        assistant="该订单符合退款条件，可以退款。",
        biz_type="agent",
        tools_called=[],
        source_refs={"businessSources": _order_evidence()},
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
        safe_fallback=fallback,
    )

    assert result.passed is False
    assert result.action == "DEGRADE"
    assert result.assistant == fallback


def test_policy_violation_rejects_an_unsafe_custom_fallback():
    result = response_verifier.verify(
        assistant="该订单符合退款条件。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        order_resolution="RESOLVED",
        policy_evidence_required=True,
        safe_fallback="可以退款。",
    )

    assert result.passed is False
    assert result.assistant.startswith("当前没有检索到足够的已发布规则依据")


def test_safe_fallback_is_reported_separately_from_original_model_failure():
    fallback = "当前没有足够的规则依据，请补充订单信息或转人工。"
    result = response_verifier.verify(
        assistant="平台规定七天内支持无理由退货。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        policy_evidence_required=True,
        safe_fallback=fallback,
    )

    assert result.passed is False
    assert result.fallback_verified is True
    assert result.terminal_quality == "SAFE_DEGRADED"
    assert result.quality()["verifierPassed"] is False
    assert result.quality()["fallbackVerified"] is True


def test_product_identity_clarification_is_not_a_policy_answer():
    result = response_verifier.verify(
        assistant="要判断续航表现，请提供具体手机品牌/型号，或发送商品卡片。",
        biz_type="agent",
        tools_called=[],
        source_refs=[],
        has_pending_action=False,
        policy_evidence_required=False,
    )
    assert result.passed is True
    assert result.terminal_quality == "PASS"


def test_supported_rag_abstention_is_rejected():
    result = response_verifier.verify(
        assistant="根据当前知识库，我无法确认该信息。请联系人工客服核实。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "knowledge_1", "source": "规则.md"}],
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code == "UNNECESSARY_RAG_ABSTENTION"


def test_supported_rag_abstention_variants_share_the_repair_gate():
    for assistant in (
        "证据不足，无法确认。[1]",
        "无法确认该信息。[1]",
        "无法确认该退款政策。[1]",
        "我无法确认订单状态。[1]",
        "根据现有证据，我无法判断退款到账时间。[1]",
        "无法确认配送时效，退款资格也无法确认。[1]",
        "无法确认订单状态，但库存是否充足也不能判断。[1]",
        "目前无法核实物流，订单状态同样无法确认。[1]",
        "证据中未出现一个超过三十个字符但仍然应该被识别的示例术语，因此无法提供定义。[1]",
        "很抱歉，根据当前知识库，我无法确认该信息。[1]",
        "非常抱歉，我无法确认该信息。[1]",
        "根据目前证据，我无法确认该信息。[1]",
        "就现有证据而言，我无法判断。[1]",
        "由于当前证据不足，我无法确认订单状态。[1]",
        "鉴于当前证据不足，暂不能判断退款资格。[1]",
        "在当前证据不足的情况下，我无法确认库存。[1]",
        "从现有证据来看，我无法确认物流状态。[1]",
        "基于现有证据，我无法判断退款资格。[1]",
        "就当前知识库而言，无法确认订单状态。[1]",
        "未找到足够证据回答退款问题。[1]",
        "没有足够信息回答订单问题。[1]",
        "缺少依据，无法回答库存问题。[1]",
        "我不确定退款资格。[1]",
        "当前信息不足以判断库存。[1]",
        "无法确认该退款政策，但退款问题请联系人工客服。[1]",
        "无法确认订单信息，但订单问题请联系人工。[1]",
        "无法判断是否可以退货，但售后问题建议转人工。[1]",
        "根据现有证据，我无法判断。[1]，平台支持退款。[1]",
        "平台支持退款。[1]，但我无法确认。[1]",
        "已找到相关规则。[1] 不过目前无法判断。[1]",
        "证据不足时系统应转人工，但我目前无法判断。[1]",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=["SEARCH_KNOWLEDGE"],
            source_refs=[{"id": "knowledge_1", "source": "规则.md"}],
            has_pending_action=False,
            policy_evidence_required=True,
            rag_citation_required=True,
            rag_evidence_state="SUPPORTED",
        )

        assert any(
            issue.code == "UNNECESSARY_RAG_ABSTENTION" for issue in result.issues
        )
        assert "有充分证据却拒答" in (
            grounding_repair_reason(
                assistant,
                evidence_state="SUPPORTED",
                evidence_count=1,
            )
            or ""
        )


def test_supported_rag_partial_abstention_is_still_rejected():
    for assistant in (
        "无法确认配送时效，但退款会按原支付渠道返回。[1]",
        "目前无法确认第三方时效 [1]；演示环境仅提供模拟轨迹 [1]。",
        "无法确认具体到账时间，因为到账时效取决于支付渠道。[1]",
    ):
        result = response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=["SEARCH_KNOWLEDGE"],
            source_refs=[{"id": "knowledge_1", "source": "规则.md"}],
            has_pending_action=False,
            policy_evidence_required=True,
            rag_citation_required=True,
            rag_evidence_state="SUPPORTED",
        )

        assert result.passed is False
        assert "有充分证据却拒答" in (grounding_repair_reason(
            assistant,
            evidence_state="SUPPORTED",
            evidence_count=1,
        ) or "")


def test_rag_citation_must_exist_and_stay_in_range():
    missing = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    invalid = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[2]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    valid = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[1]",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert missing.issues[0].code == "INVALID_RAG_CITATION"
    assert invalid.issues[0].code == "INVALID_RAG_CITATION"
    assert valid.passed is True


def test_verified_action_card_does_not_require_prose_rag_citations():
    result = response_verifier.verify(
        assistant='{"type":"ACTION_CONFIRM","actionType":"CANCEL_ORDER"}',
        biz_type="action_confirm",
        tools_called=["PROPOSE_CANCEL_ORDER"],
        source_refs=[{"id": "knowledge_1"}],
        has_pending_action=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is True


def test_refund_action_card_scopes_refund_amount_to_selected_order_item():
    import json

    business_sources = [
        *order_refs(
            [
                {
                    "order_id": "O1",
                    "order_status": 1,
                    "order_status_name": "已付款,待发货",
                    "amount": 3799,
                    "items": [
                        {
                            "order_id": "O1",
                            "order_item_id": "I1",
                            "product_name": "示例耳机",
                            "property_info": "铂金银",
                            "item_amount": 3999,
                            "buy_count": 1,
                        }
                    ],
                }
            ],
            captured="2026-08-25T00:00:00+00:00",
        ),
        _after_sales_evidence(
            "ELIGIBLE", action="REFUND", order_id="O1", order_item_id="I1"
        ),
    ]
    card = {
        "type": "ACTION_CONFIRM",
        "actionType": "REFUND",
        "orderId": "O1",
        "summary": "退款：订单项 I1（示例耳机），金额 3999 元",
        "details": [{"label": "退款金额", "value": "3999 元"}],
        "items": [
            {
                "orderItemId": "I1",
                "productName": "示例耳机",
                "propertyInfo": "铂金银",
                "itemAmount": 3999,
                "buyCount": 1,
            }
        ],
    }

    valid = response_verifier.verify(
        assistant=json.dumps(card, ensure_ascii=False),
        biz_type="action_confirm",
        tools_called=["PROPOSE_REFUND"],
        source_refs={"businessSources": business_sources},
        order_resolution="RESOLVED",
        has_pending_action=True,
    )
    card["details"][0]["value"] = "4999 元"
    tampered = response_verifier.verify(
        assistant=json.dumps(card, ensure_ascii=False),
        biz_type="action_confirm",
        tools_called=["PROPOSE_REFUND"],
        source_refs={"businessSources": business_sources},
        order_resolution="RESOLVED",
        has_pending_action=True,
    )

    assert valid.passed is True
    assert tampered.passed is False
    assert any(issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in tampered.issues)


def test_order_selection_card_verifies_each_candidate_against_its_order_ref():
    import json

    refs = order_refs(
        [
            {
                "order_id": "O1",
                "order_status": 2,
                "order_status_name": "已发货",
                "amount": 100,
                "pay_scene": "alipay_pc",
                "order_time": "2026-08-25 10:00:00",
                "items": [
                    {
                        "order_id": "O1",
                        "order_item_id": "I1",
                        "product_name": "耳机",
                        "property_info": "黑色",
                        "item_amount": 100,
                    }
                ],
            },
            {
                "order_id": "O2",
                "order_status": 3,
                "order_status_name": "已完成",
                "amount": 200,
                "pay_scene": "alipay_pc",
                "order_time": "2026-08-26 10:00:00",
                "items": [
                    {
                        "order_id": "O2",
                        "order_item_id": "I2",
                        "product_name": "音箱",
                        "property_info": "白色",
                        "item_amount": 200,
                    }
                ],
            },
        ],
        captured="2026-08-26T11:00:00+00:00",
    )
    card = {
        "type": "ORDER_SELECTION",
        "selectionId": "sel-1",
        "candidates": [
            {
                "orderId": "O1",
                "orderItemId": "I1",
                "orderStatusName": "已发货",
                "payScene": "alipay_pc",
                "orderTime": "2026-08-25 10:00:00",
                "productName": "耳机",
                "propertyInfo": "黑色",
                "amount": 100,
            },
            {
                "orderId": "O2",
                "orderItemId": "I2",
                "orderStatusName": "已完成",
                "payScene": "alipay_pc",
                "orderTime": "2026-08-26 10:00:00",
                "productName": "音箱",
                "propertyInfo": "白色",
                "amount": 200,
            },
        ],
    }

    valid = response_verifier.verify(
        assistant=json.dumps(card, ensure_ascii=False),
        biz_type="order_selection",
        tools_called=[],
        source_refs={"businessSources": refs},
        order_resolution="AMBIGUOUS",
        has_pending_action=False,
    )
    card["candidates"][1]["amount"] = 999
    tampered = response_verifier.verify(
        assistant=json.dumps(card, ensure_ascii=False),
        biz_type="order_selection",
        tools_called=[],
        source_refs={"businessSources": refs},
        order_resolution="AMBIGUOUS",
        has_pending_action=False,
    )

    assert valid.passed is True
    assert tampered.passed is False


def test_verified_cards_keep_semicolon_delimited_property_info_in_one_claim():
    import json

    refs = order_refs(
        [
            {
                "order_id": "O1",
                "order_status": 2,
                "order_status_name": "已发货",
                "order_time": "2026-08-25 10:00:00",
                "items": [
                    {
                        "order_id": "O1",
                        "order_item_id": "I1",
                        "product_name": "示例耳机",
                        "property_info": "颜色:黑色;型号:X1",
                        "item_amount": 100,
                    }
                ],
            }
        ],
        captured="2026-08-26T11:00:00+00:00",
    )
    selection = {
        "type": "ORDER_SELECTION",
        "selectionId": "sel-semicolon",
        "candidates": [
            {
                "orderId": "O1",
                "orderItemId": "I1",
                "orderStatusName": "已发货",
                "orderTime": "2026-08-25 10:00:00",
                "productName": "示例耳机",
                "propertyInfo": "颜色:黑色;型号:X1",
                "amount": 100,
            }
        ],
    }
    action = {
        "type": "ACTION_CONFIRM",
        "actionType": "CREATE_SUPPORT_CASE",
        "orderId": "O1",
        "items": [
            {
                "orderItemId": "I1",
                "productName": "示例耳机",
                "propertyInfo": "颜色:黑色;型号:X1",
                "itemAmount": 100,
            }
        ],
    }

    selection_result = response_verifier.verify(
        assistant=json.dumps(selection, ensure_ascii=False),
        biz_type="order_selection",
        tools_called=[],
        source_refs={"businessSources": refs},
        order_resolution="AMBIGUOUS",
        has_pending_action=False,
    )
    action_result = response_verifier.verify(
        assistant=json.dumps(action, ensure_ascii=False),
        biz_type="action_confirm",
        tools_called=["PROPOSE_CREATE_SUPPORT_CASE"],
        source_refs={"businessSources": refs},
        order_resolution="RESOLVED",
        has_pending_action=True,
    )

    assert selection_result.passed is True
    assert action_result.passed is True


def test_rag_citation_is_checked_per_factual_sentence():
    result = response_verifier.verify(
        assistant="每个订单只能使用一张优惠券。[1] 支付失败后优惠券会释放。",
        biz_type="agent",
        tools_called=["SEARCH_KNOWLEDGE"],
        source_refs=[{"id": "faq_9002"}],
        has_pending_action=False,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code == "INVALID_RAG_CITATION"
    assert "事实句" in result.issues[0].detail


def test_grounding_repair_catches_uncited_short_boundary_answer():
    reason = grounding_repair_reason(
        "不保证。加购价格快照可能变化 [1]。",
        evidence_state="SUPPORTED",
        evidence_count=1,
    )

    assert reason is not None
    assert "事实句缺少就近引用" in reason


def test_explicit_fact_repair_requires_canonical_claim_realization():
    fact_id = "checkout.idempotency_key"
    claims = get_fact_metadata_catalog().facts[fact_id].atomic_claims
    reason = grounding_repair_reason(
        "普通下单要求 `Idempotency-Key`。[1] 抢券下单也要求该请求头。[1] "
        + "".join(f"{claim} [1]。" for claim in claims[1:]),
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="订单提交幂等是什么意思？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": [fact_id],
                "text": "。".join(claims),
            }
        ],
    )

    assert reason is not None
    assert "未完整保留发布版原子事实" in reason
    assert claims[0] in reason

    contradictory = grounding_repair_reason(
        "无法确认该术语。[1] "
        + "".join(f"{claim} [1]。" for claim in claims),
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="订单提交幂等是什么意思？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": [fact_id],
                "text": "。".join(claims),
            }
        ],
    )
    assert contradictory is not None
    assert "有充分证据却拒答" in contradictory

    split_claim = claims[0].replace("和", "。", 1)
    split_reason = grounding_repair_reason(
        f"{split_claim} [1]。" + "".join(f"{claim} [1]。" for claim in claims[1:]),
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="订单提交幂等是什么意思？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": [fact_id],
                "text": "。".join(claims),
            }
        ],
    )
    assert split_reason is not None
    assert "未完整保留发布版原子事实" in split_reason


def test_explicit_fact_fallback_is_evidence_bound_and_canonical():
    fact_id = "checkout.idempotency_key"
    claims = get_fact_metadata_catalog().facts[fact_id].atomic_claims
    evidence = [{"citation": 1, "factIds": [fact_id], "text": "。".join(claims)}]

    result = deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    )

    assert result is not None
    assert result["event"] == "RAG_EXPLICIT_FACT_DETERMINISTIC_FALLBACK"
    assert result["factIds"] == [fact_id]
    expected_claims = [
        clause for claim in claims for clause in canonical_claim_clauses(claim)
    ]
    assert all(f"{claim} [1]" in result["answer"] for claim in expected_claims)
    duplicate = deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="SUPPORTED",
        evidence_items=[
            evidence[0],
            {"citation": 2, "factIds": [fact_id], "text": "。".join(claims)},
        ],
    )
    assert duplicate is not None
    assert duplicate["citations"] == [1]
    assert "[2]" not in duplicate["answer"]
    assert deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="INSUFFICIENT",
        evidence_items=evidence,
    ) is None
    assert deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="SUPPORTED",
        evidence_items=[{"citation": 1, "factIds": ["address.crud"], "text": "地址"}],
    ) is None
    assert deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思，同时购物车价格是最终成交价吗？",
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    ) is None
    assert deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="SUPPORTED",
        evidence_items=[{"citation": 1, "factIds": [fact_id], "text": claims[0]}],
    ) is None
    negated = "。".join(
        f"“{clause}”这一说法并不成立"
        for claim in claims
        for clause in canonical_claim_clauses(claim)
    )
    assert deterministic_explicit_fact_fallback(
        "订单提交幂等是什么意思？",
        evidence_state="SUPPORTED",
        evidence_items=[{"citation": 1, "factIds": [fact_id], "text": negated}],
    ) is None
    partial_reason = grounding_repair_reason(
        f"{claims[0]} [1]。",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="订单提交幂等是什么意思？",
        evidence_items=[{"citation": 1, "factIds": [fact_id], "text": claims[0]}],
    )
    assert partial_reason is not None
    assert "未被完整证据支持" in partial_reason


def test_checkout_revalidation_has_query_conditioned_repair_and_fallback():
    fact_id = "checkout.current_product_revalidation"
    claims = get_fact_metadata_catalog().facts[fact_id].atomic_claims
    evidence = [{"citation": 1, "factIds": [fact_id], "text": "。".join(claims)}]
    query = "结算时会重新检查商品价格库存吗？"

    reason = grounding_repair_reason(
        "结算时会重新检查商品价格和库存。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query=query,
        evidence_items=evidence,
    )
    fallback = deterministic_explicit_fact_fallback(
        query,
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    )

    assert "当前 SKU 价格" in (reason or "")
    assert fallback is not None
    assert fallback["event"] == "RAG_QUERY_CONDITIONED_DETERMINISTIC_FALLBACK"
    assert "当前 SKU 价格" in fallback["answer"]
    assert grounding_repair_reason(
        fallback["answer"],
        evidence_state="SUPPORTED",
        evidence_count=1,
        query=query,
        evidence_items=evidence,
    ) is None


def test_every_catalog_fact_can_render_a_self_verified_explicit_fallback():
    for fact_id, metadata in get_fact_metadata_catalog().facts.items():
        result = deterministic_explicit_fact_fallback(
            fact_id,
            evidence_state="SUPPORTED",
            evidence_items=[
                {
                    "citation": 1,
                    "factIds": [fact_id],
                    "text": "。".join(metadata.atomic_claims),
                }
            ],
        )

        assert result is not None, fact_id


def test_explicit_fact_contract_preserves_protocol_token_punctuation():
    fact_id = "checkout.idempotency_key"
    claims = get_fact_metadata_catalog().facts[fact_id].atomic_claims
    answer = "".join(
        f"{clause} [1]。"
        for claim in claims
        for clause in canonical_claim_clauses(claim)
    ).replace("Idempotency-Key", "IdempotencyKey")

    reason = grounding_repair_reason(
        answer,
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="订单提交幂等是什么意思？",
        evidence_items=[
            {"citation": 1, "factIds": [fact_id], "text": "。".join(claims)}
        ],
    )

    assert reason is not None
    assert "Idempotency-Key" in reason


def test_multi_explicit_fact_repair_rejects_partial_evidence_contract():
    fact_id = "checkout.idempotency_key"
    claims = get_fact_metadata_catalog().facts[fact_id].atomic_claims
    reason = grounding_repair_reason(
        "".join(f"{claim} [1]。" for claim in claims),
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="请按事实 ID checkout.idempotency_key 和 address.crud 说明",
        evidence_items=[
            {
                "citation": 1,
                "factIds": [fact_id],
                "text": "。".join(claims),
            }
        ],
    )

    assert reason is not None
    assert "未被完整证据支持" in reason


def test_grounding_repair_does_not_treat_conditional_abstention_as_refusal():
    for answer in (
        "没有足够证据时，助手应说明不确定性。[1]",
        "证据不足时，应联系人工客服。[1]",
        "当前没有足够证据时，助手应联系人工客服。[1]",
        "在当前证据不足的情况下，系统应联系人工客服。[1]",
    ):
        assert grounding_repair_reason(
            answer,
            evidence_state="SUPPORTED",
            evidence_count=1,
        ) is None

    current = grounding_repair_reason(
        "根据当前知识库，证据不足时，我无法确认该信息。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
    )
    assert current is not None
    assert "有充分证据却拒答" in current

    assert grounding_repair_reason(
        "当前不能确认收货。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
    ) is None


def test_grounding_repair_still_detects_current_abstention_about_time():
    for answer in (
        "我无法确认时间。[1]",
        "抱歉，我无法确认时间。[1]",
        "目前无法确认时间。[1]",
        "根据现有证据，我无法判断。[1]",
    ):
        reason = grounding_repair_reason(
            answer,
            evidence_state="SUPPORTED",
            evidence_count=1,
        )

        assert reason is not None
        assert "有充分证据却拒答" in reason


def test_grounding_policy_fallback_is_scoped_and_cited():
    evidence = [
        {
            "citation": 1,
            "factIds": ["rag.retrieval_and_abstention"],
            "text": "若知识库没有足够证据，助手应明确说明并建议联系人工客服。",
            "ref": {"source": "知识库", "heading": "知识检索"},
        }
    ]

    result = deterministic_grounding_policy_fallback(
        "RAG检索不足时的grounding含义是什么？",
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    )

    assert result is not None
    assert result["event"] == "RAG_GENERATION_DETERMINISTIC_FALLBACK"
    assert result["citation"] == 1
    assert result["answer"].count("[1]") == 2
    assert deterministic_grounding_policy_fallback(
        "平台退款规则是什么？",
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    ) is None
    assert deterministic_grounding_policy_fallback(
        "RAG检索不足时的grounding含义是什么？",
        evidence_state="INSUFFICIENT",
        evidence_items=evidence,
    ) is None
    assert deterministic_grounding_policy_fallback(
        "Grounding是什么，另外连续签到中断后连续天数怎么算？",
        evidence_state="SUPPORTED",
        evidence_items=evidence,
    ) is None
    assert deterministic_grounding_policy_fallback(
        "RAG检索不足时的grounding含义是什么？",
        evidence_state="SUPPORTED",
        evidence_items=[{**evidence[0], "citation": 4}],
    ) is None


def test_grounding_policy_fallback_accepts_fact_ids_on_reference():
    result = deterministic_grounding_policy_fallback(
        "证据不足时 grounding 应如何处理？",
        evidence_state="SUPPORTED",
        evidence_items=[
            {
                "citation": 1,
                "text": "证据不足时明确说明并联系人工客服。",
                "ref": {"factIds": ["rag.retrieval_and_abstention"]},
            }
        ],
    )

    assert result is not None
    assert result["factId"] == "rag.retrieval_and_abstention"


def test_grounding_prompt_requires_complete_compound_question_coverage():
    prompt = build_grounding_prompt(
        "能否自动发布，是否需要确认？",
        evidence_state="SUPPORTED",
        evidence_items=[],
    )

    assert "每个明确子问题或并列条件" in prompt.system
    assert "确认、身份或归属约束" in prompt.system


def test_mixed_explicit_query_does_not_receive_pure_fact_prompt_contract():
    prompt = build_grounding_prompt(
        "请解释术语“默认地址”，另外连续签到中断后连续天数怎么算？",
        evidence_state="SUPPORTED",
        evidence_items=[],
    )

    assert "不得拆分、改写或补充" not in prompt.system


def test_grounding_prompt_exposes_atomic_fact_checklist():
    prompt = build_grounding_prompt(
        "结算时会重新检查价格和库存吗？",
        evidence_state="SUPPORTED",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["checkout.price_and_stock_revalidation"],
                "text": (
                    "加入购物车时会记录当时价格，"
                    "结算时系统仍会重新校验最新价格和库存。"
                ),
                "ref": {"source": "05-cart-and-checkout.md", "heading": "结算重新校验"},
            }
        ],
    )

    assert "证据原子事实" in prompt.evidence
    assert "重新校验最新价格和库存" in prompt.evidence


def test_grounding_repair_detects_missing_paired_operational_facts():
    reason = grounding_repair_reason(
        "提交订单时会重新读取商品快照并校验当前价格。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="加入购物车的价格和库存到结算时还会重新检查吗？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["checkout.price_and_stock_revalidation"],
                "text": "结算时系统仍会重新校验最新价格和库存。",
            }
        ],
    )

    assert reason is not None
    assert "库存" in reason


def test_grounding_repair_detects_coupon_limit_and_checkout_revalidation_pair():
    reason = grounding_repair_reason(
        "一个订单最多选择一张优惠券，不支持多张券叠加。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="一个订单能叠加多张优惠券吗？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["coupon.single_per_order_and_revalidate"],
                "text": "当前一个订单最多选择一张用户优惠券，不支持多张券叠加。提交订单时会重新校验有效期、门槛和归属。",
            }
        ],
    )

    assert reason is not None
    assert "重新校验" in reason


def test_grounding_repair_detects_memory_storage_and_external_service_pair():
    reason = grounding_repair_reason(
        "对话记忆保存在 MySQL 和 Redis。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="对话记忆保存在哪里，依赖外部Mem0吗？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["ai.memory.local_storage"],
                "text": "记忆数据由项目自身的 MySQL 和 Redis 组件管理，当前不依赖 Mem0 等外部记忆服务。",
            }
        ],
    )

    assert reason is not None
    assert "Mem0" in reason


def test_grounding_repair_accepts_complete_memory_storage_and_external_service_pair():
    reason = grounding_repair_reason(
        "对话记忆保存在 MySQL 和 Redis，当前不依赖外部 Mem0。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="对话记忆保存在哪里，依赖外部Mem0吗？",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["ai.memory.local_storage"],
                "text": "记忆数据由项目自身的 MySQL 和 Redis 组件管理，当前不依赖 Mem0 等外部记忆服务。",
            }
        ],
    )

    assert reason is None


@pytest.mark.parametrize(
    ("query", "fact_id", "evidence", "partial_answer", "missing_label", "complete_answer"),
    (
        (
            "重复提交同一笔订单会不会创建两个订单？",
            "checkout.idempotency_key",
            "普通下单要求 `Idempotency-Key`。同一请求重试会返回已保存结果，不重复创建订单。",
            "普通下单要求 `Idempotency-Key`。[1]",
            "重复提交不重复建单",
            "普通下单要求 `Idempotency-Key`，同一请求重试会返回已保存结果，不重复创建订单。[1]",
        ),
        (
            "知识库证据不够时助手应该怎样回答？",
            "rag.retrieval_and_abstention",
            "若知识库没有足够证据，助手应明确说明并建议联系人工客服。",
            "知识库证据不足时，助手会明确说明。[1]",
            "建议人工核实",
            "知识库证据不足时，助手会明确说明并建议联系人工客服。[1]",
        ),
        (
            "AI可以不经我确认直接取消订单吗？",
            "ai.capability_and_confirmation",
            "涉及订单等业务动作时，系统应展示待确认操作，用户确认后才执行。",
            "AI 不会直接执行取消订单。[1]",
            "写操作待确认",
            "AI 会展示待确认操作，用户确认后才执行取消订单。[1]",
        ),
    ),
)
def test_grounding_repair_checks_query_conditioned_operational_completeness(
    query, fact_id, evidence, partial_answer, missing_label, complete_answer
):
    evidence_items = [{"citation": 1, "factIds": [fact_id], "text": evidence}]

    reason = grounding_repair_reason(
        partial_answer,
        evidence_state="SUPPORTED",
        evidence_count=1,
        query=query,
        evidence_items=evidence_items,
    )

    assert missing_label in (reason or "")
    assert grounding_repair_reason(
        complete_answer,
        evidence_state="SUPPORTED",
        evidence_count=1,
        query=query,
        evidence_items=evidence_items,
    ) is None


def test_cited_write_confirmation_policy_is_not_live_refund_state():
    refs = [
        {
            "id": "confirmation-policy",
            "factIds": ["ai.capability_and_confirmation"],
            "snippet": "涉及订单的写操作需展示待确认操作，用户确认后才执行。",
        }
    ]
    policy = response_verifier.verify(
        assistant=(
            "AI 会先生成待确认提案 [1]。"
            "退款等写操作必须由用户确认后才会执行成功 [1]。"
        ),
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    live_state = response_verifier.verify(
        assistant="我的退款状态已完成且确认后才执行 [1]。",
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert policy.passed is True
    assert live_state.passed is False


def _aftersales_policy_evidence() -> list[dict]:
    return [
        {
            "citation": 1,
            "factIds": ["aftersales.request_and_refund_boundary"],
            "text": (
                "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。"
                "平台会根据商品类型、订单状态和实际情况审核。"
                "退款原路返回的时间取决于支付渠道。"
                "本地演示环境不执行真实资金操作。"
            ),
        }
    ]


def test_grounding_repair_rejects_missing_natural_query_atomic_claims():
    reason = grounding_repair_reason(
        "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。[1]"
        "退款原路返回的时间取决于支付渠道。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="请完整说明退货申请与退款的全部适用边界",
        evidence_items=_aftersales_policy_evidence(),
    )

    assert reason is not None
    assert "发布版原子事实" in reason


def test_grounding_repair_accepts_complete_natural_query_atomic_claims():
    answer = "".join(
        f"{claim}[1]。"
        for claim in (
            "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整",
            "平台会根据商品类型、订单状态和实际情况审核",
            "退款原路返回的时间取决于支付渠道",
            "本地演示环境不执行真实资金操作",
        )
    )
    reason = grounding_repair_reason(
        answer,
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="请完整说明退货申请与退款的全部适用边界",
        evidence_items=_aftersales_policy_evidence(),
    )

    assert reason is None


def test_grounding_repair_scopes_ordinary_natural_query_to_requested_fact():
    reason = grounding_repair_reason(
        "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="退货申请从哪里发起？",
        evidence_items=_aftersales_policy_evidence(),
    )

    assert reason is None


def test_grounding_repair_full_request_requires_planned_fact_evidence():
    reason = grounding_repair_reason(
        "证据不足时系统应明确说明并建议联系人工客服。[1]",
        evidence_state="SUPPORTED",
        evidence_count=1,
        query="请完整说明退货申请与退款的全部适用边界",
        evidence_items=[
            {
                "citation": 1,
                "factIds": ["rag.retrieval_and_abstention"],
                "text": "证据不足时系统应明确说明并建议联系人工客服。",
            }
        ],
    )

    assert reason is not None
    assert "缺少对应的已发布证据" in reason


def test_recommendation_hard_constraints_are_deterministic():
    result = response_verifier.verify(
        assistant="为你找到了两款",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs=None,
        has_pending_action=False,
        recommendation_constraints={
            "budgetMax": 1000,
            "requiredTerms": ["降噪"],
            "excludedBrands": ["Acme"],
        },
        recommendation_candidates=[
            {"name": "Acme 降噪耳机", "brand": "Acme", "price": 800}
        ],
    )

    assert result.passed is False
    assert result.action == "CLARIFY"


def test_recommendation_budget_range_and_required_brand_are_checked():
    result = response_verifier.verify(
        assistant="已找到候选",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs=None,
        has_pending_action=False,
        recommendation_constraints={
            "budgetMin": 3000,
            "budgetMax": 5000,
            "requiredBrands": ["华为"],
        },
        recommendation_candidates=[
            {
                "productName": "其他品牌手机",
                "brand": "其他",
                "minPrice": 2000,
                "maxPrice": 2500,
            }
        ],
    )

    assert result.passed is False
    assert result.issues[0].code == "RECOMMENDATION_CONSTRAINT_VIOLATION"


def _product_property_ref(product_id: str, value: str = "支持") -> dict:
    return {
        "type": "product",
        "source": "JAVA_GATEWAY",
        "productId": product_id,
        "claims": [
            {
                "claimType": "PRODUCT_PROPERTY",
                "subjectType": "product",
                "subjectId": product_id,
                "sourceType": "JAVA_GATEWAY",
                "sourceId": product_id,
                "factPath": "product.property.降噪",
                "propertyName": "降噪",
                "value": value,
            }
        ],
    }


def _recommendation_candidate(product_id: str, value: str = "支持") -> dict:
    return {
        "productId": product_id,
        "recommendation": {
            "evidence": [
                {
                    "type": "product_property",
                    "productId": product_id,
                    "propertyName": "降噪",
                    "propertyValue": value,
                }
            ]
        },
    }


def test_recommendation_evidence_binds_current_same_product_claim():
    result = response_verifier.verify(
        assistant="已找到候选",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={
            "ragSources": [],
            "businessSources": [_product_property_ref("p1")],
        },
        has_pending_action=False,
        recommendation_candidates=[_recommendation_candidate("p1")],
    )

    assert result.passed is True


@pytest.mark.parametrize(
    ("candidate", "source_ref"),
    [
        (_recommendation_candidate("p1", "历史值"), _product_property_ref("p1")),
        (_recommendation_candidate("p2"), _product_property_ref("p1")),
    ],
)
def test_recommendation_evidence_rejects_stale_or_cross_product_claim(
    candidate, source_ref
):
    result = response_verifier.verify(
        assistant="已找到候选",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs={"ragSources": [], "businessSources": [source_ref]},
        has_pending_action=False,
        recommendation_candidates=[candidate],
    )

    assert result.passed is False
    assert result.issues[0].code == "RECOMMENDATION_EVIDENCE_WITHOUT_CLAIM"


def test_generic_refund_timing_fallback_stays_within_published_boundary():
    refs = [
        {
            "id": "knowledge_2_1_3",
            "factIds": ["aftersales.request_and_refund_boundary"],
            "snippet": "退款原路返回的时间取决于支付渠道；本地演示环境不执行真实资金操作。",
        }
    ]
    fallback = deterministic_policy_evidence_fallback(
        "退款多久到账？",
        intent="REFUND",
        evidence_state="SUPPORTED",
        source_refs=refs,
    )

    assert fallback is not None
    answer = fallback["answer"]
    assert "退款原路返回的时间取决于支付渠道" in answer
    assert answer.count("[1]") == 2
    assert "资格" not in answer
    assert "订单项" not in answer
    assert "本次不执行" not in answer
    assert "已退款" not in answer
    checked = response_verifier.verify(
        assistant=answer,
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )
    assert checked.passed is True


def test_cited_generic_refund_policy_cannot_launder_live_refund_state():
    refs = [{"id": "refund-policy"}]

    def check(assistant):
        return response_verifier.verify(
            assistant=assistant,
            biz_type="agent",
            tools_called=[],
            source_refs={"ragSources": refs, "businessSources": []},
            rag_source_refs=refs,
            has_pending_action=False,
            policy_evidence_required=True,
            rag_citation_required=True,
            rag_evidence_state="SUPPORTED",
        )

    for assistant in (
        "退款申请会根据商品类型、订单状态和实际情况审核 [1]。",
        "退款状态可以在订单详情中查看 [1]。",
    ):
        assert check(assistant).passed is True

    for assistant in (
        "我的退款状态为处理中 [1]。",
        "订单 O1 的退款状态为处理中 [1]。",
        "我的退款状态为处理中且退款申请会根据商品类型、订单状态和实际情况审核 [1]。",
    ):
        result = check(assistant)
        assert result.passed is False
        assert any(
            issue.code == "DYNAMIC_FACT_WITHOUT_CLAIM" for issue in result.issues
        )


def test_cited_generic_coupon_policy_is_not_treated_as_user_coupon_state():
    refs = [
        {
            "id": "coupon-policy",
            "factIds": ["coupon.single_per_order_and_revalidate"],
            "snippet": "每笔订单只能使用一张优惠券，提交订单时会再次校验。",
        }
    ]
    result = response_verifier.verify(
        assistant="优惠券可用状态需要在提交订单时再次校验 [1]。",
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is True


def test_cited_user_coupon_state_still_requires_java_authority():
    refs = [
        {
            "id": "coupon-policy",
            "factIds": ["coupon.single_per_order_and_revalidate"],
            "snippet": "每笔订单只能使用一张优惠券，提交订单时会再次校验。",
        }
    ]
    result = response_verifier.verify(
        assistant="你当前的优惠券状态为可用 [1]。",
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code in {
        "DYNAMIC_FACT_WITHOUT_TOOL",
        "DYNAMIC_FACT_WITHOUT_CLAIM",
    }


@pytest.mark.parametrize(
    "assistant",
    [
        "你当前优惠券每笔订单只能使用一张且已过期 [1]。",
        "你当前优惠券提交订单时会再次校验且状态为可用 [1]。",
        "优惠券提交订单时会再次校验且状态为可用 [1]。",
        "优惠券每笔订单只能使用一张且当前已过期 [1]。",
    ],
)
def test_generic_coupon_policy_cannot_launder_user_specific_state(assistant):
    refs = [
        {
            "id": "coupon-policy",
            "factIds": ["coupon.single_per_order_and_revalidate"],
            "snippet": "每笔订单只能使用一张优惠券，提交订单时会再次校验。",
        }
    ]
    result = response_verifier.verify(
        assistant=assistant,
        biz_type="agent",
        tools_called=[],
        source_refs={"ragSources": refs, "businessSources": []},
        rag_source_refs=refs,
        has_pending_action=False,
        policy_evidence_required=True,
        rag_citation_required=True,
        rag_evidence_state="SUPPORTED",
    )

    assert result.passed is False
    assert result.issues[0].code in {
        "DYNAMIC_FACT_WITHOUT_TOOL",
        "DYNAMIC_FACT_WITHOUT_CLAIM",
    }


def test_recommendation_excluded_terms_trigger_clarification():
    result = response_verifier.verify(
        assistant="已找到候选",
        biz_type="product_search",
        tools_called=["SEARCH_PRODUCTS"],
        source_refs=None,
        has_pending_action=False,
        recommendation_constraints={"excludedTerms": ["户外"]},
        recommendation_candidates=[
            {
                "productName": "户外降噪耳机",
                "brand": "示例品牌",
                "price": 899,
            }
        ],
    )

    assert result.passed is False
    assert result.action == "CLARIFY"
    assert result.issues[0].code == "RECOMMENDATION_CONSTRAINT_VIOLATION"


def test_generation_unverified_never_passes_business_only_gate():
    business_refs = _order_evidence()
    result = response_verifier.verify(
        assistant="本次回答未通过证据完整性校验，请稍后重试或回复“转人工”。",
        biz_type="query_order",
        tools_called=["QUERY_ORDERS"],
        source_refs={
            "ragSources": [{"type": "knowledge", "id": "policy-1"}],
            "businessSources": business_refs,
        },
        has_pending_action=False,
        order_resolution="RESOLVED",
        rag_evidence_state="SUPPORTED",
        rag_generation_verified=False,
        rag_source_refs=[{"type": "knowledge", "id": "policy-1"}],
    )

    assert result.passed is False
    assert result.issues[0].code == "RAG_GENERATION_UNVERIFIED"
    assert result.terminal_quality != "PASS"
