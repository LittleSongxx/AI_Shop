"""Deterministic synthetic calibration for the production response verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.services.evidence_refs import negative_lookup_ref, order_refs, product_refs
from app.services.response_verifier import response_verifier
from evaluation.core.io import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import wilson_interval

SCHEMA_VERSION = "aishop-verifier-mutation/v1"
MANIFEST_SCHEMA_VERSION = "aishop-verifier-mutation-manifest/v1"
_CAPTURED_AT = "2000-01-01T00:00:00.000Z"


def _interval(successes: int, total: int) -> dict[str, Any]:
    if total == 0:
        return {
            "method": "wilson",
            "confidenceLevel": 0.95,
            "lower": "UNAVAILABLE",
            "upper": "UNAVAILABLE",
        }
    lower, upper = wilson_interval(successes, total)
    return {
        "method": "wilson",
        "confidenceLevel": 0.95,
        "lower": round(lower, 6),
        "upper": round(upper, 6),
    }


def _scenarios() -> tuple[
    list[tuple[str, dict[str, Any], dict[str, Any]]],
    list[tuple[str, dict[str, Any], dict[str, Any]]],
    tuple[str, dict[str, Any], dict[str, Any]],
]:
    order = order_refs(
        [
            {
                "order_id": "SYNTH-ORDER-1",
                "order_status": 2,
                "order_status_name": "已发货",
                "amount": 128.5,
            }
        ],
        captured=_CAPTURED_AT,
    )
    order_without_status = order_refs(
        [{"order_id": "SYNTH-ORDER-1"}],
        captured=_CAPTURED_AT,
    )
    other_order = order_refs(
        [
            {
                "order_id": "SYNTH-ORDER-2",
                "order_status": 2,
                "order_status_name": "已发货",
            }
        ],
        captured=_CAPTURED_AT,
    )
    matched_false = [{**row, "matched": False, "authoritative": True} for row in order]
    non_authoritative = [{**row, "matched": True, "authoritative": False} for row in order]
    failed_order_lookup = negative_lookup_ref(
        "order",
        query={"orderId": "SYNTH-ORDER-1"},
        source="JAVA_ORDER_SERVICE",
        matched=False,
        authoritative=False,
        captured=_CAPTURED_AT,
    )
    product = product_refs(
        [
            {
                "product_id": "SYNTH-PRODUCT-1",
                "product_name": "合成示例商品",
                "min_price": 99,
                "total_stock": 5,
                "status": 1,
                "in_stock": True,
            }
        ],
        captured=_CAPTURED_AT,
    )
    rag_sources = [
        {
            "id": "synthetic-policy",
            "source": "SYNTHETIC",
            "snippet": "待付款订单可以直接取消。",
        }
    ]
    order_call = {
        "assistant": "订单 SYNTH-ORDER-1 当前已发货。",
        "biz_type": "query_order",
        "tools_called": ["QUERY_ORDERS"],
        "source_refs": {"businessSources": order},
        "order_resolution": "RESOLVED",
        "has_pending_action": False,
    }
    product_call = {
        "assistant": "商品价格为 99.00 元。",
        "biz_type": "product_search",
        "tools_called": ["SEARCH_PRODUCTS"],
        "source_refs": {"businessSources": product},
        "has_pending_action": False,
    }
    rag_call = {
        "assistant": "待付款订单可以直接取消。[1]",
        "biz_type": "agent",
        "tools_called": ["SEARCH_KNOWLEDGE"],
        "source_refs": {"ragSources": rag_sources, "businessSources": []},
        "rag_source_refs": rag_sources,
        "has_pending_action": False,
        "policy_evidence_required": True,
        "rag_citation_required": True,
        "rag_evidence_state": "SUPPORTED",
    }

    mutations = [
        (
            "order_status_value_flip",
            order_call,
            {**order_call, "assistant": "订单 SYNTH-ORDER-1 当前已完成。"},
        ),
        (
            "product_price_value_flip",
            product_call,
            {**product_call, "assistant": "商品价格为 199.00 元。"},
        ),
        (
            "required_claim_removal",
            order_call,
            {
                **order_call,
                "source_refs": {"businessSources": order_without_status},
            },
        ),
        (
            "evidence_subject_swap",
            order_call,
            {**order_call, "source_refs": {"businessSources": other_order}},
        ),
        (
            "matched_false",
            order_call,
            {**order_call, "source_refs": {"businessSources": matched_false}},
        ),
        (
            "non_authoritative",
            order_call,
            {**order_call, "source_refs": {"businessSources": non_authoritative}},
        ),
        (
            "opposite_conclusion",
            order_call,
            {
                **order_call,
                "assistant": "订单 SYNTH-ORDER-1 当前已发货，但其实已完成。",
            },
        ),
        (
            "failed_tool_success_claim",
            order_call,
            {
                **order_call,
                "source_refs": {"businessSources": [failed_order_lookup]},
            },
        ),
        (
            "rag_citation_removal",
            rag_call,
            {**rag_call, "assistant": "待付款订单可以直接取消。"},
        ),
        (
            "rag_citation_out_of_range",
            rag_call,
            {**rag_call, "assistant": "待付款订单可以直接取消。[2]"},
        ),
    ]
    benign = [
        (
            "terminal_punctuation",
            order_call,
            {**order_call, "assistant": "订单 SYNTH-ORDER-1 当前已发货！"},
        ),
        (
            "numeric_format_normalization",
            product_call,
            {**product_call, "assistant": "商品价格为99元！"},
        ),
        (
            "business_source_permutation",
            {
                **order_call,
                "source_refs": {"businessSources": [*order, *product]},
            },
            {
                **order_call,
                "source_refs": {"businessSources": [*product, *order]},
            },
        ),
        (
            "rag_citation_placement",
            rag_call,
            {**rag_call, "assistant": "待付款订单可以直接取消 [1]。"},
        ),
    ]
    multi = (
        "matched_false_plus_order_status_value_flip",
        order_call,
        {
            **order_call,
            "assistant": "订单 SYNTH-ORDER-1 当前已完成。",
            "source_refs": {"businessSources": matched_false},
        },
    )
    return mutations, benign, multi


def _evaluate(
    scenarios: list[tuple[str, dict[str, Any], dict[str, Any]]],
    *,
    benign: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]], int, int]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    unsupported: list[dict[str, str]] = []
    attempted = 0
    completed = 0
    for operator, baseline_call, variant_call in scenarios:
        attempted += 2
        stage = "baseline"
        try:
            baseline = response_verifier.verify(**baseline_call)
            completed += 1
            stage = "variant"
            variant = response_verifier.verify(**variant_call)
            completed += 1
        except Exception as exc:  # pragma: no cover - fail-closed accounting
            errors.append(
                {
                    "operator": operator,
                    "stage": stage,
                    "status": "ERROR",
                    "errorType": type(exc).__name__,
                }
            )
            continue
        if not baseline.passed:
            unsupported.append({"operator": operator, "status": "BASELINE_REJECTED"})
            continue
        success = int(variant.passed if benign else not variant.passed)
        row = {
            "operator": operator,
            "slice": f"operator:{operator}",
            "eligible": 1,
            "wilson95": _interval(success, 1),
        }
        if benign:
            row.update({"preserved": success, "invarianceRate": float(success)})
        else:
            row.update({"killed": success, "killRate": float(success)})
        rows.append(row)
    return rows, errors, unsupported, attempted, completed


def _summary(rows: list[dict[str, Any]], *, success_key: str, rate_key: str) -> dict[str, Any]:
    eligible = sum(int(row["eligible"]) for row in rows)
    successes = sum(int(row[success_key]) for row in rows)
    return {
        "slice": "synthetic-mainline",
        "eligible": eligible,
        success_key: successes,
        rate_key: round(successes / eligible, 6) if eligible else "UNAVAILABLE",
        "wilson95": _interval(successes, eligible),
        "operators": rows,
    }


def build_report() -> dict[str, Any]:
    """Run fixed synthetic examples and return a timestamp-free report."""

    mutations, benign, multi = _scenarios()
    mutation_rows, mutation_errors, mutation_unsupported, mutation_attempted, mutation_done = (
        _evaluate(mutations, benign=False)
    )
    benign_rows, benign_errors, benign_unsupported, benign_attempted, benign_done = _evaluate(
        benign, benign=True
    )
    multi_rows, multi_errors, multi_unsupported, multi_attempted, multi_done = _evaluate(
        [multi], benign=False
    )
    errors = [*mutation_errors, *benign_errors, *multi_errors]
    unsupported = [
        *mutation_unsupported,
        *benign_unsupported,
        *multi_unsupported,
        {
            "operator": "confirmation_ordering",
            "status": "NOT_RUN",
            "requiredEvaluator": "AGENT_ADAPTER_STATE_DIFF_REPEAT_RUNNER",
        },
        {
            "operator": "terminal_state_transition",
            "status": "NOT_RUN",
            "requiredEvaluator": "AGENT_ADAPTER_STATE_DIFF_REPEAT_RUNNER",
        },
        {
            "operator": "repeated_side_effect",
            "status": "NOT_RUN",
            "requiredEvaluator": "AGENT_ADAPTER_STATE_DIFF_REPEAT_RUNNER",
        },
    ]
    mutation = _summary(
        mutation_rows,
        success_key="killed",
        rate_key="killRate",
    )
    multi_summary = _summary(
        multi_rows,
        success_key="killed",
        rate_key="killRate",
    )
    components = {"matched_false", "order_status_value_flip"}
    single_rows = {row["operator"]: row for row in mutation_rows if row["operator"] in components}
    multi_mutant = {
        "operator": multi[0],
        "components": sorted(components),
        "eligible": multi_summary["eligible"],
        "killed": multi_summary["killed"],
        "killRate": multi_summary["killRate"],
        "wilson95": multi_summary["wilson95"],
        "monotonicAgainstSingleMutants": (
            len(single_rows) == len(components)
            and all(row["killed"] == row["eligible"] for row in single_rows.values())
            and multi_summary["eligible"] == multi_summary["killed"] == 1
        ),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "COMPLETED" if not errors else "COMPLETED_WITH_ERRORS",
        "scope": {
            "products": ["AI_SHOPPING_GUIDE", "AI_CUSTOMER_SERVICE_AGENT"],
            "sampleType": "SYNTHETIC",
            "publicTransfer": False,
            "postHoc": True,
            "exploratory": True,
            "releaseGateEligible": False,
            "canonicalSplitUsed": False,
            "llmJudge": "NOT_USED",
            "externalAgentExecution": "NOT_RUN",
        },
        "classification": {
            "positiveClass": "MUTATION",
            "positivePrediction": "VERIFIER_REJECTED",
            "claimPrecision": "UNAVAILABLE",
            "claimF1": "UNAVAILABLE",
            "citationPrecision": "UNAVAILABLE",
            "citationF1": "UNAVAILABLE",
        },
        "mutation": mutation,
        "multiMutant": multi_mutant,
        "benignInvariance": _summary(
            benign_rows,
            success_key="preserved",
            rate_key="invarianceRate",
        ),
        "runtime": {
            "attemptedVerifierCalls": mutation_attempted + benign_attempted + multi_attempted,
            "completedVerifierCalls": mutation_done + benign_done + multi_done,
            "errorCount": len(errors),
        },
        "errors": errors,
        "unsupported": unsupported,
        "weightedOverallScore": "NOT_COMPUTED",
    }


def self_check() -> dict[str, Any]:
    first = build_report()
    second = build_report()
    if canonical_json_bytes(first) != canonical_json_bytes(second):
        raise RuntimeError("verifier mutation report is not deterministic")
    if first["errors"]:
        raise RuntimeError("verifier mutation self-check had runtime errors")
    if first["mutation"]["eligible"] != 10 or first["benignInvariance"]["eligible"] != 4:
        raise RuntimeError("verifier mutation self-check has unsupported synthetic examples")
    if first["mutation"]["eligible"] != first["mutation"]["killed"]:
        raise RuntimeError("verifier mutation self-check has surviving mutations")
    if first["benignInvariance"]["eligible"] != first["benignInvariance"]["preserved"]:
        raise RuntimeError("verifier mutation self-check rejected a benign variant")
    if not first["multiMutant"]["monotonicAgainstSingleMutants"]:
        raise RuntimeError("verifier multi-mutant is not monotonic against its single mutants")
    return first


def write_report_package(report: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise FileExistsError(f"refusing to overwrite verifier mutation output: {output_dir}")
    else:
        output_dir.mkdir(parents=True)
    report_path = output_dir / "report.json"
    manifest_path = output_dir / "manifest.json"
    atomic_write_json(report_path, report, overwrite=False)
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "kind": "verifier-mutation",
        "createdAt": utc_now(),
        "deterministicReport": True,
        "releaseGateEligible": False,
        "files": {
            "report.json": {
                "bytes": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            }
        },
        "checksumPolicy": {
            "algorithm": "SHA-256",
            "inventory": "SHA256SUMS",
            "selfIncluded": False,
        },
    }
    atomic_write_json(manifest_path, manifest, overwrite=False)
    sums = "".join(
        f"{sha256_file(path)}  {path.name}\n"
        for path in sorted((manifest_path, report_path), key=lambda item: item.name)
    )
    sums_path = output_dir / "SHA256SUMS"
    atomic_write_text(sums_path, sums, overwrite=False)
    return {
        "status": report["status"],
        "output": str(output_dir),
        "sha256SumsSha256": sha256_file(sums_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = self_check() if args.self_check else build_report()
    print(json.dumps(write_report_package(report, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
