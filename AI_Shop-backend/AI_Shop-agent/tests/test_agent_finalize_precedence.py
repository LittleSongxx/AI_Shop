from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent_runtime import finalize_agent_response


@pytest.mark.asyncio
async def test_valid_order_cards_beat_an_earlier_failed_write_attempt():
    cards = json.dumps(
        [
            {
                "orderId": "SM202608050002",
                "orderStatus": 1,
                "orderStatusName": "已付款，待发货",
                "orderItemList": [],
            }
        ],
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 30, "userMessage": "耳机退款"},
            ["未能生成有效确认卡片"],
            [],
            assistant_cards=cards,
            tools_called=["PROPOSE_REFUND", "QUERY_ORDERS"],
            user_text="耳机退款",
        )

    assert complete.await_args.args[1] == cards
    assert complete.await_args.args[2] == "query_order"


@pytest.mark.asyncio
async def test_order_selection_is_persisted_as_the_terminal_payload():
    selection = json.dumps(
        {
            "type": "ORDER_SELECTION",
            "selectionId": "sel_1",
            "sourceMessageId": "30",
            "intent": "REFUND",
            "prompt": "请选择订单",
            "expiresAt": "2099-01-01T00:00:00",
            "candidates": [{"targetType": "ORDER", "targetId": "o1", "orderId": "o1"}],
        },
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 30, "userMessage": "退款"},
            [],
            [],
            assistant_cards=selection,
            biz_type="order_selection",
            tools_called=[],
            user_text="退款",
        )

    assert complete.await_args.args[1] == selection
    assert complete.await_args.args[2] == "order_selection"


@pytest.mark.asyncio
async def test_server_action_card_is_authoritative_without_model_token_echo():
    token = "act_" + "a" * 32
    card = json.dumps(
        {
            "type": "ACTION_CONFIRM",
            "actionToken": token,
            "actionType": "CANCEL_ORDER",
            "summary": "untrusted client text",
        },
        ensure_ascii=False,
    )
    pending = {
        "token": token,
        "userId": "u1",
        "actionType": "CANCEL_ORDER",
        "paramsJson": json.dumps({"orderId": "O-1"}),
        "summary": "取消订单 O-1",
        "status": 0,
    }
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
        patch(
            "app.services.agent_runtime.pending_action_service.get_by_token",
            AsyncMock(return_value=pending),
        ),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 34, "userMessage": "取消订单"},
            ["请核对信息"],
            [],
            assistant_cards=card,
            tools_called=["PROPOSE_CANCEL_ORDER"],
            user_text="取消订单",
        )

    persisted = json.loads(complete.await_args.args[1])
    assert persisted["type"] == "ACTION_CONFIRM"
    assert persisted["actionToken"] == token
    assert persisted["summary"] == pending["summary"]
    assert complete.await_args.args[2] == "action_confirm"


@pytest.mark.asyncio
async def test_product_comparison_card_is_the_terminal_payload():
    comparison = json.dumps(
        {
            "type": "PRODUCT_COMPARISON",
            "snapshotType": "REAL_TIME",
            "products": [
                {"productId": "p1", "productName": "A", "minPrice": 100},
                {"productId": "p2", "productName": "B", "minPrice": 120},
            ],
        },
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 31, "userMessage": "比较这两个"},
            ["已经比较好了"],
            [],
            assistant_cards=comparison,
            biz_type="product_comparison",
            tools_called=["COMPARE_PRODUCTS"],
            user_text="比较这两个",
        )

    assert complete.await_args.args[1] == comparison
    assert complete.await_args.args[2] == "product_comparison"


@pytest.mark.asyncio
async def test_consult_turn_drops_unrelated_product_cards_and_asks_for_identity():
    unrelated = json.dumps(
        [{"productId": "other", "productName": "另一款手机", "minPrice": 8999}],
        ensure_ascii=False,
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {
                "userId": "u1",
                "messageId": 35,
                "userMessage": "这款手机续航怎么样",
                "intentDecision": {"intent": "PRODUCT_CONSULT"},
            },
            ["模型误返回商品列表"],
            [],
            assistant_cards=unrelated,
            tools_called=["SEARCH_PRODUCTS"],
            consult_card={"productId": "selected", "productName": "当前手机"},
            user_text="这款手机续航怎么样",
        )

    assert "请提供具体商品名称、型号或商品卡片" in complete.await_args.args[1]
    assert complete.await_args.args[2] == "agent"
    assert complete.await_args.args[3] is None


