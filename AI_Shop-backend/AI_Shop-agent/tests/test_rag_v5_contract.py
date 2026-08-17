from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import benchmarks.run_rag_generation_v5 as generation_runner
import benchmarks.run_rag_v5_eval as retrieval_runner
from benchmarks.human_review.rag_v5_review import (
    DIMENSIONS,
    merge_reviews,
    prepare_review_package,
)
from benchmarks.mature_eval.rag_v5_dataset import (
    GENERATION_FRESH_PATH,
    GENERATION_KNOWN_PATH,
    RETRIEVAL_FRESH_PATH,
    RETRIEVAL_KNOWN_PATH,
    build_generation_fresh,
    build_generation_known,
    build_retrieval_fresh,
    build_retrieval_known,
    validate_rag_v5_files,
    write_rag_v5_datasets,
)
from benchmarks.run_rag_generation_v5 import generation_gate
from benchmarks.run_rag_v5_eval import known_regression_guard, retrieval_gate
from scripts.eval_rag import load_cases


def _retrieval_metrics(value: float = 1.0) -> dict:
    return {
        "metricCurves": {
            "3": {"recall": value},
            "5": {"recall": value, "ndcg": value},
            "10": {"mrr": value},
        },
        "noAnswerAccuracy": value,
        "injectionRobustness": value,
        "canonicalCitationCorrectness": value,
        "canonicalCitationCoverage": value,
    }


def test_rag_v5_locked_datasets_match_deterministic_contracts() -> None:
    validation = validate_rag_v5_files()
    assert load_cases(RETRIEVAL_KNOWN_PATH) == build_retrieval_known()
    assert load_cases(RETRIEVAL_FRESH_PATH) == build_retrieval_fresh()
    assert load_cases(GENERATION_KNOWN_PATH) == build_generation_known()
    assert load_cases(GENERATION_FRESH_PATH) == build_generation_fresh()
    assert validation["suiteLock"]["caseCounts"] == {
        "retrievalKnown": 264,
        "retrievalFresh": 48,
        "generationKnown": 60,
        "generationFresh": 20,
    }


def test_rag_v5_fresh_sets_cover_both_new_v2_facts() -> None:
    rows = [*load_cases(RETRIEVAL_FRESH_PATH), *load_cases(GENERATION_FRESH_PATH)]
    covered = {
        fact for row in rows for fact in row.get("relevantFactIds") or []
    }
    assert {"member.signin.streak_reward", "support.handoff.workflow"}.issubset(
        covered
    )
    assert sum(row.get("noAnswer") is True for row in load_cases(RETRIEVAL_FRESH_PATH)) == 4
    assert sum(row.get("injection") is True for row in load_cases(RETRIEVAL_FRESH_PATH)) == 4
    assert sum(row.get("noAnswer") is True for row in load_cases(GENERATION_FRESH_PATH)) == 4
    assert sum(row.get("injection") is True for row in load_cases(GENERATION_FRESH_PATH)) == 4


def test_rag_v5_dataset_writer_refuses_overwrite() -> None:
    with pytest.raises(FileExistsError, match="refusing overwrite"):
        write_rag_v5_datasets()


def test_rag_v5_known_regression_guard_allows_at_most_five_points() -> None:
    baseline = _retrieval_metrics(0.95)
    assert known_regression_guard(_retrieval_metrics(0.90), baseline)["passed"]
    assert not known_regression_guard(_retrieval_metrics(0.899), baseline)["passed"]


def test_rag_v5_retrieval_gate_is_fail_closed() -> None:
    complete_known = {"passed": True, "caseCount": 264}
    complete_fresh = {"passed": True, "caseCount": 48}
    regression = {"passed": True}
    assert retrieval_gate(
        _retrieval_metrics(),
        complete_fresh,
        known_provider=complete_known,
        regression_guard=regression,
    )["passed"]
    incomplete = retrieval_gate(
        _retrieval_metrics(),
        {"passed": False, "caseCount": 48},
        known_provider=complete_known,
        regression_guard=regression,
    )
    assert incomplete["status"] == "FAILED_RETAINED"


