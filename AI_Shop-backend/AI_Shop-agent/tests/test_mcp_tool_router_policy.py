"""路由器的准入门：表外工具进不来，写操作留痕。

这道门是模型与业务写接口之间唯一的代码级拦截点，之前没有测试覆盖。
重点不是"合法工具能调通"，而是"不合法的调不动，且拦截发生在打到 MCP 之前"——
放行了再靠下游校验就已经晚了。
"""

from unittest.mock import AsyncMock

import pytest
import structlog

from app.harness.agents.contracts import VerifiedImageContext, VisualSubject
from app.services import mcp_tool_router as router_module
from app.services.episode_service import bind_episode
from app.services.mcp_tool_router import mcp_tool_router
from app.services.tool_invoke_result import ToolInvokeResult


@pytest.fixture
def sent_to_mcp(monkeypatch):
    """记录真正打到 MCP 的调用；被拦下的调用不该出现在这里。"""
    calls: list[tuple[str, dict]] = []

    async def fake_call_tool(name, args):
        calls.append((name, args))
        return ToolInvokeResult(content="ok")

    monkeypatch.setattr(router_module.mcp_streamable_client, "call_tool", fake_call_tool)
    return calls


@pytest.mark.parametrize(
    "tool_name",
    [
        "DROP_TABLE_ORDERS",
        # 光看前缀像写工具，但没在策略表里注册：不能凭命名混进来。
        "PROPOSE_NOT_REGISTERED",
        "",
    ],
)
async def test_unregistered_tool_never_reaches_mcp(sent_to_mcp, tool_name):
    out = await mcp_tool_router.invoke(tool_name, {}, "u1")

    assert "未知工具" in out.content
    assert sent_to_mcp == []


async def test_spoofed_user_id_is_replaced_not_forwarded(sent_to_mcp):
    """模型编的 userId 不能透传：以调用方传入的身份为准，两种命名都要覆盖。"""
    await mcp_tool_router.invoke("QUERY_ORDERS", {"userId": "attacker", "orderId": "O9"}, "u1")
    await mcp_tool_router.invoke("QUERY_ORDERS", {"user_id": "attacker"}, "u1")

    assert [args["userId"] for _, args in sent_to_mcp] == ["u1", "u1"]


async def test_snake_case_user_id_cannot_survive_as_a_fallback(sent_to_mcp):
    """身份注入不能依赖调用方保证 user_id 非空。

    _to_mcp_args 取身份时会在 userId 为 None 时退回去取 user_id。目前两个调用方都保证了
    非空（require_login / str(...)），所以退路走不到；但这道防线不该建立在别人的不变量上。
    """
    await mcp_tool_router.invoke("QUERY_ORDERS", {"user_id": "attacker"}, None)

    _, args = sent_to_mcp[0]
    assert args["userId"] != "attacker"


async def test_user_id_mismatch_is_logged_as_a_signal(sent_to_mcp):
    """自称身份与实际不符时要留下线索：这是提示注入或跨会话串号的征兆。"""
    with structlog.testing.capture_logs() as logs:
        await mcp_tool_router.invoke("QUERY_ORDERS", {"userId": "attacker"}, "u1")

    hits = [e for e in logs if e["event"] == "tool_arg_user_id_mismatch"]
    assert len(hits) == 1
    assert hits[0]["claimed"] == "attacker"
    assert hits[0]["user_id"] == "u1"
    # 只报警不拒绝：调用照常带着正确身份打出去
    assert sent_to_mcp[0][1]["userId"] == "u1"


async def test_matching_user_id_is_not_flagged(sent_to_mcp):
    """模型把工具结果里的 userId 原样带回来是正常行为，不该当异常报。"""
    with structlog.testing.capture_logs() as logs:
        await mcp_tool_router.invoke("QUERY_ORDERS", {"userId": "u1"}, "u1")

    assert [e for e in logs if e["event"] == "tool_arg_user_id_mismatch"] == []


async def test_absent_user_id_is_not_flagged(sent_to_mcp):
    with structlog.testing.capture_logs() as logs:
        await mcp_tool_router.invoke("QUERY_ORDERS", {"orderId": "O1"}, "u1")

    assert [e for e in logs if e["event"] == "tool_arg_user_id_mismatch"] == []


