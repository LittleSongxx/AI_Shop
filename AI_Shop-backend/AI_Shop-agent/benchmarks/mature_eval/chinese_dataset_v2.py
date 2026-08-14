"""Deterministic independent Chinese commerce Search v2 dataset."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from benchmarks.mature_eval.chinese_dataset import CATEGORY_SPECS
from benchmarks.mature_eval.common import atomic_write_json, canonical_json_bytes, sha256_bytes

DATASET_VERSION = "chinese-commerce-search-v2"
EVIDENCE_SOURCE = "SYNTHETIC"
PRODUCTS_PER_CATEGORY = 30
SPLIT_COUNTS = {"public": 120, "fresh_holdout": 80, "challenge": 40}
RUNTIME_BRANDS = ("华为", "苹果", "小米", "荣耀", "联想")


def _product_id(category: str, index: int) -> str:
    return f"zh2-{category}-{index + 1:02d}"


def _category_name(category: str) -> str:
    return str(CATEGORY_SPECS[category]["name"])


def build_products() -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for category_index, (category, spec) in enumerate(CATEGORY_SPECS.items()):
        attribute_keys = list(spec["attributes"])
        for index in range(PRODUCTS_PER_CATEGORY):
            attributes = {
                key: spec["attributes"][key][
                    (index // (3**position)) % len(spec["attributes"][key])
                ]
                for position, key in enumerate(attribute_keys)
            }
            brand = RUNTIME_BRANDS[index % len(RUNTIME_BRANDS)]
            scenario = spec["scenarios"][(index // 2) % len(spec["scenarios"])]
            audience = spec["audiences"][(index // 3) % len(spec["audiences"])]
            price = round(80 + category_index * 37 + index * 89, 2)
            attributes_text = "、".join(str(value) for value in attributes.values())
            products.append(
                {
                    "id": _product_id(category, index),
                    "category": category,
                    "categoryName": spec["name"],
                    "brand": brand,
                    "price": price,
                    "attributes": attributes,
                    "scenario": scenario,
                    "audience": audience,
                    "exclusions": [],
                    "evidenceSource": EVIDENCE_SOURCE,
                    "name": f"{brand}{spec['name']}{attributes_text} V2-{index + 1:02d}",
                    "description": (
                        f"面向{audience}的{scenario}{spec['name']}，核心属性为{attributes_text}。"
                    ),
                }
            )
    return products


def compute_relevance_grades(
    products: list[dict[str, Any]], constraints: dict[str, Any]
) -> dict[str, int]:
    if constraints.get("outOfDomain"):
        return {str(product["id"]): 0 for product in products}
    grades: dict[str, int] = {}
    required_attributes = constraints.get("attributes") or {}
    for product in products:
        if product["categoryName"] != constraints.get("category"):
            grades[product["id"]] = 0
            continue
        price_ok = not (
            constraints.get("priceMax") is not None
            and float(product["price"]) > float(constraints["priceMax"])
        )
        brand_ok = not constraints.get("requiredBrands") or product["brand"] in constraints["requiredBrands"]
        scenario_ok = not constraints.get("scenario") or product["scenario"] == constraints["scenario"]
        audience_ok = not constraints.get("audience") or product["audience"] == constraints["audience"]
        matched_attributes = sum(
            product["attributes"].get(key) == value
            for key, value in required_attributes.items()
        )
        exact = (
            price_ok
            and brand_ok
            and scenario_ok
            and audience_ok
            and matched_attributes == len(required_attributes)
        )
        if exact:
            grade = 3
        elif price_ok and brand_ok and matched_attributes >= max(1, len(required_attributes) - 1):
            grade = 2
        else:
            grade = 1
        grades[product["id"]] = grade
    return grades


def _standard_query(product: dict[str, Any], local_index: int) -> str:
    attributes = list(product["attributes"].values())
    suffixes = (
        "近期自用",
        "准备日常使用",
        "希望耐用一些",
        "这次优先看硬条件",
        "不考虑超预算款",
        "想比较几款再决定",
        "准备替换旧设备",
        "希望操作简单",
        "主要在周末使用",
        "工作日使用较多",
        "希望尽快选定",
        "先看符合条件的款",
    )
    return (
        f"预算{int(float(product['price']) + 1)}元以内，想买{product['brand']}品牌的"
        f"{product['categoryName']}，用于{product['scenario']}，适合{product['audience']}，"
        f"希望有{'和'.join(str(value) for value in attributes)}，{suffixes[local_index]}"
    )


def build_queries(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_category = {
        category: [product for product in products if product["category"] == category]
        for category in CATEGORY_SPECS
    }
    queries: list[dict[str, Any]] = []
    sequence = 0
    for category, spec in CATEGORY_SPECS.items():
        category_products = by_category[category]
        for local_index in range(12):
            sequence += 1
            product = category_products[(local_index * 2 + len(category)) % len(category_products)]
            split = "public" if local_index < 6 else "fresh_holdout" if local_index < 10 else "challenge"
            query_type = "standard"
            constraints: dict[str, Any] = {
                "category": spec["name"],
                "priceMax": float(product["price"]) + 1,
                "requiredBrands": [product["brand"]],
                "scenario": product["scenario"],
                "audience": product["audience"],
                "attributes": dict(product["attributes"]),
            }
            query = _standard_query(product, local_index)
            if split == "challenge":
                challenge_index = len([row for row in queries if row["split"] == "challenge"])
                if challenge_index < 20:
                    query_type = "typo"
                    query = query.replace("希望有", "想整一个带").replace("品牌", "牌子")
                elif challenge_index < 30:
                    query_type = "conflict"
                    constraints["priceMax"] = 1
                    query = f"只要1元以内的{product['brand']}{product['categoryName']}，用于{product['scenario']}，条件不能放宽"
                else:
                    query_type = "no_match"
                    constraints = {"outOfDomain": True, "category": "火星样本"}
                    attribute_hint = "、".join(
                        str(value) for value in product["attributes"].values()
                    )
                    query = (
                        "想买可当天送达的火星土壤样本，"
                        f"给{product['audience']}在{product['scenario']}时使用，"
                        f"还要求具备{attribute_hint}并能当作{product['categoryName']}直接使用"
                    )
            grades = compute_relevance_grades(products, constraints)
            queries.append(
                {
                    "id": f"zh2-query-{sequence:03d}",
                    "query": query,
                    "split": split,
                    "queryType": query_type,
                    "constraints": constraints,
                    "relevanceGrades": grades,
                    "expectedNoResults": not any(value >= 2 for value in grades.values()),
                    "evidenceSource": EVIDENCE_SOURCE,
                }
            )
    return queries


def validate_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("datasetVersion") != DATASET_VERSION:
        raise ValueError("unsupported Chinese Search v2 dataset version")
    products = payload.get("products") or []
    queries = payload.get("queries") or []
    if len(products) != 600 or len({_product_id_value(row) for row in products}) != 600:
        raise ValueError("Chinese Search v2 must contain 600 unique products")
    category_counts = Counter(str(row.get("category")) for row in products)
    if category_counts != Counter({category: 30 for category in CATEGORY_SPECS}):
        raise ValueError("Chinese Search v2 products must use a 20 x 30 distribution")
    if len({str(row.get("name")) for row in products}) != len(products):
        raise ValueError("Chinese Search v2 product names must be unique")
    for product in products:
        if product.get("evidenceSource") != EVIDENCE_SOURCE:
            raise ValueError("Chinese Search v2 product source must be SYNTHETIC")
        if product.get("brand") not in RUNTIME_BRANDS:
            raise ValueError("Chinese Search v2 product brand is outside the parser contract")
        category = str(product.get("category") or "")
        if category not in CATEGORY_SPECS or product.get("categoryName") != _category_name(category):
            raise ValueError("Chinese Search v2 product category is invalid")
        allowed = {
            key: {str(value) for value in values}
            for key, values in CATEGORY_SPECS[category]["attributes"].items()
        }
        attributes = product.get("attributes")
        if not isinstance(attributes, dict) or set(attributes) != set(allowed):
            raise ValueError("Chinese Search v2 product attributes do not match the schema")
        if any(str(value) not in allowed[key] for key, value in attributes.items()):
            raise ValueError("Chinese Search v2 product contains a hallucinated attribute")
    if len(queries) != 240 or len({str(row.get("id")) for row in queries}) != 240:
        raise ValueError("Chinese Search v2 must contain 240 unique queries")
    if len({str(row.get("query")) for row in queries}) != len(queries):
        raise ValueError("Chinese Search v2 query text must be unique")
    split_counts = Counter(str(row.get("split")) for row in queries)
    if split_counts != Counter(SPLIT_COUNTS):
        raise ValueError(f"Chinese Search v2 split counts are invalid: {dict(split_counts)}")
    challenge = [row for row in queries if row.get("split") == "challenge"]
    challenge_counts = Counter(str(row.get("queryType")) for row in challenge)
    if challenge_counts != Counter({"typo": 20, "conflict": 10, "no_match": 10}):
        raise ValueError("Chinese Search v2 challenge distribution is invalid")
    product_ids = {_product_id_value(row) for row in products}
    for query in queries:
        if query.get("evidenceSource") != EVIDENCE_SOURCE:
            raise ValueError("Chinese Search v2 query source must be SYNTHETIC")
        constraints = query.get("constraints")
        if not isinstance(constraints, dict):
            raise ValueError("Chinese Search v2 query constraints must be an object")
        allowed_constraint_keys = {
            "outOfDomain",
            "category",
            "priceMax",
            "requiredBrands",
            "scenario",
            "audience",
            "attributes",
        }
        if not set(constraints).issubset(allowed_constraint_keys):
            raise ValueError("Chinese Search v2 query contains an unsupported constraint")
        if constraints.get("outOfDomain"):
            if constraints != {"outOfDomain": True, "category": "火星样本"}:
                raise ValueError("Chinese Search v2 out-of-domain constraint is invalid")
        else:
            category_names = {
                str(spec["name"]): category for category, spec in CATEGORY_SPECS.items()
            }
            category = category_names.get(str(constraints.get("category") or ""))
            if category is None:
                raise ValueError("Chinese Search v2 query category is invalid")
            brands = constraints.get("requiredBrands")
            if not isinstance(brands, list) or not brands or any(
                brand not in RUNTIME_BRANDS for brand in brands
            ):
                raise ValueError("Chinese Search v2 query brand constraint is invalid")
            if not isinstance(constraints.get("priceMax"), (int, float)):
                raise ValueError("Chinese Search v2 query price constraint is invalid")
            spec = CATEGORY_SPECS[category]
            if constraints.get("scenario") not in spec["scenarios"]:
                raise ValueError("Chinese Search v2 query scenario is invalid")
            if constraints.get("audience") not in spec["audiences"]:
                raise ValueError("Chinese Search v2 query audience is invalid")
            attributes = constraints.get("attributes")
            if not isinstance(attributes, dict) or set(attributes) != set(spec["attributes"]):
                raise ValueError("Chinese Search v2 query attribute schema is invalid")
            if any(
                value not in spec["attributes"][key]
                for key, value in attributes.items()
            ):
                raise ValueError("Chinese Search v2 query contains an invalid attribute value")
        supplied = {
            str(key): int(value)
            for key, value in (query.get("relevanceGrades") or {}).items()
        }
        if set(supplied) != product_ids:
            raise ValueError("Chinese Search v2 labels must cover the complete catalog")
        if supplied != compute_relevance_grades(products, constraints):
            raise ValueError("Chinese Search v2 labels are inconsistent with constraints")
        has_relevant = any(value >= 2 for value in supplied.values())
        if bool(query.get("expectedNoResults")) == has_relevant:
            raise ValueError("Chinese Search v2 expectedNoResults is inconsistent")
    return {
        "products": len(products),
        "queries": len(queries),
        "categories": len(category_counts),
        "splits": dict(sorted(split_counts.items())),
        "challengeTypes": dict(sorted(challenge_counts.items())),
    }


def _product_id_value(row: dict[str, Any]) -> str:
    return str(row.get("id") or "")


def generate_dataset(output_path: Path) -> dict[str, Any]:
    products = build_products()
    queries = build_queries(products)
    payload = {
        "schemaVersion": 2,
        "datasetVersion": DATASET_VERSION,
        "evidenceSource": EVIDENCE_SOURCE,
        "generator": {
            "type": "deterministic-structured",
            "labeling": "structured constraints over allowed values",
            "llmAuthority": "NONE",
        },
        "products": products,
        "queries": queries,
    }
    summary = validate_dataset(payload)
    atomic_write_json(output_path, payload)
    lock = {
        "schemaVersion": 2,
        "datasetVersion": DATASET_VERSION,
        "datasetSha256": sha256_bytes(canonical_json_bytes(payload)),
        "evidenceSource": EVIDENCE_SOURCE,
        "counts": summary,
        "labelSource": "deterministic structured constraints; no LLM self-grading",
    }
    atomic_write_json(output_path.with_suffix(".lock.json"), lock)
    return {"dataset": payload, "lock": lock}
