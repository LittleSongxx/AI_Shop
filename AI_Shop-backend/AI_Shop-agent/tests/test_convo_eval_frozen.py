"""冻结评测集在 pytest 里的守卫。

单独放一个 benchmarks 脚本不够：没人会记得手动跑。这里把三件事拉进普通测试——
数据集有没有被静默改、分数有没有退步、已知失败清单是不是还等于事实。

注意这里不重复评单条 case 的对错（那是数据集自己的事），只守"冻结"这个性质。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.convo_eval_dataset import (
    DATASET_PATH,
    DATASET_VERSION,
    LOCK_PATH,
    dataset_sha256,
    load_cases,
    load_lock,
)
from benchmarks.convo_eval_runner import run_all, summarize
from benchmarks.run_convo_eval import check_gate

LIMITATIONS_DOC = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "冻结会话评测限制与变更记录.md"
)


@pytest.fixture(scope="module")
def lock() -> dict:
    return load_lock()


async def _summary() -> dict:
    return summarize(await run_all(load_cases()))


def test_dataset_sha256_matches_lock(lock):
    """题面改了但 lock 没改，就不再是同一个评测集。"""
    assert dataset_sha256() == lock["datasetSha256"], (
        f"{DATASET_PATH.name} 的内容变了。改题面是可以的，但必须跑一次 "
        "`run_convo_eval.py --bootstrap-lock` 重新生成基线，"
        "并在冻结会话评测限制与变更记录.md 里说明变的是什么。"
    )


def test_lock_declares_the_frozen_policy(lock):
    """lock 里那两条 forbidden 是这个评测集的全部价值所在，删掉就没意义了。"""
    policy = lock.get("frozenPolicy", {})
    assert policy.get("resampling") == "forbidden"
    assert policy.get("relabeling") == "forbidden"
    assert lock["datasetVersion"] == DATASET_VERSION


async def test_no_regression_against_frozen_baseline(lock):
    summary = await _summary()
    problems = check_gate(summary, lock, dataset_sha256())
    assert not problems, "\n".join(problems)


async def test_known_failures_are_exactly_the_current_failures(lock):
    """两个方向都要相等。

    只查"没有新失败"的话，lock 里会慢慢积一堆早就修好的条目；
    那时它记录的就不是事实，而是历史。
    """
    summary = await _summary()
    assert set(summary["failedIds"]) == set(lock["knownFailures"]), (
        "已知失败清单和实际失败不一致。修好了就从 lock 和冻结会话评测限制与变更记录.md 里删掉，"
        "新坏了就先修，确实修不了再加进去并写清原因。"
    )


def _documented_ids() -> set[str]:
    """取冻结会话评测限制与变更记录里的显式块内容。

    不用全文搜 case id：正文会引用通过的 case 做对照（"cancel-004 是通过的"），
    全文搜会把这些对照当成"文档挂着已修好的失败"。对照本身是文档里最有信息量的部分
    ——同一类问法在一处答对、在另一处答错，才说明问题不是取舍而是漏做。
    """
    text = LIMITATIONS_DOC.read_text(encoding="utf-8")
    begin = "<!-- KNOWN_FAILURE_IDS:BEGIN -->"
    end = "<!-- KNOWN_FAILURE_IDS:END -->"
    assert begin in text and end in text, "冻结会话评测限制与变更记录里的已知失败清单块不见了"
    block = text[text.index(begin) + len(begin): text.index(end)]
    return {token for token in block.replace("`", " ").split() if "-" in token}


def test_documented_ids_equal_lock_known_failures(lock):
    """文档块和 lock 必须完全相等，两个方向都查。

    只查一边的话：漏写就是"有失败没人知道"，多写就是"文档挂着一条不存在的失败"。
    """
    documented = _documented_ids()
    known = set(lock["knownFailures"])
    assert documented == known, (
        f"只在 lock 里：{sorted(known - documented)}；"
        f"只在文档里：{sorted(documented - known)}"
    )


def test_documented_ids_are_real_case_ids():
    """文档块里的 id 必须真的存在于数据集，否则就是打错字或者 case 被删了。"""
    all_ids = {c.id for c in load_cases()}
    unknown = sorted(_documented_ids() - all_ids)
    assert not unknown, f"文档块里这些 id 在数据集里不存在：{unknown}"


def test_every_known_failure_has_prose_explanation(lock):
    """光有 id 清单不够：每条都要在正文里出现过，说明它是什么、为什么留着。"""
    text = LIMITATIONS_DOC.read_text(encoding="utf-8")
    end = text.index("<!-- KNOWN_FAILURE_IDS:END -->")
    prose = text[end:]
    missing = sorted(cid for cid in lock["knownFailures"] if f"`{cid}`" not in prose)
    assert not missing, f"这些已知失败只在清单块里，正文没解释：{missing}"


async def test_security_dimensions_have_no_known_failures(lock):
    """防护类维度不接受"已知失败"。

    意图判错是质量问题，可以先记下来慢慢修；身份归属和注入拦截判错是安全问题，
    没有"先记着"这个选项。所以这几维一旦有失败，测试直接红，不许进 knownFailures。
    """
    summary = await _summary()
    for dimension in ("argsUserId", "noSnakeCaseLeak", "mismatchFlagged", "blocked"):
        stats = summary["byDimension"].get(dimension)
        assert stats is not None, f"维度 {dimension} 没有任何 case，防护面被删空了"
        assert stats["passRate"] == 1.0, (
            f"{dimension} 有失败：{stats}。这一维不接受已知失败，必须修。"
        )


def test_lock_is_valid_json_with_baseline_shape(lock):
    assert LOCK_PATH.exists()
    baseline = lock["baseline"]
    for key in ("cases", "passed", "passRate", "bySplit", "bySubset", "byDimension"):
        assert key in baseline, f"lock 的 baseline 缺 {key}"
    # 满分只有一种可信来源：题面一字未改、实现变好。
    # 数据集与 lock 哈希一致由 test_dataset_sha256_matches_lock 守着；
    # 满分还要求限制与变更文档显式声明数据集未动过——否则
    # "改题面对齐实现"之后拿到的满分就蒙混过关了。
    if baseline["passRate"] >= 1.0:
        text = LIMITATIONS_DOC.read_text(encoding="utf-8")
        assert "题面一个字没动" in text, (
            "基线通过率是 1.0 但没有「题面一个字没动」的声明。满分必须配合 "
            "冻结会话评测限制与变更记录里的进度记录（数据集 SHA-256 全程不变），"
            "否则就是题面被对齐到了实现。"
        )
    # 恒真断言修复（P1 审查）：`lock` fixture 就是 load_lock()——对同一个文件
    # json.loads 再和自己比永远为真，没有守卫价值。改为校验 lock 顶层结构
    # 契约：六键必须齐全，缺键/多键都说明 lock 格式被改了。
    _LOCK_KEYS = {
        "datasetVersion",
        "datasetSha256",
        "runnerVersion",
        "frozenPolicy",
        "baseline",
        "knownFailures",
    }
    assert set(lock) == _LOCK_KEYS, (
        f"lock 顶层键与契约不符，缺/多: {sorted(set(lock) ^ _LOCK_KEYS)}"
    )
