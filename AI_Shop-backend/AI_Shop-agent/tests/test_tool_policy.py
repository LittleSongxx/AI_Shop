"""策略表的完整性约束。

这张表的价值全在"它和别处不会不一致"上。所以这里断言的不是表里某一行长什么样，
而是表与另外两个事实源双向一致：MCP 注册表（模型真能调到什么）和 description 里的
``[READ]``/``[WRITE]`` 标记（模型看到的风险提示）。加工具漏改表时，这些断言会失败。
"""

import re

from app.domain.tool_policy import (
    ALL_ALLOWED_TOOLS,
    READ_TOOLS,
    TOOL_MANIFEST_SCHEMA,
    TOOL_OWNER,
    TOOL_POLICIES,
    WRITE_TOOLS,
    ToolRiskLevel,
    build_tool_manifest,
    fallback_biz_type,
    is_allowed,
    is_write_tool,
    policy_for,
)
from app.harness.guardrails.tool_guard import ToolGuardrail
from app.mcp.tools import build_mcp_tools


def _registered_tools() -> dict[str, str]:
    return {t.name: t.description for t in build_mcp_tools()}


def test_explicit_empty_tool_scope_binds_no_tools():
    assert build_mcp_tools(frozenset()) == []


def test_policy_table_matches_mcp_registry_both_ways():
    registered = set(_registered_tools())
    assert set(TOOL_POLICIES) == registered, (
        f"表里多出: {sorted(set(TOOL_POLICIES) - registered)}; "
        f"表里缺少: {sorted(registered - set(TOOL_POLICIES))}"
    )


def test_description_read_write_tag_matches_risk():
    for name, description in _registered_tools().items():
        tag = re.match(r"\[(READ|WRITE)\]", description)
        assert tag, f"{name} 的 description 缺少 [READ]/[WRITE] 标记"
        expected_write = tag.group(1) == "WRITE"
        assert TOOL_POLICIES[name].is_write is expected_write, (
            f"{name}: description 标记 {tag.group(1)}，表里是 {TOOL_POLICIES[name].risk.value}"
        )


def test_every_entry_is_well_formed():
    for name, policy in TOOL_POLICIES.items():
        assert policy.name == name, "键必须等于 policy.name，否则查表和回读会不一致"
        assert isinstance(policy.risk, ToolRiskLevel)
        assert policy.biz_type is None or policy.biz_type


def test_propose_prefix_and_risk_agree():
    """命名约定与风险等级互为校验：只靠前缀会被拼错的名字骗过去。"""
    for name, policy in TOOL_POLICIES.items():
        assert name.startswith("PROPOSE_") is policy.is_write, name
        if policy.is_write:
            assert policy.risk is ToolRiskLevel.PROPOSE
            # 写操作必须能渲染确认卡片，否则用户没有确认入口就等于操作丢了。
            assert policy.biz_type == "action_confirm", name


def test_derived_sets_partition_the_table():
    assert READ_TOOLS | WRITE_TOOLS == ALL_ALLOWED_TOOLS
    assert not (READ_TOOLS & WRITE_TOOLS)
    assert len(ALL_ALLOWED_TOOLS) == len(TOOL_POLICIES)


def test_unknown_tool_is_denied_not_defaulted():
    assert policy_for("DROP_TABLE_ORDERS") is None
    assert not is_allowed("DROP_TABLE_ORDERS")
    # 未知工具不能被当成写工具，也不能凭前缀混进确认卡片链路。
    assert not is_write_tool("DROP_TABLE_ORDERS")
    assert not is_write_tool("PROPOSE_NOT_REGISTERED")
    assert fallback_biz_type("PROPOSE_NOT_REGISTERED") is None


def test_guardrail_delegates_to_the_table():
    guard = ToolGuardrail()
    for name, policy in TOOL_POLICIES.items():
        assert guard.is_allowed(name)
        assert guard.is_write_tool(name) is policy.is_write
    assert not guard.is_allowed("DROP_TABLE_ORDERS")


def test_tool_manifest_exposes_health_version_entitlement_and_timeout():
    manifest = build_tool_manifest(
        timeout_seconds=4,
        listed_tools=set(TOOL_POLICIES) | {"MCP_CONTRACT", "MCP_RUNTIME_IDENTITY"},
        registry_health="READY",
    )
    assert manifest["schemaVersion"] == TOOL_MANIFEST_SCHEMA
    assert manifest["owner"] == TOOL_OWNER
    assert manifest["health"] == "READY"
    assert manifest["missingTools"] == []
    assert manifest["unexpectedTools"] == []
    by_name = {item["name"]: item for item in manifest["tools"]}
    assert by_name["SEARCH_PRODUCTS"] == {
        "name": "SEARCH_PRODUCTS",
        "version": "aishop-tools/current",
        "owner": TOOL_OWNER,
        "health": "READY",
        "entitlement": "READ_ONLY",
        "timeoutSeconds": 4.0,
        "requiresConfirmation": False,
    }
    assert by_name["PROPOSE_REFUND"]["entitlement"] == "PROPOSE_CONFIRM_REQUIRED"
    assert by_name["PROPOSE_REFUND"]["requiresConfirmation"] is True


def test_tool_manifest_marks_registry_drift_without_weakening_policy():
    manifest = build_tool_manifest(
        timeout_seconds=4,
        listed_tools={"SEARCH_PRODUCTS", "DROP_TABLE_ORDERS"},
        registry_health="READY",
    )
    assert manifest["health"] == "DEGRADED"
    assert "PROPOSE_REFUND" in manifest["missingTools"]
    assert manifest["unexpectedTools"] == ["DROP_TABLE_ORDERS"]
