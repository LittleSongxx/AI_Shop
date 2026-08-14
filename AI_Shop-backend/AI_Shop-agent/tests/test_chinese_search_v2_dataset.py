from copy import deepcopy

import pytest

from benchmarks.mature_eval.chinese_dataset_v2 import (
    SPLIT_COUNTS,
    build_products,
    build_queries,
    compute_relevance_grades,
    validate_dataset,
)


def _dataset():
    products = build_products()
    return {
        "schemaVersion": 2,
        "datasetVersion": "chinese-commerce-search-v2",
        "evidenceSource": "SYNTHETIC",
        "products": products,
        "queries": build_queries(products),
    }


def test_v2_dataset_has_600_products_and_240_split_queries():
    report = validate_dataset(_dataset())

    assert report["products"] == 600
    assert report["queries"] == 240
    assert report["categories"] == 20
    assert report["splits"] == SPLIT_COUNTS
    assert report["challengeTypes"] == {"conflict": 10, "no_match": 10, "typo": 20}


def test_v2_labels_are_derived_only_from_structured_constraints():
    payload = _dataset()
    query = payload["queries"][0]

    assert query["relevanceGrades"] == compute_relevance_grades(
        payload["products"], query["constraints"]
    )
    assert any(value == 3 for value in query["relevanceGrades"].values())


def test_v2_validator_rejects_label_and_split_leakage():
    payload = _dataset()
    first_product = payload["products"][0]["id"]
    payload["queries"][0]["relevanceGrades"][first_product] = 99
    with pytest.raises(ValueError, match="inconsistent"):
        validate_dataset(payload)

    payload = _dataset()
    payload["queries"][0]["split"] = "fresh_holdout"
    with pytest.raises(ValueError, match="split"):
        validate_dataset(payload)


def test_v2_validator_rejects_duplicate_products():
    payload = _dataset()
    payload["products"][1] = deepcopy(payload["products"][0])
    with pytest.raises(ValueError, match="600 unique"):
        validate_dataset(payload)
