from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from benchmarks.agentic_commerce_v2 import (
    SUBSETS,
    gate_failures,
    inventory_reference,
    load_cases,
    load_lock,
    validate_contract,
)


def test_frozen_agentic_commerce_v2_dataset_matches_lock():
    report = validate_contract()

    assert report["caseCount"] == 27
    assert set(report["subsetCounts"]) == SUBSETS
    assert set(report["subsetCounts"].values()) == {3}
    assert report["datasetSha256"] == load_lock()["datasetSha256"]


def test_each_v2_subset_has_dev_and_test_cases():
    splits: dict[str, Counter[str]] = {subset: Counter() for subset in SUBSETS}
    for case in load_cases():
        splits[case["subset"]][case["split"]] += 1

    assert all(counts["dev"] >= 1 and counts["test"] >= 1 for counts in splits.values())


def test_inventory_reference_applies_rop_review_period_inbound_and_moq():
    assert inventory_reference(
        {
            "ewmaDailyDemand": 2,
            "leadTimeDays": 7,
            "safetyStock": 3,
            "reviewPeriodDays": 14,
            "minOrderQuantity": 5,
            "currentStock": 4,
            "inboundQuantity": 0,
        }
    ) == {
        "reorderPoint": 17.0,
        "suggestedReplenishQuantity": 45,
        "coverageDays": 2.0,
    }
    assert inventory_reference(
        {
            "ewmaDailyDemand": 0.5,
            "leadTimeDays": 6,
            "safetyStock": 2,
            "reviewPeriodDays": 14,
            "minOrderQuantity": 10,
            "currentStock": 20,
            "inboundQuantity": 8,
        }
    )["suggestedReplenishQuantity"] == 0


def test_contract_rejects_unsafe_inventory_and_commercial_expectations():
    cases = deepcopy(load_cases())
    inventory = next(case for case in cases if case["subset"] == "inventory_forecast")
    inventory["expected"]["manualOnly"] = False
    operation = next(
        case
        for case in cases
        if case["subset"] == "commercial_ranking"
        and case["expected"].get("operationInsertCount")
    )
    operation["expected"]["disclosed"] = False

    with pytest.raises(ValueError, match="manualOnly|必须披露"):
        validate_contract(cases)


def test_runtime_gate_enforces_safety_metrics_without_requiring_optional_metrics():
    thresholds = load_lock()["thresholds"]
    perfect = {
        **{metric: 1.0 for metric in thresholds["minimum"]},
        **{metric: 0.0 for metric in thresholds["maximum"]},
    }
    assert gate_failures(perfect, thresholds) == []

    failed = {**perfect, "hardConstraintCompliance": 0.99, "operationFirstPositionRate": 0.01}
    failures = gate_failures(failed, thresholds)
    assert any("hardConstraintCompliance" in failure for failure in failures)
    assert any("operationFirstPositionRate" in failure for failure in failures)
