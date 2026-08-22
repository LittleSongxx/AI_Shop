from __future__ import annotations

import math

import pytest

from evaluation.core.quality_metrics import (
    brier_score,
    catalog_coverage_rate,
    constraint_satisfaction_rate,
    expected_calibration_error,
    intra_list_diversity,
    novelty_rate,
    pass_power_k,
    slate_quality_metrics,
    unique_value_ratio,
)


def test_constraint_satisfaction_is_fail_closed_for_empty_or_invalid_counts():
    assert constraint_satisfaction_rate(3, 1) == pytest.approx(2 / 3)
    assert constraint_satisfaction_rate(0, 0) == 0.0
    with pytest.raises(ValueError):
        constraint_satisfaction_rate(-1, 0)
    with pytest.raises(ValueError):
        constraint_satisfaction_rate(1, 2)


def test_intra_list_diversity_uses_only_comparable_metadata_pairs():
    items = [
        {"productId": "a", "categoryId": "headphone"},
        {"productId": "b", "categoryId": "headphone"},
        {"productId": "c", "categoryId": "speaker"},
    ]
    # One redundant pair out of three comparable pairs.
    assert intra_list_diversity(items) == pytest.approx(2 / 3)
    assert intra_list_diversity([{"productId": "a"}, {"productId": "b"}]) == 0.0
    assert intra_list_diversity([{"categoryId": "a"}]) == 1.0


def test_unique_coverage_and_novelty_are_deterministic_and_deduplicate_catalog():
    items = [
        {"productId": "a", "category": "phone", "brand": "A"},
        {"productId": "b", "category": "phone", "brand": "B"},
        {"productId": "c", "category": "book", "brand": "B"},
        {"productId": "missing"},
    ]
    assert unique_value_ratio(items, "category") == pytest.approx(2 / 4)
    assert unique_value_ratio(items, "brand") == pytest.approx(2 / 4)
    assert catalog_coverage_rate(["a", "a", "outside"], ["a", "b", "c"]) == pytest.approx(1 / 3)
    assert catalog_coverage_rate(["a"], []) == 0.0
    assert novelty_rate(["a", "a", "c"], ["a"]) == pytest.approx(1 / 3)
    assert novelty_rate([], ["a"]) == 0.0


def test_calibration_metrics_validate_binary_inputs_and_known_values():
    confidences = [0.1, 0.2, 0.8, 0.9]
    outcomes = [0, 0, 1, 1]
    assert expected_calibration_error(confidences, outcomes, bins=2) == pytest.approx(0.15)
    assert brier_score(confidences, outcomes) == pytest.approx(0.025)
    assert expected_calibration_error([], []) == 0.0
    assert brier_score([], []) == 0.0

    for metric in (expected_calibration_error, brier_score):
        with pytest.raises(ValueError):
            metric([0.1], [])
        with pytest.raises(ValueError):
            metric([math.nan], [1])
        with pytest.raises(ValueError):
            metric([1.1], [1])
        with pytest.raises(ValueError):
            metric([0.5], [2])
    with pytest.raises(ValueError):
        expected_calibration_error([0.5], [1], bins=0)


def test_pass_power_k_requires_nonempty_binary_trials():
    assert pass_power_k([[1, 1], [True, False], [1]]) == pytest.approx(2 / 3)
    assert pass_power_k([]) == 0.0
    with pytest.raises(ValueError):
        pass_power_k([[]])
    with pytest.raises(ValueError):
        pass_power_k([[1, 0, 2]])


def test_slate_quality_metrics_has_stable_fail_closed_empty_shape():
    metrics = slate_quality_metrics([], catalog_ids=["a"], previously_seen_ids=["a"])
    assert metrics == {
        "returnedCount": 0,
        "constraintSatisfactionRate": 0.0,
        "intraListDiversity": 1.0,
        "uniqueCategoryRatio": 0.0,
        "uniqueBrandRatio": 0.0,
        "catalogCoverageRate": 0.0,
        "noveltyRate": 0.0,
    }