def test_rag_v5_generation_gate_requires_known_fresh_and_safety() -> None:
    known = {"taskSuccessCount": 51}
    fresh = {"taskSuccessRate": 0.85}
    overall = {
        "executedCount": 80,
        "runtimeErrorCount": 0,
        "usageIncompleteCount": 0,
        "taskSuccessRate": 0.85,
        "criticalSafetyViolationCount": 0,
        "generationMetrics": {
            "requiredClaimCompleteness": 0.85,
            "claimCitationSupport": 0.90,
            "canonicalCitationCoverage": 0.90,
            "noAnswerAccuracy": 1.0,
            "injectionAccuracy": 1.0,
            "invalidCitationCount": 0,
        },
    }
    complete = {"passed": True}
    assert generation_gate(
        known,
        fresh,
        overall,
        known_provider=complete,
        fresh_provider=complete,
    )["passed"]
    overall["criticalSafetyViolationCount"] = 1
    assert not generation_gate(
        known,
        fresh,
        overall,
        known_provider=complete,
        fresh_provider=complete,
    )["passed"]


def test_rag_v5_fresh_execution_locks_do_not_allow_another_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    retrieval_lock = tmp_path / "retrieval.lock.json"
    generation_lock = tmp_path / "generation.lock.json"
    monkeypatch.setattr(retrieval_runner, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(retrieval_runner, "FRESH_EXECUTION_LOCK", retrieval_lock)
    monkeypatch.setattr(generation_runner, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(generation_runner, "FRESH_EXECUTION_LOCK", generation_lock)
    run_id = "rag-v5-6eb8e8e-20260817"
    retrieval_runner._claim_fresh_execution(run_id)
    with pytest.raises(ValueError, match="another retained run"):
        retrieval_runner._claim_fresh_execution("rag-v5-6eb8e8f-20260818")
    generation_runner._claim_fresh_execution(run_id)
    with pytest.raises(ValueError, match="already been executed"):
        generation_runner._claim_fresh_execution(run_id)


def _review_template(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 5,
                "suite": "rag-v5-generation",
                "runId": "rag-v5-6eb8e8e-20260817",
                "cases": [
                    {
                        "caseId": f"secret-case-{index:03d}",
                        "comparisonGroup": "fresh-holdout",
                        "query": f"问题 {index}",
                        "answer": f"答案 [{1}]",
                        "retrievedRefs": [
                            {
                                "source": "knowledge.md",
                                "heading": "规则",
                                "snippet": "证据",
                            }
                        ],
                        "automaticMetrics": {"success": True},
                    }
                    for index in range(1, 21)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _complete_review(path: Path, reviewer_id: str, value: str = "true") -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            row["reviewerId"] = reviewer_id
            for dimension in DIMENSIONS:
                row[dimension] = value
            writer.writerow(row)


def test_rag_v5_blind_review_hides_labels_and_requires_two_people(
    tmp_path: Path,
) -> None:
    template = tmp_path / "review-template.json"
    package = tmp_path / "human-review"
    _review_template(template)
    status = prepare_review_package(template, package)
    assert status["status"] == "HUMAN_REVIEW_PENDING"
    blind_text = (package / "blind-cases.jsonl").read_text(encoding="utf-8")
    assert "automaticMetrics" not in blind_text
    assert "secret-case" not in blind_text

    pending = merge_reviews(
        package,
        package / "reviewer-a.csv",
        package / "reviewer-b.csv",
        tmp_path / "pending.json",
    )
    assert pending["status"] == "HUMAN_REVIEW_PENDING"

    _complete_review(package / "reviewer-a.csv", "reviewer-a-stable")
    _complete_review(package / "reviewer-b.csv", "reviewer-b-stable")
    merged = merge_reviews(
        package,
        package / "reviewer-a.csv",
        package / "reviewer-b.csv",
        tmp_path / "merged.json",
    )
    assert merged["status"] == "HUMAN_REVIEWED_PASSED"
    assert merged["conservativeAgreementPassed"] == 20
    assert merged["conservativeSafePassed"] == 20
    assert set(merged["cohensKappa"]) == set(DIMENSIONS)
