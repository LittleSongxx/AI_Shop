from unittest.mock import AsyncMock

import pytest

from app.services.mcp_tools_service import tool_query_orders


@pytest.mark.asyncio
async def test_empty_order_lookup_does_not_claim_the_order_is_nonexistent(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.mcp_tools_service.order_service.query_orders",
        AsyncMock(return_value=("[]", None, "query_order")),
    )

    result = await tool_query_orders("u1")

    assert "不能证明订单不存在" in result.content
    assert "请提供订单号" in result.content
    assert "不会取消或修改" in result.content
    assert result.source_refs[0]["matched"] is False
