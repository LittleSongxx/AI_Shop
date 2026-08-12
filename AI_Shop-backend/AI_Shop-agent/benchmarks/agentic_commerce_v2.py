"""Frozen contract checks for the Agentic Commerce v2 decision loop.

This dataset is deliberately executable without a database or a model.  It
checks the boundaries that must remain true when providers, prompts, or
catalogue data change: one high-value clarification, authoritative offers,
user-benefit ranking, governed analytics, manual-only inventory advice, and
root-only actions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.inventory_ops_service import calculate_inventory_forecast

BENCHMARKS_DIR = Path(__file__).resolve().parent
DATASET_PATH = BENCHMARKS_DIR / "agentic_commerce_v2.jsonl"
LOCK_PATH = BENCHMARKS_DIR / "agentic_commerce_v2.lock.json"

SUBSETS = frozenset(
    {
        "mission_clarification",
        "offer_constraints",
        "commercial_ranking",
        "after_sales_eligibility",
        "outcome_attribution",
        "data_analyst_sql",
        "inventory_forecast",
        "visual_mission",
        "multi_agent_e2e",
    }
)
REQUIRED_FIELDS = frozenset({"id", "subset", "split", "priority", "input", "expected", "note"})
_PII_LIKE = (
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{17}[0-9Xx]\b"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
)


def sha256_file(path: Path = DATASET_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_no} 不是合法 JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_no} 必须是 JSON 对象")
        cases.append(row)
    return cases


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_reference(input_data: dict[str, Any]) -> dict[str, float | int]:
    """Return the deterministic ROP/MOQ oracle used by the frozen cases."""
    return calculate_inventory_forecast(input_data)


def runtime_metric_projection(cases: list[dict[str, Any]]) -> dict[str, float]:
    """Project the frozen runtime assertions onto the public gate metrics."""

    def rate(subset: str, key: str, expected: Any = True) -> float:
        rows = [row for row in cases if row.get("subset") == subset]
        if not rows:
            return 0.0
        return sum((row.get("observations") or {}).get(key) == expected for row in rows) / len(rows)

    offer_rows = [row for row in cases if row.get("subset") == "offer_constraints"]
    ranking_rows = [row for row in cases if row.get("subset") == "commercial_ranking"]
    return {
        "hardConstraintCompliance": rate("offer_constraints", "hardConstraintCompliant"),
        "authoritativeOfferAccuracy": rate("offer_constraints", "authoritativeOfferAccurate"),
        "operationDisclosureRate": rate("commercial_ranking", "operationDisclosureCorrect"),
        "afterSalesDecisionAccuracy": rate("after_sales_eligibility", "decisionCorrect"),
        "attributionIntegrity": rate("outcome_attribution", "attributionIntegrity"),
        "sqlGuardRecall": rate("data_analyst_sql", "sqlGuardCorrect"),
        "inventoryFormulaAccuracy": rate("inventory_forecast", "formulaCorrect"),
        "specialistReadOnlyRate": rate("multi_agent_e2e", "specialistReadOnly"),
        "traceCompleteness": rate("multi_agent_e2e", "traceComplete"),
        "unavailableProductRate": (
            sum(not (row.get("observations") or {}).get("availableOnly", False) for row in offer_rows)
            / len(offer_rows)
            if offer_rows
            else 1.0
        ),
        "operationFirstPositionRate": (
            sum((row.get("observations") or {}).get("operationFirst", False) for row in ranking_rows)
            / len(ranking_rows)
            if ranking_rows
            else 1.0
        ),
        "unverifiedCouponClaimRate": (
            sum((row.get("observations") or {}).get("unverifiedCouponClaim", False) for row in offer_rows)
            / len(offer_rows)
            if offer_rows
            else 1.0
        ),
        "hardConstraintViolationRate": (
            sum(not (row.get("observations") or {}).get("hardConstraintCompliant", False) for row in offer_rows)
            / len(offer_rows)
            if offer_rows
            else 1.0
        ),
    }


def validate_contract(
    cases: list[dict[str, Any]] | None = None,
    *,
    dataset_path: Path = DATASET_PATH,
    lock_path: Path = LOCK_PATH,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases(dataset_path)
    lock = load_lock(lock_path)
    errors: list[str] = []
    ids: list[str] = []
    subset_counts: Counter[str] = Counter()
    split_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    serialized = dataset_path.read_text(encoding="utf-8")

    if lock.get("schemaVersion") != 1:
        errors.append("schemaVersion 必须为 1")
    if sha256_file(dataset_path) != lock.get("datasetSha256"):
        errors.append("数据集 SHA-256 与锁文件不一致")
    if len(cases) != int(lock.get("caseCount") or 0):
        errors.append("数据集 case 数与锁文件不一致")
    if any(pattern.search(serialized) for pattern in _PII_LIKE):
        errors.append("评测集包含手机号、身份证或邮箱形状的 PII")

    for index, row in enumerate(cases, 1):
        where = str(row.get("id") or f"line-{index}")
        missing = REQUIRED_FIELDS - row.keys()
        if missing:
            errors.append(f"{where} 缺字段 {sorted(missing)}")
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{where} id 不能为空")
        else:
            ids.append(case_id)
        subset = row.get("subset")
        if subset not in SUBSETS:
            errors.append(f"{where} subset 非法: {subset}")
        else:
            subset_counts[subset] += 1
            split_counts[subset][str(row.get("split"))] += 1
        if row.get("split") not in {"dev", "test"}:
            errors.append(f"{where} split 必须是 dev 或 test")
        if row.get("priority") not in {"P0", "P1", "P2"}:
            errors.append(f"{where} priority 必须是 P0/P1/P2")
        if not isinstance(row.get("input"), dict) or not isinstance(row.get("expected"), dict):
            errors.append(f"{where} input/expected 必须是对象")
        if not isinstance(row.get("note"), str) or not row["note"].strip():
            errors.append(f"{where} note 不能为空")

    duplicates = sorted(case_id for case_id, count in Counter(ids).items() if count > 1)
    if duplicates:
        errors.append(f"重复 case id: {duplicates}")
    missing_subsets = sorted(SUBSETS - set(subset_counts))
    if missing_subsets:
        errors.append(f"缺少评测子集: {missing_subsets}")
    for subset in sorted(SUBSETS):
        if not split_counts[subset]["dev"] or not split_counts[subset]["test"]:
            errors.append(f"子集 {subset} 必须同时包含 dev 和 test")

    expected_counts = lock.get("subsetCounts") or {}
    if dict(sorted(subset_counts.items())) != dict(sorted(expected_counts.items())):
        errors.append("子集数量与锁文件不一致")

    errors.extend(_validate_semantic_contracts(cases))
    if errors:
        raise ValueError("Agentic Commerce v2 评测契约无效:\n- " + "\n- ".join(errors))
    return {
        "caseCount": len(cases),
        "subsetCounts": dict(sorted(subset_counts.items())),
        "datasetSha256": sha256_file(dataset_path),
        "thresholds": lock.get("thresholds") or {},
    }


def _validate_semantic_contracts(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for row in cases:
        case_id = row.get("id") or "unknown"
        data = row.get("input") or {}
        expected = row.get("expected") or {}
        subset = row.get("subset")
        if subset == "mission_clarification":
            if expected.get("maxQuestions") not in {1, 2}:
                errors.append(f"{case_id} 追问上限必须是 1 或 2")
            if expected.get("clarificationSlot") and expected.get("clarificationSlot") not in {
                "category", "useCase", "budget", "feature", "brand", "portability"
            }:
                errors.append(f"{case_id} clarificationSlot 非法")
        elif subset == "offer_constraints":
            offers = data.get("offers") or []
            offer_by_sku = {str(offer.get("skuKey")): offer for offer in offers}
            for sku in expected.get("acceptedSkuKeys") or []:
                offer = offer_by_sku.get(str(sku))
                if not offer:
                    errors.append(f"{case_id} accepted SKU {sku} 不存在")
                    continue
                if "quote" in offer and offer.get("quote") is None:
                    errors.append(f"{case_id} accepted SKU {sku} 缺少权威报价")
                if offer.get("onSale") is not True or float(offer.get("stock") or 0) <= 0:
                    errors.append(f"{case_id} accepted SKU {sku} 不可购买")
                if float(offer.get("quoteExpiresInSeconds") or 0) <= 0:
                    errors.append(f"{case_id} accepted SKU {sku} 报价已过期")
                budget_max = data.get("budgetMax")
                if budget_max is not None and float(offer.get("estimatedPayable")) > float(
                    budget_max
                ):
                    errors.append(f"{case_id} accepted SKU {sku} 超出预算")
        elif subset == "commercial_ranking":
            if int(expected.get("operationInsertCount", 0)) > 1:
                errors.append(f"{case_id} 运营候选最多插入 1 个")
            if expected.get("operationInsertCount") and expected.get("disclosed") is not True:
                errors.append(f"{case_id} 运营候选必须披露")
            if expected.get("maxOperationPosition", 2) < 2:
                errors.append(f"{case_id} 运营候选不能占首位")
        elif subset == "after_sales_eligibility":
            if expected.get("decision") not in {
                "ELIGIBLE", "INELIGIBLE", "NEEDS_EVIDENCE", "POLICY_UNAVAILABLE", "CONFLICT"
            }:
                errors.append(f"{case_id} 售后资格结论非法")
            if expected.get("decision") != "ELIGIBLE" and expected.get("writeActionAllowed") is True:
                errors.append(f"{case_id} 非可申请结论不能允许写操作")
        elif subset == "outcome_attribution":
            if expected.get("accepted") is True and expected.get("attributionStatus") != "VERIFIED":
                errors.append(f"{case_id} 已接受的归因事件必须是 VERIFIED")
        elif subset == "data_analyst_sql":
            if expected.get("allowed") is True and expected.get("readOnlyUser") != "analytics_reader":
                errors.append(f"{case_id} Text2SQL 必须使用 analytics_reader")
            if expected.get("allowed") is False and not expected.get("rejectReasons"):
                errors.append(f"{case_id} SQL 拒绝 case 必须列明原因")
        elif subset == "inventory_forecast":
            if expected.get("manualOnly") is not True:
                errors.append(f"{case_id} InventoryOps 必须 manualOnly=true")
            if all(key in expected for key in ("reorderPoint", "suggestedReplenishQuantity")):
                reference = inventory_reference(data)
                if float(expected["reorderPoint"]) != reference["reorderPoint"]:
                    errors.append(f"{case_id} ROP 与公式不一致")
                if int(expected["suggestedReplenishQuantity"]) != reference["suggestedReplenishQuantity"]:
                    errors.append(f"{case_id} MOQ 补货量与公式不一致")
        elif subset == "visual_mission":
            if expected.get("intent") != "VISUAL_PRODUCT_SEARCH":
                errors.append(f"{case_id} 图片导购必须使用 VISUAL_PRODUCT_SEARCH")
            if expected.get("matchTypeNotExactUnlessHashEqual") is not True:
                errors.append(f"{case_id} 视觉相似不能无条件声称同款")
        elif subset == "multi_agent_e2e":
            if len(expected.get("specialists") or []) > 2:
                errors.append(f"{case_id} 专家最多两个")
            if expected.get("actionOwner") and expected.get("actionOwner") != "supervisor":
                errors.append(f"{case_id} 写操作只能由 supervisor 拥有")
            if expected.get("specialistCannotWrite") is True and expected.get("rawHistoryIncluded") is True:
                errors.append(f"{case_id} 专家不能同时接收完整历史和写权限")
    return errors


def gate_failures(report: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Evaluate mandatory runtime metrics.

    Contract validation and runtime evaluation are separate operations, but a
    runtime gate is never allowed to pass a report that omitted a thresholded
    metric. This prevents a contract-only run from being presented as a
    production-code evaluation.
    """
    failures: list[str] = []
    for metric, threshold in (thresholds.get("minimum") or {}).items():
        if report.get(metric) is None:
            failures.append(f"{metric} 缺失")
        elif float(report[metric]) < float(threshold):
            failures.append(f"{metric} 低于门槛 {threshold}")
    for metric, threshold in (thresholds.get("maximum") or {}).items():
        if report.get(metric) is None:
            failures.append(f"{metric} 缺失")
        elif float(report[metric]) > float(threshold):
            failures.append(f"{metric} 超过门槛 {threshold}")
    return failures
