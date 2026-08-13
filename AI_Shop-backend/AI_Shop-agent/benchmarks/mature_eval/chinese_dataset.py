"""Validated Chinese synthetic commerce Search dataset generation."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.infra.http_client import get_client
from benchmarks.mature_eval.common import atomic_write_json, canonical_json_bytes, sha256_bytes

DATASET_VERSION = "chinese-commerce-search-v1"
EVIDENCE_SOURCE = "SYNTHETIC"

CATEGORY_SPECS: dict[str, dict[str, Any]] = {
    "earphone": {"name": "耳机", "aliases": ["耳机", "蓝牙耳机"], "attributes": {"wear": ["入耳式", "半入耳式", "头戴式"], "noiseControl": ["主动降噪", "被动降噪", "无降噪"]}, "scenarios": ["通勤", "办公", "运动"], "audiences": ["学生", "上班族", "运动人群"]},
    "phone": {"name": "手机", "aliases": ["手机", "智能手机"], "attributes": {"storage": ["128GB", "256GB", "512GB"], "focus": ["拍照", "续航", "游戏"]}, "scenarios": ["日常", "摄影", "手游"], "audiences": ["学生", "职场人士", "长辈"]},
    "laptop": {"name": "笔记本电脑", "aliases": ["笔记本", "电脑"], "attributes": {"memory": ["16GB", "24GB", "32GB"], "focus": ["轻薄", "性能", "长续航"]}, "scenarios": ["办公", "编程", "设计"], "audiences": ["学生", "程序员", "设计师"]},
    "tablet": {"name": "平板电脑", "aliases": ["平板", "平板电脑"], "attributes": {"size": ["8英寸", "11英寸", "13英寸"], "focus": ["学习", "影音", "绘画"]}, "scenarios": ["网课", "娱乐", "创作"], "audiences": ["学生", "儿童", "设计师"]},
    "camera": {"name": "相机", "aliases": ["相机", "微单"], "attributes": {"format": ["APS-C", "全画幅", "一英寸"], "focus": ["人像", "旅行", "视频"]}, "scenarios": ["旅行", "人像", "短视频"], "audiences": ["新手", "摄影爱好者", "视频创作者"]},
    "smartwatch": {"name": "智能手表", "aliases": ["智能手表", "运动手表"], "attributes": {"battery": ["3天", "7天", "14天"], "focus": ["健康监测", "运动", "通话"]}, "scenarios": ["健身", "通勤", "户外"], "audiences": ["跑者", "上班族", "长辈"]},
    "backpack": {"name": "双肩包", "aliases": ["双肩包", "背包"], "attributes": {"capacity": ["15L", "25L", "35L"], "material": ["尼龙", "帆布", "聚酯纤维"]}, "scenarios": ["通勤", "旅行", "上学"], "audiences": ["学生", "上班族", "旅行者"]},
    "running_shoes": {"name": "跑鞋", "aliases": ["跑鞋", "运动鞋"], "attributes": {"support": ["缓震", "支撑", "竞速"], "surface": ["公路", "跑步机", "轻越野"]}, "scenarios": ["慢跑", "竞速", "健走"], "audiences": ["初跑者", "进阶跑者", "大体重人群"]},
    "jacket": {"name": "外套", "aliases": ["外套", "夹克"], "attributes": {"season": ["春秋", "冬季", "夏季防晒"], "feature": ["防风", "保暖", "轻薄"]}, "scenarios": ["通勤", "户外", "旅行"], "audiences": ["学生", "上班族", "户外人群"]},
    "skincare": {"name": "面霜", "aliases": ["面霜", "护肤霜"], "attributes": {"skinType": ["干性", "油性", "敏感肌"], "effect": ["保湿", "控油", "舒缓"]}, "scenarios": ["日常护肤", "换季", "熬夜后"], "audiences": ["学生", "上班族", "敏感肌人群"]},
    "air_fryer": {"name": "空气炸锅", "aliases": ["空气炸锅", "炸锅"], "attributes": {"capacity": ["3L", "5L", "7L"], "feature": ["可视窗", "免翻面", "低油"]}, "scenarios": ["一人食", "家庭聚餐", "减脂餐"], "audiences": ["独居人群", "三口之家", "健身人群"]},
    "rice_cooker": {"name": "电饭煲", "aliases": ["电饭煲", "电饭锅"], "attributes": {"capacity": ["2L", "4L", "5L"], "feature": ["预约", "低糖饭", "IH加热"]}, "scenarios": ["一人食", "家庭用餐", "老人使用"], "audiences": ["独居人群", "家庭", "长辈"]},
    "vacuum": {"name": "吸尘器", "aliases": ["吸尘器", "无线吸尘器"], "attributes": {"type": ["手持", "立式", "除螨"] , "feature": ["轻量", "大吸力", "低噪"]}, "scenarios": ["小户型", "有宠家庭", "床褥清洁"], "audiences": ["租房人群", "养宠人群", "家庭"]},
    "air_purifier": {"name": "空气净化器", "aliases": ["空气净化器", "净化器"], "attributes": {"area": ["20平方米", "40平方米", "60平方米"], "focus": ["除甲醛", "除花粉", "除异味"]}, "scenarios": ["卧室", "新房", "有宠家庭"], "audiences": ["过敏人群", "新居家庭", "养宠人群"]},
    "coffee_machine": {"name": "咖啡机", "aliases": ["咖啡机", "意式咖啡机"], "attributes": {"type": ["胶囊", "半自动", "全自动"], "feature": ["奶泡", "研磨一体", "快速预热"]}, "scenarios": ["办公室", "家庭早餐", "咖啡进阶"], "audiences": ["上班族", "新手", "咖啡爱好者"]},
    "snack": {"name": "零食", "aliases": ["零食", "小吃"], "attributes": {"taste": ["咸香", "甜味", "微辣"], "feature": ["低糖", "独立包装", "高蛋白"]}, "scenarios": ["办公室", "追剧", "户外"], "audiences": ["学生", "上班族", "健身人群"]},
    "toy": {"name": "积木玩具", "aliases": ["积木", "玩具"], "attributes": {"age": ["3-5岁", "6-9岁", "10岁以上"], "theme": ["城市", "机械", "太空"]}, "scenarios": ["亲子互动", "生日礼物", "益智学习"], "audiences": ["幼儿", "小学生", "青少年"]},
    "guitar": {"name": "吉他", "aliases": ["吉他", "民谣吉他"], "attributes": {"size": ["36英寸", "40英寸", "41英寸"], "level": ["入门", "进阶", "演出"]}, "scenarios": ["自学", "校园弹唱", "舞台演出"], "audiences": ["初学者", "学生", "乐手"]},
    "office_chair": {"name": "办公椅", "aliases": ["办公椅", "人体工学椅"], "attributes": {"support": ["腰托", "头枕", "脚托"], "material": ["网布", "皮革", "织物"]}, "scenarios": ["居家办公", "长时编程", "学习"], "audiences": ["上班族", "程序员", "学生"]},
    "suitcase": {"name": "行李箱", "aliases": ["行李箱", "拉杆箱"], "attributes": {"size": ["20英寸", "24英寸", "28英寸"], "feature": ["前开盖", "静音轮", "可扩容"]}, "scenarios": ["短途出差", "长途旅行", "留学"], "audiences": ["商务人士", "旅行者", "学生"]},
}

BRAND_PREFIXES = ["澄屿", "星序", "青岚", "微澜", "简川"]
SPLIT_COUNTS = {"public": 60, "fresh_holdout": 40, "challenge": 20}
QUERY_TYPES = {"standard", "typo", "conflict", "no_match"}
_PRODUCT_ID = re.compile(r"^zh-[a-z0-9_]+-\d{2}$")


def _allowed_values(category: str) -> dict[str, set[str]]:
    spec = CATEGORY_SPECS[category]
    return {
        key: {str(item) for item in values}
        for key, values in (spec.get("attributes") or {}).items()
    }


def validate_product(product: dict[str, Any]) -> None:
    category = str(product.get("category") or "")
    if category not in CATEGORY_SPECS:
        raise ValueError(f"unknown product category: {category}")
    if not _PRODUCT_ID.fullmatch(str(product.get("id") or "")):
        raise ValueError("invalid synthetic product ID")
    if product.get("evidenceSource") != EVIDENCE_SOURCE:
        raise ValueError("synthetic product must declare evidenceSource=SYNTHETIC")
    if product.get("brand") not in BRAND_PREFIXES:
        raise ValueError(f"product uses an unapproved brand: {product.get('brand')}")
    price = product.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not 20 <= price <= 20_000:
        raise ValueError("product price is outside the allowed range")
    attributes = product.get("attributes")
    allowed = _allowed_values(category)
    if not isinstance(attributes, dict) or set(attributes) != set(allowed):
        raise ValueError("product attributes do not match the category schema")
    for key, value in attributes.items():
        if str(value) not in allowed[key]:
            raise ValueError(f"hallucinated attribute value: {category}.{key}={value}")
    spec = CATEGORY_SPECS[category]
    if product.get("scenario") not in spec["scenarios"]:
        raise ValueError("product scenario is outside the allowed values")
    if product.get("audience") not in spec["audiences"]:
        raise ValueError("product audience is outside the allowed values")
    exclusions = product.get("exclusions")
    if not isinstance(exclusions, list) or any(not isinstance(item, str) for item in exclusions):
        raise ValueError("product exclusions must be a string list")
    if not str(product.get("name") or "").strip() or not str(product.get("description") or "").strip():
        raise ValueError("product name and description are required")


def _satisfies(product: dict[str, Any], constraints: dict[str, Any]) -> bool:
    if constraints.get("category") and product["category"] != constraints["category"]:
        return False
    price = float(product["price"])
    if constraints.get("priceMin") is not None and price < float(constraints["priceMin"]):
        return False
    if constraints.get("priceMax") is not None and price > float(constraints["priceMax"]):
        return False
    if constraints.get("scenario") and product["scenario"] != constraints["scenario"]:
        return False
    if constraints.get("audience") and product["audience"] != constraints["audience"]:
        return False
    required = constraints.get("attributes") or {}
    return all(product["attributes"].get(key) == value for key, value in required.items())


def compute_relevance_grades(
    products: list[dict[str, Any]], constraints: dict[str, Any]
) -> dict[str, int]:
    """Compute 0-3 labels only from structured constraints."""

    grades: dict[str, int] = {}
    category = constraints.get("category")
    hard_keys = set((constraints.get("attributes") or {}).keys())
    for product in products:
        grade = 0
        if category and product["category"] == category:
            exact = _satisfies(product, constraints)
            if exact:
                grade = 3
            else:
                price_ok = not (
                    constraints.get("priceMin") is not None
                    and float(product["price"]) < float(constraints["priceMin"])
                ) and not (
                    constraints.get("priceMax") is not None
                    and float(product["price"]) > float(constraints["priceMax"])
                )
                matched_attributes = sum(
                    product["attributes"].get(key) == value
                    for key, value in (constraints.get("attributes") or {}).items()
                )
                if price_ok and (not hard_keys or matched_attributes >= max(1, len(hard_keys) - 1)):
                    grade = 2
                else:
                    grade = 1
        grades[product["id"]] = grade
    return grades


def validate_query(query: dict[str, Any], products: list[dict[str, Any]]) -> None:
    if query.get("split") not in SPLIT_COUNTS:
        raise ValueError("query split is invalid")
    if query.get("queryType") not in QUERY_TYPES:
        raise ValueError("query type is invalid")
    if query.get("evidenceSource") != EVIDENCE_SOURCE:
        raise ValueError("query must declare evidenceSource=SYNTHETIC")
    constraints = query.get("constraints")
    if not isinstance(constraints, dict):
        raise ValueError("query constraints are required")
    category = constraints.get("category")
    if category not in CATEGORY_SPECS:
        raise ValueError("query category is invalid")
    allowed = _allowed_values(category)
    for key, value in (constraints.get("attributes") or {}).items():
        if key not in allowed or str(value) not in allowed[key]:
            raise ValueError(f"query contains hallucinated constraint: {key}={value}")
    computed = compute_relevance_grades(products, constraints)
    supplied = {str(key): int(value) for key, value in (query.get("relevanceGrades") or {}).items()}
    if computed != supplied:
        raise ValueError("query relevance labels were not computed from constraints")
    expected_no_results = bool(query.get("expectedNoResults"))
    has_relevant = any(value >= 2 for value in computed.values())
    if expected_no_results == has_relevant:
        raise ValueError("expectedNoResults is inconsistent with structured labels")


def validate_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    products = payload.get("products") or []
    queries = payload.get("queries") or []
    if payload.get("datasetVersion") != DATASET_VERSION:
        raise ValueError("unsupported Chinese synthetic dataset version")
    if len(products) != 300 or len({row.get("id") for row in products}) != 300:
        raise ValueError("Chinese synthetic dataset must contain 300 unique products")
    product_names = [str(row.get("name") or "").strip() for row in products]
    if "" in product_names:
        raise ValueError("Chinese synthetic product names must be non-empty")
    category_counts = Counter(str(row.get("category")) for row in products)
    if category_counts != Counter({category: 15 for category in CATEGORY_SPECS}):
        raise ValueError("Chinese synthetic products must use a 20 x 15 category distribution")
    for product in products:
        validate_product(product)
    if len(queries) != 120 or len({row.get("id") for row in queries}) != 120:
        raise ValueError("Chinese synthetic dataset must contain 120 unique queries")
    query_texts = [str(row.get("query") or "").strip() for row in queries]
    if "" in query_texts or len(query_texts) != len(set(query_texts)):
        raise ValueError("Chinese synthetic query texts must be non-empty and unique")
    split_counts = Counter(str(row.get("split")) for row in queries)
    if split_counts != Counter(SPLIT_COUNTS):
        raise ValueError(f"query split distribution is invalid: {dict(split_counts)}")
    challenge = [row for row in queries if row.get("split") == "challenge"]
    if sum(row.get("queryType") == "typo" for row in challenge) != 10:
        raise ValueError("challenge split must contain 10 typo/colloquial positives")
    if sum(row.get("queryType") in {"conflict", "no_match"} for row in challenge) != 10:
        raise ValueError("challenge split must contain 10 conflict/no-match negatives")
    for query in queries:
        validate_query(query, products)
    return {
        "products": len(products),
        "queries": len(queries),
        "categories": len(category_counts),
        "splits": dict(sorted(split_counts.items())),
        "challengeTypes": dict(sorted(Counter(row["queryType"] for row in challenge).items())),
    }


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def _llm_json(settings, system: str, user: str) -> dict[str, Any]:
    client = await get_client("mature_eval_dataset_llm", timeout=settings.llm_timeout)
    error: Exception | None = None
    for _attempt in range(2):
        try:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={
                    "model": settings.llm_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                    "max_tokens": 8_192,
                    "response_format": {"type": "json_object"},
                },
                timeout=settings.llm_timeout,
            )
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"])
            payload = json.loads(_strip_json_fence(content))
            if not isinstance(payload, dict):
                raise ValueError("dataset LLM returned a non-object")
            return payload
        except Exception as exc:  # one bounded Provider retry per batch
            error = exc
    raise RuntimeError("dataset LLM batch failed after one retry") from error


async def generate_dataset(output_path: Path, *, resume_dir: Path) -> dict[str, Any]:
    """Generate natural surface text while retaining deterministic structures."""

    from app.config.settings import get_settings

    settings = get_settings()
    if not settings.llm_api_key.strip():
        raise RuntimeError("LLM_API_KEY is required to generate the synthetic dataset")
    resume_dir.mkdir(parents=True, exist_ok=True)
    products: list[dict[str, Any]] = []
    for category, spec in CATEGORY_SPECS.items():
        skeletons = []
        attribute_names = list(spec["attributes"])
        for index in range(15):
            attributes = {
                key: spec["attributes"][key][index % len(spec["attributes"][key])]
                for key in attribute_names
            }
            skeletons.append(
                {
                    "id": f"zh-{category}-{index + 1:02d}",
                    "category": category,
                    "categoryName": spec["name"],
                    "brand": BRAND_PREFIXES[index % len(BRAND_PREFIXES)],
                    "price": round(49 + index * 73 + len(category) * 11, 2),
                    "attributes": attributes,
                    "scenario": spec["scenarios"][index % len(spec["scenarios"])],
                    "audience": spec["audiences"][index % len(spec["audiences"])],
                    "exclusions": [],
                    "evidenceSource": EVIDENCE_SOURCE,
                }
            )
        batch = []
        for chunk_start in range(0, len(skeletons), 5):
            chunk = skeletons[chunk_start : chunk_start + 5]
            batch_path = resume_dir / f"products-{category}-{chunk_start // 5 + 1:02d}.json"
            if batch_path.is_file():
                chunk_products = json.loads(batch_path.read_text(encoding="utf-8"))
            else:
                response = await _llm_json(
                    settings,
                    (
                        "你为虚构中文电商评测集撰写自然商品文本。"
                        "只能返回每项的 id、name、description，绝不能复述、修改、增加或猜测其他字段。"
                        "name 必须包含给定虚构品牌和品类且不超过28个汉字；description 不超过60个汉字。"
                        "输出 JSON 对象，唯一顶层键为 products。"
                    ),
                    json.dumps({"category": spec, "products": chunk}, ensure_ascii=False),
                )
                surfaces = response.get("products") or []
                by_id = {str(row.get("id")): row for row in surfaces if isinstance(row, dict)}
                chunk_products = []
                for skeleton in chunk:
                    surface = by_id.get(skeleton["id"], {})
                    name = str(surface.get("name") or "").strip()
                    description = str(surface.get("description") or "").strip()
                    if not name:
                        raise ValueError(f"dataset LLM omitted product name: {skeleton['id']}")
                    # Natural generation can legitimately reuse a brand/category
                    # phrase. Add a deterministic SKU suffix for uniqueness; the
                    # semantic fields and labels remain unchanged.
                    if name in {row.get("name") for row in chunk_products}:
                        name = f"{name} 款{int(skeleton['id'].rsplit('-', 1)[-1])}"
                    if len(name) > 28 or len(description) > 60:
                        raise ValueError(f"dataset LLM surface text exceeds length limit: {skeleton['id']}")
                    chunk_products.append(
                        {**skeleton, "name": name, "description": description}
                    )
                atomic_write_json(batch_path, chunk_products)
            batch.extend(chunk_products)
        # Enforce uniqueness across sub-batches without changing model-derived
        # attributes or constraints.
        seen_names: set[str] = set()
        for product in batch:
            name = str(product["name"])
            if name in seen_names:
                suffix = int(product["id"].rsplit("-", 1)[-1])
                name = f"{name} 款{suffix}"
                product["name"] = name[:28]
            seen_names.add(name)
        for product in batch:
            validate_product(product)
        products.extend(batch)

    # Query structures and labels are deterministic. The model only paraphrases
    # their surface strings and receives no authority to grade its own output.
    query_skeletons: list[dict[str, Any]] = []
    categories = list(CATEGORY_SPECS)
    for index in range(120):
        split = "public" if index < 60 else "fresh_holdout" if index < 100 else "challenge"
        category = categories[index % len(categories)]
        spec = CATEGORY_SPECS[category]
        category_products = [row for row in products if row["category"] == category]
        product = category_products[(index // len(categories)) % len(category_products)]
        query_type = "standard"
        constraints: dict[str, Any] = {
            "category": category,
            "priceMax": float(product["price"]) + 1,
            "scenario": product["scenario"],
            "audience": product["audience"],
            "attributes": dict(product["attributes"]),
        }
        if split == "challenge":
            offset = index - 100
            query_type = "typo" if offset < 10 else "conflict"
            if query_type == "conflict":
                constraints["priceMax"] = 1
        grades = compute_relevance_grades(products, constraints)
        query_skeletons.append(
            {
                "id": f"zh-query-{index + 1:03d}",
                "split": split,
                "queryType": query_type,
                "constraints": constraints,
                "relevanceGrades": grades,
                "expectedNoResults": not any(value >= 2 for value in grades.values()),
                "evidenceSource": EVIDENCE_SOURCE,
                "query": "",
            }
        )
    queries: list[dict[str, Any]] = []
    for batch_index in range(0, len(query_skeletons), 10):
        skeleton_batch = query_skeletons[batch_index : batch_index + 10]
        batch_path = resume_dir / f"queries-{batch_index // 10 + 1:02d}.json"
        if batch_path.is_file():
            surfaces = json.loads(batch_path.read_text(encoding="utf-8"))
        else:
            response = await _llm_json(
                settings,
                "你为中文电商检索评测集改写自然查询。只输出 id 和 query；每条 query 不超过60个汉字且必须忠实表达 constraints。queryType=typo 时加入自然错别字或口语，conflict 时表达不可满足约束。输出 JSON 对象，唯一顶层键为 queries。",
                json.dumps(
                    {
                        "categories": CATEGORY_SPECS,
                        "queries": [
                            {key: row[key] for key in ("id", "queryType", "constraints")}
                            for row in skeleton_batch
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
            surfaces = response.get("queries") or []
            atomic_write_json(batch_path, surfaces)
        by_id = {str(row.get("id")): row for row in surfaces if isinstance(row, dict)}
        for skeleton in skeleton_batch:
            query_text = str((by_id.get(skeleton["id"]) or {}).get("query") or "").strip()
            queries.append({**skeleton, "query": query_text})

    payload = {
        "schemaVersion": 1,
        "datasetVersion": DATASET_VERSION,
        "evidenceSource": EVIDENCE_SOURCE,
        "generator": {
            "model": settings.llm_model,
            "temperature": 0,
            "role": "surface-text-only",
            "labeling": "deterministic-structured-constraints",
        },
        "products": products,
        "queries": queries,
    }
    summary = validate_dataset(payload)
    atomic_write_json(output_path, payload)
    lock = {
        "schemaVersion": 1,
        "datasetVersion": DATASET_VERSION,
        "datasetSha256": sha256_bytes(canonical_json_bytes(payload)),
        "evidenceSource": EVIDENCE_SOURCE,
        "counts": summary,
        "labelSource": "structured constraints over an allowed-value schema; LLM writes surface text only",
    }
    atomic_write_json(output_path.with_suffix(".lock.json"), lock)
    return {"dataset": payload, "lock": lock}
