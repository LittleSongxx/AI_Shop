"""Build and validate the immutable Search v3 evaluation datasets.

The v3 holdouts are labelled from structured constraints or the locked 47-product
catalog.  No model output is used as relevance truth.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.mature_eval.chinese_dataset import CATEGORY_SPECS
from benchmarks.mature_eval.chinese_dataset_v2 import (
    compute_relevance_grades,
)
from benchmarks.mature_eval.chinese_dataset_v2 import (
    validate_dataset as validate_v2_dataset,
)
from benchmarks.mature_eval.common import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    combined_sha,
    sha256_bytes,
    sha256_file,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parents[1]
DATASETS_ROOT = PROJECT_ROOT / "benchmarks" / "datasets"
V2_PATH = DATASETS_ROOT / "mature_v2" / "chinese-commerce-search-v2.json"
V2_LOCK_PATH = V2_PATH.with_suffix(".lock.json")
PUBLIC_RUNTIME_PATH = PROJECT_ROOT / "benchmarks" / "search_relevance_v1.jsonl"
PUBLIC_RUNTIME_LOCK_PATH = PUBLIC_RUNTIME_PATH.with_suffix(".lock.json")
KNOWN_RUNTIME_PATH = DATASETS_ROOT / "search_holdout_v1.jsonl"
KNOWN_RUNTIME_LOCK_PATH = KNOWN_RUNTIME_PATH.with_suffix(".lock.json")
CATALOG_PATH = PROJECT_ROOT.parent / "data" / "simlect_catalog" / "catalog.json"
FRESH_CHALLENGE_PATH = DATASETS_ROOT / "search_v3_fresh_challenge.json"
FRESH_CHALLENGE_LOCK_PATH = FRESH_CHALLENGE_PATH.with_suffix(".lock.json")
RUNTIME_HOLDOUT_PATH = DATASETS_ROOT / "search_v3_runtime_holdout.jsonl"
RUNTIME_HOLDOUT_LOCK_PATH = RUNTIME_HOLDOUT_PATH.with_suffix(".lock.json")
SUITE_LOCK_PATH = DATASETS_ROOT / "search_v3_suite.lock.json"

DATASET_VERSION = "search-v3-fresh-challenge"
MANDATORY_NO_RESULT_ID = (
    "unknown-mars-soil-with-snack-and-block-attributes-must-return-no-result"
)
MANDATORY_DYNAMIC_CATEGORY_ID = (
    "new-real-catalog-category-must-not-be-rejected-by-static-taxonomy"
)
MANDATORY_DYNAMIC_CATEGORY_PRODUCT_ID = "293985085089344"


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} contains a non-object row")
            rows.append(value)
    return rows


def _write_jsonl(path: Path, header: str, rows: Sequence[dict[str, Any]]) -> None:
    body = [f"# {header}\n"]
    body.extend(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    atomic_write_bytes(path, "".join(body).encode("utf-8"))


def _load_v2() -> dict[str, Any]:
    payload = _json(V2_PATH)
    validate_v2_dataset(payload)
    lock = _json(V2_LOCK_PATH)
    if sha256_bytes(canonical_json_bytes(payload)) != lock.get("datasetSha256"):
        raise ValueError("Search v2 dataset SHA does not match its immutable lock")
    return payload


def _target_query(product: dict[str, Any], *, variant: int) -> str:
    attributes = list((product.get("attributes") or {}).values())
    openings = (
        "帮我筛一下",
        "这次只看硬条件，想找",
        "给家里选一个",
        "我想换新设备，优先找",
    )
    endings = (
        "不符合预算和品牌的先排除",
        "请按符合程度排序",
        "希望候选不要混入别的品类",
        "先给最匹配的几款",
    )
    return (
        f"{openings[variant]}{product['brand']}的{product['categoryName']}，"
        f"预算不超过{int(float(product['price']) + 1)}元，主要用于{product['scenario']}，"
        f"给{product['audience']}使用，要有{'、'.join(str(value) for value in attributes)}，"
        f"{endings[variant]}"
    )


def build_fresh_challenge_payload() -> dict[str, Any]:
    base = _load_v2()
    products = [dict(row) for row in base["products"]]
    by_category = {
        category: [row for row in products if row.get("category") == category]
        for category in CATEGORY_SPECS
    }
    queries: list[dict[str, Any]] = []
    fresh_sequence = 0
    challenge_sequence = 0
    for category_index, (category, spec) in enumerate(CATEGORY_SPECS.items()):
        category_products = by_category[category]
        for variant in range(4):
            fresh_sequence += 1
            product = category_products[(13 + category_index * 7 + variant * 4) % 30]
            constraints = {
                "category": spec["name"],
                "priceMax": float(product["price"]) + 1,
                "requiredBrands": [product["brand"]],
                "scenario": product["scenario"],
                "audience": product["audience"],
                "attributes": dict(product["attributes"]),
            }
            grades = compute_relevance_grades(products, constraints)
            queries.append(
                {
                    "id": f"search-v3-fresh-{fresh_sequence:03d}",
                    "query": _target_query(product, variant=variant),
                    "split": "fresh_holdout",
                    "queryType": "natural_constraint",
                    "challengeGroup": None,
                    "constraints": constraints,
                    "relevanceGrades": grades,
                    "expectedNoResults": not any(value >= 2 for value in grades.values()),
                    "evidenceSource": "SYNTHETIC",
                }
            )

        challenge_sequence += 1
        product = category_products[(19 + category_index * 5) % 30]
        constraints = {
            "category": spec["name"],
            "priceMax": float(product["price"]) + 1,
            "requiredBrands": [product["brand"]],
            "scenario": product["scenario"],
            "audience": product["audience"],
            "attributes": dict(product["attributes"]),
        }
        attribute_text = "、".join(str(value) for value in product["attributes"].values())
        grades = compute_relevance_grades(products, constraints)
        queries.append(
            {
                "id": f"search-v3-challenge-positive-{challenge_sequence:03d}",
                "query": (
                    f"预算卡在{int(float(product['price']) + 1)}以内，{product['brand']}牌"
                    f"{product['categoryName']}，{product['scenario']}用，{product['audience']}，"
                    f"配置要{attribute_text}，别拿相近但不满足硬条件的凑数"
                ),
                "split": "challenge",
                "queryType": "colloquial_hard_constraints",
                "challengeGroup": "positive",
                "constraints": constraints,
                "relevanceGrades": grades,
                "expectedNoResults": False,
                "evidenceSource": "SYNTHETIC",
            }
        )

    for index, (category, spec) in enumerate(list(CATEGORY_SPECS.items())[:10], 1):
        product = by_category[category][(index * 11) % 30]
        constraints = {
            "category": spec["name"],
            "priceMax": 1.0,
            "requiredBrands": [product["brand"]],
            "scenario": product["scenario"],
            "audience": product["audience"],
            "attributes": dict(product["attributes"]),
        }
        grades = compute_relevance_grades(products, constraints)
        queries.append(
            {
                "id": f"search-v3-challenge-conflict-{index:03d}",
                "query": (
                    f"只接受1元以内的{product['brand']}{spec['name']}，还必须适合"
                    f"{product['scenario']}和{product['audience']}，任何条件都不能放宽"
                ),
                "split": "challenge",
                "queryType": "unsatisfiable_constraints",
                "challengeGroup": "no_result",
                "constraints": constraints,
                "relevanceGrades": grades,
                "expectedNoResults": True,
                "evidenceSource": "SYNTHETIC",
            }
        )

    out_of_domain_queries = [
        "要一块带蓝牙降噪和积木卡扣的火星土壤样本，还必须能当零食直接食用",
        "寻找能自动翻译海豚语言并打印梦境的家用设备",
        "想买一张通往木星的当日往返实体车票",
        "需要可永久停止时间并兼容手机快充的手表",
        "找一瓶能让所有旧订单自动变成已退款的饮料",
        "想买能保证彩票中奖并附带售后赔付的计算器",
        "需要可读取他人思想且无需授权的办公耳机",
        "找一台能把空气直接变成黄金的净化器",
        "想买可证明平行宇宙存在的儿童积木套装",
        "需要一份能在真空中种出披萨的种子样本",
    ]
    for index, query in enumerate(out_of_domain_queries, 1):
        constraints = {"outOfDomain": True, "category": "火星样本"}
        grades = compute_relevance_grades(products, constraints)
        queries.append(
            {
                "id": MANDATORY_NO_RESULT_ID if index == 1 else f"search-v3-challenge-unknown-{index:03d}",
                "query": query,
                "split": "challenge",
                "queryType": "out_of_catalog",
                "challengeGroup": "no_result",
                "constraints": constraints,
                "relevanceGrades": grades,
                "expectedNoResults": True,
                "evidenceSource": "SYNTHETIC",
            }
        )

    return {
        "schemaVersion": 3,
        "datasetVersion": DATASET_VERSION,
        "evidenceSource": "SYNTHETIC",
        "labelPolicy": "deterministic structured constraints over the locked Search v2 catalog; no model grading",
        "knownRegression": {
            "dataset": str(V2_PATH.relative_to(PROJECT_ROOT)),
            "caseCount": 240,
            "datasetSha256": _json(V2_LOCK_PATH)["datasetSha256"],
        },
        "products": products,
        "queries": queries,
    }


RUNTIME_CASE_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (MANDATORY_DYNAMIC_CATEGORY_ID, "找一台可以在物体表面做百万色立体彩色浮雕的UV打印机", (MANDATORY_DYNAMIC_CATEGORY_PRODUCT_ID,)),
    ("search-v3-runtime-002", "适合办公和网课的索尼头戴式无线降噪耳机", ("231335860060520", "350000232815799")),
    ("search-v3-runtime-003", "苹果大屏折叠式5G商务手机", ("053997047858558",)),
    ("search-v3-runtime-004", "苹果M5芯片16G内存1T硬盘的14英寸笔记本", ("869004898763662",)),
    ("search-v3-runtime-005", "可以给无人机供电的100W多口车载快充", ("683735539720416",)),
    ("search-v3-runtime-006", "初学者用的雅马哈单板民谣木吉他", ("549376645121601",)),
    ("search-v3-runtime-007", "二十四罐装可乐雪碧芬达混合汽水", ("303019597302892",)),
    ("search-v3-runtime-008", "厚烧海苔味旺旺雪饼零食", ("065293686460191",)),
    ("search-v3-runtime-009", "送儿童的趴姿小猪毛绒玩偶抱枕", ("622491960431656",)),
    ("search-v3-runtime-010", "支持鞋仓和干湿分离的大容量健身旅行包", ("270126564877983",)),
    ("search-v3-runtime-011", "户外登山防风透气的男女软壳衣", ("993921843864063",)),
    ("search-v3-runtime-012", "带蒸烤炸和自清洁功能的嵌入式蒸烤一体机", ("563738828031657",)),
    ("search-v3-runtime-013", "母婴直饮1200G反渗透净水器和管线机套装", ("055216728343001",)),
    ("search-v3-runtime-014", "新房除甲醛花粉猫毛的塔式空气净化器", ("547755968243478",)),
    ("search-v3-runtime-015", "专业级便携筋膜枪用于肩颈腰背放松", ("484914171487881",)),
    ("search-v3-runtime-016", "带独立显卡适合AI大模型设计渲染的商用台式机", ("995230446006541",)),
    ("search-v3-runtime-017", "适合家用办公的i9高性能台式电脑整机", ("650980987345712",)),
    ("search-v3-runtime-018", "苹果17 Pro Max双卡双待5G手机", ("895150981058759",)),
    ("search-v3-runtime-019", "低音炮环绕立体声桌面蓝牙音箱", ("158081823347974",)),
    ("search-v3-runtime-020", "送妈妈的滋润紧致抗皱胶原面霜", ("811128851953351",)),
    ("search-v3-runtime-021", "香奈儿女士香水和唇釉生日礼盒", ("100766326868880",)),
    ("search-v3-runtime-022", "家庭宿舍做饭用油盐酱醋调味料组合", ("327158568449097",)),
    ("search-v3-runtime-023", "可放洗碗机的景德镇整套餐具碗碟", ("694835806434643",)),
    ("search-v3-runtime-024", "新生儿衣服和用品见面礼盒66厘米", ("467794132963439",)),
    ("search-v3-runtime-025", "学校工厂用不锈钢落地拖把扫把收纳架", ("578084699484498",)),
    ("search-v3-runtime-026", "蔚来汽车六座车型耐刮防护TPE脚垫", ("627057993813554",)),
    ("search-v3-runtime-027", "学生论文排版翻译PPT用WPS两年会员", ("365554660873099",)),
    ("search-v3-runtime-028", "任天堂Switch香港服eshop充值点卡", ("519183041848998",)),
    ("search-v3-runtime-029", "红楼梦古典小说纸书和MP3套装", ("583435458113015",)),
    ("search-v3-runtime-030", "送父母长辈的同仁堂西洋参片礼盒", ("485810554704298",)),
)


def build_runtime_holdout() -> list[dict[str, Any]]:
    catalog = _json(CATALOG_PATH)
    product_ids = {
        str(row.get("productInfo", {}).get("productId") or "")
        for row in catalog.get("products") or []
    }
    rows: list[dict[str, Any]] = []
    for case_id, query, targets in RUNTIME_CASE_SPECS:
        missing = sorted(set(targets) - product_ids)
        if missing:
            raise ValueError(f"Search v3 runtime label references missing products: {missing}")
        rows.append(
            {
                "id": case_id,
                "subset": "real_catalog_holdout",
                "split": "runtime_holdout",
                "priority": "P0" if case_id == MANDATORY_DYNAMIC_CATEGORY_ID else "P1",
                "query": query,
                "relevantProductIds": list(targets),
                "relevanceGrades": {
                    product_id: 3 if index == 0 else 2
                    for index, product_id in enumerate(targets)
                },
                "expectedNoResults": False,
                "labelSource": "developer review of locked 47-product mirror",
            }
        )
    return rows


def validate_fresh_challenge(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("datasetVersion") != DATASET_VERSION:
        raise ValueError("unsupported Search v3 dataset version")
    products = list(payload.get("products") or [])
    queries = list(payload.get("queries") or [])
    if len(products) != 600 or len({str(row.get("id")) for row in products}) != 600:
        raise ValueError("Search v3 must retain the exact 600-product catalog")
    v2 = _load_v2()
    if products != v2.get("products"):
        raise ValueError("Search v3 product catalog differs from locked Search v2")
    if len(queries) != 120 or len({str(row.get("id")) for row in queries}) != 120:
        raise ValueError("Search v3 requires 120 unique new cases")
    if len({str(row.get("query")) for row in queries}) != 120:
        raise ValueError("Search v3 query text must be unique")
    split_counts = Counter(str(row.get("split")) for row in queries)
    if split_counts != Counter({"fresh_holdout": 80, "challenge": 40}):
        raise ValueError(f"Search v3 split counts changed: {dict(split_counts)}")
    challenge_counts = Counter(
        str(row.get("challengeGroup"))
        for row in queries
        if row.get("split") == "challenge"
    )
    if challenge_counts != Counter({"positive": 20, "no_result": 20}):
        raise ValueError(f"Search v3 challenge distribution changed: {dict(challenge_counts)}")
    case_by_id = {str(row["id"]): row for row in queries}
    mandatory = case_by_id.get(MANDATORY_NO_RESULT_ID)
    if not mandatory or not mandatory.get("expectedNoResults"):
        raise ValueError("mandatory Mars-soil no-result case is missing")
    for row in queries:
        constraints = row.get("constraints")
        if not isinstance(constraints, dict):
            raise ValueError(f"{row.get('id')}: constraints must be an object")
        supplied = {str(key): int(value) for key, value in (row.get("relevanceGrades") or {}).items()}
        expected = compute_relevance_grades(products, constraints)
        if supplied != expected:
            raise ValueError(f"{row.get('id')}: relevance labels differ from constraints")
        has_relevant = any(value >= 2 for value in supplied.values())
        if bool(row.get("expectedNoResults")) == has_relevant:
            raise ValueError(f"{row.get('id')}: expectedNoResults is inconsistent")
    return {
        "products": len(products),
        "queries": len(queries),
        "splits": dict(sorted(split_counts.items())),
        "challengeGroups": dict(sorted(challenge_counts.items())),
    }


def validate_runtime_holdout(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != 30 or len({str(row.get("id")) for row in rows}) != 30:
        raise ValueError("Search v3 runtime holdout requires 30 unique cases")
    if len({str(row.get("query")) for row in rows}) != 30:
        raise ValueError("Search v3 runtime holdout queries must be unique")
    by_id = {str(row.get("id")): row for row in rows}
    mandatory = by_id.get(MANDATORY_DYNAMIC_CATEGORY_ID)
    if not mandatory or MANDATORY_DYNAMIC_CATEGORY_PRODUCT_ID not in (
        mandatory.get("relevanceGrades") or {}
    ):
        raise ValueError("mandatory dynamic-category case is missing its UV printer label")
    catalog = _json(CATALOG_PATH)
    product_ids = {
        str(row.get("productInfo", {}).get("productId") or "")
        for row in catalog.get("products") or []
    }
    for row in rows:
        labels = row.get("relevanceGrades")
        if not isinstance(labels, dict) or not labels:
            raise ValueError(f"{row.get('id')}: runtime relevance labels are missing")
        if set(map(str, labels)) - product_ids:
            raise ValueError(f"{row.get('id')}: runtime label is outside the catalog")
        if row.get("split") != "runtime_holdout" or row.get("expectedNoResults"):
            raise ValueError(f"{row.get('id')}: runtime split contract changed")
    return {"cases": len(rows), "catalogProducts": len(product_ids)}


def write_search_v3_datasets() -> dict[str, Any]:
    targets = (
        FRESH_CHALLENGE_PATH,
        FRESH_CHALLENGE_LOCK_PATH,
        RUNTIME_HOLDOUT_PATH,
        RUNTIME_HOLDOUT_LOCK_PATH,
        SUITE_LOCK_PATH,
    )
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise FileExistsError(
            "Search v3 immutable dataset files already exist; refusing overwrite: "
            + ", ".join(existing)
        )
    payload = build_fresh_challenge_payload()
    fresh_summary = validate_fresh_challenge(payload)
    runtime_rows = build_runtime_holdout()
    runtime_summary = validate_runtime_holdout(runtime_rows)
    atomic_write_json(FRESH_CHALLENGE_PATH, payload)
    _write_jsonl(
        RUNTIME_HOLDOUT_PATH,
        "Search v3 one-shot 47-product runtime holdout; labels are human-reviewed catalog IDs.",
        runtime_rows,
    )
    fresh_lock = {
        "schemaVersion": 3,
        "dataset": FRESH_CHALLENGE_PATH.name,
        "datasetSha256": sha256_file(FRESH_CHALLENGE_PATH),
        "caseCount": 120,
        "counts": fresh_summary,
        "baseDatasetSha256": _json(V2_LOCK_PATH)["datasetSha256"],
        "labelPolicy": "deterministic constraints; no LLM relevance grading",
        "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
    }
    runtime_lock = {
        "schemaVersion": 3,
        "dataset": RUNTIME_HOLDOUT_PATH.name,
        "datasetSha256": sha256_file(RUNTIME_HOLDOUT_PATH),
        "caseCount": 30,
        "counts": runtime_summary,
        "catalogSha256": sha256_file(CATALOG_PATH),
        "labelPolicy": "developer review of locked 47-product mirror",
        "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
    }
    atomic_write_json(FRESH_CHALLENGE_LOCK_PATH, fresh_lock)
    atomic_write_json(RUNTIME_HOLDOUT_LOCK_PATH, runtime_lock)
    bound = [
        V2_PATH,
        V2_LOCK_PATH,
        PUBLIC_RUNTIME_PATH,
        PUBLIC_RUNTIME_LOCK_PATH,
        KNOWN_RUNTIME_PATH,
        KNOWN_RUNTIME_LOCK_PATH,
        CATALOG_PATH,
        FRESH_CHALLENGE_PATH,
        FRESH_CHALLENGE_LOCK_PATH,
        RUNTIME_HOLDOUT_PATH,
        RUNTIME_HOLDOUT_LOCK_PATH,
    ]
    suite_lock = {
        "schemaVersion": 3,
        "suite": "search-v3",
        "caseCounts": {
            "knownChineseV2": 240,
            "knownProductServiceV2": 45,
            "fresh": 80,
            "challenge": 40,
            "runtimeHoldout": 30,
        },
        "mandatoryCases": [MANDATORY_NO_RESULT_ID, MANDATORY_DYNAMIC_CATEGORY_ID],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): sha256_file(path) for path in bound
        },
        "inputSetSha256": combined_sha(bound, relative_to=REPO_ROOT),
        "freshPolicy": "ONE_SHOT_FAIL_RETAINED",
    }
    atomic_write_json(SUITE_LOCK_PATH, suite_lock)
    return {"freshChallenge": fresh_lock, "runtimeHoldout": runtime_lock, "suite": suite_lock}


def validate_search_v3_files() -> dict[str, Any]:
    required = (
        FRESH_CHALLENGE_PATH,
        FRESH_CHALLENGE_LOCK_PATH,
        RUNTIME_HOLDOUT_PATH,
        RUNTIME_HOLDOUT_LOCK_PATH,
        SUITE_LOCK_PATH,
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"Search v3 immutable datasets are missing: {missing}")
    payload = _json(FRESH_CHALLENGE_PATH)
    fresh_summary = validate_fresh_challenge(payload)
    runtime_rows = _jsonl(RUNTIME_HOLDOUT_PATH)
    runtime_summary = validate_runtime_holdout(runtime_rows)
    fresh_lock = _json(FRESH_CHALLENGE_LOCK_PATH)
    runtime_lock = _json(RUNTIME_HOLDOUT_LOCK_PATH)
    suite_lock = _json(SUITE_LOCK_PATH)
    if sha256_file(FRESH_CHALLENGE_PATH) != fresh_lock.get("datasetSha256"):
        raise ValueError("Search v3 fresh/challenge SHA mismatch")
    if sha256_file(RUNTIME_HOLDOUT_PATH) != runtime_lock.get("datasetSha256"):
        raise ValueError("Search v3 runtime holdout SHA mismatch")
    inputs = suite_lock.get("inputs") or {}
    for raw_path, expected_sha in inputs.items():
        path = REPO_ROOT / str(raw_path)
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ValueError(f"Search v3 suite-bound input changed: {raw_path}")
    bound = [REPO_ROOT / str(path) for path in inputs]
    if combined_sha(bound, relative_to=REPO_ROOT) != suite_lock.get("inputSetSha256"):
        raise ValueError("Search v3 suite input-set SHA mismatch")
    return {
        "freshChallenge": fresh_summary,
        "runtimeHoldout": runtime_summary,
        "suiteLock": suite_lock,
    }
