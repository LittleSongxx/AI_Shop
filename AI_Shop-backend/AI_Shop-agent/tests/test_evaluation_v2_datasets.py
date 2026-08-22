from copy import deepcopy

import pytest

from evaluation.core.catalog import load_catalog_fixture
from evaluation.core.contracts import Split, ValidationError
from evaluation.core.datasets import (
    case_content_sha256,
    load_split,
    parse_case,
    validate_repository_datasets,
    validate_unique_cases,
)


def test_visible_datasets_are_locked_disjoint_and_above_minimums():
    locks = validate_repository_datasets()

    assert locks["development"]["domainCounts"] == {
        "agent": 7,
        "rag": 18,
        "search": 18,
    }
    assert locks["regression"]["domainCounts"] == {
        "agent": 5,
        "rag": 26,
        "search": 20,
    }


def test_content_overlap_is_rejected_even_with_different_ids():
    case = load_split(Split.DEVELOPMENT)[0]
    duplicate = deepcopy(case)
    object.__setattr__(duplicate, "case_id", "different-case-id")

    assert case_content_sha256(case) == case_content_sha256(duplicate)
    with pytest.raises(ValidationError, match="duplicate case inputs"):
        validate_unique_cases([case, duplicate])


def test_search_case_must_bind_to_current_catalog_snapshot():
    raw = next(
        case for case in load_split(Split.DEVELOPMENT) if case.domain.value == "search"
    ).public()
    raw["expected"]["catalogSha256"] = "0" * 64

    with pytest.raises(ValidationError, match="catalog fixture"):
        parse_case(raw, expected_split=Split.DEVELOPMENT)


def test_catalog_fixture_is_self_hashing_and_nontrivial():
    fixture = load_catalog_fixture()

    assert fixture["productCount"] == 47
    assert len(fixture["canonicalSha256"]) == 64
    assert any(product["productId"] == "231335860060520" for product in fixture["products"])
    assert fixture["schemaVersion"] == "aishop-evaluation-product-catalog/v2"
    assert all(product["availabilitySource"] == "JAVA_GATEWAY" for product in fixture["products"])
    assert any(product["authoritativeAvailable"] is False for product in fixture["products"])


def test_unavailable_product_cannot_be_a_positive_qrel():
    fixture = load_catalog_fixture()
    unavailable = next(
        product for product in fixture["products"] if not product["authoritativeAvailable"]
    )
    raw = next(
        case for case in load_split(Split.DEVELOPMENT) if case.domain.value == "search"
    ).public()
    raw["expected"]["qrels"] = {unavailable["productId"]: 3}
    raw["expected"]["catalogSha256"] = fixture["canonicalSha256"]

    with pytest.raises(ValidationError, match="not authoritatively available"):
        parse_case(raw, expected_split=Split.DEVELOPMENT)


def test_rag_pattern_groups_are_validated_as_non_empty_alias_sets():
    raw = next(case for case in load_split(Split.REGRESSION) if case.domain.value == "rag").public()
    raw["expected"]["requiredClaims"][0].pop("patterns", None)
    raw["expected"]["requiredClaims"][0]["patternGroups"] = [["同义词一", "同义词二"]]

    parsed = parse_case(raw, expected_split=Split.REGRESSION)
    assert parsed.expected["requiredClaims"][0]["patternGroups"] == [["同义词一", "同义词二"]]

    raw["expected"]["requiredClaims"][0]["patternGroups"] = [[]]
    with pytest.raises(ValidationError, match="patternGroups"):
        parse_case(raw, expected_split=Split.REGRESSION)