async def test_episode_request_id_is_forwarded_for_forced_mcp_calls(sent_to_mcp):
    """Forced workflow tools must preserve the API request binding across MCP."""
    with bind_episode(
        "run-eval-1",
        message_id=101,
        user_id="u1",
        request_id="eval-req-1",
    ):
        await mcp_tool_router.invoke(
            "SEARCH_PRODUCTS", {"keyword": "耳机"}, "u1", call_id="forced_mcp"
        )

    assert sent_to_mcp == [
        (
            "SEARCH_PRODUCTS",
            {
                "userId": "u1",
                "keyword": "耳机",
                "requestId": "eval-req-1",
                "runId": "run-eval-1",
            },
        )
    ]


async def test_write_tool_leaves_audit_log(sent_to_mcp):
    with structlog.testing.capture_logs() as logs:
        await mcp_tool_router.invoke("PROPOSE_REFUND", {"orderItemId": "42"}, "u1")

    audit = [e for e in logs if e["event"] == "write_tool_invoked"]
    assert len(audit) == 1
    assert audit[0]["tool"] == "PROPOSE_REFUND"
    assert audit[0]["risk"] == "PROPOSE"
    assert audit[0]["user_id"] == "u1"


async def test_support_case_category_is_normalized_before_mcp(sent_to_mcp):
    await mcp_tool_router.invoke(
        "PROPOSE_CREATE_SUPPORT_CASE",
        {"category": "商品破损", "description": "外壳破裂"},
        "u1",
    )

    assert sent_to_mcp[0][1]["category"] == "DAMAGED"


async def test_read_tool_leaves_no_audit_log(sent_to_mcp):
    """只读调用量大且没有追溯价值，记了只会淹掉写操作的线索。"""
    with structlog.testing.capture_logs() as logs:
        await mcp_tool_router.invoke("QUERY_ORDERS", {"orderId": "O1"}, "u1")

    assert [e for e in logs if e["event"] == "write_tool_invoked"] == []


async def test_visual_tool_requires_server_verified_image_context(sent_to_mcp):
    result = await mcp_tool_router.invoke(
        "SEARCH_PRODUCTS_BY_IMAGE",
        {"imageAssetId": "img_0123456789abcdef0123456789abcdef"},
        "u1",
    )

    assert result.success is False
    assert result.error_code == "VISUAL_CONTEXT_REQUIRED"
    assert sent_to_mcp == []


async def test_visual_tool_ignores_model_forged_asset_subject_and_bbox(
    monkeypatch, sent_to_mcp
):
    trusted_subject = VisualSubject(
        subject_id="subject_1", label="可信主体", bbox=(10, 20, 800, 900)
    )
    trusted = VerifiedImageContext(
        asset_id="img_0123456789abcdef0123456789abcdef",
        content_sha256="a" * 64,
        mime_type="image/jpeg",
        width=640,
        height=480,
        selected_subject=trusted_subject,
    )
    expected = ToolInvokeResult(content="ok", product_ids=["P1"])
    search = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "app.visual.search_service.visual_product_search_service.search", search
    )

    result = await mcp_tool_router.invoke(
        "SEARCH_PRODUCTS_BY_IMAGE",
        {
            "imageAssetId": "img_ffffffffffffffffffffffffffffffff",
            "selectedSubjectId": "subject_attacker",
            "bbox": [0, 0, 999, 999],
            "queryText": "红色，500 元以内",
        },
        "u1",
        verified_image_context=trusted,
        source_message_id=42,
    )

    assert result is expected
    search.assert_awaited_once_with(
        user_id="u1",
        image_context=trusted,
        query_text="红色，500 元以内",
        source_message_id=42,
    )
    assert sent_to_mcp == []


async def test_tool_result_records_factual_episode_reward_signal(monkeypatch):
    async def fake_call_tool(name, args):
        assert name == "QUERY_ORDERS"
        return ToolInvokeResult(
            content="订单查询完成",
            biz_type="query_order",
            assistant_cards='[{"orderId":"O1"}]',
            source_refs=[{"type": "ORDER", "id": "O1"}],
        )

    updates: list[dict] = []
    monkeypatch.setattr(router_module.mcp_streamable_client, "call_tool", fake_call_tool)
    monkeypatch.setattr(
        router_module.episode_service,
        "update_run",
        lambda **kwargs: updates.append(kwargs),
    )

    await mcp_tool_router.invoke(
        "QUERY_ORDERS",
        {"orderId": "O1"},
        "u1",
        call_id="call-order-1",
    )

    assert len(updates) == 1
    tool_results = updates[0]["reward_signals"]["toolResults"]
    assert len(tool_results) == 1
    assert next(iter(tool_results.values())) == {
        "toolName": "QUERY_ORDERS",
        "success": True,
        "errorCode": None,
        "bizType": "query_order",
        "hasCards": True,
        "hasSourceRefs": True,
    }
