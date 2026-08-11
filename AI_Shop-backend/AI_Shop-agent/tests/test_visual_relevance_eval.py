from __future__ import annotations

import hashlib
from collections import Counter

import pytest

from benchmarks.visual_eval import (
    build_query_image,
    evaluate_predictions,
    gate_failures,
    load_cases,
    validate_contract,
)


def test_frozen_visual_dataset_matches_catalog_assets_and_lock():
    cases = load_cases()
    contract = validate_contract(cases)

    assert contract["cases"] == 25
    assert contract["subsetCounts"] == {
        "exact_image": 5,
        "compressed_or_cropped": 5,
        "alternate_view": 5,
        "category_similarity": 5,
        "no_match": 5,
    }


def test_query_transforms_are_deterministic_and_nonempty():
    cases = load_cases()
    digests: dict[str, str] = {}
    for case in cases:
        first = build_query_image(case)
        second = build_query_image(case)
        assert first == second
        assert len(first) > 100
        digests[case.case_id] = hashlib.sha256(first).hexdigest()

    assert len(digests) == len(cases)
    assert len(set(digests.values())) == len(cases)


def test_visual_metrics_and_frozen_gate_accept_perfect_predictions():
    cases = load_cases()
    predictions = {}
    for case in cases:
        if case.raw["expectReject"]:
            predictions[case.case_id] = []
        else:
            predictions[case.case_id] = list(case.raw["relevanceGrades"])
    fallback = {
        "grounding_to_whole_image": True,
        "embedding_to_understanding": True,
        "rerank_to_rrf": True,
        "index_to_exact_or_understanding": True,
        "fallback_search_failure_is_explicit": True,
    }

    report = evaluate_predictions(cases, predictions, fallback_outcomes=fallback)
    thresholds = validate_contract(cases)["thresholds"]

    assert report["exactTop1"] == 1.0
    assert report["robustnessRecallAt5"] == 1.0
    assert report["alternateViewRecallAt5"] == 1.0
    assert report["rejectionAccuracy"] == 1.0
    assert report["unavailableProductRate"] == 0.0
    assert report["fallbackSuccessRate"] == 1.0
    assert gate_failures(report, thresholds) == []


def test_visual_gate_rejects_false_matches_and_missing_fallback_evidence():
    cases = load_cases()
    predictions = {case.case_id: ["622491960431656"] for case in cases}

    report = evaluate_predictions(cases, predictions)
    failures = gate_failures(report, validate_contract(cases)["thresholds"])

    assert report["rejectionAccuracy"] == 0.0
    assert report["fallbackSuccessRate"] is None
    assert any("rejectionAccuracy" in failure for failure in failures)
    assert any("fallbackSuccessRate" in failure for failure in failures)


@pytest.mark.parametrize("subset", [
    "exact_image",
    "compressed_or_cropped",
    "alternate_view",
    "category_similarity",
    "no_match",
])
def test_each_visual_subset_has_five_cases(subset):
    counts = Counter(case.subset for case in load_cases())
    assert counts[subset] == 5
