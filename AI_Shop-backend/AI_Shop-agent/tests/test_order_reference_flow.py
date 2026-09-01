from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.order_reference_flow import resolve_order_reference_turn
from app.services.order_reference_resolver import (
    OrderReferenceOutcome,
    OrderReferenceResolution,
)


def _order(order_id: str, item_id: str, name: str, when: str) -> dict:
    return {
        "order_id": order_id,
        "order_status": 1,
        "order_time": when,
        "amount": 3999,
        "comment_status": 0,
        "items": [
            {
                "order_id": order_id,
                "order_item_id": item_id,
                "product_id": item_id[-4:],
                "product_name": name,
                "item_amount": 3999,
                "order_item_status": 1,
            }
        ],
    }


def _state() -> dict:
    return {
        "user_id": "u1",
        "message_id": 30,
        "user_text": "没发货的耳机我要退款",
        "intent": "REFUND",
        "intent_decision": {"entities": {}},
        "llm_messages": [],
        "card": None,
    }


def _refund_decision(decision: str, order_id: str, item_id: str) -> dict:
    return {
        "decision": decision,
        "decisionId": f"after-sales-{decision.lower()}",
        "action": "REFUND",
        "orderId": order_id,
        "orderItemId": item_id,
        "policyId": "refund-policy",
        "policyVersion": "v1",
        "evaluatedAt": "2026-08-25T00:00:00+00:00",
    }


def _return_decision(decision: str, order_id: str, item_id: str) -> dict:
    return {
        "decision": decision,
        "decisionId": f"after-sales-return-{decision.lower()}",
        "action": "RETURN",
        "orderId": order_id,
        "orderItemId": item_id,
        "policyId": "return-policy",
        "policyVersion": "v1",
        "evaluatedAt": "2026-08-25T00:00:00+00:00",
    }


def _capability_decision(
    decision: str, action: str, order_id: str, item_id: str | None = None
) -> dict:
    return {
        "decision": decision,
        "action": action,
        "orderId": order_id,
        "orderItemId": item_id,
        "reasonCode": f"TEST_{decision}",
        "capabilityVersion": "order-action-capability/v1",
        "evaluatedAt": "2026-08-25T00:00:00+00:00",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text",
    [
        "退款需要满足哪些条件",
        "退款政策一般多久到账",
        "退款多久到账呀",
        "如何申请退款",
    ],
)
async def test_generic_refund_policy_bypasses_order_resolution(user_text: str) -> None:
    resolver = AsyncMock(
        side_effect=AssertionError("generic refund policy must not resolve an order")
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "request_mode": "INFORMATIONAL",
                "user_text": user_text,
            }
        )

    assert update == {"route": "orchestration_router"}
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_auto_receipt_aftersales_policy_bypasses_order_resolution() -> None:
    resolver = AsyncMock(
        side_effect=AssertionError("generic receipt policy must not resolve an order")
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "CONFIRM_RECEIPT",
                "request_mode": "INFORMATIONAL",
                "user_text": "系统显示快自动收货了，自动确认后还能售后吗",
            }
        )

    assert update == {"route": "orchestration_router"}
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_order_record_no_match_routes_to_actual_handoff() -> None:
    resolver = AsyncMock(
        return_value=OrderReferenceResolution(
            outcome=OrderReferenceOutcome.NO_MATCH,
            intent="QUERY_ORDER",
            reason="未找到相关订单。",
            source_refs=[{"type": "order", "matched": False}],
        )
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "QUERY_ORDER",
                "request_mode": "READ_QUERY",
                "user_text": "订单列表突然少了一笔 1,299 元的订单",
                "intent_decision": {"entities": {"amount": "1,299 元"}},
            }
        )

    assert update["route"] == "human_handoff"
    assert update["dynamic_handoff_reason"] == "STATE_CONFLICT"
    assert update["order_resolution"] == "NO_MATCH"
    assert update["order_reference_evidence"]["route"] == "human_handoff"


