#!/usr/bin/env python3
"""题面体检：只看数据集本身合不合规，不跑任何被测代码。

跟 runner 分开是有意的：runner 失败可能是实现回归，validator 失败一定是题面写坏了。
混在一起会让"分数掉了"和"题目写错了"看起来一样。
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.intent.types import IntentKind, NextAction  # noqa: E402
from app.domain.tool_policy import ALL_ALLOWED_TOOLS  # noqa: E402
from benchmarks.convo_eval_dataset import (  # noqa: E402
    ALL_SUBSETS,
    DATASET_PATH,
    REQUIRED_BY_KIND,
    dataset_sha256,
    load_cases,
)

# 题面里不能出现真实手机号/身份证：这个文件会进版本库，也会被贴进报告。
_PHONE_LIKE = re.compile(r"\b1[3-9]\d{9}\b")
_ID_LIKE = re.compile(r"\b\d{17}[0-9Xx]\b")
_HANDOFF_REASONS = {
    "USER_REQUEST",
    "FUND_DISPUTE",
    "SEVERE_NEGATIVE_SENTIMENT",
    "REPEATED_UNRESOLVED",
    "REPEATED_INTENT",  # A3：同一意图连续多轮未解决 → 主动建议转人工
    "LOW_CONFIDENCE",
}


def fail(problems: list[str], message: str) -> None:
    problems.append(message)


def main() -> int:
    cases = load_cases()
    problems: list[str] = []

    if not cases:
        print("数据集是空的", file=sys.stderr)
        return 1

    ids = [c.id for c in cases]
    duplicates = [i for i, n in collections.Counter(ids).items() if n > 1]
    if duplicates:
        fail(problems, f"重复 id: {sorted(duplicates)}")

    for case in cases:
        row = case.raw
        where = f"{case.id}"
        missing = REQUIRED_BY_KIND[case.kind] - row.keys()
        if missing:
            fail(problems, f"{where} 缺字段 {sorted(missing)}")
        if case.subset not in ALL_SUBSETS:
            fail(problems, f"{where} subset 不在清单里: {case.subset}")
        if case.split not in {"dev", "test"}:
            fail(problems, f"{where} split 非法: {case.split}")
        if not case.note.strip():
            fail(problems, f"{where} note 为空——每条 case 都要说清在考什么")

        if case.kind == "convo":
            if not (row.get("userText") or "").strip():
                fail(problems, f"{where} userText 为空")
            intent = row.get("expectIntent")
            if intent is not None and intent not in IntentKind.__members__:
                fail(problems, f"{where} expectIntent 不是合法意图: {intent}")
            action = row.get("expectNextAction")
            if action is not None and action not in NextAction.__members__:
                fail(problems, f"{where} expectNextAction 非法: {action}")
            reason = row.get("expectHandoff")
            if reason is not None and reason not in _HANDOFF_REASONS:
                fail(problems, f"{where} expectHandoff 非法: {reason}")
            tool = row.get("expectTool")
            if tool is not None and tool not in ALL_ALLOWED_TOOLS:
                fail(problems, f"{where} expectTool 不在白名单里: {tool}")
            # 期望调工具就必须写清期望参数（哪怕是空 dict）；否则等于只考了工具名。
            if tool is not None and not isinstance(row.get("expectArgs"), dict):
                fail(problems, f"{where} 期望调 {tool} 但 expectArgs 不是 dict")
            if tool is None and row.get("expectArgs") not in (None, {}):
                fail(problems, f"{where} 不期望调工具却写了 expectArgs")
            # Verified-Action 维度：值必须是 bool（runner 按值区分"参与/不参与"），
            # 且只允许出现在 refund subset（fixture 只给 REFUND 喂订单项桩数据）。
            verified = row.get("expectToolVerified")
            if verified is not None and not isinstance(verified, bool):
                fail(problems, f"{where} expectToolVerified 必须是 bool")
            if verified is True and case.subset != "refund":
                fail(problems, f"{where} expectToolVerified=true 只允许出现在 refund subset")
            selection = row.get("selection")
            if selection is not None:
                if "orderFixture" not in row:
                    fail(problems, f"{where} selection 必须与 orderFixture 一起使用")
                if not isinstance(selection, dict):
                    fail(problems, f"{where} selection 必须是 dict")
                else:
                    if selection.get("targetType") not in {"ORDER", "ORDER_ITEM"}:
                        fail(problems, f"{where} selection.targetType 非法")
                    if not str(selection.get("targetId") or "").strip():
                        fail(problems, f"{where} selection.targetId 为空")
                    if "followUpText" in selection and not isinstance(
                        selection["followUpText"], str
                    ):
                        fail(problems, f"{where} selection.followUpText 必须是 str")
                selection_intent = row.get("expectSelectionIntent")
                if (
                    selection_intent is not None
                    and selection_intent not in IntentKind.__members__
                ):
                    fail(
                        problems,
                        f"{where} expectSelectionIntent 不是合法意图: {selection_intent}",
                    )
                selection_tool = row.get("expectSelectionOrderTool")
                if selection_tool is not None and selection_tool not in ALL_ALLOWED_TOOLS:
                    fail(
                        problems,
                        f"{where} expectSelectionOrderTool 不在白名单里: {selection_tool}",
                    )
                selection_args = row.get("expectSelectionArgs")
                if selection_tool is not None and not isinstance(selection_args, dict):
                    fail(
                        problems,
                        f"{where} 期望选择后调用 {selection_tool} 但 expectSelectionArgs 不是 dict",
                    )
                if selection_args is not None and not isinstance(selection_args, dict):
                    fail(problems, f"{where} expectSelectionArgs 必须是 dict")
            # context 注入的键必须白名单化：拼错键（如 sessionIntnet）会静默
            # 改变 case 语义而不是报错，跑出来的分数就不可信了。
            ctx = row.get("context") or {}
            if not isinstance(ctx, dict):
                fail(problems, f"{where} context 必须是 dict")
            else:
                allowed_ctx = {
                    "sessionIntent": str,
                    "recentIntents": list,
                    "fromProduct": bool,
                    "consultCard": dict,
                    "messageCard": dict,
                    "unresolvedCount": int,
                }
                unknown_ctx = set(ctx) - set(allowed_ctx)
                if unknown_ctx:
                    fail(problems, f"{where} context 含未知键 {sorted(unknown_ctx)}")
                for ctx_key, ctx_value in ctx.items():
                    # 类型也白名单化：拼错类型（如 unresolvedCount=2.5）会被
                    # runner 的 int() 静默截断、悄悄改变 case 语义（P1 审查）。
                    if not isinstance(ctx_value, allowed_ctx[ctx_key]):
                        fail(
                            problems,
                            f"{where} context.{ctx_key} 类型必须是 "
                            f"{allowed_ctx[ctx_key].__name__}，实际是 {type(ctx_value).__name__}",
                        )

        if case.kind == "guard" and not isinstance(row.get("expectBlocked"), bool):
            fail(problems, f"{where} expectBlocked 必须是 bool")

        if case.kind == "identity":
            if not isinstance(row.get("toolArgs"), dict):
                fail(problems, f"{where} toolArgs 必须是 dict")
            if not (row.get("callerUserId") or "").strip():
                fail(problems, f"{where} callerUserId 为空")
            if row.get("expectArgsUserId") != row.get("callerUserId"):
                # 归属校验的唯一正确答案就是"落到 MCP 的一定是调用者"。
                # 写成别的值说明题面自己就放弃了这条不变量。
                fail(problems, f"{where} expectArgsUserId 必须等于 callerUserId")

        if case.kind == "policy":
            if not (row.get("toolName") or "").strip():
                fail(problems, f"{where} toolName 为空")
            allowed = row.get("expectAllowed")
            if not isinstance(allowed, bool):
                fail(problems, f"{where} expectAllowed 必须是 bool")
            if allowed is False and row.get("expectWrite") is not False:
                fail(problems, f"{where} 表外工具不该被判成写工具")

    serialized = json.dumps([c.raw for c in cases], ensure_ascii=False)
    if _PHONE_LIKE.search(serialized):
        fail(problems, "题面里有手机号形状的串")
    if _ID_LIKE.search(serialized):
        fail(problems, "题面里有身份证形状的串")

    # 每个 subset 都要同时有 dev 和 test：只有一个 split 的 subset 没法说明泛化。
    by_subset = collections.defaultdict(collections.Counter)
    for case in cases:
        by_subset[case.subset][case.split] += 1
    for subset, splits in sorted(by_subset.items()):
        if not splits["dev"] or not splits["test"]:
            fail(problems, f"subset {subset} 缺 split：{dict(splits)}")

    if problems:
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "dataset": DATASET_PATH.name,
                "sha256": dataset_sha256(),
                "cases": len(cases),
                "subsets": {s: dict(c) for s, c in sorted(by_subset.items())},
                "splits": dict(collections.Counter(c.split for c in cases)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
