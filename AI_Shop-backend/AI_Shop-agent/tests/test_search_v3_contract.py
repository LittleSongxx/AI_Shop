from __future__ import annotations

import json
from pathlib import Path

import pytest

import benchmarks.run_search_v3_eval as search_v3_runner
from benchmarks.mature_eval.search_v3_dataset import (
    FRESH_CHALLENGE_PATH,
    MANDATORY_DYNAMIC_CATEGORY_ID,
    MANDATORY_DYNAMIC_CATEGORY_PRODUCT_ID,
    MANDATORY_NO_RESULT_ID,
    RUNTIME_HOLDOUT_PATH,
    build_fresh_challenge_payload,
    build_runtime_holdout,
    validate_search_v3_files,
    write_search_v3_datasets,
)
from benchmarks.run_search_relevance import load_cases
from benchmarks.run_search_v3_eval import _claim_fresh_execution, _validate_run_id


def test_search_v3_locked_datasets_match_deterministic_sources() -> None:
    validation = validate_search_v3_files()
    locked = json.loads(FRESH_CHALLENGE_PATH.read_text(encoding="utf-8"))
    assert locked == build_fresh_challenge_payload()
    assert load_cases(RUNTIME_HOLDOUT_PATH) == build_runtime_holdout()
    assert validation["suiteLock"]["caseCounts"] == {
        "knownChineseV2": 240,
        "knownProductServiceV2": 45,
        "fresh": 80,
        "challenge": 40,
        "runtimeHoldout": 30,
    }


def test_search_v3_mandatory_cases_are_exact_and_labelled() -> None:
    payload = json.loads(FRESH_CHALLENGE_PATH.read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in payload["queries"]}
    mars = by_id[MANDATORY_NO_RESULT_ID]
    assert mars["expectedNoResults"] is True
    assert "火星土壤" in mars["query"]
    assert "零食" in mars["query"]
    assert "积木" in mars["query"]

    runtime = {row["id"]: row for row in load_cases(RUNTIME_HOLDOUT_PATH)}
    uv = runtime[MANDATORY_DYNAMIC_CATEGORY_ID]
    assert uv["relevanceGrades"] == {MANDATORY_DYNAMIC_CATEGORY_PRODUCT_ID: 3}
    assert "UV打印机" in uv["query"]


def test_search_v3_dataset_writer_refuses_overwrite() -> None:
    with pytest.raises(FileExistsError, match="refusing overwrite"):
        write_search_v3_datasets()


def test_search_v3_run_id_contract() -> None:
    assert (
        _validate_run_id("search-v3-6eb8e8e-20260817")
        == "search-v3-6eb8e8e-20260817"
    )
    assert (
        _validate_run_id("search-v3-6eb8e8eb822a-20260817-retry-1")
        == "search-v3-6eb8e8eb822a-20260817-retry-1"
    )
    for value in (
        "search-v2-6eb8e8e-20260817",
        "search-v3-NOTHEX-20260817",
        "search-v3-6eb8e8e-2026-08-17",
        "search-v3-6eb8e8e-20260817-RETRY",
    ):
        with pytest.raises(ValueError, match="run-id"):
            _validate_run_id(value)


def test_search_v3_fresh_claim_is_single_run_and_failure_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result_root = tmp_path / "search-v3"
    lock_path = result_root / "_fresh-execution-lock.json"
    monkeypatch.setattr(search_v3_runner, "RESULTS_ROOT", result_root)
    monkeypatch.setattr(search_v3_runner, "FRESH_EXECUTION_LOCK", lock_path)
    first = _claim_fresh_execution("search-v3-6eb8e8e-20260817")
    repeated = _claim_fresh_execution("search-v3-6eb8e8e-20260817")
    assert repeated == first
    assert json.loads(lock_path.read_text(encoding="utf-8"))["policy"] == (
        "ONE_SHOT_FAIL_RETAINED"
    )
    with pytest.raises(ValueError, match="already claimed"):
        _claim_fresh_execution("search-v3-6eb8e8f-20260818")
