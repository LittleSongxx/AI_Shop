from app.services.tool_invoke_result import (
    MCP_PROTOCOL,
    MCP_TOOL_CONTRACT,
    ToolInvokeResult,
    parse_tool_wire,
)


def test_tool_result_round_trip_preserves_contract_and_business_data():
    source = ToolInvokeResult(
        content="ok",
        success=False,
        error_code="REMOTE_REJECTED",
        biz_type="PRODUCT_LIST",
        product_ids=["1001"],
        source_refs=[{"chunkId": "c1", "knowledgeVersion": 2}],
        retrieval_trace={"hit": True, "candidateCount": 3},
    )

    restored = parse_tool_wire(source.to_wire())

    assert restored.content == "ok"
    assert restored.success is False
    assert restored.error_code == "REMOTE_REJECTED"
    assert restored.biz_type == "PRODUCT_LIST"
    assert restored.product_ids == ["1001"]
    assert restored.source_refs[0]["chunkId"] == "c1"
    assert restored.retrieval_trace["candidateCount"] == 3
    assert restored.protocol_version == MCP_PROTOCOL
    assert restored.contract_version == MCP_TOOL_CONTRACT


def test_plain_or_malformed_tool_result_is_not_treated_as_current_contract():
    plain = parse_tool_wire("plain result")
    malformed = parse_tool_wire("AISHOP_TOOL_RESULT:{")

    assert plain.protocol_version == ""
    assert plain.contract_version == ""
    assert malformed.protocol_version == ""
    assert malformed.contract_version == ""


def test_snake_case_fields_are_not_another_wire_contract():
    payload = (
        "AISHOP_TOOL_RESULT:"
        '{"protocol_version":"ignored","contract_version":"ignored",'
        '"product_ids":["ignored"],"content":"plain"}'
    )

    parsed = parse_tool_wire(payload)

    assert parsed.content == "plain"
    assert parsed.product_ids == []
    assert parsed.protocol_version == ""
    assert parsed.contract_version == ""


def test_mcp_server_does_not_wrap_business_failure_text_as_success():
    from app.mcp_server.server import _text

    parsed = parse_tool_wire(_text("【退款失败】请输入要退款的订单项ID"))

    assert parsed.success is False
    assert parsed.error_code == "BUSINESS_REJECTED"
