from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.quality_scorecard import (
    ScorecardError,
    _search_scorecard,
    build_scorecard,
    write_scorecard,
)


def _search_row(case_id: str, product_ids: list[str], *, mrr: float = 1.0) -> dict:
    return {
        "case_id": case_id,
        "domain": "search",
        "status": "PASSED",
        "slice": "test",
        "latency_ms": 10.0,
        "metrics": {
            "providerCompleteness": 1,
            "constraintViolationCount": 0,
            "recallAt3": 1.0 if product_ids else 0.0,
            "recallAt5": 1.0 if product_ids else 0.0,
            "recallAt10": 1.0 if product_ids else 0.0,
            "mrrAt10": mrr,
            "ndcgAt10": mrr,
        },
        "output": {
            "query": "测试查询",
            "products": [{"productId": value, "productName": value} for value in product_ids],
        },
    }


def test_scorecard_extracts_qrel_miss_even_when_runtime_case_passed() -> None:
    row = _search_row("search-pass-with-miss", ["a"])
    holdout = {
        "search-pass-with-miss": {
            "id": "search-pass-with-miss",
            "input": {"query": "测试查询"},
            "expected": {"qrels": {"a": 3, "b": 2}, "noResult": False},
        }
    }

    domain, badcases, _ = _search_scorecard(
        [row],
        holdout,
        {
            "a": {"productId": "a", "productName": "商品 A"},
            "b": {"productId": "b", "productName": "商品 B"},
        },
    )

    assert row["status"] == "PASSED"
    assert domain["primaryMetrics"]["Recall@10 micro/qrel"]["value"] == pytest.approx(0.5)
    assert domain["primaryMetrics"]["Recall@10 micro/qrel"]["badcaseIds"] == [
        "search-pass-with-miss"
    ]
    assert any(item["kind"] == "search-recall-miss" for item in badcases)


def test_scorecard_keeps_ranking_badcase_separate_from_runtime_bad_cases() -> None:
    row = _search_row("search-ranking-only", ["b", "a"], mrr=0.5)
    holdout = {
        "search-ranking-only": {
            "id": "search-ranking-only",
            "input": {"query": "排序查询"},
            "expected": {"qrels": {"a": 3}, "noResult": False},
        }
    }

    domain, badcases, _ = _search_scorecard([row], holdout, {})

    assert domain["primaryMetrics"]["Recall@10 macro/query"]["value"] == pytest.approx(1.0)
    ranking_bad = [item for item in badcases if item["kind"] == "search-ranking-order"]
    assert {item["metric"] for item in ranking_bad} == {"mrrAt10", "ndcgAt10"}


def test_scorecard_recomputes_ranking_badcase_instead_of_trusting_published_metric() -> None:
    row = _search_row("search-recompute-ranking", ["b", "a"], mrr=1.0)
    row["metrics"]["ndcgAt10"] = 1.0
    holdout = {
        "search-recompute-ranking": {
            "id": "search-recompute-ranking",
            "input": {"query": "独立重算排序"},
            "expected": {"qrels": {"a": 3}, "noResult": False},
        }
    }

    domain, badcases, _ = _search_scorecard([row], holdout, {})

    assert domain["primaryMetrics"]["MRR@10 macro/query"]["value"] == pytest.approx(0.5)
    assert domain["primaryMetrics"]["NDCG@10 macro/query"]["value"] < 1.0
    assert {item["metric"] for item in badcases if item["kind"] == "search-ranking-order"} == {
        "mrrAt10",
        "ndcgAt10",
    }
    assert domain["contractGates"]["recomputeMismatch"]["badcaseIds"] == [
        "search-recompute-ranking"
    ]


def test_build_scorecard_fails_closed_without_search_holdout() -> None:
    evidence = Path(__file__).parents[1] / "evaluation-evidence" / "current"
    with pytest.raises(ScorecardError, match="evidence directory|holdout"):
        build_scorecard(evidence, holdout_path=evidence / "missing-holdout.jsonl")


@pytest.mark.private_holdout
def test_scorecard_uses_repo_relative_paths_for_tracked_evidence() -> None:
    evidence = Path(__file__).parents[1] / "evaluation-evidence" / "current"
    scorecard = build_scorecard(evidence)

    assert scorecard["evidence"]["path"] == "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/current"
    assert scorecard["holdout"]["path"].startswith(
        "AI_Shop-backend/AI_Shop-agent/evaluation/.holdouts/"
    )
    assert scorecard["catalog"]["path"] == (
        "AI_Shop-backend/AI_Shop-agent/evaluation/fixtures/product-catalog.v2.json"
    )


def test_write_scorecard_refuses_to_write_inside_immutable_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "current"
    evidence.mkdir()
    with pytest.raises(ScorecardError, match="immutable evidence"):
        write_scorecard(
            {"schemaVersion": "test"},
            evidence / "derived.md",
            evidence / "derived.json",
            evidence_path=evidence,
        )
