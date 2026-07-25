"""模型漏调工具时的兜底分支。

这四个分支原先内联在 agent_loop_node 里且没有测试，但它们决定了
"用户问订单能不能看到订单卡片"，回归了就是可见故障。
"""

import pytest

from app.domain.intent.types import IntentKind
from app.graph import forced_tools
from app.services.tool_invoke_result import ToolInvokeResult


@pytest.fixture
def record_invoke(monkeypatch):
    """替掉 MCP 调用，记录参数并返回可控结果。"""
    calls: list[tuple[str, dict, str]] = []
    result_box: dict[str, ToolInvokeResult] = {"result": ToolInvokeResult(content="ok")}

    async def fake_invoke(tool_name, args, user_id):
        calls.append((tool_name, args, user_id))
        return result_box["result"]

    monkeypatch.setattr(forced_tools.mcp_tool_router, "invoke", fake_invoke)
    return calls, result_box


async def test_similar_search_excludes_current_product(record_invoke):
    calls, _ = record_invoke

    out = await forced_tools.forced_product_search(
        messages=[],
        user_id="u1",
        keyword="有没有类似的",
        llm_body="我帮你找找",
        exclude_product_id="p100",
        log_event="search_fallback_after_llm_skip",
    )

    assert calls == [("SEARCH_PRODUCTS", {"keyword": "有没有类似的", "excludeProductId": "p100"}, "u1")]
    assert out["route"] == "finalize"
    assert out["search_fallback_done"] is True
    # 模型已经说出来的话必须保留，否则用户看到回答凭空消失。
    assert out["chunks"] == ["我帮你找找"]


async def test_category_switch_search_keeps_current_product(record_invoke):
    calls, _ = record_invoke

    await forced_tools.forced_product_search(
        messages=[],
        user_id="u1",
        keyword="想买零食",
        llm_body="",
        exclude_product_id=None,
        log_event="category_switch_search_fallback",
    )

    assert calls == [("SEARCH_PRODUCTS", {"keyword": "想买零食"}, "u1")]


async def test_forced_intent_tool_appends_tool_message_and_infers_biz_type(record_invoke):
    calls, box = record_invoke
    box["result"] = ToolInvokeResult(content="订单查询结果", order_ids=["o1"])
    messages: list = []

    out = await forced_tools.forced_tool_for_intent(
        messages=messages,
        user_id="u1",
        intent=IntentKind.QUERY_ORDER.value,
        intent_data=None,
        user_text="我的订单呢",
    )

    assert calls[0][0] == "QUERY_ORDERS"
    # 工具结果要以 ToolMessage 回灌，否则模型下一轮看不到自己"调过"什么。
    assert len(messages) == 1
    assert messages[0].content == "订单查询结果"
    assert out["biz_type"] == "query_order"
    assert out["tool_biz"] == {"productIds": [], "productNames": [], "orderIds": ["o1"]}
    assert out["route"] == "finalize"


async def test_forced_intent_tool_suppresses_text_when_cards_present(record_invoke):
    _, box = record_invoke
    box["result"] = ToolInvokeResult(content="文本描述", assistant_cards='[{"orderId":"o1"}]')

    out = await forced_tools.forced_tool_for_intent(
        messages=[],
        user_id="u1",
        intent=IntentKind.QUERY_ORDER.value,
        intent_data=None,
        user_text="我的订单呢",
    )

    # 有卡片就不再重复输出文本，否则同一批数据渲染两遍。
    assert out["chunks"] == []
    assert out["assistant_cards"] == '[{"orderId":"o1"}]'


async def test_cancel_order_prepends_self_service_guide(record_invoke):
    _, box = record_invoke
    box["result"] = ToolInvokeResult(content="订单详情")

    out = await forced_tools.forced_tool_for_intent(
        messages=[],
        user_id="u1",
        intent=IntentKind.CANCEL_ORDER.value,
        intent_data="20260612204304352OBbW6OiMj2BUUhY",
        user_text="帮我取消订单",
    )

    assert "我的订单" in out["chunks"][0]
    assert "订单详情" in out["chunks"][0]


async def test_forced_intent_tool_returns_none_when_args_incomplete(record_invoke):
    calls, _ = record_invoke

    out = await forced_tools.forced_tool_for_intent(
        messages=[],
        user_id="u1",
        intent=IntentKind.QUERY_LOGISTICS.value,
        intent_data=None,
        user_text="我的快递到哪了",  # 没有订单号
    )

    # 参数不全时不能瞎调工具，交回模型去追问。
    assert out is None
    assert calls == []


async def test_propose_tool_maps_to_action_confirm(record_invoke):
    _, box = record_invoke
    box["result"] = ToolInvokeResult(content="请确认收货")

    out = await forced_tools.forced_tool_for_intent(
        messages=[],
        user_id="u1",
        intent=IntentKind.CONFIRM_RECEIPT.value,
        intent_data="20260612204304352OBbW6OiMj2BUUhY",
        user_text="确认收货",
    )

    assert out["biz_type"] == "action_confirm"


async def test_forced_order_list_defaults_to_order_card_type(record_invoke):
    calls, _ = record_invoke

    out = await forced_tools.forced_order_list(
        messages=[],
        user_id="u1",
        intent=IntentKind.QUERY_ORDER.value,
        order_id=None,
    )

    assert calls == [("QUERY_ORDERS", {}, "u1")]
    assert out["biz_type"] == "query_order"
    assert out["chunks"] == []


async def test_forced_order_list_passes_order_id_when_known(record_invoke):
    calls, _ = record_invoke

    await forced_tools.forced_order_list(
        messages=[], user_id="u1", intent=IntentKind.QUERY_ORDER.value, order_id="o9"
    )

    assert calls == [("QUERY_ORDERS", {"orderId": "o9"}, "u1")]
