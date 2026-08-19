"""LangGraph 状态机结构测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.constants import MSG_STATUS_NORMAL
from app.domain.intent.types import IntentKind
from app.graph.builder import build_agent_graph
from app.graph.nodes import (
    agent_loop_node,
    requires_rag_evidence,
    should_open_agentic_rag,
    should_prefetch_rag,
)
from app.graph.runner import _should_resume
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

def test_thread_id_format():
    assert thread_id_for("user_a", 42) == "user_a:42"


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