@pytest.mark.asyncio
async def test_consult_without_card_asks_attribute_specific_identity_question():
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {
                "userId": "u1",
                "messageId": 36,
                "userMessage": "这款耳机支持蓝牙 5.4 吗",
                "intentDecision": {"intent": "PRODUCT_CONSULT"},
            },
            ["根据当前知识库，我无法确认该信息。请联系人工客服核实。"],
            [],
            tools_called=[],
            user_text="这款耳机支持蓝牙 5.4 吗",
        )

    assert "蓝牙或版本" in complete.await_args.args[1]
    assert complete.await_args.args[2] == "agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("card_type", "biz_type"),
    [
        ("SUPPORT_CASE_LIST", "support_case_list"),
        ("SUPPORT_CASE_DETAIL", "support_case_detail"),
    ],
)
async def test_support_case_cards_are_terminal_and_preserve_structured_payload(
    card_type, biz_type
):
    payload = (
        {"type": card_type, "cases": [{"caseId": 1, "caseNo": "SC20260807ABC123"}]}
        if card_type == "SUPPORT_CASE_LIST"
        else {
            "type": card_type,
            "case": {"caseId": 1, "caseNo": "SC20260807ABC123", "status": "OPEN"},
        }
    )
    cards = json.dumps(payload, ensure_ascii=False)
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 32, "userMessage": "查工单"},
            ["模型补充文本"],
            [],
            assistant_cards=cards,
            tools_called=["QUERY_SUPPORT_CASES"],
            user_text="查我的工单",
        )

    assert complete.await_args.args[1] == cards
    assert complete.await_args.args[2] == biz_type


@pytest.mark.asyncio
async def test_failed_rag_repair_draft_is_replaced_by_verifier_fallback():
    unsafe_draft = "平台支持七天无理由退货。"
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
        patch("app.services.agent_runtime.badcase_service.add_candidate", AsyncMock()),
        patch("app.services.agent_runtime.judge_service.enqueue"),
    ):
        await finalize_agent_response(
            {"userId": "u1", "messageId": 33, "userMessage": "退货政策是什么"},
            [unsafe_draft],
            [],
            tools_called=[],
            user_text="退货政策是什么",
            source_refs={
                "trace": {"evidenceState": "SUPPORTED"},
                "sources": [{"id": "returns-policy", "citation": 1}],
            },
            rag_evidence_required=True,
            rag_evidence_state="SUPPORTED",
        )

    persisted = complete.await_args.args[1]
    assert persisted != unsafe_draft
    assert persisted == "本次回答的知识引用不完整或无效。请稍后重试，或回复“转人工”。"


@pytest.mark.asyncio
async def test_deterministic_payment_clarification_bypasses_policy_evidence_gate():
    guidance = (
        "根据你的描述，本次支付失败且没有扣款。请先检查支付方式和页面提示，再自行重新发起支付；"
        "若之后出现扣款、重复扣款或非本人支付，请回复“转人工”以进入人工核查。"
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
        patch("app.services.agent_runtime.badcase_service.add_candidate", AsyncMock()) as badcase,
        patch("app.services.agent_runtime.judge_service.enqueue"),
        patch("app.services.agent_runtime.episode_service.record_step"),
        patch("app.services.agent_runtime.episode_service.update_run"),
    ):
        await finalize_agent_response(
            {
                "userId": "u1",
                "messageId": 37,
                "userMessage": "支付失败但没有扣款，怎么办",
                "intentDecision": {"intent": "PAYMENT_ISSUE"},
            },
            [guidance],
            [],
            tools_called=[],
            user_text="支付失败但没有扣款，怎么办",
            source_refs={"ragSources": [], "businessSources": [], "sources": []},
            rag_evidence_required=True,
            rag_evidence_state="INSUFFICIENT",
            deterministic_clarification=True,
        )

    assert complete.await_args.args[1] == guidance
    assert complete.await_args.args[2] == "agent"
    badcase.assert_not_awaited()


@pytest.mark.asyncio
async def test_deterministic_invoice_target_clarification_bypasses_policy_evidence_gate():
    guidance = (
        "我需要先定位具体订单才能继续处理开票请求。仅凭金额无法唯一匹配订单，"
        "请补充订单号或商品信息；如需人工帮助可回复“转人工”。"
    )
    with (
        patch(
            "app.services.agent_runtime.agent_message_service.complete_message",
            AsyncMock(),
        ) as complete,
        patch("app.services.agent_runtime.stream_service.push_done", AsyncMock()),
        patch("app.services.agent_runtime.badcase_service.add_candidate", AsyncMock()) as badcase,
        patch("app.services.agent_runtime.judge_service.enqueue"),
        patch("app.services.agent_runtime.episode_service.record_step"),
        patch("app.services.agent_runtime.episode_service.update_run"),
    ):
        await finalize_agent_response(
            {
                "userId": "u1",
                "messageId": 38,
                "userMessage": "¥199.00的订单我要开发票",
                "intentDecision": {"intent": "INVOICE"},
            },
            [guidance],
            [],
            tools_called=[],
            user_text="¥199.00的订单我要开发票",
            source_refs={"ragSources": [], "businessSources": [], "sources": []},
            rag_evidence_required=True,
            rag_evidence_state="INSUFFICIENT",
            deterministic_clarification=True,
        )

    assert complete.await_args.args[1] == guidance
    assert complete.await_args.args[2] == "agent"
    badcase.assert_not_awaited()
