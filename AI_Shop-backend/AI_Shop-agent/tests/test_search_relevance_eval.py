"""把搜索相关性评测集的第一层拉进 pytest。

单独一个 benchmarks 脚本没人会记得跑，而 taxonomy 的改动恰恰是最容易静默破坏
整个品类召回的地方——fillers 是拼成一条正则轮替的，顺序即语义，加一条就可能让
另一条失效（"买个空气炸锅" 泄出量词就是这么来的）。

这里只跑第二层之外的部分：纯函数，不连 ES、不调 LLM、不需要商品数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.run_search_relevance import (
    DEFAULT_CATALOG,
    DEFAULT_DATASET,
    DEFAULT_LOCK,
    _ndcg,
    evaluate_graded_relevance,
    evaluate_query_understanding,
    graded_gate_failures,
    load_cases,
    validate_graded_contract,
)


@pytest.fixture(scope="module")
def report() -> dict:
    return evaluate_query_understanding(load_cases(DEFAULT_DATASET))


def test_dataset_is_not_empty():
    cases = load_cases(DEFAULT_DATASET)
    assert len(cases) >= 90, "评测集被删剩太少，覆盖度不够了"
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)), "有重复的 case id"
    # 允许空 query：edge-001 存在的意义就是断言空输入不抛异常。
    # 但空 query 必须显式声明期望值，避免"忘了填"混进来当成一条有效用例。
    for case in cases:
        if not (case.get("query") or "").strip():
            assert "expectKeyword" in case, f"{case['id']} 空 query 却没写 expectKeyword"


def test_query_normalization_is_fully_correct(report: dict):
    """这一层要求满分：每条都是确定性的纯函数断言，错一条就是真的坏了。"""
    assert report["keywordAccuracy"] == 1.0, _format_failures(report, "keyword")


def test_term_expansion_is_fully_covered(report: dict):
    assert report["termCoverage"] == 1.0, _format_failures(report, "terms")


def test_every_subset_is_covered(report: dict):
    """一个品类整体挂掉会体现为某个 subset 全错，这里单独看一眼分组。"""
    for subset, stats in report["bySubset"].items():
        assert stats["passed"] == stats["graded"], f"subset {subset} 有失败用例"


def test_graded_dataset_and_catalog_match_frozen_lock():
    contract = validate_graded_contract(
        load_cases(DEFAULT_DATASET), DEFAULT_DATASET, DEFAULT_LOCK, DEFAULT_CATALOG
    )
    assert contract["productCount"] == 47
    assert contract["labelledCases"] == 30
    assert contract["thresholds"] == {"recallAt10": 0.8, "mrr": 0.65, "ndcgAt10": 0.7}


def test_graded_ndcg_respects_relevance_grades():
    grades = {"best": 3, "related": 1}
    assert _ndcg(["best", "related"], grades, 10) == pytest.approx(1.0)
    assert _ndcg(["related", "best"], grades, 10) < 1.0
    assert _ndcg(["unrelated"], grades, 10) == 0.0


@pytest.mark.asyncio
async def test_graded_gate_fails_when_one_recall_channel_is_globally_empty(monkeypatch):
    async def fake_retrieve(_query: str, _k: int) -> dict[str, list[str]]:
        return {"keyword": ["p1"], "vector": [], "fused": ["p1"]}

    monkeypatch.setattr(
        "benchmarks.run_search_relevance._retrieve_channels", fake_retrieve
    )
    result = await evaluate_graded_relevance(
        [
            {
                "id": "graded-test",
                "query": "query",
                "relevantProductIds": ["p1"],
                "relevanceGrades": {"p1": 3},
            }
        ],
        10,
    )

    assert result["recallAt10"] == 1.0
    assert result["recallChannelsHealthy"] is False
    assert graded_gate_failures(
        result, k=10, min_recall=0.8, min_mrr=0.65, min_ndcg=0.7
    ) == [
        "both keyword and vector recall channels must return candidates (keyword=1, vector=0)"
    ]


def _format_failures(report: dict, field: str) -> str:
    failures = [f for f in report["failures"] if f["field"] == field]
    lines = [f"{len(failures)} 条 {field} 断言失败:"]
    for failure in failures:
        if field == "terms":
            detail = f"missing={failure['missing']!r} actual={failure['actual']!r}"
        else:
            detail = f"expected={failure['expected']!r} actual={failure['actual']!r}"
        lines.append(f"  [{failure['id']}] {failure['query']!r} {detail}")
        if failure.get("note"):
            lines.append(f"      note: {failure['note']}")
    return "\n".join(lines)
