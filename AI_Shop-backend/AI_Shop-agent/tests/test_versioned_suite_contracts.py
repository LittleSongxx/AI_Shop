from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITES = PROJECT_ROOT / "benchmarks" / "suites"


def _load(name: str) -> dict:
    return json.loads((SUITES / name).read_text(encoding="utf-8"))


def test_versioned_suites_have_disjoint_result_roots_and_valid_run_ids() -> None:
    samples = {
        "search-v3.json": "search-v3-6eb8e8e-20260817",
        "rag-v5.json": "rag-v5-6eb8e8e-20260817",
        "agent-v2.json": "agent-v2-adaptive-6eb8e8e-20260817",
    }
    contracts = [_load(name) for name in samples]
    assert len({item["suiteId"] for item in contracts}) == len(contracts)
    assert len({item["resultRoot"] for item in contracts}) == len(contracts)
    for name, sample in samples.items():
        contract = _load(name)
        assert re.fullmatch(contract["runIdPattern"], sample)
        assert contract["freshPolicy"] == "ONE_SHOT_FAIL_RETAINED"


def test_historical_failures_are_explicit_and_immutable() -> None:
    baseline = _load("baseline-20260817.json")
    assert baseline["gitHead"] == "6eb8e8eb822a20394e6cc05958d72823379614cc"
    assert {item["status"] for item in baseline["historicalEvidence"]} == {
        "FAILED_RETAINED"
    }
    assert baseline["immutability"] == {
        "overwriteHistoricalRuns": False,
        "acceptBaselineToEraseFailure": False,
        "reuseFreshDataAfterTuning": False,
    }


def test_agent_v2_contract_keeps_all_44_tasks() -> None:
    contract = _load("agent-v2.json")
    assert contract["cases"]["knownSingleTurn"] == 37
    assert contract["cases"]["sequence"] == 7
    assert contract["cases"]["total"] == 44


def test_rag_v5_requires_real_two_reviewer_completion() -> None:
    contract = _load("rag-v5.json")
    review = contract["humanReview"]
    assert review["requiredReviewers"] == 2
    assert review["statusUntilComplete"] == "HUMAN_REVIEW_PENDING"
    assert review["scope"] == "fresh-holdout-only"
    assert review["automaticVerdictsExposed"] is False
    assert review["originalCaseIdsExposed"] is False


def test_search_v3_contract_binds_runner_locks_and_mandatory_cases() -> None:
    contract = _load("search-v3.json")
    assert contract["runner"] == "benchmarks/eval.py"
    assert contract["adapter"] == "search-v3"
    assert contract["stages"] == ["known", "final", "package"]
    assert contract["datasets"] == {
        "knownChineseV2": 240,
        "knownProductServiceV2": 45,
        "fresh": 80,
        "challenge": 40,
        "runtimeHoldout": 30,
    }
    assert set(contract["mandatoryCases"]) == {
        "unknown-mars-soil-with-snack-and-block-attributes-must-return-no-result",
        "new-real-catalog-category-must-not-be-rejected-by-static-taxonomy",
    }
    assert (PROJECT_ROOT / contract["runner"]).is_file()
    assert (PROJECT_ROOT / contract["suiteLock"]).is_file()


def test_rag_v5_contract_binds_v2_data_and_domain_modules() -> None:
    contract = _load("rag-v5.json")
    assert contract["retrieval"]["known"] == 264
    assert contract["retrieval"]["fresh"] == 48
    assert contract["generation"]["known"] == 60
    assert contract["generation"]["fresh"] == 20
    assert contract["requiredNewFacts"] == [
        "member.signin.streak_reward",
        "support.handoff.workflow",
    ]
    for path in [
        contract["suiteLock"],
        *contract["domainModules"].values(),
        contract["retrieval"]["knownDataset"],
        contract["retrieval"]["freshDataset"],
        contract["generation"]["knownDataset"],
        contract["generation"]["freshDataset"],
        contract["generation"]["selection"],
    ]:
        assert (PROJECT_ROOT / path).is_file()
