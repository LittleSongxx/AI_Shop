"""跑分逻辑。CLI 在 run_convo_eval.py，pytest 在 tests/test_convo_eval_frozen.py，都调这里。

为什么不带 LLM：见 benchmarks/README.md。一句话是——带 LLM 就没法冻结，
每次分数都不一样时"跑一次记下来"没有意义，而允许重跑取最好那次就是自己给自己发奖。

所有外部依赖在这里桩掉，桩的行为由 case 的 fixture 字段声明，不由环境决定。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from benchmarks.convo_eval_dataset import Case, load_cases


@dataclass
class CaseOutcome:
    """一条 case 的结果。``checks`` 是维度名到是否通过的映射。"""

    id: str
    subset: str
    split: str
    kind: str
    passed: bool
    checks: dict[str, bool]
    actual: dict[str, Any]
    note: str = ""

    @property
    def failed_dimensions(self) -> list[str]:
        return sorted(k for k, ok in self.checks.items() if not ok)


@dataclass
class EvalReport:
    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def failures(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if not o.passed]

    @property
    def failed_ids(self) -> list[str]:
        return sorted(o.id for o in self.failures)


def _rate(hit: int, total: int) -> float:
    return round(hit / total, 6) if total else 0.0


def _args_match(expected: dict, actual: dict | None) -> bool:
    """期望参数是实际参数的子集即通过。

    用子集而不是全等：工具参数以后可能加可选字段（比如分页），全等会让每加一个
    可选字段就要改一遍题面，而那种改动跟"这一维考的东西"无关。
    但期望里写了的键必须一字不差——包括值的类型，`"5"` 和 `5` 不算相等。
    """
    if actual is None:
        return False
    for key, value in expected.items():
        if key not in actual:
            return False
        if actual[key] != value or type(actual[key]) is not type(value):
            return False
    return True


def _verify_action(row: dict, actual_tool: str | None, actual_args: dict | None) -> bool:
    """校验"动作在该业务状态下是否正确地可执行/被拒绝"。

    期望值为 True 的 case 表达两种正确行为之一：
      - expectTool 为 None：业务状态不可执行时系统拒绝发起动作（没有幻觉提案）；
      - expectTool 非 None：动作正常产出，且关键参数已解析（如 orderItemId）。
    参数是否可解析由 fixture 的桩数据决定（_StubOrderService），与线上一致。
    """
    expected_tool = row.get("expectTool")
    if expected_tool is None:
        return actual_tool is None
    if actual_tool != expected_tool:
        return False
    if expected_tool == "PROPOSE_REFUND":
        return bool(actual_args and actual_args.get("orderItemId"))
    return True


class _StubOrderService:
    """按 case 的 fixture 喂订单项数据。

    退款那条链路要问 Java 侧"这个 ID 是订单项还是订单""这单有几个可退项"，
    答案决定走 PROPOSE_REFUND 还是先摆订单。真连 Java 的话这条维度就不可复现了，
    所以把答案写进题面：fixture 声明的是"假设 Java 侧这么答"，考的是本地怎么决策。
    """

    def __init__(self) -> None:
        self.fixture: dict = {}

    async def get_order_item(self, raw_id: str) -> dict | None:
        if self.fixture.get("itemFound") and "_" in raw_id:
            return {"order_item_id": raw_id}
        return None

    async def list_refundable_items(self, user_id: str, order_id: str) -> list[dict]:
        count = int(self.fixture.get("refundableCount") or 0)
        return [{"order_item_id": f"{order_id}_{i + 1}"} for i in range(count)]


async def _evaluate_order_turn(
    *,
    orders: list[dict],
    intent: str,
    user_text: str,
    entities: dict | None = None,
    consult_card: dict | None = None,
    pending_reference: dict | None = None,
) -> tuple[Any, str | None, dict | None, str | None]:
    from app.graph.order_reference_flow import _direct_response, _tool_for_target
    from app.services import order_reference_resolver as resolver_module
    from app.services.order_reference_resolver import order_reference_resolver

    async def fixture_orders(*_args, **_kwargs):
        return list(orders)

    original_list_orders = resolver_module.java_internal_client.list_orders
    resolver_module.java_internal_client.list_orders = fixture_orders
    try:
        resolved = await order_reference_resolver.resolve(
            user_id="eval-user",
            intent=intent,
            user_text=user_text,
            entities=entities or {},
            consult_card=consult_card,
            pending_reference=pending_reference,
        )
    finally:
        resolver_module.java_internal_client.list_orders = original_list_orders

    selected_tool = None
    card_type = None
    if resolved.target:
        direct = await _direct_response(
            {"user_id": "eval-user"}, intent, resolved.target
        )
        selected_tool = (
            None
            if direct is not None
            else _tool_for_target(intent, user_text, resolved.target)
        )
        if selected_tool and selected_tool[0].startswith("PROPOSE_"):
            card_type = "ACTION_CONFIRM"
    if resolved.candidates and resolved.outcome.value in {"AMBIGUOUS", "NO_MATCH"}:
        card_type = "ORDER_SELECTION"
    return resolved, (selected_tool[0] if selected_tool else None), (
        selected_tool[1] if selected_tool else None
    ), card_type


async def _run_convo_case(case: Case, order_stub: _StubOrderService) -> CaseOutcome:
    from app.domain.intent.classifier import resolve_intent
    from app.domain.intent.write_args import required_tool_for_intent

    row = case.raw
    order_stub.fixture = row.get("fixture") or {}
    context = row.get("context") or {}

    decision = await resolve_intent(
        "eval-user",
        row["userText"],
        from_product=bool(context.get("fromProduct", False)),
        consult_card=context.get("consultCard"),
        message_card=context.get("messageCard"),
        unresolved_count=int(context.get("unresolvedCount", 0)),
        # A2/A3：会话级意图延续与死循环检测由题面注入（模拟上一轮的事实）。
        session_intent=context.get("sessionIntent"),
        recent_intents=context.get("recentIntents"),
        # 关键：只评确定性路径。开了 LLM 这个评测集就不再是冻结的。
        allow_llm=False,
    )

    forced = await required_tool_for_intent(
        decision.intent.value, decision.data, row["userText"], "eval-user"
    )
    actual_tool = forced[0] if forced else None
    actual_args = forced[1] if forced else None
    order_resolution = None
    order_target_id = None
    order_tool = None
    order_tool_args = None
    order_card_type = None
    selection_intent = None
    selection_valid = None
    selection_resolution = None
    selection_target_id = None
    selection_tool = None
    selection_tool_args = None
    selection_card_type = None

    if "orderFixture" in row:
        orders = list(row.get("orderFixture") or [])
        resolved, order_tool, order_tool_args, order_card_type = (
            await _evaluate_order_turn(
                orders=orders,
                intent=decision.intent.value,
                user_text=row["userText"],
                entities=decision.entities,
                consult_card=context.get("consultCard"),
            )
        )
        order_resolution = resolved.outcome.value
        if resolved.target:
            order_target_id = resolved.target.get("targetId")

        selection = row.get("selection")
        if isinstance(selection, dict):
            selected_candidate = next(
                (
                    candidate
                    for candidate in resolved.candidates
                    if str(candidate.get("targetType") or "")
                    == str(selection.get("targetType") or "")
                    and str(candidate.get("targetId") or "")
                    == str(selection.get("targetId") or "")
                ),
                None,
            )
            selection_valid = selected_candidate is not None
            if selected_candidate:
                follow_text = str(
                    selection.get("followUpText") or row["userText"]
                )
                follow_decision = await resolve_intent(
                    "eval-user",
                    follow_text,
                    session_intent=decision.intent.value,
                    recent_intents=[decision.intent.value],
                    allow_llm=False,
                )
                selection_intent = follow_decision.intent.value
                pending_reference = {
                    **selected_candidate,
                    "intent": decision.intent.value,
                    "expiresAt": (
                        datetime.now() + timedelta(minutes=30)
                    ).isoformat(timespec="seconds"),
                }
                (
                    selected_resolution,
                    selection_tool,
                    selection_tool_args,
                    selection_card_type,
                ) = await _evaluate_order_turn(
                    orders=orders,
                    intent=follow_decision.intent.value,
                    user_text=follow_text,
                    entities=follow_decision.entities,
                    pending_reference=pending_reference,
                )
                selection_resolution = selected_resolution.outcome.value
                if selected_resolution.target:
                    selection_target_id = selected_resolution.target.get("targetId")

    checks: dict[str, bool] = {}
    if row.get("expectIntent") is not None:
        checks["intent"] = decision.intent.value == row["expectIntent"]
    if row.get("expectNextAction") is not None:
        checks["nextAction"] = decision.next_action.value == row["expectNextAction"]
    if row.get("expectHandoff") is not None:
        checks["handoffReason"] = decision.handoff_reason == row["expectHandoff"]
    if "expectTool" in row:
        checks["tool"] = actual_tool == row["expectTool"]
        if row["expectTool"] is not None:
            checks["toolArgs"] = _args_match(row.get("expectArgs") or {}, actual_args)
    if row.get("expectResolution") is not None:
        checks["orderResolution"] = order_resolution == row["expectResolution"]
    if row.get("expectTargetId") is not None:
        checks["orderTarget"] = order_target_id == row["expectTargetId"]
    if "expectOrderTool" in row:
        checks["orderTool"] = order_tool == row.get("expectOrderTool")
    if "expectCardType" in row:
        checks["orderCard"] = order_card_type == row.get("expectCardType")
    if row.get("expectForbiddenTools") is not None:
        checks["forbiddenTools"] = order_tool not in set(row["expectForbiddenTools"])
    if "expectSelectionValid" in row:
        checks["selectionValid"] = selection_valid == row["expectSelectionValid"]
    if row.get("expectSelectionIntent") is not None:
        checks["selectionIntent"] = selection_intent == row["expectSelectionIntent"]
    if row.get("expectSelectionResolution") is not None:
        checks["selectionResolution"] = (
            selection_resolution == row["expectSelectionResolution"]
        )
    if row.get("expectSelectionTargetId") is not None:
        checks["selectionTarget"] = (
            selection_target_id == row["expectSelectionTargetId"]
        )
    if "expectSelectionOrderTool" in row:
        checks["selectionOrderTool"] = (
            selection_tool == row.get("expectSelectionOrderTool")
        )
    if row.get("expectSelectionArgs") is not None:
        checks["selectionToolArgs"] = _args_match(
            row["expectSelectionArgs"], selection_tool_args
        )
    if "expectSelectionCardType" in row:
        checks["selectionCard"] = (
            selection_card_type == row.get("expectSelectionCardType")
        )
    if row.get("expectSelectionForbiddenTools") is not None:
        checks["selectionForbiddenTools"] = selection_tool not in set(
            row["expectSelectionForbiddenTools"]
        )
    # A1 结果层（Verified-Action 雏形）：该业务状态下"动作要么可执行、要么被正确拒绝"。
    # 考的是系统会不会在 fixture 声明不可退/参数不可解析时仍然发起提案——
    # 对应行业"Verified Resolution"哲学里"拒绝不可执行动作"的一半。
    # expectToolVerified=true 参与校验；显式写 false 表示该条明确不参与本维度
    # （值必须是 bool，validate_convo_eval.py 强制），避免"写了等于没写"的陷阱。
    verified_flag = row.get("expectToolVerified")
    if verified_flag is True:
        checks["verifiedAction"] = _verify_action(row, actual_tool, actual_args)
    elif verified_flag is False:
        checks["verifiedAction"] = True

    return CaseOutcome(
        id=case.id,
        subset=case.subset,
        split=case.split,
        kind=case.kind,
        passed=all(checks.values()),
        checks=checks,
        actual={
            "intent": decision.intent.value,
            "source": decision.source,
            "nextAction": decision.next_action.value,
            "handoffReason": decision.handoff_reason,
            "confidence": decision.confidence,
            "tool": actual_tool,
            "toolArgs": actual_args,
            "orderResolution": order_resolution,
            "orderTargetId": order_target_id,
            "orderTool": order_tool,
            "orderToolArgs": order_tool_args,
            "orderCardType": order_card_type,
            "selectionIntent": selection_intent,
            "selectionValid": selection_valid,
            "selectionResolution": selection_resolution,
            "selectionTargetId": selection_target_id,
            "selectionOrderTool": selection_tool,
            "selectionToolArgs": selection_tool_args,
            "selectionCardType": selection_card_type,
        },
        note=case.note,
    )


def _run_guard_case(case: Case) -> CaseOutcome:
    from app.harness.guardrails.input_guard import InputGuardrail

    verdict = InputGuardrail().inspect(case.raw["userText"])
    checks = {"blocked": verdict.blocked == case.raw["expectBlocked"]}
    return CaseOutcome(
        id=case.id,
        subset=case.subset,
        split=case.split,
        kind=case.kind,
        passed=all(checks.values()),
        checks=checks,
        actual={"blocked": verdict.blocked, "matchedRules": list(verdict.matched_rules)},
        note=case.note,
    )


async def _run_identity_case(case: Case) -> CaseOutcome:
    """走真实的 mcp_tool_router.invoke，只桩掉出网那一步。

    不直接调 tool_guard：要考的是"落到 MCP 的 userId 是谁"，
    而那取决于 router 里校验和覆写的先后顺序——正是之前写反过的地方。
    只调 guard 的话顺序反了也能通过。
    """
    from app.harness.metrics.runtime_sensors import TOOL_CALL_TOTAL
    from app.services import mcp_tool_router as router_module
    from app.services.tool_invoke_result import ToolInvokeResult

    row = case.raw
    tool_name = row.get("toolName", "QUERY_ORDERS")
    captured: dict[str, Any] = {}

    async def fake_call_tool(name: str, args: dict) -> ToolInvokeResult:
        captured["name"] = name
        captured["args"] = dict(args)
        return ToolInvokeResult(content="ok")

    original = router_module.mcp_streamable_client.call_tool
    router_module.mcp_streamable_client.call_tool = fake_call_tool
    mismatch_metric = TOOL_CALL_TOTAL.labels(tool=tool_name, status="user_id_mismatch")
    before = mismatch_metric._value.get()
    try:
        await router_module.mcp_tool_router.invoke(
            tool_name, dict(row["toolArgs"]), row["callerUserId"]
        )
    finally:
        router_module.mcp_streamable_client.call_tool = original
    flagged = mismatch_metric._value.get() > before

    sent_user_id = (captured.get("args") or {}).get("userId")
    checks = {
        "argsUserId": sent_user_id == row["expectArgsUserId"],
        "mismatchFlagged": flagged == row["expectMismatchFlagged"],
        # 下划线写法必须被清掉：留着的话 _to_mcp_args 在 userId 为 None 时会退回去取它。
        "noSnakeCaseLeak": "user_id" not in (captured.get("args") or {}),
    }
    return CaseOutcome(
        id=case.id,
        subset=case.subset,
        split=case.split,
        kind=case.kind,
        passed=all(checks.values()),
        checks=checks,
        actual={"sentArgs": captured.get("args"), "mismatchFlagged": flagged},
        note=case.note,
    )


def _run_policy_case(case: Case) -> CaseOutcome:
    from app.domain.tool_policy import is_allowed, is_write_tool

    row = case.raw
    allowed = is_allowed(row["toolName"])
    write = is_write_tool(row["toolName"])
    checks = {"allowed": allowed == row["expectAllowed"], "write": write == row["expectWrite"]}
    return CaseOutcome(
        id=case.id,
        subset=case.subset,
        split=case.split,
        kind=case.kind,
        passed=all(checks.values()),
        checks=checks,
        actual={"allowed": allowed, "write": write},
        note=case.note,
    )


async def run_all(cases: list[Case] | None = None) -> EvalReport:
    cases = cases if cases is not None else load_cases()
    order_stub = _StubOrderService()

    # 退款分支通过模块属性拿 order_service，这里整体替掉。
    from app.services import order_service as order_module

    original = order_module.order_service
    order_module.order_service = order_stub
    try:
        report = EvalReport()
        for case in cases:
            if case.kind == "convo":
                report.outcomes.append(await _run_convo_case(case, order_stub))
            elif case.kind == "guard":
                report.outcomes.append(_run_guard_case(case))
            elif case.kind == "identity":
                report.outcomes.append(await _run_identity_case(case))
            else:
                report.outcomes.append(_run_policy_case(case))
        return report
    finally:
        order_module.order_service = original


def run_all_sync(cases: list[Case] | None = None) -> EvalReport:
    return asyncio.run(run_all(cases))


def summarize(report: EvalReport) -> dict:
    """按 subset / split / 维度分别汇总。

    只给一个总分没用：111 条里错 5 条，错在 injection 还是错在 chat_howto
    是完全不同的两件事，一个是防护失效一个是答得笨。
    """
    outcomes = report.outcomes
    by_subset: dict[str, dict] = {}
    for subset in sorted({o.subset for o in outcomes}):
        group = [o for o in outcomes if o.subset == subset]
        by_subset[subset] = {
            "cases": len(group),
            "passed": sum(o.passed for o in group),
            "passRate": _rate(sum(o.passed for o in group), len(group)),
        }

    by_split: dict[str, dict] = {}
    for split in ("dev", "test"):
        group = [o for o in outcomes if o.split == split]
        by_split[split] = {
            "cases": len(group),
            "passed": sum(o.passed for o in group),
            "passRate": _rate(sum(o.passed for o in group), len(group)),
        }

    dimensions: dict[str, dict] = {}
    for name in sorted({k for o in outcomes for k in o.checks}):
        graded = [o.checks[name] for o in outcomes if name in o.checks]
        dimensions[name] = {
            "graded": len(graded),
            "passed": sum(graded),
            "passRate": _rate(sum(graded), len(graded)),
        }

    return {
        "cases": len(outcomes),
        "passed": sum(o.passed for o in outcomes),
        "passRate": _rate(sum(o.passed for o in outcomes), len(outcomes)),
        "bySubset": by_subset,
        "bySplit": by_split,
        "byDimension": dimensions,
        "failedIds": report.failed_ids,
    }
