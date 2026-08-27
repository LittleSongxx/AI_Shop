"""LangGraph 状态机结构测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.constants import MSG_STATUS_NORMAL
from app.domain.intent.types import IntentKind, NextAction, RequestMode, RiskLevel
from app.graph.builder import build_agent_graph
from app.graph.nodes import (
    _rag_query_variants_for_turn,
    agent_loop_node,
    dynamic_handoff_node,
    requires_rag_evidence,
    should_open_agentic_rag,
    should_prefetch_rag,
    tools_node,
)
from app.graph.runner import _should_resume, run_agent_graph
from app.graph.state import initial_state, thread_id_for


def test_graph_compiles():
    graph = build_agent_graph()
    assert graph is not None

def test_initial_state_shape():
    agent_msg = {
        "userId": "u1",
        "messageId": 1,
        "userMessage": "你好",
    }
    state = initial_state(agent_msg, None, "你好")
    assert state["user_id"] == "u1"
    assert state["message_id"] == 1
    assert state["react_round"] == 0
    assert state["deterministic_clarification"] is False
    assert state["dynamic_handoff_reason"] is None


def test_auto_receipt_policy_turn_adds_bounded_retrieval_variants():
    variants = _rag_query_variants_for_turn(
        "系统显示快自动收货了，自动确认后还能售后吗",
        "自动确认收货后还能售后",
    )

    assert variants == [
        "自动确认收货后还能售后",
        "确认收货 订单完成状态 未实际收到不要提前确认",
        "售后资格 订单状态 实时规则核验",
    ]


@pytest.mark.asyncio
async def test_read_query_blocks_model_selected_write_proposal(monkeypatch):
    invoke = AsyncMock(side_effect=AssertionError("blocked proposal must not reach MCP"))
    recorded = MagicMock()
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.mcp_tool_router.invoke", invoke)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", recorded)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(rag_mode="conditional", graph_max_react_rounds=4),
    )
    call = {
        "id": "call-1",
        "name": "PROPOSE_CREATE_SUPPORT_CASE",
        "args": {"category": "ADDRESS_CHANGE", "description": "怎么改地址"},
    }
    result = await tools_node(
        {
            "agent_msg": {"runId": "run-1"},
            "user_id": "u1",
            "message_id": 1,
            "user_text": "订单 A1 还没发货，收货地址怎么改",
            "request_mode": RequestMode.READ_QUERY.value,
            "pending_tool_calls": [call],
            "llm_messages": [AIMessage(content="", tool_calls=[call])],
            "tools_called": [],
            "react_round": 1,
        }
    )

    invoke.assert_not_awaited()
    assert result["route"] == "agent_loop"
    assert result["tools_called"] == []
    assert isinstance(result["llm_messages"][-1], ToolMessage)
    assert "安全策略拒绝" in result["llm_messages"][-1].content
    denied = next(
        call for call in recorded.call_args_list if call.args[0] == "TOOL_POLICY_DENIED"
    )
    assert denied.kwargs["output_data"]["sideEffectAllowed"] is False


def _agent_loop_state(
    *,
    intent: str,
    user_text: str,
    intent_decision: dict | None = None,
    request_mode: str | None = None,
) -> dict:
    return {
        "agent_msg": {"userMessage": user_text},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [HumanMessage(content=user_text)],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": user_text,
        "from_product": False,
        "tools_called": [],
        "pending_tool_calls": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": intent,
        "intent_decision": intent_decision or {},
        "intent_data": None,
        "request_mode": request_mode,
        "rag_evidence_required": False,
        "chunks": [],
    }


@pytest.mark.asyncio
async def test_auto_receipt_policy_answers_from_numbered_visible_evidence(monkeypatch):
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(graph_max_react_rounds=4),
    )
    state = _agent_loop_state(
        intent=IntentKind.CONFIRM_RECEIPT.value,
        user_text="系统显示快自动收货了，自动确认后还能售后吗",
        request_mode=RequestMode.INFORMATIONAL.value,
    )
    state["rag_source_refs"] = [
        {"factIds": ["logistics.confirm_receipt"]},
        {"factIds": ["aftersales.rule_engine_authoritative"]},
    ]

    result = await agent_loop_node(state)

    assert result["route"] == "finalize"
    assert result["llm_skip_reason"] == "auto_receipt_aftersales_boundary"
    assert "完成状态。[1]" in result["chunks"][0]
    assert "实时售后规则核验。[2]" in result["chunks"][0]
    assert "不要提前手动确认。[1]" in result["chunks"][0]


@pytest.mark.asyncio
async def test_generic_refund_conditions_use_only_visible_snippet_claims(monkeypatch):
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    state = _agent_loop_state(
        intent=IntentKind.REFUND.value,
        user_text="退款需要满足哪些条件",
        request_mode=RequestMode.INFORMATIONAL.value,
    )
    state["rag_source_refs"] = [
        {
            "id": "refund-policy",
            "heading": "退货与退款",
            "snippet": (
                "用户应在订单详情中发起售后申请，并保持商品、附件和包装完整。"
                "平台会根据商品类型、订单状态和实际情况审核。"
            ),
        }
    ]

    result = await agent_loop_node(state)

    assert result["llm_skip_reason"] == "refund_conditions_evidence_answer"
    assert "包装完整。[1]" in result["chunks"][0]
    assert "实际情况审核。[1]" in result["chunks"][0]
    assert "幂等" not in result["chunks"][0]


@pytest.mark.asyncio
async def test_dynamic_handoff_uses_post_resolution_reason_and_verified_order(monkeypatch):
    transfer = AsyncMock(return_value={})
    clear = AsyncMock()
    monkeypatch.setattr(
        "app.services.agent_service.agent_orchestrator._transfer_to_support",
        transfer,
    )
    monkeypatch.setattr("app.graph.nodes.redis_service.clear_bound_message_id", clear)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    agent_msg = {"userId": "u1", "messageId": 9, "runId": "run-9"}

    result = await dynamic_handoff_node(
        {
            "agent_msg": agent_msg,
            "user_id": "u1",
            "message_id": 9,
            "user_text": "订单还没发货，收货地址怎么改",
            "intent": IntentKind.ADDRESS_CHANGE.value,
            "intent_decision": {
                "intent": IntentKind.ADDRESS_CHANGE.value,
                "next_action": NextAction.ANSWER.value,
                "request_mode": RequestMode.INFORMATIONAL.value,
            },
            "dynamic_handoff_reason": "STATE_CONFLICT",
            "dynamic_handoff_order_refs": {
                "orderId": "o-1",
                "orderItemId": "i-1",
            },
        }
    )

    args = transfer.await_args.args
    kwargs = transfer.await_args.kwargs
    assert args[:2] == (agent_msg, "订单还没发货，收货地址怎么改")
    assert args[2]["next_action"] == NextAction.HANDOFF.value
    assert args[2]["request_mode"] == RequestMode.HUMAN_SUPPORT.value
    assert args[2]["handoff_reason"] == "STATE_CONFLICT"
    assert kwargs == {
        "verified_order_refs": {"orderId": "o-1", "orderItemId": "i-1"},
        "finish_episode": False,
    }
    clear.assert_awaited_once_with("u1")
    assert result["outcome"] == "human_support"
    assert result["route"] == "end"

def test_thread_id_format():
    assert thread_id_for("user_a", 42) == "user_a:42"


@pytest.mark.asyncio
async def test_product_consult_without_authoritative_identity_skips_llm(monkeypatch):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(graph_max_react_rounds=4),
    )

    result = await agent_loop_node(
        _agent_loop_state(
            intent=IntentKind.PRODUCT_CONSULT.value,
            user_text="这款耳机支持主动降噪吗？",
        )
    )

    bind_llm.assert_not_called()
    assert result["llm_skipped"] is True
    assert result["llm_skip_reason"] == "missing_authoritative_product_identity"
    assert result["chunks"] == [
        "要核对是否支持主动降噪，请提供具体耳机品牌/型号，或发送商品卡片。"
    ]


@pytest.mark.asyncio
async def test_display_technology_comparison_skips_identity_clarification_and_llm(
    monkeypatch,
):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())

    state = _agent_loop_state(
        intent=IntentKind.PRODUCT_CONSULT.value,
        user_text="不要给我列一堆商品，只解释 OLED 和 Mini LED 的区别",
    )
    state["rag_source_refs"] = [
        {
            "id": "display-guide",
            "factIds": ["product.display_technology_boundary"],
        }
    ]
    result = await agent_loop_node(state)

    bind_llm.assert_not_called()
    assert result["llm_skip_reason"] == "bounded_display_technology_explanation"
    assert "像素自发光" in result["chunks"][0]
    assert "[1]" in result["chunks"][0]


@pytest.mark.asyncio
async def test_named_product_comparison_resolves_names_instead_of_reasking(monkeypatch):
    compare = AsyncMock(
        return_value={
            "chunks": ["已比较"],
            "tools_called": ["SEARCH_PRODUCTS", "COMPARE_PRODUCTS"],
            "route": "finalize",
        }
    )
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.forced_named_product_comparison", compare)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())

    result = await agent_loop_node(
        _agent_loop_state(
            intent=IntentKind.PRODUCT_CONSULT.value,
            user_text="这款 WH-1000XM6 和十周年版主要差在哪",
        )
    )

    compare.assert_awaited_once()
    assert result["llm_skip_reason"] == "named_product_comparison"
    assert result["tools_called"] == ["SEARCH_PRODUCTS", "COMPARE_PRODUCTS"]


@pytest.mark.asyncio
async def test_ambiguous_payment_failure_skips_llm_for_funds_state(monkeypatch):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(graph_max_react_rounds=4),
    )

    result = await agent_loop_node(
        _agent_loop_state(
            intent=IntentKind.PAYMENT_ISSUE.value,
            user_text="支付失败了",
            intent_decision={"risk_level": RiskLevel.MEDIUM.value},
        )
    )

    bind_llm.assert_not_called()
    assert result["llm_skipped"] is True
    assert result["llm_skip_reason"] == "funds_state_not_confirmed"
    assert result["deterministic_clarification"] is True
    assert "是否已有扣款记录" in result["chunks"][0]


@pytest.mark.asyncio
async def test_confirmed_no_deduction_payment_failure_skips_llm(monkeypatch):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())

    state = _agent_loop_state(
        intent=IntentKind.PAYMENT_ISSUE.value,
        user_text="支付失败但没有扣款，怎么办",
        intent_decision={"risk_level": RiskLevel.MEDIUM.value},
        request_mode=RequestMode.INFORMATIONAL.value,
    )
    state["rag_source_refs"] = [
        {"id": "payment-guide", "factIds": ["payment.safe_retry_guidance"]}
    ]
    result = await agent_loop_node(state)

    bind_llm.assert_not_called()
    assert result["llm_skipped"] is True
    assert result["llm_skip_reason"] == "funds_not_deducted"
    assert result["deterministic_clarification"] is False
    assert result["structured_result_finalized"] is True
    assert result["route"] == "finalize"
    assert "支付失败且没有扣款。[1]" in result["chunks"][0]
    assert "若仍为待支付" in result["chunks"][0]
    assert result["chunks"][0].endswith("[1]")


@pytest.mark.asyncio
async def test_payment_retry_before_password_entry_answers_conditions_without_llm(
    monkeypatch,
):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())

    state = _agent_loop_state(
        intent=IntentKind.PAYMENT_ISSUE.value,
        user_text="付款页卡住了，我还没输入密码，先告诉我能否重试",
        intent_decision={"risk_level": RiskLevel.MEDIUM.value},
        request_mode=RequestMode.INFORMATIONAL.value,
    )
    state["rag_source_refs"] = [
        {"id": "payment-guide", "factIds": ["payment.safe_retry_guidance"]}
    ]
    result = await agent_loop_node(state)

    bind_llm.assert_not_called()
    assert result["llm_skip_reason"] == "payment_preauth_retry_guidance"
    assert "确认没有扣款。[1]" in result["chunks"][0]
    assert "可从订单页重新发起一次支付。[1]" in result["chunks"][0]
    assert result["route"] == "finalize"


@pytest.mark.asyncio
async def test_feedback_only_without_handoff_is_acknowledged_without_llm(monkeypatch):
    bind_llm = MagicMock(side_effect=AssertionError("LLM must not be called"))
    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())

    result = await agent_loop_node(
        _agent_loop_state(
            intent=IntentKind.COMPLAINT.value,
            user_text="物流慢归慢，我现在只反馈一下，不用转人工",
            intent_decision={"risk_level": RiskLevel.MEDIUM.value},
            request_mode=RequestMode.INFORMATIONAL.value,
        )
    )

    bind_llm.assert_not_called()
    assert result["llm_skip_reason"] == "feedback_only_acknowledgement"
    assert "不会发起人工转接" in result["chunks"][0]
    assert result["route"] == "finalize"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_decision", "request_mode", "user_text"),
    [
        ({"risk_level": RiskLevel.HIGH.value}, RequestMode.READ_QUERY.value, "支付失败了"),
        ({"next_action": NextAction.HANDOFF.value}, RequestMode.READ_QUERY.value, "支付失败了"),
        ({}, RequestMode.HUMAN_SUPPORT.value, "支付失败了"),
        ({"risk_level": RiskLevel.HIGH.value}, RequestMode.READ_QUERY.value, "支付失败但没有扣款"),
        ({"next_action": NextAction.HANDOFF.value}, RequestMode.READ_QUERY.value, "支付失败但没有扣款"),
        ({}, RequestMode.HUMAN_SUPPORT.value, "支付失败但没有扣款"),
    ],
)
async def test_payment_failure_clarification_does_not_override_risk_or_user_boundary(
    monkeypatch,
    intent_decision,
    request_mode,
    user_text,
):
    bind_llm = MagicMock(return_value=object())

    async def stream(_llm, _messages, *_args, **_kwargs):
        return AIMessage(content="已进入后续处理路径。")

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.rt.stream_llm_turn", stream)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", MagicMock())
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=4,
            force_mcp_on_llm_skip=False,
            llm_model="test-model",
        ),
    )

    result = await agent_loop_node(
        _agent_loop_state(
            intent=IntentKind.PAYMENT_ISSUE.value,
            user_text=user_text,
            intent_decision=intent_decision,
            request_mode=request_mode,
        )
    )

    bind_llm.assert_called_once()
    assert result.get("llm_skipped") is not True


def test_policy_and_support_intents_prefetch_published_knowledge():
    for intent in (
        IntentKind.CHAT,
        IntentKind.PRODUCT_CONSULT,
        IntentKind.REFUND,
        IntentKind.QUERY_LOGISTICS,
        IntentKind.QUERY_COUPON,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.AFTERSALES_UNKNOWN,
    ):
        assert should_prefetch_rag(intent, agentic_rag=False)

    for intent in (
        IntentKind.PRODUCT_SEARCH,
        IntentKind.QUERY_ORDER,
        IntentKind.HUMAN_REQUEST,
    ):
        assert not should_prefetch_rag(intent, agentic_rag=False)

    assert not should_prefetch_rag(IntentKind.REFUND, agentic_rag=True)


def test_conditional_rag_opens_only_for_miss_or_complex_policy_question():
    assert should_prefetch_rag(IntentKind.REFUND, rag_mode="conditional")
    assert not should_prefetch_rag(IntentKind.REFUND, rag_mode="agentic")
    assert not should_open_agentic_rag(
        rag_mode="conditional",
        user_text="退款规则",
        intent=IntentKind.REFUND,
        prefetched=True,
        has_evidence=True,
    )
    assert should_open_agentic_rag(
        rag_mode="conditional",
        user_text="退款规则",
        intent=IntentKind.REFUND,
        prefetched=True,
        has_evidence=False,
    )
    assert should_open_agentic_rag(
        rag_mode="conditional",
        user_text="这个订单能不能退，同时运费规则是什么",
        intent=IntentKind.REFUND,
        prefetched=True,
        has_evidence=True,
    )
    assert requires_rag_evidence(
        "这个订单能不能退，规则是什么", IntentKind.QUERY_ORDER
    )


@pytest.mark.asyncio
async def test_grounded_policy_answer_uses_one_bounded_tool_free_turn(monkeypatch):
    bound_options: list[dict] = []

    def bind_llm(**kwargs):
        bound_options.append(kwargs)
        return object()

    async def invoke(_llm, _messages, *, model):
        assert model == "test-model"
        return AIMessage(content="支持七天无理由退货。[1]")

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.invoke_llm_with_metrics", invoke)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=4,
            force_mcp_on_llm_skip=True,
            llm_model="test-model",
        ),
    )
    state = {
        "agent_msg": {"userMessage": "平台的七天退货政策是什么？"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [
            SystemMessage(content="grounding rules"),
            HumanMessage(content="平台的七天退货政策是什么？"),
        ],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": "平台的七天退货政策是什么？",
        "from_product": False,
        "tools_called": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": IntentKind.REFUND.value,
        "intent_data": None,
        "rag_evidence_required": True,
        "rag_evidence_state": "SUPPORTED",
        "rag_evidence_items": [{"citation": 1, "text": "七天规则"}],
        "rag_agentic_allowed": False,
        "chunks": [],
    }

    result = await agent_loop_node(state)

    assert bound_options == [
        {"tools_enabled": False, "max_tokens": 384, "disable_thinking": True}
    ]
    assert result["route"] == "finalize"
    assert result["pending_tool_calls"] == []
    assert result["chunks"] == ["支持七天无理由退货。[1]"]


@pytest.mark.asyncio
async def test_action_proposal_disables_sdk_retry_and_suppresses_fallback(
    monkeypatch,
):
    bound_options: list[dict] = []
    recorded = MagicMock()

    def bind_llm(**kwargs):
        bound_options.append(kwargs)
        return object()

    async def invoke(_llm, _messages, *, model):
        assert model == "primary-model"
        raise TimeoutError("provider timeout")

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", bind_llm)
    monkeypatch.setattr("app.graph.nodes.rt.push_chat_error", AsyncMock())
    monkeypatch.setattr(
        "app.graph.nodes.redis_service.clear_bound_message_id", AsyncMock()
    )
    monkeypatch.setattr("app.graph.nodes.invoke_llm_with_metrics", invoke)
    monkeypatch.setattr("app.graph.nodes.has_fallback_chat_llm", lambda: True)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", recorded)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=3,
            force_mcp_on_llm_skip=True,
            llm_model="primary-model",
            llm_fallback_model="fallback-model",
        ),
    )
    state = {
        "agent_msg": {"userMessage": "取消订单 A123"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [HumanMessage(content="取消订单 A123")],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": "取消订单 A123",
        "from_product": False,
        "tools_called": [],
        "pending_tool_calls": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": IntentKind.CANCEL_ORDER.value,
        "intent_data": None,
        "request_mode": "ACTION_PROPOSAL",
        "rag_evidence_required": False,
        "chunks": [],
    }

    result = await agent_loop_node(state)

    assert result == {"finished": True, "route": "end", "outcome": "llm_error"}
    assert bound_options == [
        {
            "tools_enabled": True,
            "max_tokens": None,
            "disable_thinking": False,
            "max_retries": 0,
        }
    ]
    suppressed = next(
        call
        for call in recorded.call_args_list
        if call.args[0] == "LLM_FALLBACK_SUPPRESSED"
    )
    assert suppressed.kwargs["output_data"]["reason"] == (
        "non_idempotent_or_write_path"
    )


@pytest.mark.asyncio
async def test_grounded_policy_answer_uses_one_bounded_repair(monkeypatch):
    responses = iter(
        [
            AIMessage(content="支持七天无理由退货。"),
            AIMessage(content="平台支持七天无理由退货。[1]"),
        ]
    )
    recorded = MagicMock()

    async def invoke(_llm, _messages, *, model):
        assert model == "test-model"
        return next(responses)

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.nodes.invoke_llm_with_metrics", invoke)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", recorded)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=4,
            force_mcp_on_llm_skip=True,
            llm_model="test-model",
        ),
    )
    state = {
        "agent_msg": {"userMessage": "平台的七天退货政策是什么？"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [HumanMessage(content="平台的七天退货政策是什么？")],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": "平台的七天退货政策是什么？",
        "from_product": False,
        "tools_called": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": IntentKind.REFUND.value,
        "intent_data": None,
        "rag_evidence_required": True,
        "rag_evidence_state": "SUPPORTED",
        "rag_evidence_items": [
            {
                "citation": 1,
                "text": "平台支持七天无理由退货。",
                "ref": {"source": "退货政策"},
            }
        ],
        "rag_safe_business_query": "平台的七天退货政策是什么？",
        "rag_agentic_allowed": False,
        "chunks": [],
    }

    result = await agent_loop_node(state)

    assert result["chunks"] == ["平台支持七天无理由退货。[1]"]
    assert result["rag_repair_attempted"] is True
    repair_call = next(
        call for call in recorded.call_args_list if call.args[0] == "RAG_GENERATION_REPAIR"
    )
    assert repair_call.kwargs["status"] == "OK"
    assert repair_call.kwargs["output_data"]["repairedAnswer"] == result["chunks"][0]


@pytest.mark.asyncio
async def test_grounded_policy_repair_failure_is_explicit(monkeypatch):
    calls = 0
    recorded = MagicMock()

    async def invoke(_llm, _messages, *, model):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AIMessage(content="支持七天无理由退货。")
        raise TimeoutError("repair provider timeout")

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.nodes.invoke_llm_with_metrics", invoke)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", recorded)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=4,
            force_mcp_on_llm_skip=True,
            llm_model="test-model",
        ),
    )
    state = {
        "agent_msg": {"userMessage": "平台的七天退货政策是什么？"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [HumanMessage(content="平台的七天退货政策是什么？")],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": "平台的七天退货政策是什么？",
        "from_product": False,
        "tools_called": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": IntentKind.REFUND.value,
        "intent_data": None,
        "rag_evidence_required": True,
        "rag_evidence_state": "SUPPORTED",
        "rag_evidence_items": [
            {
                "citation": 1,
                "text": "平台支持七天无理由退货。",
                "ref": {"source": "退货政策"},
            }
        ],
        "rag_safe_business_query": "平台的七天退货政策是什么？",
        "rag_agentic_allowed": False,
        "chunks": [],
    }

    result = await agent_loop_node(state)

    assert result["chunks"] == ["支持七天无理由退货。"]
    repair_call = next(
        call for call in recorded.call_args_list if call.args[0] == "RAG_GENERATION_REPAIR"
    )
    assert repair_call.kwargs["status"] == "ERROR"
    assert repair_call.kwargs["error_code"] == "TimeoutError"


@pytest.mark.asyncio
async def test_grounded_policy_uses_deterministic_fallback_only_for_grounding_evidence(
    monkeypatch,
):
    responses = iter(
        [
            AIMessage(content="根据当前知识库，我无法确认该信息。请联系人工客服核实。"),
            AIMessage(content="根据当前知识库，我无法确认该信息。请联系人工客服核实。"),
        ]
    )
    recorded = MagicMock()

    async def invoke(_llm, _messages, *, model):
        assert model == "test-model"
        return next(responses)

    monkeypatch.setattr("app.graph.nodes.rt.is_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr("app.graph.nodes.rt.bind_agent_llm", lambda **_kwargs: object())
    monkeypatch.setattr("app.graph.nodes.invoke_llm_with_metrics", invoke)
    monkeypatch.setattr("app.graph.nodes.episode_service.record_step", recorded)
    monkeypatch.setattr(
        "app.graph.nodes.get_settings",
        lambda: SimpleNamespace(
            graph_max_react_rounds=4,
            force_mcp_on_llm_skip=True,
            llm_model="test-model",
        ),
    )
    state = {
        "agent_msg": {"userMessage": "RAG检索不足时的grounding含义是什么？"},
        "user_id": "u1",
        "message_id": 1,
        "llm_messages": [HumanMessage(content="RAG检索不足时的grounding含义是什么？")],
        "react_round": 0,
        "card": None,
        "message_card": None,
        "user_text": "RAG检索不足时的grounding含义是什么？",
        "from_product": False,
        "tools_called": [],
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": IntentKind.CHAT.value,
        "intent_data": None,
        "rag_evidence_required": True,
        "rag_evidence_state": "SUPPORTED",
        "rag_evidence_items": [
            {
                "citation": 1,
                "factIds": ["rag.retrieval_and_abstention"],
                "text": "知识库证据不足时，助手应明确说明并建议联系人工客服。",
                "ref": {"source": "知识检索"},
            }
        ],
        "rag_safe_business_query": "RAG检索不足时的grounding含义是什么？",
        "rag_agentic_allowed": False,
        "chunks": [],
    }

    result = await agent_loop_node(state)

    assert result["chunks"] == [
        "Grounding 表示回答必须以检索到的证据为依据。[1] 当证据不足时，系统会明确说明当前证据不足，并建议联系人工客服。[1]"
    ]
    assert getattr(result["llm_messages"][-1], "content") == result["chunks"][0]
    fallback_call = next(
        call
        for call in recorded.call_args_list
        if call.args[0] == "RAG_GENERATION_DETERMINISTIC_FALLBACK"
    )
    assert fallback_call.kwargs["output_data"]["usageAdded"] is False


@pytest.mark.asyncio
async def test_should_resume_reads_dict_cursor_row():
    """DictCursor 返回 dict，不能用 row[0]（会 KeyError(0)）。"""
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(
        return_value={"status": MSG_STATUS_NORMAL, "assistant_message": None}
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.graph.runner.acquire", return_value=mock_cm):
        with patch("app.graph.runner.get_checkpointer") as mock_ckpt:
            mock_ckpt.return_value.hydrate_thread = AsyncMock(return_value=False)
            mock_ckpt.return_value.aget_tuple = AsyncMock(return_value=None)
            with patch("app.graph.runner.redis_service") as mock_redis:
                mock_redis.client = MagicMock()
                result = await _should_resume("u1", 1, "u1:1")
    assert result is False


@pytest.mark.asyncio
async def test_graph_end_exposes_deterministic_path_and_llm_call_outcomes(monkeypatch):
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "outcome": "ok",
                "intent": IntentKind.CHAT.value,
                "tools_called": [],
                "orchestration_mode": "workflow",
                "orchestration_reason": "deterministic_business_path",
                "llm_skipped": True,
                "llm_skip_reason": "deterministic_social_reply",
                "structured_result_finalized": True,
            }
        )
    )
    checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    recorded = MagicMock()
    monkeypatch.setattr("app.graph.runner.get_compiled_graph", lambda: graph)
    monkeypatch.setattr(
        "app.graph.runner.get_checkpointer", lambda _client: checkpointer
    )
    monkeypatch.setattr(
        "app.graph.runner.redis_service", SimpleNamespace(client=object())
    )
    monkeypatch.setattr("app.graph.runner._should_resume", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "app.graph.runner.get_settings",
        lambda: SimpleNamespace(agent_budget_enabled=False),
    )
    monkeypatch.setattr(
        "app.graph.runner.snapshot_cost_summary",
        lambda **_kwargs: {
            "llmCalls": 1,
            "successfulLlmCalls": 0,
            "failedLlmCalls": 1,
            "inputTokens": 0,
            "outputTokens": 0,
            "costStatus": "MISSING_USAGE",
        },
    )
    monkeypatch.setattr("app.graph.runner.episode_service.record_step", recorded)
    monkeypatch.setattr("app.graph.runner.episode_service.update_run", MagicMock())
    monkeypatch.setattr("app.graph.runner.episode_service.finish_run", MagicMock())

    outcome = await run_agent_graph(
        {
            "userId": "u1",
            "messageId": 7,
            "userMessage": "谢谢",
            "intent": IntentKind.CHAT.value,
        }
    )

    assert outcome == "ok"
    graph_end = next(
        call for call in recorded.call_args_list if call.args[0] == "GRAPH_END"
    )
    output = graph_end.kwargs["output_data"]
    assert output["orchestrationMode"] == "workflow"
    assert output["llmSkipped"] is True
    assert output["llmSkipReason"] == "deterministic_social_reply"
    assert output["structuredResultFinalized"] is True
    assert output["llmCallCount"] == 1
    assert output["successfulLlmCallCount"] == 0
    assert output["failedLlmCallCount"] == 1