@pytest.mark.asyncio
async def test_post_order_address_policy_routes_verified_order_to_handoff() -> None:
    target = {
        "orderId": "SM202608050002",
        "orderItemId": "SMITEM202608050002",
        "productName": "索尼无线降噪耳机",
        "orderStatusName": "已付款,待发货",
    }
    resolver = AsyncMock(
        return_value=OrderReferenceResolution(
            outcome=OrderReferenceOutcome.RESOLVED,
            intent="ADDRESS_CHANGE",
            target=target,
            matched_candidates=[target],
            source_refs=[{"type": "order", "orderId": target["orderId"]}],
        )
    )
    with (
        patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "ADDRESS_CHANGE",
                "request_mode": "INFORMATIONAL",
                "user_text": "订单 SM202608050002 还没发货，收货地址怎么改",
                "rag_source_refs": [
                    {"factIds": ["address.post_order_contact_support"]}
                ],
            }
        )

    assert update["route"] == "human_handoff"
    assert update["dynamic_handoff_order_refs"] == {
        "orderId": "SM202608050002",
        "orderItemId": "SMITEM202608050002",
    }
    assert update["verified_order_context"] == target
    assert "resolved_order_tool" not in update


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_text", "expected"),
    [
        ("这件预售商品大概几天能发货", "无法给出具体发货天数"),
        ("我问的是仓库何时出库，不是快递到了哪里", "不能给出未经核验的出库时限"),
    ],
)
async def test_fulfillment_without_target_clarifies_without_lookup_or_sla(
    user_text: str, expected: str
) -> None:
    resolver = AsyncMock(
        side_effect=AssertionError("missing fulfillment target must not query an order")
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "QUERY_FULFILLMENT",
                "request_mode": "INFORMATIONAL",
                "user_text": user_text,
            }
        )

    resolver.assert_not_awaited()
    assert update["route"] == "finalize"
    assert update["llm_skip_reason"] == "missing_fulfillment_target"
    assert expected in update["chunks"][0]
    assert "24" not in update["chunks"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_text",
    [
        "退款单显示处理中第 5 天了，现在到哪一步",
        "我不是问退款规则，¥199.00 那笔一直没到账",
    ],
)
async def test_refund_status_without_order_id_still_resolves_owned_records(
    user_text: str,
) -> None:
    resolver = AsyncMock(
        return_value=OrderReferenceResolution(
            outcome=OrderReferenceOutcome.NO_MATCH,
            intent="REFUND_STATUS",
            reason="请补充订单号或商品信息后继续。",
        )
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "REFUND_STATUS",
                "request_mode": "READ_QUERY",
                "user_text": user_text,
                "intent_decision": {"entities": {"amount": "¥199.00"}},
            }
        )

    resolver.assert_awaited_once()
    assert update["route"] == "finalize"
    assert update["order_resolution"] == "NO_MATCH"
    assert update["llm_skipped"] is True
    assert update["structured_result_finalized"] is True
    assert update["deterministic_clarification"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_text", "request_mode"),
    [
        ("我要退款", "ACTION_PROPOSAL"),
        ("订单 SM202608050002 能退款吗？", "INFORMATIONAL"),
    ],
)
async def test_refund_action_or_specific_order_still_resolves_order(
    user_text: str, request_mode: str
) -> None:
    resolver = AsyncMock(
        return_value=OrderReferenceResolution(
            outcome=OrderReferenceOutcome.NO_MATCH,
            intent="REFUND",
            reason="未找到可处理的订单。",
        )
    )
    with patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "request_mode": request_mode,
                "user_text": user_text,
            }
        )

    resolver.assert_awaited_once()
    assert update["route"] == "finalize"
    assert update["order_resolution"] == "NO_MATCH"


