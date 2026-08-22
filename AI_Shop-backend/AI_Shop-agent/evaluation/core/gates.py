from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evaluation.core.contracts import GateDecision


def _compare(observed: float, operator: str, threshold: float) -> bool:
    tolerance = 1e-12
    if operator == ">=":
        return observed + tolerance >= threshold
    if operator == "<=":
        return observed - tolerance <= threshold
    if operator == "==":
        return abs(observed - threshold) <= tolerance
    raise ValueError(f"unsupported gate operator: {operator}")


def evaluate_gates(
    summary: Mapping[str, Any],
    suite: Mapping[str, Any],
    *,
    split: str,
) -> dict[str, Any]:
    decisions: list[GateDecision] = []
    domain_outcomes: dict[str, bool] = {}
    minimums = suite["splitMinimums"][split]
    for domain, domain_config in suite["domains"].items():
        domain_summary = summary.get("domains", {}).get(domain)
        if not isinstance(domain_summary, Mapping):
            decisions.append(
                GateDecision(
                    domain=domain,
                    metric="domainPresent",
                    passed=False,
                    operator="==",
                    threshold=1,
                    observed=None,
                    evaluated_field="value",
                    reason="domain summary is missing",
                )
            )
            domain_outcomes[domain] = False
            continue
        case_count = int(domain_summary.get("caseCount") or 0)
        minimum = int(minimums[domain])
        count_passed = case_count >= minimum
        decisions.append(
            GateDecision(
                domain=domain,
                metric="minimumCaseCount",
                passed=count_passed,
                operator=">=",
                threshold=minimum,
                observed=case_count,
                evaluated_field="value",
                reason=(
                    f"{case_count} cases meet the predeclared minimum {minimum}"
                    if count_passed
                    else f"{case_count} cases are below the predeclared minimum {minimum}"
                ),
            )
        )
        # Aggregate quality can hide an individual bad case (for example,
        # 11/12 RAG answers can still clear a 0.85 mean threshold).  A release
        # gate must therefore fail closed unless every executed case reached
        # the explicit PASSED state.  This is a framework integrity invariant,
        # independent of the domain-specific metric thresholds below.
        status_counts = domain_summary.get("statusCounts") or {}
        try:
            passed_cases = int(status_counts.get("PASSED") or 0)
            status_total = sum(int(value or 0) for value in status_counts.values())
        except (TypeError, ValueError):
            # Malformed status accounting is itself a failed measurement.
            passed_cases = -1
            status_total = -1
        observed_case_pass_rate = passed_cases / case_count if case_count else 0.0
        case_passed = (
            count_passed
            and status_total == case_count
            and passed_cases == case_count
        )
        decisions.append(
            GateDecision(
                domain=domain,
                metric="casePassRate",
                passed=case_passed,
                operator="==",
                threshold=1.0,
                observed=observed_case_pass_rate,
                evaluated_field="value",
                reason=(
                    f"{passed_cases}/{case_count} cases passed"
                    if case_passed
                    else f"{passed_cases}/{case_count} cases passed; every case must pass"
                ),
            )
        )
        metrics = domain_summary.get("metrics") or {}
        domain_passed = count_passed and case_passed
        for gate in domain_config["gates"]:
            name = str(gate["metric"])
            field = str(gate.get("field") or "value")
            estimate = metrics.get(name)
            observed: float | None = None
            reason: str
            if not isinstance(estimate, Mapping) or int(estimate.get("sampleCount") or 0) == 0:
                passed = False
                reason = "required metric has no eligible samples"
            else:
                raw = estimate.get(field)
                if raw is None:
                    passed = False
                    reason = f"required metric field {field} is absent"
                else:
                    observed = float(raw)
                    passed = _compare(
                        observed,
                        str(gate["operator"]),
                        float(gate["threshold"]),
                    )
                    reason = f"{field}={observed:.6f} {gate['operator']} {gate['threshold']}"
            decisions.append(
                GateDecision(
                    domain=domain,
                    metric=name,
                    passed=passed,
                    operator=str(gate["operator"]),
                    threshold=float(gate["threshold"]),
                    observed=observed,
                    evaluated_field=field,
                    reason=reason,
                )
            )
            domain_passed = domain_passed and passed
        domain_outcomes[domain] = domain_passed
    return {
        "passed": all(domain_outcomes.values()) and bool(domain_outcomes),
        "domainOutcomes": domain_outcomes,
        "decisions": [decision.public() for decision in decisions],
        "policy": "ALL_DOMAINS_AND_ALL_HARD_GATES_MUST_PASS",
    }
