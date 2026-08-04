"""冻结评测集的读取与结构约束。

单独一个模块是为了让 runner、validator 和 pytest 三处读同一份定义：
字段名、subset 清单、SHA-256 的算法只写一遍。三处各写一遍的话，
"改了题面但只有一处校验跟上"就成了必然。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent
DATASET_PATH = BENCHMARKS_DIR / "aishop_convo_v1.jsonl"
LOCK_PATH = BENCHMARKS_DIR / "aishop_convo_v1.lock.json"
RESULTS_DIR = BENCHMARKS_DIR / "results"

DATASET_VERSION = "aishop_convo_v1"

#: 会话类 case：跑 resolve_intent + required_tool_for_intent
CONVO_SUBSETS = frozenset(
    {
        "order_query",
        "logistics",
        "refund",
        "cancel_order",
        "confirm_receipt",
        "review",
        "coupon",
        "product_search",
        "product_consult",
        "aftersales",
        "chat_howto",
        "handoff",
        "continuation",
    }
)
#: 输入防护类 case：跑 InputGuardrail.inspect
GUARD_SUBSETS = frozenset({"injection"})
#: 身份归属类 case：跑 mcp_tool_router.invoke，看落到 MCP 的 userId
IDENTITY_SUBSETS = frozenset({"identity"})
#: 工具白名单类 case：查 tool_policy 表
POLICY_SUBSETS = frozenset({"tool_policy"})

ALL_SUBSETS = CONVO_SUBSETS | GUARD_SUBSETS | IDENTITY_SUBSETS | POLICY_SUBSETS

#: 每类 case 必须有的字段。缺字段说明题面写漏了，不是"这一维不检查"——
#: 不检查要显式写 null，让"没写"和"写了不检查"区分开。
REQUIRED_BY_KIND: dict[str, frozenset[str]] = {
    "convo": frozenset({"id", "subset", "split", "userText", "expectIntent", "note"}),
    "guard": frozenset({"id", "subset", "split", "userText", "expectBlocked", "note"}),
    "identity": frozenset(
        {"id", "subset", "split", "toolArgs", "callerUserId", "expectArgsUserId",
         "expectMismatchFlagged", "note"}
    ),
    "policy": frozenset({"id", "subset", "split", "toolName", "expectAllowed", "expectWrite", "note"}),
}


@dataclass(frozen=True)
class Case:
    """一条 case。原始 dict 保留在 ``raw`` 里，评分函数按 kind 各取所需。"""

    id: str
    subset: str
    split: str
    kind: str
    raw: dict

    @property
    def note(self) -> str:
        return self.raw.get("note", "")


def kind_of(subset: str) -> str:
    if subset in CONVO_SUBSETS:
        return "convo"
    if subset in GUARD_SUBSETS:
        return "guard"
    if subset in IDENTITY_SUBSETS:
        return "identity"
    if subset in POLICY_SUBSETS:
        return "policy"
    raise ValueError(f"未知 subset: {subset}")


def dataset_sha256(path: Path = DATASET_PATH) -> str:
    """对文件原始字节做哈希，不是对解析后的对象。

    对解析结果做哈希会让"改注释、改字段顺序"逃过校验；而这个文件里的注释行
    正是说明期望值怎么来的地方，改了也该重新过一遍门禁。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path: Path = DATASET_PATH) -> list[Case]:
    cases: list[Case] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}:{lineno} 不是合法 JSON: {e}") from e
        subset = row.get("subset")
        if not isinstance(subset, str):
            raise ValueError(f"{path.name}:{lineno} 缺 subset")
        cases.append(
            Case(id=row["id"], subset=subset, split=row["split"], kind=kind_of(subset), raw=row)
        )
    return cases


def load_lock(path: Path = LOCK_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