@pytest.mark.asyncio
async def test_specific_refund_conditions_combine_snapshot_eligibility_and_policy() -> None:
    target = {
        "orderId": "SM202608050002",
        "orderItemId": "SMITEM202608050002",
        "productName": "索尼无线降噪耳机",
        "orderStatusName": "已付款,待发货",
    }
    resolver = AsyncMock(
        return_value=OrderReferenceResolution(
            outcome=OrderReferenceOutcome.RESOLVED,
            intent="REFUND",
            target=target,
            matched_candidates=[target],
            source_refs=[{"type": "order", "orderId": target["orderId"]}],
        )
    )
    with (
        patch("app.graph.order_reference_flow.order_reference_resolver.resolve", resolver),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_refund_decision(
                    "ELIGIBLE", target["orderId"], target["orderItemId"]
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "request_mode": "INFORMATIONAL",
                "user_text": "订单 SM202608050002 申请退款，先告诉我需要哪些条件",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
                "rag_source_refs": [
                    {
                        "id": "refund-policy",
                        "heading": "退货与退款",
                        "snippet": (
                            "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。"
                            "平台会根据商品类型、订单状态和实际情况审核。"
                        ),
                    }
                ],
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "本次资格核验结果为可申请退款" in update["chunks"][0]
    assert "包装完整。[1]" in update["chunks"][0]
    assert "只询问条件" in update["chunks"][0]
    assert "resolved_order_tool" not in update


@pytest.mark.asyncio
async def test_unique_target_prepares_verified_proposal_before_any_llm_turn():
    orders = [
        _order(
            "SM202608050002",
            "SMITEM202608050002",
            "索尼无线降噪耳机",
            "2026-08-05 21:00:00",
        )
    ]
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=orders),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_refund_decision(
                    "ELIGIBLE", "SM202608050002", "SMITEM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(_state())

    assert update["route"] == "orchestration_router"
    assert update["order_resolution"] == "RESOLVED"
    assert update["verified_order_context"]["orderId"] == "SM202608050002"
    assert update["resolved_order_tool"] == {
        "name": "PROPOSE_REFUND",
        "args": {"orderItemId": "SMITEM202608050002"},
    }


@pytest.mark.asyncio
async def test_multiple_targets_persist_an_order_selection_card():
    orders = [
        _order("SM202608050002", "SMITEM202608050002", "索尼无线耳机", "2026-08-05 21:00:00"),
        _order("SM202608040001", "SMITEM202608040001", "苹果无线耳机", "2026-08-04 10:00:00"),
    ]
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=orders),
        ),
        patch(
            "app.graph.order_reference_flow.order_selection_store.create",
            AsyncMock(return_value={"selectionId": "sel_1", "expiresAt": "2099-01-01T00:00:00"}),
        ) as create,
    ):
        update = await resolve_order_reference_turn(_state())

    card = json.loads(update["assistant_cards"])
    assert update["route"] == "finalize"
    assert update["order_resolution"] == "AMBIGUOUS"
    assert card["type"] == "ORDER_SELECTION"
    assert len(card["candidates"]) == 2
    assert all("_searchText" not in row for row in card["candidates"])
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoice_without_a_verifiable_order_keeps_a_non_policy_clarification():
    state = {
        **_state(),
        "intent": "INVOICE",
        "request_mode": "ACTION_PROPOSAL",
        "user_text": "¥199.00的订单我要开发票",
        "intent_decision": {"entities": {"amount": "199.00"}},
    }
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=[]),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "NO_MATCH"
    assert update["llm_skipped"] is True
    assert update["llm_skip_reason"] == "order_reference_deterministic_result"
    assert update["structured_result_finalized"] is True
    assert update["deterministic_clarification"] is True
    assert "resolved_order_tool" not in update
    assert update["chunks"] == [
        "我需要先定位具体订单才能继续处理开票请求。仅凭金额无法唯一匹配订单，"
        "请补充订单号或商品信息；如需人工帮助可回复“转人工”。"
    ]
    assert update["order_reference_evidence"] == {
        "outcome": "NO_MATCH",
        "route": "finalize",
        "resolvedTool": None,
        "businessSourceRefCount": 1,
        "capabilityDecisionRefCount": 0,
        "hasVerifiedOrderContext": False,
        "matchedCandidateCount": 0,
        "dependencyError": False,
    }


