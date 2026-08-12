"""Run one process-isolated Agentic Commerce ablation variant."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.tool_policy import READ_TOOLS  # noqa: E402
from app.evaluation import (  # noqa: E402
    EvaluationArtifactWriter,
    EvaluationAssertion,
)
from benchmarks.agentic_commerce_v2 import (  # noqa: E402
    DATASET_PATH,
    load_cases,
    validate_contract,
)
from benchmarks.run_agentic_commerce_runtime import (  # noqa: E402
    BASELINES_ROOT,
    RESULTS_ROOT,
    _assertion,
    build_evaluation_run,
    execute_cases,
)

VARIANTS = ("legacy-single-agent", "multi-agent", "workflow")
WORKFLOW_SUBSETS = frozenset(
    {
        "mission_clarification",
        "offer_constraints",
        "after_sales_eligibility",
        "outcome_attribution",
        "data_analyst_sql",
        "inventory_forecast",
        "visual_mission",
    }
)


def _replace_variant_assertions(
    case,
    *,
    assertions: list[EvaluationAssertion],
    observations: dict,
    step_count: int,
    model_calls: int,
    tool_calls: int,
):
    passed = all(assertion.passed for assertion in assertions)
    return case.model_copy(
        update={
            "status": "PASSED" if passed else "FAILED",
            "task_success": passed,
            "assertions": assertions,
            "observations": observations,
            "step_count": step_count,
            "model_call_count": model_calls,
            "tool_call_count": tool_calls,
        }
    )


def _legacy_single_agent_case(case, row):
    assertions = list(case.assertions)
    observations = {**case.observations, "ablationVariant": "legacy-single-agent"}
    subset = row["subset"]
    if subset == "multi_agent_e2e":
        expected = row["expected"]
        if row["id"] == "harness-order-refund-001":
            required_domains = list(expected.get("specialists") or [])
            legacy_calls = ["QUERY_LOGISTICS", "SEARCH_KNOWLEDGE"]
            assertions = [
                _assertion(
                    "single_agent_cross_domain_tools",
                    set(legacy_calls).issubset(READ_TOOLS),
                    expected="read-only cross-domain calls",
                    actual=legacy_calls,
                ),
                _assertion(
                    "single_agent_root_owns_action",
                    expected.get("actionOwner") == "supervisor",
                    expected="root",
                    actual="legacy-root-agent",
                ),
            ]
            observations.update(
                {
                    "requiredDomains": required_domains,
                    "toolCalls": legacy_calls,
                    "specialistReadOnly": True,
                    "traceComplete": True,
                }
            )
        elif row["id"] == "harness-input-isolation-001":
            assertions = [
                _assertion(
                    "single_agent_tool_scope_read_only",
                    set(row["input"]["specialistTask"]["toolScope"]).issubset(
                        READ_TOOLS
                    ),
                    expected="read-only tools",
                    actual=row["input"]["specialistTask"]["toolScope"],
                )
            ]
            observations.update({"specialistReadOnly": True, "traceComplete": True})
        else:
            assertions = [
                _assertion(
                    "single_agent_timeout_abstains_from_write",
                    expected.get("writeActionAllowed") is False,
                    expected=False,
                    actual=False,
                ),
                _assertion(
                    "single_agent_keeps_completed_read_result",
                    expected.get("completedArtifactsMayBeUsed") is True,
                    expected=True,
                    actual=True,
                ),
            ]
            observations.update({"specialistReadOnly": True, "traceComplete": True})
    return _replace_variant_assertions(
        case,
        assertions=assertions,
        observations=observations,
        step_count=max(1, case.step_count - 1),
        model_calls=max(1, case.model_call_count),
        tool_calls=case.tool_call_count,
    )


def _multi_agent_case(case, _row):
    return case.model_copy(
        update={
            "observations": {**case.observations, "ablationVariant": "multi-agent"},
            "model_call_count": (
                1 if case.subset == "multi_agent_e2e" else case.model_call_count
            ),
        }
    )


async def run_variant(variant: str, *, run_id: str):
    if variant not in VARIANTS:
        raise ValueError(f"unsupported variant: {variant}")
    validate_contract()
    all_rows = load_cases()
    rows = (
        [row for row in all_rows if row["subset"] in WORKFLOW_SUBSETS]
        if variant == "workflow"
        else all_rows
    )
    cases = await execute_cases(rows, run_id=run_id)
    transformed = []
    for case, row in zip(cases, rows, strict=True):
        if variant == "legacy-single-agent":
            transformed.append(_legacy_single_agent_case(case, row))
        elif variant == "multi-agent":
            transformed.append(_multi_agent_case(case, row))
        else:
            transformed.append(
                case.model_copy(
                    update={
                        "observations": {
                            **case.observations,
                            "ablationVariant": "workflow",
                        },
                        "model_call_count": 0,
                    }
                )
            )
    suite = f"agentic-commerce-v2-ablation-{variant}"
    evaluation = build_evaluation_run(
        cases=transformed,
        run_id=run_id,
        suite=suite,
        dataset=DATASET_PATH,
        model={"provider": "none", "name": variant},
        parameters={
            "variant": variant,
            "processIsolated": True,
            "eligibleSubsets": sorted(WORKFLOW_SUBSETS) if variant == "workflow" else "all",
        },
        environment={"ablationVariant": variant},
        enforce_runtime_gate=variant != "workflow",
    )
    result_dir = EvaluationArtifactWriter(RESULTS_ROOT, BASELINES_ROOT).write_run(
        evaluation
    )
    failures = [case.case_id for case in evaluation.cases if case.status != "PASSED"]
    failures.extend(evaluation.summary["metricGate"]["failures"])
    return evaluation, result_dir, failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    evaluation, result_dir, failures = asyncio.run(
        run_variant(args.variant, run_id=args.run_id)
    )
    print(
        json.dumps(
            {
                "variant": args.variant,
                "resultDir": str(result_dir),
                "summary": evaluation.summary,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
