from copy import deepcopy

import pytest

from benchmarks.mature_eval.chinese_dataset import (
    BRAND_PREFIXES,
    CATEGORY_SPECS,
    DATASET_VERSION,
    EVIDENCE_SOURCE,
    SPLIT_COUNTS,
    compute_relevance_grades,
    validate_dataset,
    validate_product,
    validate_query,
)


def _product(category: str, index: int) -> dict:
    spec = CATEGORY_SPECS[category]
    attributes = {
        key: values[index % len(values)]
        for key, values in spec["attributes"].items()
    }
    return {
        "id": f"zh-{category}-{index + 1:02d}",
        "category": category,
        "categoryName": spec["name"],
        "brand": BRAND_PREFIXES[index % len(BRAND_PREFIXES)],
        "price": 100 + index * 10,
        "attributes": attributes,
        "scenario": spec["scenarios"][index % len(spec["scenarios"])],
        "audience": spec["audiences"][index % len(spec["audiences"])],
        "exclusions": [],
        "evidenceSource": EVIDENCE_SOURCE,
        "name": f"{spec['name']} {category} 测试款 {index + 1}",
        "description": f"用于 {spec['scenarios'][index % len(spec['scenarios'])]} 的虚构商品。",
    }


def _dataset() -> dict:
    products = [
        _product(category, index)
        for category in CATEGORY_SPECS
        for index in range(15)
    ]
    queries = []
    categories = list(CATEGORY_SPECS)
    for index in range(120):
        split = "public" if index < 60 else "fresh_holdout" if index < 100 else "challenge"
        product = products[index]
        query_type = "standard"
        constraints = {
            "category": product["category"],
            "priceMax": product["price"],
            "scenario": product["scenario"],
            "audience": product["audience"],
            "attributes": dict(product["attributes"]),
        }
        if split == "challenge":
            query_type = "typo" if index < 110 else "conflict"
            if query_type == "conflict":
                constraints["priceMax"] = 1
        grades = compute_relevance_grades(products, constraints)
        queries.append(
            {
                "id": f"query-{index + 1:03d}",
                "query": f"测试查询 {categories[index % len(categories)]} {index}",
                "split": split,
                "queryType": query_type,
                "constraints": constraints,
                "relevanceGrades": grades,
                "expectedNoResults": not any(value >= 2 for value in grades.values()),
                "evidenceSource": EVIDENCE_SOURCE,
            }
        )
    return {
        "schemaVersion": 1,
        "datasetVersion": DATASET_VERSION,
        "evidenceSource": EVIDENCE_SOURCE,
        "products": products,
        "queries": queries,
    }


def test_dataset_has_300_products_20_by_15_and_120_split_queries():
    payload = _dataset()

    report = validate_dataset(payload)

    assert report["products"] == 300
    assert report["categories"] == 20
    assert report["queries"] == 120
    assert report["splits"] == SPLIT_COUNTS
    assert report["challengeTypes"] == {"conflict": 10, "typo": 10}


def test_structured_labels_use_grade_three_two_one_zero():
    products = [
        {
            **_product("earphone", 0),
            "price": 100,
            "attributes": {"wear": "入耳式", "noiseControl": "主动降噪"},
            "scenario": "通勤",
            "audience": "上班族",
        },
        {
            **_product("earphone", 1),
            "price": 120,
            "attributes": {"wear": "入耳式", "noiseControl": "被动降噪"},
            "scenario": "办公",
            "audience": "学生",
        },
        _product("phone", 0),
    ]
    constraints = {
        "category": "earphone",
        "priceMax": 150,
        "scenario": "通勤",
        "audience": "上班族",
        "attributes": {"wear": "入耳式", "noiseControl": "主动降噪"},
    }

    grades = compute_relevance_grades(products, constraints)

    assert grades[products[0]["id"]] == 3
    assert grades[products[1]["id"]] == 2
    assert grades[products[2]["id"]] == 0


def test_product_rejects_llm_hallucinated_fields_and_values():
    product = _product("earphone", 0)
    product["attributes"]["noiseControl"] = "量子降噪"
    with pytest.raises(ValueError, match="hallucinated"):
        validate_product(product)

    product = _product("earphone", 0)
    product["attributes"]["invented"] = "yes"
    with pytest.raises(ValueError, match="schema"):
        validate_product(product)


def test_query_rejects_labels_not_derived_from_constraints():
    payload = _dataset()
    query = deepcopy(payload["queries"][0])
    query["relevanceGrades"][payload["products"][0]["id"]] = 0

    with pytest.raises(ValueError, match="not computed"):
        validate_query(query, payload["products"])


def test_dataset_rejects_duplicate_products_and_split_leakage():
    payload = _dataset()
    payload["products"][1]["id"] = payload["products"][0]["id"]
    with pytest.raises(ValueError, match="300 unique"):
        validate_dataset(payload)

    payload = _dataset()
    payload["queries"][0]["split"] = "fresh_holdout"
    with pytest.raises(ValueError, match="split distribution"):
        validate_dataset(payload)

    payload = _dataset()
    payload["queries"][1]["query"] = payload["queries"][0]["query"]
    with pytest.raises(ValueError, match="query texts"):
        validate_dataset(payload)