@pytest.mark.asyncio
async def test_review_details_reuse_the_selected_order_reference():
    order = _order(
        "SM202608010001",
        "SMITEM202608010001",
        "索尼无线降噪耳机",
        "2026-08-01 20:00:00",
    )
    order["order_status"] = 3
    state = {
        **_state(),
        "user_text": "五星，音质很好",
        "intent": "PRODUCT_REVIEW",
        "pending_order_reference": {
            "intent": "PRODUCT_REVIEW",
            "targetType": "ORDER",
            "targetId": "SM202608010001",
            "orderId": "SM202608010001",
            "orderItemId": "SMITEM202608010001",
            "expiresAt": (datetime.now() + timedelta(minutes=20)).isoformat(),
        },
    }
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.java_internal_client.get_order_action_capability",
            AsyncMock(
                return_value=_capability_decision(
                    "ALLOWED",
                    "PRODUCT_REVIEW",
                    "SM202608010001",
                    "SMITEM202608010001",
                )
            ),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert update["resolved_order_tool"] == {
        "name": "PROPOSE_PRODUCT_REVIEW",
        "args": {
            "orderId": "SM202608010001",
            "commentContent": "音质很好",
            "star": 5,
        },
    }


@pytest.mark.asyncio
async def test_unshipped_order_answers_from_snapshot_without_querying_fake_logistics():
    state = {
        **_state(),
        "user_text": "没发货的耳机物流到哪了",
        "intent": "QUERY_LOGISTICS",
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"
    assert "尚未发货" in update["chunks"][0]
    assert "没有物流轨迹" in update["chunks"][0]


@pytest.mark.asyncio
async def test_order_resolution_is_independent_from_serving_mode():
    state = {
        **_state(),
        "user_text": "已发货的耳机物流到哪了",
        "intent": "QUERY_LOGISTICS",
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 2
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "RESOLVED"


@pytest.mark.asyncio
async def test_no_eligible_keeps_verified_snapshot_without_proposing_write():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.java_internal_client.get_order_action_capability",
            AsyncMock(
                return_value=_capability_decision(
                    "DENIED", "CANCEL_ORDER", "SM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "CANCEL_ORDER",
                "user_text": "取消订单 SM202608050002",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert update["verified_order_context"]["orderStatusName"] == "已付款,待发货"
    assert update["order_reference_evidence"] == {
        "outcome": "NO_ELIGIBLE",
        "route": "finalize",
        "resolvedTool": None,
        "businessSourceRefCount": 2,
        "capabilityDecisionRefCount": 1,
        "hasVerifiedOrderContext": True,
        "matchedCandidateCount": 1,
        "dependencyError": False,
    }


@pytest.mark.asyncio
async def test_capability_service_unavailable_never_prepares_a_write_proposal():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.java_internal_client.get_order_action_capability",
            AsyncMock(side_effect=TimeoutError("capability timeout")),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "CANCEL_ORDER",
                "request_mode": "ACTION_PROPOSAL",
                "user_text": "取消订单 SM202608050002",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "资格服务暂时无法" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert update["order_reference_evidence"]["capabilityDecisionRefCount"] == 0


@pytest.mark.asyncio
async def test_informational_paid_unshipped_wrong_item_is_ineligible() -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_return_decision(
                    "INELIGIBLE", "SM202608050002", "SMITEM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "DAMAGED_OR_WRONG_ITEM",
                "request_mode": "INFORMATIONAL",
                "user_text": "商家发错商品了，订单 SM202608050002 怎么处理？",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert "resolved_order_tool" not in update
    assert "不会生成售后工单确认卡" in update["chunks"][0]
    assert update["order_reference_evidence"]["hasVerifiedOrderContext"] is True
    assert update["order_reference_evidence"]["outcome"] == "NO_ELIGIBLE"


@pytest.mark.asyncio
async def test_shipped_wrong_item_with_return_eligibility_prepares_confirmation() -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 2
    eligibility = AsyncMock(
        return_value=_return_decision(
            "ELIGIBLE", "SM202608050002", "SMITEM202608050002"
        )
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            eligibility,
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "DAMAGED_OR_WRONG_ITEM",
                "request_mode": "INFORMATIONAL",
                "user_text": "商家发错商品了，订单 SM202608050002 怎么处理？",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "orchestration_router"
    assert update["order_resolution"] == "RESOLVED"
    assert update["resolved_order_tool"]["name"] == "PROPOSE_CREATE_SUPPORT_CASE"
    assert update["resolved_order_tool"]["args"]["category"] == "WRONG_ITEM"
    assert update["order_reference_evidence"]["capabilityDecisionRefCount"] == 1
    eligibility.assert_awaited_once_with(
        user_id="u1",
        action="RETURN",
        order_id="SM202608050002",
        order_item_id="SMITEM202608050002",
        evidence=[],
    )


@pytest.mark.asyncio
async def test_address_howto_resolves_order_without_creating_support_case_proposal() -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "ADDRESS_CHANGE",
                "request_mode": "READ_QUERY",
                "user_text": "订单 SM202608050002 还没发货，收货地址怎么改",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "orchestration_router"
    assert update["order_resolution"] == "RESOLVED"
    assert update["resolved_order_tool"] is None
    assert update["verified_order_context"]["orderId"] == "SM202608050002"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision",
    ["NEEDS_EVIDENCE", "CONFLICT", "POLICY_UNAVAILABLE"],
)
async def test_return_policy_non_eligible_decisions_fail_closed(decision: str) -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 2
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_return_decision(
                    decision, "SM202608050002", "SMITEM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "DAMAGED_OR_WRONG_ITEM",
                "request_mode": "ACTION_PROPOSAL",
                "user_text": "订单 SM202608050002 收到时已经破损，帮我处理",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "resolved_order_tool" not in update
    assert update["order_reference_evidence"]["resolvedTool"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "message", "category"),
    [
        ("INVOICE", "订单 SM202608050002 请开具发票", "INVOICE"),
        ("ADDRESS_CHANGE", "订单 SM202608050002 修改收货地址", "ADDRESS_CHANGE"),
    ],
)
async def test_non_return_support_categories_do_not_call_return_policy(
    intent: str, message: str, category: str
) -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    eligibility = AsyncMock(
        side_effect=AssertionError("non-return support category must not use RETURN policy")
    )
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            eligibility,
        ),
        patch("app.graph.order_reference_flow._clear_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": intent,
                "request_mode": "ACTION_PROPOSAL",
                "user_text": message,
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "orchestration_router"
    assert update["resolved_order_tool"]["name"] == "PROPOSE_CREATE_SUPPORT_CASE"
    assert update["resolved_order_tool"]["args"]["category"] == category
    eligibility.assert_not_awaited()


@pytest.mark.asyncio
async def test_recomment_without_body_stays_a_snapshot_backed_clarification() -> None:
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 3
    order["comment_status"] = 1
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "RECOMMENT",
                "request_mode": "ACTION_PROPOSAL",
                "user_text": "我想追评订单 SM202608050002",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "想追加的评价内容" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert update["verified_order_context"]["orderId"] == "SM202608050002"
    assert update["order_reference_evidence"]["hasVerifiedOrderContext"] is True


@pytest.mark.asyncio
async def test_read_only_refund_question_checks_eligibility_without_proposing_write():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 3
    state = {
        **_state(),
        "user_text": "订单号SM202608050002为什么延迟，现在能否退款？",
        "request_mode": "READ_QUERY",
    }
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_refund_decision(
                    "INELIGIBLE", "SM202608050002", "SMITEM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert update["route"] == "finalize"
    assert "不符合退款资格" in update["chunks"][0]
    assert "resolved_order_tool" not in update
    assert update["verified_order_context"]["orderId"] == "SM202608050002"


@pytest.mark.asyncio
async def test_selected_refund_rechecks_latest_status_before_proposing():
    state = {
        **_state(),
        "user_text": "选择索尼无线降噪耳机订单继续退款。",
        "pending_order_reference": {
            "intent": "REFUND",
            "targetType": "ORDER_ITEM",
            "targetId": "SMITEM202608050002",
            "orderId": "SM202608050002",
            "orderItemId": "SMITEM202608050002",
            "expiresAt": (datetime.now() + timedelta(minutes=2)).isoformat(),
        },
    }
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    order["order_status"] = 0
    with (
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
        patch(
            "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
            AsyncMock(
                return_value=_refund_decision(
                    "INELIGIBLE", "SM202608050002", "SMITEM202608050002"
                )
            ),
        ),
        patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
    ):
        update = await resolve_order_reference_turn(state)

    assert update["order_resolution"] == "NO_ELIGIBLE"
    assert "待付款" in update["chunks"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "user_text", "expected_category"),
    [
        (
            "DAMAGED_OR_WRONG_ITEM",
            "订单 SM202608050002 少了一个配件，麻烦处理。",
            "MISSING_ITEM",
        ),
        (
            "QUERY_LOGISTICS",
            "订单 SM202608050002 的物流一直不动，麻烦核查。",
            "LOGISTICS",
        ),
        (
            "QUERY_LOGISTICS",
            "订单 SM202608050002 物流三天没更新了",
            "LOGISTICS",
        ),
    ],
)
async def test_verified_exception_complaints_prepare_support_case_confirmation(
    intent,
    user_text,
    expected_category,
):
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    patches = [
        patch(
            "app.services.order_reference_resolver.java_internal_client.list_orders",
            AsyncMock(return_value=[order]),
        ),
    ]
    if intent == "DAMAGED_OR_WRONG_ITEM":
        patches.extend(
            [
                patch(
                    "app.graph.order_reference_flow.after_sales_policy_service.evaluate",
                    AsyncMock(
                        return_value=_return_decision(
                            "INELIGIBLE",
                            "SM202608050002",
                            "SMITEM202608050002",
                        )
                    ),
                ),
                patch("app.graph.order_reference_flow._remember_reference", AsyncMock()),
            ]
        )
    else:
        patches.append(patch("app.graph.order_reference_flow._clear_reference", AsyncMock()))
    with ExitStack() as stack:
        for context in patches:
            stack.enter_context(context)
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": intent,
                "request_mode": "READ_QUERY",
                "user_text": user_text,
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    if intent == "DAMAGED_OR_WRONG_ITEM":
        assert update["route"] == "finalize"
        assert update["order_resolution"] == "NO_ELIGIBLE"
        assert "resolved_order_tool" not in update
        assert update["order_reference_evidence"]["resolvedTool"] is None
    else:
        assert update["route"] == "orchestration_router"
        assert update["order_resolution"] == "RESOLVED"
        assert update["resolved_order_tool"]["name"] == "PROPOSE_CREATE_SUPPORT_CASE"
        assert update["resolved_order_tool"]["args"]["category"] == expected_category
        assert update["order_reference_evidence"]["resolvedTool"] == (
            "PROPOSE_CREATE_SUPPORT_CASE"
        )


@pytest.mark.asyncio
async def test_plain_unshipped_fulfillment_keeps_snapshot_response_without_capability_claim():
    order = _order(
        "SM202608050002",
        "SMITEM202608050002",
        "索尼无线降噪耳机",
        "2026-08-05 21:00:00",
    )
    with patch(
        "app.services.order_reference_resolver.java_internal_client.list_orders",
        AsyncMock(return_value=[order]),
    ):
        update = await resolve_order_reference_turn(
            {
                **_state(),
                "intent": "QUERY_FULFILLMENT",
                "user_text": "订单 SM202608050002 怎么还没发货？",
                "intent_decision": {"entities": {"orderId": "SM202608050002"}},
            }
        )

    assert update["route"] == "finalize"
    assert update["order_resolution"] == "RESOLVED"
    assert "商家尚未发货" in update["chunks"][0]
    assert "如需催发货或进一步核查" in update["chunks"][0]
    assert "暂无催发货写工具" not in update["chunks"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "text", "expected_category"),
    [
        ("ADDRESS_CHANGE", "我想修改收货地址", "ADDRESS_CHANGE"),
        ("INVOICE", "请开具发票", "INVOICE"),
        ("DAMAGED_OR_WRONG_ITEM", "收到的商品破损了", "DAMAGED"),
    ],
)
async def test_after_sales_intents_propose_owned_support_case(intent, text, expected_category):
    from app.graph.order_reference_flow import _tool_for_target

    target = {
        "orderId": "SM202608050002",
        "orderItemId": "SMITEM202608050002",
        "productName": "索尼无线降噪耳机",
    }
    tool_name, args = _tool_for_target(
        intent,
        text,
        target,
        {"message_id": 30, "after_sales_workflow": True},
    )

    assert tool_name == "PROPOSE_CREATE_SUPPORT_CASE"
    assert args["category"] == expected_category
    assert args["orderId"] == "SM202608050002"
    assert args["orderItemId"] == "SMITEM202608050002"
