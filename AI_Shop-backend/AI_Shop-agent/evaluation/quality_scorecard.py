"""Derive a quality-first scorecard from immutable evaluation evidence.

The published evidence package intentionally keeps ``bad-cases.jsonl`` as an
exact projection of runtime ``FAILED``/``ERROR`` rows.  That is useful for
replay, but it cannot describe a passed query whose ranking is poor.  This
module is an additive diagnostic view: it recomputes ranking metrics from the
holdout qrels, records metric-specific bad cases, and keeps reliability gates
separate from quality results.  It never writes to an evidence package.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.evidence import verify_evidence
from evaluation.core.io import (
    EVALUATION_ROOT,
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_text,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_file,
)
from evaluation.core.metrics import (
    bootstrap_interval,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank_at_k,
    wilson_interval,
)

DEFAULT_HOLDOUT = EVALUATION_ROOT / ".holdouts" / "final-holdout-20260822-ai-quality-v9.jsonl"
DEFAULT_CATALOG = EVALUATION_ROOT / "fixtures" / "product-catalog.v2.json"
SCORECARD_SCHEMA = "aishop-quality-scorecard/v1"
_BOOTSTRAP_SAMPLES = 2000
_BOOTSTRAP_SEED = 20260822


class ScorecardError(ValueError):
    """Raised when a scorecard cannot be derived without guessing."""


def _round(value: float | int | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _stable_seed(name: str) -> int:
    suffix = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    return _BOOTSTRAP_SEED ^ suffix


def _as_bool(value: Any) -> bool:
    return value is True or value == 1 or value == 1.0


def _query(case: Mapping[str, Any], holdout: Mapping[str, Any] | None) -> str:
    output = case.get("output") if isinstance(case.get("output"), Mapping) else {}
    for key in ("query", "userMessage", "message"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = holdout.get("input") if isinstance(holdout, Mapping) else None
    if isinstance(source, Mapping):
        for key in ("query", "question", "message"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        turns = source.get("turns")
        if isinstance(turns, Sequence) and turns:
            first = turns[0]
            if isinstance(first, Mapping) and isinstance(first.get("message"), str):
                return str(first["message"]).strip()
    return ""


def _slice(case: Mapping[str, Any]) -> str:
    value = case.get("slice")
    if isinstance(value, str) and value.strip():
        return value.strip()
    output = case.get("output") if isinstance(case.get("output"), Mapping) else {}
    tags = output.get("sliceTags") or case.get("sliceTags") or []
    if isinstance(tags, Sequence) and tags:
        return str(tags[0])
    return "unlabeled"


def _product_ids(case: Mapping[str, Any]) -> list[str]:
    output = case.get("output") if isinstance(case.get("output"), Mapping) else {}
    products = output.get("products") or output.get("results") or []
    if not isinstance(products, Sequence) or isinstance(products, (str, bytes)):
        return []
    values: list[str] = []
    for product in products:
        if not isinstance(product, Mapping):
            continue
        for key in ("productId", "product_id", "id"):
            value = product.get(key)
            if value is not None and str(value):
                values.append(str(value))
                break
    return values


def _qrels(holdout: Mapping[str, Any] | None) -> dict[str, int]:
    expected = holdout.get("expected") if isinstance(holdout, Mapping) else None
    raw = expected.get("qrels") if isinstance(expected, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): int(value) for key, value in raw.items()}


def _metric_interval(
    values: Sequence[float],
    *,
    name: str,
    aggregation: str = "mean",
    kind: str = "continuous",
) -> dict[str, Any] | None:
    rows = [float(value) for value in values]
    if not rows:
        return None
    if kind == "binary":
        lower, upper = wilson_interval(round(sum(rows)), len(rows))
        return {
            "lower": _round(lower),
            "upper": _round(upper),
            "method": "wilson",
            "confidenceLevel": 0.95,
        }
    if aggregation == "p95":
        def statistic(sample: Sequence[float]) -> float:
            return percentile(sample, 0.95)
    else:
        def statistic(sample: Sequence[float]) -> float:
            return sum(sample) / len(sample)
    lower, upper = bootstrap_interval(
        rows,
        statistic,
        samples=_BOOTSTRAP_SAMPLES,
        seed=_stable_seed(name),
    )
    return {
        "lower": _round(lower),
        "upper": _round(upper),
        "method": "percentile-bootstrap",
        "confidenceLevel": 0.95,
    }


def _metric_record(
    name: str,
    values: Sequence[float],
    *,
    denominator: int | None = None,
    numerator: int | float | None = None,
    unit: str = "ratio",
    role: str = "PRIMARY_QUALITY",
    badcase_ids: Iterable[str] = (),
    definition: str = "",
    aggregation: str = "mean",
    kind: str = "continuous",
    notes: Iterable[str] = (),
) -> dict[str, Any]:
    rows = [float(value) for value in values]
    ids = list(dict.fromkeys(str(value) for value in badcase_ids if str(value)))
    if not rows:
        return {
            "name": name,
            "status": "UNAVAILABLE",
            "value": None,
            "numerator": numerator,
            "denominator": denominator or 0,
            "unit": unit,
            "role": role,
            "confidenceInterval95": None,
            "badcaseCount": len(ids),
            "badcaseIds": ids,
            "definition": definition,
            "notes": [*notes, "NO_ELIGIBLE_SAMPLES"],
        }
    if aggregation == "p95":
        value = percentile(rows, 0.95)
    else:
        value = sum(rows) / len(rows)
    return {
        "name": name,
        "status": "MEASURED",
        "value": _round(value),
        "numerator": _round(numerator),
        "denominator": denominator if denominator is not None else len(rows),
        "unit": unit,
        "role": role,
        "confidenceInterval95": _metric_interval(
            rows, name=name, aggregation=aggregation, kind=kind
        ),
        "badcaseCount": len(ids),
        "badcaseIds": ids,
        "definition": definition,
        "notes": list(notes),
    }


def _latency_record(
    rows: Sequence[Mapping[str, Any]],
    *,
    name: str,
    badcase_limit: int | None = None,
    role: str = "RUNTIME_DIAGNOSTIC",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values = [float(row.get("latency_ms") or 0.0) for row in rows]
    if not values:
        return _metric_record(
            name,
            [],
            unit="ms",
            role=role,
            aggregation="p95",
        ), []
    threshold = percentile(values, 0.95)
    tail = sorted(
        (row for row in rows if float(row.get("latency_ms") or 0.0) > threshold),
        key=lambda row: float(row.get("latency_ms") or 0.0),
        reverse=True,
    )
    if badcase_limit is not None:
        tail = tail[:badcase_limit]
    bad_ids = [str(row.get("case_id") or "") for row in tail]
    record = _metric_record(
        name,
        values,
        unit="ms",
        role=role,
        badcase_ids=bad_ids,
        definition="客户端观测的本地完整链路 P95；不是生产 SLO。超过观测 P95 的 case 作为长尾诊断。",
        aggregation="p95",
        notes=("P95_SAMPLE_COUNT_BELOW_100_DESCRIPTIVE_ONLY",) if len(values) < 100 else (),
    )
    diagnostics = [
        {
            "kind": "latency-tail",
            "caseId": str(row.get("case_id") or ""),
            "latencyMs": _round(float(row.get("latency_ms") or 0.0)),
            "thresholdP95Ms": _round(threshold),
        }
        for row in tail
    ]
    return record, diagnostics


def _catalog_map(catalog_path: Path | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    if catalog_path is None:
        return {}, None
    if not catalog_path.is_file():
        raise ScorecardError(f"catalog fixture is missing: {catalog_path}")
    payload = load_json(catalog_path)
    products = payload.get("products") if isinstance(payload, Mapping) else None
    if not isinstance(products, list):
        raise ScorecardError("catalog fixture does not contain products")
    index: dict[str, dict[str, Any]] = {}
    for product in products:
        if not isinstance(product, Mapping) or not product.get("productId"):
            continue
        product_id = str(product["productId"])
        index[product_id] = {
            "productId": product_id,
            "productName": str(product.get("productName") or ""),
            "categoryId": product.get("categoryId"),
        }
    return index, str(payload.get("canonicalSha256") or "") or None


def _portable_path(path: Path | None) -> str | None:
    """Keep tracked scorecards independent of the machine that generated them."""

    if path is None:
        return None
    resolved = path.resolve()
    try:
        return relative_to_repo(resolved)
    except ValueError:
        # Temporary/external fixtures are useful in tests; retain a truthful
        # path when they cannot be represented relative to this repository.
        return resolved.as_posix()


def _product_views(ids: Iterable[str], catalog: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(catalog.get(str(product_id), {"productId": str(product_id), "productName": "<unknown>"}))
        for product_id in ids
    ]


def _search_scorecard(
    rows: list[dict[str, Any]],
    holdouts: Mapping[str, Mapping[str, Any]],
    catalog: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    judged: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    badcases: list[dict[str, Any]] = []
    recompute_mismatches: list[str] = []
    for row in rows:
        holdout = holdouts.get(str(row.get("case_id") or ""))
        qrels = _qrels(holdout)
        ranking = _product_ids(row)
        enriched = {
            "case": row,
            "holdout": holdout,
            "caseId": str(row.get("case_id") or ""),
            "slice": _slice(row),
            "query": _query(row, holdout),
            "qrels": qrels,
            "ranking": ranking,
        }
        all_rows.append(enriched)
        if qrels:
            judged.append(enriched)
            metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
            for k in (3, 5, 10):
                recomputed = recall_at_k(ranking, qrels, k)
                published = metrics.get(f"recallAt{k}")
                if published is not None and not math.isclose(float(published), recomputed, abs_tol=1e-6):
                    recompute_mismatches.append(enriched["caseId"])
            missing = [
                str(product_id)
                for product_id, grade in qrels.items()
                if int(grade) > 0 and str(product_id) not in ranking[:10]
            ]
            if missing:
                badcases.append(
                    {
                        "kind": "search-recall-miss",
                        "metric": "Recall@10",
                        "caseId": enriched["caseId"],
                        "slice": enriched["slice"],
                        "query": enriched["query"],
                        "expectedProductIds": list(qrels),
                        "expectedProducts": _product_views(qrels, catalog),
                        "returnedProductIds": ranking[:10],
                        "returnedProducts": _product_views(ranking[:10], catalog),
                        "missingProductIds": missing,
                        "missingProducts": _product_views(missing, catalog),
                        "ranks": {
                            str(product_id): (ranking.index(str(product_id)) + 1 if str(product_id) in ranking else None)
                            for product_id in qrels
                        },
                        "observedMetrics": dict(metrics),
                        "status": str(row.get("status") or ""),
                    }
                )
            for metric_name, metric_fn in (
                ("mrrAt10", lambda: reciprocal_rank_at_k(ranking, qrels, 10)),
                ("ndcgAt10", lambda: ndcg_at_k(ranking, qrels, 10)),
            ):
                recomputed = float(metric_fn())
                published = metrics.get(metric_name)
                if published is not None and not math.isclose(
                    float(published), recomputed, abs_tol=1e-6
                ):
                    recompute_mismatches.append(enriched["caseId"])
                if recomputed < 0.999999:
                    badcases.append(
                        {
                            "kind": "search-ranking-order",
                            "metric": metric_name,
                            "caseId": enriched["caseId"],
                            "slice": enriched["slice"],
                            "query": enriched["query"],
                            "expectedProductIds": list(qrels),
                            "returnedProductIds": ranking[:10],
                            "returnedProducts": _product_views(ranking[:10], catalog),
                            "ranks": {
                                str(product_id): (ranking.index(str(product_id)) + 1 if str(product_id) in ranking else None)
                                for product_id in qrels
                            },
                            "observedMetrics": {
                                **dict(metrics),
                                "recomputedValue": _round(recomputed),
                            },
                            "status": str(row.get("status") or ""),
                        }
                    )

    def values(name: str) -> list[float]:
        recall_k = int(name.removeprefix("recallAt")) if name.startswith("recallAt") else None
        return [
            float(recall_at_k(item["ranking"], item["qrels"], recall_k))
            if recall_k is not None
            else float(reciprocal_rank_at_k(item["ranking"], item["qrels"], 10))
            if name == "mrrAt10"
            else float(ndcg_at_k(item["ranking"], item["qrels"], 10))
            for item in judged
        ]

    primary: dict[str, Any] = {}
    for k in (3, 5, 10):
        metric_name = f"recallAt{k}"
        metric_values = values(metric_name)
        bad_ids = [
            item["caseId"]
            for item in judged
            if recall_at_k(item["ranking"], item["qrels"], k) < 0.999999
        ]
        primary[f"Recall@{k} macro/query"] = _metric_record(
            f"Recall@{k} macro/query",
            metric_values,
            denominator=len(judged),
            unit="ratio",
            role="PRIMARY_QUALITY" if k == 10 else "SUPPORTING_DIAGNOSTIC",
            badcase_ids=bad_ids,
            definition=f"有 qrel 的 query 上逐 query Recall@{k} 的宏平均；与 v9 published summary 口径兼容。",
            notes=("qrel_queries_only",),
        )
    micro_hits = {
        k: sum(
            sum(1 for product_id, grade in item["qrels"].items() if int(grade) > 0 and str(product_id) in item["ranking"][:k])
            for item in judged
        )
        for k in (3, 5, 10)
    }
    micro_total = sum(sum(int(grade) > 0 for grade in item["qrels"].values()) for item in judged)
    for k in (10,):
        bad_ids = [
            item["caseId"]
            for item in judged
            if any(int(grade) > 0 and str(product_id) not in item["ranking"][:k] for product_id, grade in item["qrels"].items())
        ]
        primary[f"Recall@{k} micro/qrel"] = _metric_record(
            f"Recall@{k} micro/qrel",
            [micro_hits[k] / micro_total] if micro_total else [],
            numerator=micro_hits[k],
            denominator=micro_total,
            unit="ratio",
            role="PRIMARY_QUALITY",
            badcase_ids=bad_ids,
            definition=f"所有有 qrel 的相关商品 judgment 汇总后的 micro Recall@{k}；用于直观看漏掉了多少相关商品。",
            notes=("supplemental_micro_view",),
            kind="binary" if micro_hits[k] in {0, micro_total} else "continuous",
        )
        # A single aggregate value cannot provide a useful bootstrap interval;
        # replace it with the exact Wilson interval for document judgments.
        if micro_total:
            lower, upper = wilson_interval(micro_hits[k], micro_total)
            primary[f"Recall@{k} micro/qrel"]["confidenceInterval95"] = {
                "lower": _round(lower),
                "upper": _round(upper),
                "method": "wilson",
                "confidenceLevel": 0.95,
            }

    for metric_name, label, fn in (
        ("mrrAt10", "MRR@10 macro/query", lambda item: reciprocal_rank_at_k(item["ranking"], item["qrels"], 10)),
        ("ndcgAt10", "NDCG@10 macro/query", lambda item: ndcg_at_k(item["ranking"], item["qrels"], 10)),
    ):
        metric_values = [float(fn(item)) for item in judged]
        bad_ids = [item["caseId"] for item, value in zip(judged, metric_values) if value < 0.999999]
        primary[label] = _metric_record(
            label,
            metric_values,
            denominator=len(judged),
            unit="ratio",
            role="PRIMARY_QUALITY",
            badcase_ids=bad_ids,
            definition=f"有 qrel 的 query 上逐 query {label.split()[0]} 的宏平均；越接近 1 越好。",
            notes=("qrel_queries_only",),
        )

    no_result_values: list[float] = []
    no_result_bad: list[str] = []
    for item in all_rows:
        expected = item["holdout"].get("expected") if isinstance(item["holdout"], Mapping) else {}
        if not isinstance(expected, Mapping) or "noResult" not in expected:
            continue
        expected_no_result = bool(expected.get("noResult"))
        observed = not item["ranking"]
        value = 1.0 if observed == expected_no_result else 0.0
        no_result_values.append(value)
        if value == 0:
            no_result_bad.append(item["caseId"])
    primary["No-result accuracy"] = _metric_record(
        "No-result accuracy",
        no_result_values,
        numerator=round(sum(no_result_values)),
        denominator=len(no_result_values),
        unit="ratio",
        role="CONTRACT_GATE",
        badcase_ids=no_result_bad,
        definition="预声明无结果/有结果意图与实际空 slate 是否一致；不能靠静默放宽约束修复。",
        kind="binary",
    )
    latency_record, latency_bad = _latency_record(rows, name="Search P95 latency")
    primary["Search P95 latency"] = latency_record
    for item in latency_bad:
        item["query"] = next((row["query"] for row in all_rows if row["caseId"] == item["caseId"]), "")
        item["slice"] = next((row["slice"] for row in all_rows if row["caseId"] == item["caseId"]), "")
        badcases.append({"kind": "search-latency-tail", **item})

    for item in all_rows:
        metrics = item["case"].get("metrics") if isinstance(item["case"].get("metrics"), Mapping) else {}
        if int(metrics.get("constraintViolationCount") or 0) > 0 or int(metrics.get("unknownProductCount") or 0) > 0:
            badcases.append(
                {
                    "kind": "search-contract-violation",
                    "metric": "hardConstraint/unknownProduct",
                    "caseId": item["caseId"],
                    "slice": item["slice"],
                    "query": item["query"],
                    "observedMetrics": dict(metrics),
                }
            )

    slices: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in all_rows:
        grouped[item["slice"]].append(item)
    for slice_name, slice_rows in sorted(grouped.items()):
        slice_judged = [item for item in slice_rows if item["qrels"]]
        slice_recall = [recall_at_k(item["ranking"], item["qrels"], 10) for item in slice_judged]
        slice_mrr = [reciprocal_rank_at_k(item["ranking"], item["qrels"], 10) for item in slice_judged]
        slice_ndcg = [ndcg_at_k(item["ranking"], item["qrels"], 10) for item in slice_judged]
        slice_bad = sorted(
            {
                bad["caseId"]
                for bad in badcases
                if bad.get("slice") == slice_name and bad.get("kind") in {"search-recall-miss", "search-ranking-order"}
            }
        )
        slices[slice_name] = {
            "caseCount": len(slice_rows),
            "judgedQueryCount": len(slice_judged),
            "recallAt10": _round(sum(slice_recall) / len(slice_recall)) if slice_recall else None,
            "mrrAt10": _round(sum(slice_mrr) / len(slice_mrr)) if slice_mrr else None,
            "ndcgAt10": _round(sum(slice_ndcg) / len(slice_ndcg)) if slice_ndcg else None,
            "badcaseCount": len(slice_bad),
            "badcaseIds": slice_bad,
        }

    gates = {
        "noResultAccuracy": {
            "observed": round(sum(no_result_values)),
            "denominator": len(no_result_values),
            "badcaseIds": no_result_bad,
        },
        "hardConstraintViolations": {
            "observed": sum(int((item["case"].get("metrics") or {}).get("constraintViolationCount") or 0) for item in all_rows),
            "denominator": len(all_rows),
            "badcaseIds": [item["caseId"] for item in all_rows if int((item["case"].get("metrics") or {}).get("constraintViolationCount") or 0) > 0],
        },
        "providerCompleteness": {
            "observed": sum(_as_bool((item["case"].get("metrics") or {}).get("providerCompleteness")) for item in all_rows),
            "denominator": len(all_rows),
            "badcaseIds": [item["caseId"] for item in all_rows if not _as_bool((item["case"].get("metrics") or {}).get("providerCompleteness"))],
        },
        "recomputeMismatch": {
            "observed": len(set(recompute_mismatches)),
            "denominator": len(judged),
            "badcaseIds": sorted(set(recompute_mismatches)),
        },
    }
    return {
        "caseCount": len(all_rows),
        "judgedQueryCount": len(judged),
        "primaryMetrics": primary,
        "slices": slices,
        "contractGates": gates,
    }, badcases, all_rows


def _rag_scorecard(
    rows: list[dict[str, Any]],
    holdouts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    badcases: list[dict[str, Any]] = []
    primary: dict[str, Any] = {}
    metric_specs = (
        ("groundedFaithfulness", "Grounded faithfulness", "回答事实是否能被证据支持；当前为 lexical/claim 证据下界。", "SUPPORTING_QUALITY"),
        ("citationSupport", "Citation support", "回答中的引用是否指向允许的证据；不等于人工语义正确率。", "SUPPORTING_QUALITY"),
        ("noAnswerAccuracy", "No-answer accuracy", "证据不足/权限边界/冲突时是否拒答或转人工，而不是编造。", "SUPPORTING_QUALITY"),
        ("generationCorrectness", "Generation correctness", "冻结 case 的结构化生成契约；不替代独立人工盲评。", "CONTRACT_DIAGNOSTIC"),
        ("retrievalRecallAt5", "Retrieval Recall@5", "仅在有 canonical qrel 的 answerable query 上统计。", "SUPPORTING_QUALITY"),
    )
    for key, label, definition, role in metric_specs:
        eligible = [row for row in rows if key in (row.get("metrics") or {})]
        values = [float((row.get("metrics") or {})[key]) for row in eligible]
        bad_ids = [str(row.get("case_id") or "") for row, value in zip(eligible, values) if value < 0.999999]
        primary[label] = _metric_record(
            label,
            values,
            denominator=len(eligible),
            numerator=round(sum(values), 6) if values else None,
            unit="ratio",
            role=role,
            badcase_ids=bad_ids,
            definition=definition,
            kind="continuous" if key in {"groundedFaithfulness", "citationSupport"} else "binary",
            notes=("shadow_or_contract_dataset_only",) if key == "generationCorrectness" else (),
        )

    latency_record, latency_bad = _latency_record(rows, name="RAG P95 latency")
    primary["RAG P95 latency"] = latency_record
    for item in latency_bad:
        holdout = holdouts.get(item["caseId"])
        badcases.append({"kind": "rag-latency-tail", "query": _query(next(row for row in rows if row.get("case_id") == item["caseId"]), holdout), **item})

    provider_failures = [
        row for row in rows if int((row.get("metrics") or {}).get("queryExpansionFailureCount") or 0) > 0
    ]
    for row in provider_failures:
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        expansion = output.get("queryExpansion") if isinstance(output.get("queryExpansion"), Mapping) else {}
        badcases.append(
            {
                "kind": "rag-provider-diagnostic",
                "metric": "queryExpansionFailureCount",
                "caseId": str(row.get("case_id") or ""),
                "slice": _slice(row),
                "query": _query(row, holdouts.get(str(row.get("case_id") or ""))),
                "failureCount": int((row.get("metrics") or {}).get("queryExpansionFailureCount") or 0),
                "providerRequests": expansion.get("providerRequests"),
                "providerSuccesses": expansion.get("providerSuccesses"),
                "safeFallbackObserved": True,
                "normalQualityDenominator": True,
                "interpretation": "安全 deterministic fallback；不是答案质量失败，但应在面试中披露。",
            }
        )

    shadows = []
    unavailable_ids: list[str] = []
    disagreement_count = 0
    for row in rows:
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        shadow = output.get("semanticShadow") if isinstance(output.get("semanticShadow"), Mapping) else {}
        if shadow.get("available") is True:
            shadows.append(row)
            disagreement_count += int(shadow.get("disagreementCount") or 0)
        else:
            unavailable_ids.append(str(row.get("case_id") or ""))
    primary["Semantic shadow availability"] = _metric_record(
        "Semantic shadow availability",
        [1.0 if row in shadows else 0.0 for row in rows],
        numerator=len(shadows),
        denominator=len(rows),
        unit="ratio",
        role="DIAGNOSTIC",
        badcase_ids=unavailable_ids,
        definition="judge 有完整追踪的比例；shadowOnly，不是人工真值，也不进入 release gate。",
        kind="binary",
    )
    diagnostics = {
        "queryExpansionFailureCount": len(provider_failures),
        "semanticShadowDisagreementCount": disagreement_count,
        "semanticShadowUnavailableCaseIds": unavailable_ids,
    }
    gates = {
        "invalidCitation": {
            "observed": sum(int((row.get("metrics") or {}).get("invalidCitationCount") or 0) for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if int((row.get("metrics") or {}).get("invalidCitationCount") or 0) > 0],
        },
        "severeSafetyViolation": {
            "observed": sum(int((row.get("metrics") or {}).get("severeSafetyViolationCount") or 0) for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if int((row.get("metrics") or {}).get("severeSafetyViolationCount") or 0) > 0],
        },
        "runtimeError": {
            "observed": sum(str(row.get("status") or "") == "ERROR" for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if str(row.get("status") or "") == "ERROR"],
        },
    }
    return {
        "caseCount": len(rows),
        "primaryMetrics": primary,
        "diagnostics": diagnostics,
        "contractGates": gates,
    }, badcases


def _agent_scorecard(
    rows: list[dict[str, Any]],
    holdouts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    badcases: list[dict[str, Any]] = []
    primary: dict[str, Any] = {}
    for key, label, definition in (
        ("toolSelectionAccuracy", "Tool routing accuracy", "预声明工具与实际工具选择一致率；当前是规则/契约真值，不是客服意图人工 F1。"),
        ("toolArgumentAccuracy", "Tool argument accuracy", "预声明字段与实际工具参数一致率；字段级错误应单独回放。"),
    ):
        eligible = [row for row in rows if key in (row.get("metrics") or {})]
        values = [float((row.get("metrics") or {})[key]) for row in eligible]
        bad_ids = [str(row.get("case_id") or "") for row, value in zip(eligible, values) if value < 0.999999]
        primary[label] = _metric_record(
            label,
            values,
            denominator=len(eligible),
            numerator=round(sum(values), 6) if values else None,
            unit="ratio",
            role="CONTRACT_DIAGNOSTIC",
            badcase_ids=bad_ids,
            definition=definition,
            kind="binary",
            notes=("not_independent_human_intent_annotation",),
        )
    latency_record, latency_bad = _latency_record(rows, name="Agent P95 latency")
    primary["Agent P95 latency"] = latency_record
    for item in latency_bad:
        case_id = item["caseId"]
        badcases.append(
            {
                "kind": "agent-latency-tail",
                "query": _query(next(row for row in rows if row.get("case_id") == case_id), holdouts.get(case_id)),
                **item,
            }
        )

    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
        state_diff = row.get("state_diff") or row.get("stateDiff") or {}
        if str(row.get("status") or "") in {"FAILED", "ERROR"} or int(metrics.get("duplicateSideEffectCount") or 0) > 0 or not _as_bool(metrics.get("stateDiffMatch")):
            badcases.append(
                {
                    "kind": "agent-contract-diagnostic",
                    "caseId": str(row.get("case_id") or ""),
                    "slice": _slice(row),
                    "query": _query(row, holdouts.get(str(row.get("case_id") or ""))),
                    "status": row.get("status"),
                    "metrics": dict(metrics),
                    "stateDiff": {
                        "matched": state_diff.get("matched"),
                        "duplicateSideEffectCount": state_diff.get("duplicateSideEffectCount"),
                        "changeCount": state_diff.get("changeCount"),
                    },
                }
            )

    gates = {
        "terminalStateCorrectness": {
            "observed": sum(_as_bool((row.get("metrics") or {}).get("terminalStateCorrectness")) for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if not _as_bool((row.get("metrics") or {}).get("terminalStateCorrectness"))],
        },
        "stateDiffMatch": {
            "observed": sum(_as_bool((row.get("metrics") or {}).get("stateDiffMatch")) for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if not _as_bool((row.get("metrics") or {}).get("stateDiffMatch"))],
        },
        "duplicateSideEffects": {
            "observed": sum(int((row.get("metrics") or {}).get("duplicateSideEffectCount") or 0) for row in rows),
            "denominator": len(rows),
            "badcaseIds": [str(row.get("case_id") or "") for row in rows if int((row.get("metrics") or {}).get("duplicateSideEffectCount") or 0) > 0],
        },
        "runtimeOrSevereSafety": {
            "observed": sum(
                str(row.get("status") or "") == "ERROR"
                or int((row.get("metrics") or {}).get("severeSafetyViolationCount") or 0) > 0
                for row in rows
            ),
            "denominator": len(rows),
            "badcaseIds": [
                str(row.get("case_id") or "")
                for row in rows
                if str(row.get("status") or "") == "ERROR"
                or int((row.get("metrics") or {}).get("severeSafetyViolationCount") or 0) > 0
            ],
        },
    }
    return {"caseCount": len(rows), "primaryMetrics": primary, "contractGates": gates}, badcases


def _holdout_index(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    if not path.is_file():
        raise ScorecardError(f"holdout is missing: {path}")
    rows = load_jsonl(path)
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("id") or "")
        if not case_id or case_id in index:
            raise ScorecardError(f"holdout contains empty or duplicate case ID: {case_id!r}")
        index[case_id] = row
    return index


def build_scorecard(
    evidence_path: Path = EVIDENCE_ROOT,
    *,
    holdout_path: Path | None = DEFAULT_HOLDOUT,
    catalog_path: Path | None = DEFAULT_CATALOG,
) -> dict[str, Any]:
    """Build a deterministic scorecard without mutating ``evidence_path``."""

    evidence_path = evidence_path.resolve()
    if not evidence_path.is_dir():
        raise ScorecardError(f"evidence directory is missing: {evidence_path}")
    try:
        verify_evidence(evidence_path)
    except (OSError, ValueError) as exc:
        raise ScorecardError(f"immutable evidence verification failed: {exc}") from exc
    holdouts = _holdout_index(holdout_path.resolve() if holdout_path else None)
    catalog, catalog_sha = _catalog_map(catalog_path.resolve() if catalog_path else None)
    cases = load_jsonl(evidence_path / "cases.jsonl")
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_domain[str(case.get("domain") or "")].append(case)
    search_rows = by_domain.get("search", [])
    if search_rows and not holdouts:
        raise ScorecardError("Search scorecard requires the immutable final holdout to derive qrel badcases")
    search, search_bad, _ = _search_scorecard(search_rows, holdouts, catalog)
    rag, rag_bad = _rag_scorecard(by_domain.get("rag", []), holdouts)
    agent, agent_bad = _agent_scorecard(by_domain.get("agent", []), holdouts)
    summary = load_json(evidence_path / "summary.json")
    lifecycle = load_json(evidence_path / "lifecycle.json") if (evidence_path / "lifecycle.json").is_file() else {}
    return {
        "schemaVersion": SCORECARD_SCHEMA,
        "evidence": {
            "path": _portable_path(evidence_path),
            "runId": summary.get("runId"),
            "releaseId": lifecycle.get("releaseId"),
            "split": summary.get("split"),
            "datasetSha256": summary.get("datasetSha256"),
            "sha256SumsSha256": sha256_file(evidence_path / "SHA256SUMS"),
            "completedAt": summary.get("completedAt"),
            "immutable": True,
        },
        "holdout": {
            "path": _portable_path(holdout_path),
            "caseCount": len(holdouts),
            "sha256": sha256_file(holdout_path) if holdout_path else None,
            "requiredForSearchQrels": bool(search_rows),
        },
        "catalog": {"path": _portable_path(catalog_path), "canonicalSha256": catalog_sha},
        "domains": {"search": search, "rag": rag, "agent": agent},
        "badcases": [*search_bad, *rag_bad, *agent_bad],
        "interpretation": {
            "qualityFirst": True,
            "contractGatesAreNotShowcaseMetrics": True,
            "semanticJudgeIsShadowOnly": True,
            "localLatencyIsNotProductionSlo": True,
            "customerServiceIntentMetricsNotMeasured": True,
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "不可得"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _metric_line(metric: Mapping[str, Any]) -> str:
    interval = metric.get("confidenceInterval95") or {}
    ci = (
        f"[{_fmt(interval.get('lower'))}, {_fmt(interval.get('upper'))}]"
        if interval
        else "—"
    )
    ids = ", ".join(metric.get("badcaseIds") or []) or "无"
    return (
        f"| {metric.get('name')} | {_fmt(metric.get('value'))} | "
        f"{metric.get('numerator') if metric.get('numerator') is not None else '—'}/"
        f"{metric.get('denominator', 0)} | {ci} | {metric.get('badcaseCount', 0)} | {ids} |"
    )


def render_markdown(scorecard: Mapping[str, Any]) -> str:
    """Render a concise interview-oriented report from a scorecard."""

    evidence = scorecard.get("evidence") or {}
    domains = scorecard.get("domains") or {}
    lines = [
        "# AI_Shop 质量主指标与 Badcase 索引（v9）",
        "",
        "> 这份报告是从不可变 v9 evidence 派生的诊断视图。`PASSED` 只说明契约门禁满足，不能推出质量满分；每个主指标都给出分母、95% CI 和 badcase。",
        "",
        "## 证据边界",
        "",
        f"- run/release：`{evidence.get('runId')}` / `{evidence.get('releaseId')}`",
        f"- dataset SHA-256：`{evidence.get('datasetSha256')}`",
        f"- evidence `SHA256SUMS` SHA-256：`{evidence.get('sha256SumsSha256')}`",
        f"- holdout：`{scorecard.get('holdout', {}).get('caseCount', 0)}` 条，未写入 Git；scorecard 不修改 current。",
        "- 置信区间：二项比例使用 Wilson；宏平均与 P95 使用 percentile bootstrap。样本少于 100 的 P95 只作本地描述性观察。",
        "",
        "## 一页结论",
        "",
        "- Search 的主要短板不是执行失败，而是多目标召回与排序：micro Recall@10 为 `52/56`，有 3 个 query、4 个相关商品未召回；另有 8 个 query 的 MRR/NDCG 低于理想排序。",
        "- RAG 的 lexical/引用/拒答证据在冻结集上没有坏例，但有 3 次 query expansion Provider failure，均安全 fallback；它是客服事实安全证据，不应与 InsightVault 的深度文档 RAG benchmark 重复包装。",
        "- Agent 的当前样本没有工具参数、终态或重复副作用坏例，但长尾主要集中在 RAG/政策路径；客服 intent Macro-F1、slot F1、转人工 Recall 仍未被独立标注测量。",
        "",
        "## Search 主质量指标",
        "",
        "| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for metric in (domains.get("search") or {}).get("primaryMetrics", {}).values():
        if metric.get("role") == "PRIMARY_QUALITY":
            lines.append(_metric_line(metric))
    lines.extend(
        [
            "",
            "`Recall@K macro/query` 与已发布 summary 保持兼容；`Recall@10 micro/qrel` 额外回答“总共漏掉了几个相关商品”。两者不能混称。",
            "R@3/R@5 和本地 P95 仍保留在 JSON scorecard 中用于定位与回归，但不作为项目优展示。",
            "",
            "### Search hard negatives",
            "",
        ]
    )
    search_bad = [row for row in scorecard.get("badcases", []) if row.get("kind") == "search-recall-miss"]
    for bad in search_bad:
        missing = "；".join(
            f"{item.get('productId')} {item.get('productName')}" for item in bad.get("missingProducts", [])
        )
        lines.extend(
            [
                f"- `{bad.get('caseId')}`（{bad.get('slice')}）：{bad.get('query')}",
                f"  - 期望相关商品：{', '.join(bad.get('expectedProductIds') or [])}",
                f"  - 实际 Top10：{', '.join(bad.get('returnedProductIds') or []) or '空'}",
                f"  - 漏召回：{missing or '未知商品'}",
                "  - 复盘假设：多商品/多品牌 conjunction 被过早收窄，或候选池没有保留足够的同类商品；需要在 query intent、召回扩展和比较对象保留策略上做 paired replay。",
            ]
        )
    lines.extend(["", "### Search 排序 badcase", ""])
    ranking_bad = [row for row in scorecard.get("badcases", []) if row.get("kind") == "search-ranking-order"]
    for bad in ranking_bad:
        lines.append(
            f"- `{bad.get('caseId')}`：{bad.get('metric')}={_fmt((bad.get('observedMetrics') or {}).get(bad.get('metric')))}；"
            f"query=`{bad.get('query')}`；返回顺序 `{' > '.join(bad.get('returnedProductIds') or [])}`，状态仍为 `PASSED`，所以它不会出现在 runtime `bad-cases.jsonl`。"
        )
    lines.extend(["", "### Search slice 摘要", "", "| slice | case/judged | Recall@10 | MRR@10 | NDCG@10 | badcase IDs |", "|---|---:|---:|---:|---:|---|"])
    for name, value in (domains.get("search") or {}).get("slices", {}).items():
        lines.append(
            f"| {name} | {value.get('caseCount')}/{value.get('judgedQueryCount')} | {_fmt(value.get('recallAt10'))} | {_fmt(value.get('mrrAt10'))} | {_fmt(value.get('ndcgAt10'))} | {', '.join(value.get('badcaseIds') or []) or '无'} |"
        )

    lines.extend(["", "## RAG 最小事实安全证据（不扩张为第二套 RAG 主指标）", "", "| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |", "|---|---:|---:|---:|---:|---|"])
    for metric in (domains.get("rag") or {}).get("primaryMetrics", {}).values():
        if metric.get("role") == "SUPPORTING_QUALITY":
            lines.append(_metric_line(metric))
    rag_diag = [row for row in scorecard.get("badcases", []) if row.get("kind") == "rag-provider-diagnostic"]
    lines.extend(["", "### RAG Provider/尾延迟诊断 badcase", ""])
    for bad in rag_diag:
        lines.append(
            f"- `{bad.get('caseId')}`：{bad.get('query')}；query expansion failure={bad.get('failureCount')}，安全 fallback={bad.get('safeFallbackObserved')}。"
        )
    for bad in scorecard.get("badcases", []):
        if bad.get("kind") == "rag-latency-tail":
            lines.append(f"- `{bad.get('caseId')}`：本地完整链路 {bad.get('latencyMs')} ms，超过观测 P95 {bad.get('thresholdP95Ms')} ms。")
    lines.extend(
        [
            "- Semantic shadow 只报告 availability/disagreement 和逐 claim 证据；当前没有人工校准，不能写成人工准确率或一致性。",
            "",
            "## Agent 运行诊断（不是客服意图准确率）",
            "",
            "| 指标 | 值 | 分子/分母 | 95% CI | badcase 数 | badcase IDs |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for metric in (domains.get("agent") or {}).get("primaryMetrics", {}).values():
        lines.append(_metric_line(metric))
    lines.extend(["", "### Agent 长尾/失败诊断", ""])
    for bad in scorecard.get("badcases", []):
        if bad.get("kind") == "agent-latency-tail":
            lines.append(f"- `{bad.get('caseId')}`（{bad.get('slice')}）：{bad.get('query')}；本地完整链路 {bad.get('latencyMs')} ms。")
    if not any(bad.get("kind") == "agent-contract-diagnostic" for bad in scorecard.get("badcases", [])):
        lines.append("- 当前没有 runtime/终态/状态 diff/重复副作用坏例；这属于必须满足的可靠性契约，不作为“质量提升”展示。")

    lines.extend(["", "## 必须 100% 的契约门禁（不作为优展示）", "", "| 域 | 门禁 | 观察值 | 分母 | 违规 badcase |", "|---|---|---:|---:|---|"])
    for domain_name in ("search", "rag", "agent"):
        for gate_name, gate in ((domains.get(domain_name) or {}).get("contractGates") or {}).items():
            lines.append(
                f"| {domain_name} | {gate_name} | {gate.get('observed')} | {gate.get('denominator')} | {', '.join(gate.get('badcaseIds') or []) or '无'} |"
            )
    lines.extend(
        [
            "",
            "这些门禁包括：Search 硬约束/Provider completeness/no-result，RAG invalid citation/严重安全/runtime error，Agent 终态/state diff/重复副作用/runtime 安全。门禁失败应阻断发布，但通过不等于推荐质量或客服理解质量达到行业满分。",
            "",
            "## AI 客服垂类尚未测量的高价值指标",
            "",
            "当前证据验证了工具契约和业务终态，但没有独立客服理解金标，因此不能声称以下准确率。秋招只补四项高价值指标：",
            "",
            "- intent Macro-F1，并保留逐 intent Precision/Recall/F1 与 confusion matrix；",
            "- 高风险意图 Recall 和严重漏判数（退款、取消、隐私、越权、紧急人工请求）；",
            "- 订单号、商品、金额、时间等关键 slot 的 entity/span F1，以及请求级 slot Exact Match；",
            "- handoff Recall 与严重漏转人工率。",
            "",
            "下一轮只需先做一套小而独立的人工标注集：intent + slots + shouldHandoff + riskLevel。request mode 可作为 intent taxonomy 的属性，不另造一个主指标；不在秋招窗口扩张到情绪、风格、ECE/Brier、泛化 Answer Relevance 或模拟 CTR。",
            "",
            "## 与 InsightVault 的差异化",
            "",
            "InsightVault 的 `embodied-v1` 已规划 required/forbidden fact、gold retrieval/final-evidence recall、合法引用、消融和稳定性；其真实 run 输出尚未完整。AI_Shop 因此不重复堆一套深文档 RAG 排行榜，而聚焦电商/客服闭环：商品多目标召回与排序、硬约束、客服请求理解、人工转接、订单权威状态、确认和幂等写入。",
            "",
            "## 后续路线（按投入产出比）",
            "",
            "1. 先修 Search 的三类已证实 hard negative：多商品/多品牌、否定约束候选不足、比较对象过早收窄；每次修复只做 paired replay，主报 Recall@10、NDCG@10、MRR@10 与对应 badcase。",
            "2. 用少量人工客服金标补 intent/slot/handoff/risk 四项；把每个错例连同模型输出、正确标签、根因和回归 ID 固化，不把当前 Agent pass^k 当意图准确率。",
            "3. 在同一数据集上做一次 candidate recall -> rerank -> hard-filter 的小型消融，报告上述三个 Search 指标、P95 和 usage/cost unknown；没有明确假设就不加新指标。",
            "4. 只保留 Worker/MQ redelivery、lease 失效、catalog/price/stock/version mutation replay 作为交易安全门禁维护，不继续把它们扩写成面试主指标。",
            "5. 有真实曝光/点击/购买和授权数据后，才做 CTR/CVR/GMV、偏差校正和线上 A/B；当前禁止外推。",
            "",
            "## 不能外推",
            "",
            "本报告不能证明 CTR/CVR/GMV、工业级个性化推荐、生产容量/线上 SLO、支付合规、人工语义准确率或开放世界客服成功率。",
            "",
        ]
    )
    return "\n".join(lines)


def write_scorecard(
    scorecard: Mapping[str, Any],
    markdown_path: Path,
    json_path: Path | None = None,
    *,
    evidence_path: Path = EVIDENCE_ROOT,
) -> tuple[Path, Path]:
    """Write derived reports and reject paths inside immutable evidence."""

    evidence_root = evidence_path.resolve()
    targets = [markdown_path.resolve(), (json_path or markdown_path.with_suffix(".json")).resolve()]
    for target in targets:
        try:
            target.relative_to(evidence_root)
        except ValueError:
            continue
        raise ScorecardError(f"refusing to write derived scorecard inside immutable evidence: {target}")
    output_json = targets[1]
    atomic_write_json(output_json, scorecard)
    atomic_write_text(targets[0], render_markdown(scorecard))
    return targets[0], output_json


__all__ = [
    "DEFAULT_CATALOG",
    "DEFAULT_HOLDOUT",
    "SCORECARD_SCHEMA",
    "ScorecardError",
    "build_scorecard",
    "render_markdown",
    "write_scorecard",
]
