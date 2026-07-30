"""工具白名单与风险等级的单一事实源。

原先这些信息散在三处，各自用不同方式表达同一件事：

* ``tool_guard`` 里两个 frozenset（``READ_TOOLS`` / ``WRITE_TOOLS``）决定放行与写标记；
* ``forced_tools`` 里 ``_BIZ_TYPE_BY_TOOL`` 加一句 ``startswith("PROPOSE_")`` 决定卡片类型；
* ``app/mcp/tools.py`` 的 description 里写着 ``[READ]`` / ``[WRITE]`` 给模型看。

加一个工具就要记得改三个地方，漏一处的表现各不相同：漏 guard 是调用被拒，
漏 biz_type 是卡片渲染不出来，漏 description 是模型判断变差。都不会报错，
只会在某条链路上安静地不对。所以这里收敛成一张表，其余模块只查表。

风险只分两档，没有照搬"高危写操作"那一档：本服务里所有写操作都是 ``PROPOSE_*``，
即产出待确认提案、由用户点确认后才真正落库（token 服务端签发并校验归属，
见 ``app/services/agent_runtime.py``）。也就是说模型手上没有能直接改数据的工具，
再分一档出来会是一张空集合，只增加读代码的人要理解的概念。真出现直接落库的工具时，
在 ``ToolRiskLevel`` 里加一档、给它单独的确认策略，比现在预留一个空壳更清楚。

新增工具的检查项由 ``tests/test_tool_policy.py`` 双向断言：表和 MCP 注册表必须
互相覆盖，且 description 里的 ``[READ]``/``[WRITE]`` 标记要和表里的风险等级一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolRiskLevel(str, Enum):
    """工具风险等级。

    注意与 ``app.domain.intent.types.RiskLevel`` 不是一回事：那个描述"这轮会话有多棘手"
    （LOW/MEDIUM/HIGH，由意图识别给出，用于决定是否转人工），这个描述"这个工具会不会改数据"
    （静态属性，写死在表里，不由模型或对话内容决定）。
    """

    READ_ONLY = "READ_ONLY"
    """只读查询。参数校验通过即可直接执行，结果只用于回答。"""

    PROPOSE = "PROPOSE"
    """写操作提案。只产出待确认单，用户点确认后才落库。"""


@dataclass(frozen=True)
class ToolPolicy:
    """一个工具的静态策略。"""

    name: str
    risk: ToolRiskLevel
    biz_type: str | None
    """工具自身没返回 biz_type 时，前端业务卡片类型的兜底值；None 表示不渲染卡片。"""

    @property
    def is_write(self) -> bool:
        return self.risk is not ToolRiskLevel.READ_ONLY


def _policy(name: str, risk: ToolRiskLevel, biz_type: str | None = None) -> ToolPolicy:
    return ToolPolicy(name=name, risk=risk, biz_type=biz_type)


_READ = ToolRiskLevel.READ_ONLY
_PROPOSE = ToolRiskLevel.PROPOSE

# 必须与 app/mcp/tools.py 的 build_mcp_tools() 一一对应。
TOOL_POLICIES: dict[str, ToolPolicy] = {
    p.name: p
    for p in (
        # 商品检索/详情不进业务卡片：结果由 biz_payload 单独拼商品卡，不走这里的兜底。
        _policy("SEARCH_PRODUCTS", _READ),
        _policy("GET_PRODUCT_DETAIL", _READ),
        _policy("QUERY_ORDERS", _READ, "query_order"),
        _policy("QUERY_LOGISTICS", _READ, "query_logistics"),
        _policy("QUERY_COMMENT", _READ, "query_comment"),
        _policy("QUERY_USER_COUPONS", _READ, "query_coupon"),
        # P3-1 Agentic RAG: in-process knowledge/FAQ retrieval tool.
        _policy("SEARCH_KNOWLEDGE", _READ),
        # 写操作一律渲染确认卡片，用户点了才落库。
        _policy("PROPOSE_REFUND", _PROPOSE, "action_confirm"),
        _policy("PROPOSE_CONFIRM_RECEIPT", _PROPOSE, "action_confirm"),
        _policy("PROPOSE_PRODUCT_REVIEW", _PROPOSE, "action_confirm"),
        _policy("PROPOSE_RECOMMENT", _PROPOSE, "action_confirm"),
    )
}

ALL_ALLOWED_TOOLS = frozenset(TOOL_POLICIES)
READ_TOOLS = frozenset(n for n, p in TOOL_POLICIES.items() if p.risk is _READ)
WRITE_TOOLS = frozenset(n for n, p in TOOL_POLICIES.items() if p.is_write)


def policy_for(tool_name: str) -> ToolPolicy | None:
    """查表；表外工具返回 None（调用方应据此拒绝，而不是放行）。"""
    return TOOL_POLICIES.get(tool_name)


def is_allowed(tool_name: str) -> bool:
    return tool_name in TOOL_POLICIES


def is_write_tool(tool_name: str) -> bool:
    """表外工具返回 False：未知工具在 ``is_allowed`` 就该被拒，不该走到写分支。"""
    policy = TOOL_POLICIES.get(tool_name)
    return bool(policy and policy.is_write)


def fallback_biz_type(tool_name: str) -> str | None:
    policy = TOOL_POLICIES.get(tool_name)
    return policy.biz_type if policy else None
