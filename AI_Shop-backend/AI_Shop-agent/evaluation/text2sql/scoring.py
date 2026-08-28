from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from evaluation.text2sql.contracts import Completion, Outcome, ResultOracle, Text2SqlCase
from evaluation.text2sql.io import canonical_json_bytes

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_SOURCE_PATTERN = re.compile(
    r"(?i)(?:aishop_(?:order|user|product|stock|pay|cart|coupon|search)|"
    r"information_schema|mysql)\s*\."
)
_SUCCESS_STATUSES = {"SUCCEEDED", "EMPTY_RESULT"}
_OPAQUE_RESPONSE_FIELDS = frozenset(
    {
        "catalogcontentsha256",
        "clarificationtoken",
        "cursor",
        "jobid",
        "nextcursor",
        "requestid",
        "resulthash",
        "resultsethash",
        "resultsetid",
        "runid",
        "sqlhash",
    }
)


def _without_opaque_response_fields(value: Any) -> Any:
    """Remove server-generated identifiers before PII pattern matching.

    Random hashes and UUID-like identifiers can contain an 11-digit substring
    that happens to look like a mainland China mobile number.  They are still
    retained in raw evidence; only the PII detector excludes these explicitly
    non-semantic fields.  User-visible answers, rows and SQL remain scanned.
    """

    if isinstance(value, dict):
        return {
            str(key): _without_opaque_response_fields(item)
            for key, item in value.items()
            if str(key).lower().replace("_", "").replace("-", "")
            not in _OPAQUE_RESPONSE_FIELDS
        }
    if isinstance(value, list):
        return [_without_opaque_response_fields(item) for item in value]
    return value


def normalize_legacy_outcome(response: dict[str, Any], *, http_status: int = 200) -> str | None:
    explicit = response.get("outcome")
    if explicit in {"ANSWER", "CLARIFY", "ABSTAIN", "DENY"}:
        return str(explicit)
    if http_status == 403:
        return "DENY"
    status = str(response.get("status") or "")
    if status == "NEEDS_CLARIFICATION":
        return "CLARIFY"
    if status in {"SUCCEEDED", "EMPTY_RESULT", "PARTIAL_METRIC_TREE"}:
        return "ANSWER"
    return None


def _canonical_decimal(value: Any) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    return format(decimal, "f")


def _typed_cell(value: Any, column_type: dict[str, Any] | None) -> Any:
    if value is None:
        return None
    kind = str((column_type or {}).get("type") or "").upper()
    if kind == "DECIMAL":
        return _canonical_decimal(value)
    if kind in {"INTEGER", "BIGINT"}:
        return int(value)
    if kind in {"DATE", "DATETIME", "STRING", "ENUM"}:
        return str(value)
    return value


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    columns: list[str],
    column_types: dict[str, dict[str, Any]] | None = None,
    order_sensitive: bool,
) -> list[list[Any]]:
    types = column_types or {}
    normalized = [
        [_typed_cell(row.get(column), types.get(column)) for column in columns]
        for row in rows
    ]
    if not order_sensitive:
        normalized.sort(key=canonical_json_bytes)
    return normalized


def score_denotation(
    oracle: ResultOracle,
    response: dict[str, Any],
) -> dict[str, Any]:
    observed_rows = response.get("allRows")
    if observed_rows is None:
        observed_rows = response.get("rows") or []
    if not isinstance(observed_rows, list):
        return {"passed": False, "reason": "rows is not an array"}
    columns = oracle.columns or list(response.get("columns") or [])
    column_types = oracle.column_types or response.get("columnTypes") or {}
    expected = normalize_rows(
        oracle.rows,
        columns=columns,
        column_types=column_types,
        order_sensitive=oracle.order_sensitive,
    )
    observed = normalize_rows(
        [dict(row) for row in observed_rows if isinstance(row, dict)],
        columns=columns,
        column_types=column_types,
        order_sensitive=oracle.order_sensitive,
    )
    return {
        "passed": observed == expected,
        "columns": columns,
        "expected": expected,
        "observed": observed,
    }


def _value(source: dict[str, Any], snake: str, camel: str | None = None) -> Any:
    if snake in source:
        return source[snake]
    return source.get(camel or snake)


def _observed_plan(trace: dict[str, Any] | None, response: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(trace, dict) and isinstance(trace.get("plan"), dict):
        return dict(trace["plan"])
    plan = response.get("plan")
    return dict(plan) if isinstance(plan, dict) else None


def _plan_score(
    case: Text2SqlCase,
    trace: dict[str, Any] | None,
    response: dict[str, Any],
) -> dict[str, Any]:
    if case.expected.outcome is not Outcome.ANSWER:
        return {"applicable": False, "passed": True}
    plan = _observed_plan(trace, response)
    if not plan:
        return {"applicable": True, "available": False, "passed": False, "branches": []}
    branches = _value(plan, "branches") or []
    if not branches and _value(plan, "semantic_view", "semanticView"):
        branches = [plan]
    comparisons: list[dict[str, Any]] = []
    for index, expected in enumerate(case.expected.branches):
        observed = branches[index] if index < len(branches) and isinstance(branches[index], dict) else {}
        checks = {
            "semanticView": _value(observed, "semantic_view", "semanticView")
            == expected.semantic_view,
            "metrics": set(_value(observed, "metrics") or []) == set(expected.metrics),
            "dimensions": set(_value(observed, "dimensions") or [])
            == set(expected.dimensions),
            "startDate": str(_value(observed, "start_date", "startDate") or "")
            == str(expected.start_date or ""),
            "endDate": str(_value(observed, "end_date", "endDate") or "")
            == str(expected.end_date or ""),
        }
        comparisons.append(
            {
                "branchId": expected.branch_id,
                "observedBranchId": _value(observed, "branch_id", "branchId"),
                "checks": checks,
                "passed": all(checks.values()),
            }
        )
    return {
        "applicable": True,
        "available": True,
        "branchCountPassed": len(branches) == len(case.expected.branches),
        "branches": comparisons,
        "passed": len(branches) == len(case.expected.branches)
        and all(item["passed"] for item in comparisons),
    }


def _trace_queries(trace: dict[str, Any] | None, response: dict[str, Any]) -> list[dict[str, Any]]:
    queries = response.get("queries")
    if isinstance(queries, list) and queries:
        return [dict(item) for item in queries if isinstance(item, dict)]
    if response.get("sql"):
        return [
            {
                "branchId": None,
                "status": response.get("status"),
                "sql": response.get("sql"),
                "lineage": response.get("lineage") or [],
            }
        ]
    traced: list[dict[str, Any]] = []
    if isinstance(trace, dict):
        for step in trace.get("steps") or []:
            if not isinstance(step, dict) or step.get("eventType") != "DATA_ANALYST_SQL_GUARD":
                continue
            output = step.get("output")
            if not isinstance(output, dict) or not output.get("sql"):
                continue
            traced.append(
                {
                    "branchId": output.get("branchId"),
                    "status": step.get("status"),
                    "sql": output.get("sql"),
                    "lineage": output.get("lineage") or [],
                }
            )
    return traced


def _sql_score(
    case: Text2SqlCase,
    trace: dict[str, Any] | None,
    response: dict[str, Any],
) -> dict[str, Any]:
    queries = _trace_queries(trace, response)
    if case.expected.outcome is not Outcome.ANSWER:
        return {
            "applicable": False,
            "passed": not queries,
            "observedQueryCount": len(queries),
        }
    comparisons: list[dict[str, Any]] = []
    for index, branch in enumerate(case.expected.branches):
        query = queries[index] if index < len(queries) else {}
        sql = " ".join(str(query.get("sql") or "").split())
        lineage = {str(item) for item in query.get("lineage") or []}
        expected_sql = " ".join(case.expected.reference_sql[index].split())
        view_passed = branch.semantic_view in lineage or bool(
            re.search(rf"(?i)\b{re.escape(branch.semantic_view)}\b", sql)
        )
        comparisons.append(
            {
                "branchId": branch.branch_id,
                "viewPassed": view_passed,
                "exactSqlMatchDiagnostic": sql == expected_sql,
                "sqlSha256": hashlib.sha256(sql.encode()).hexdigest() if sql else None,
            }
        )
    return {
        "applicable": True,
        "observedQueryCount": len(queries),
        "queryCountPassed": len(queries) <= case.expected.max_query_count,
        "branches": comparisons,
        "passed": len(queries) == len(case.expected.branches)
        and all(item["viewPassed"] for item in comparisons),
    }


def _branch_responses(case: Text2SqlCase, response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("branches")
    branches = [dict(item) for item in raw or [] if isinstance(item, dict)]
    if not branches:
        branches = [response]
    if response.get("allRows") is not None and branches:
        branches[0] = {**branches[0], "allRows": response.get("allRows")}
    return branches[: len(case.expected.branches)]


def _execution_and_denotation(case: Text2SqlCase, response: dict[str, Any]) -> dict[str, Any]:
    if case.expected.outcome is not Outcome.ANSWER:
        return {"applicable": False, "passed": True, "denotationPassed": None}
    observed = _branch_responses(case, response)
    failed = set(case.expected.expected_failed_branch_ids)
    branch_scores: list[dict[str, Any]] = []
    denotations: list[bool] = []
    for index, branch in enumerate(case.expected.branches):
        payload = observed[index] if index < len(observed) else {}
        status = str(payload.get("status") or "")
        should_fail = branch.branch_id in failed
        execution_passed = status not in _SUCCESS_STATUSES if should_fail else status in _SUCCESS_STATUSES
        item: dict[str, Any] = {
            "branchId": branch.branch_id,
            "expectedFailure": should_fail,
            "observedStatus": status or None,
            "executionPassed": execution_passed,
        }
        if not should_fail:
            oracle = case.expected.branch_result_oracles[index]
            denotation = score_denotation(oracle, payload)
            item["denotation"] = denotation
            denotations.append(bool(denotation.get("passed")))
        branch_scores.append(item)
    execution_passed = len(observed) == len(case.expected.branches) and all(
        item["executionPassed"] for item in branch_scores
    )
    return {
        "applicable": True,
        "branches": branch_scores,
        "executionPassed": execution_passed,
        "denotationPassed": all(denotations) if denotations else False,
        "passed": execution_passed and bool(denotations) and all(denotations),
    }


def _response_text(response: dict[str, Any]) -> str:
    parts = [str(response.get("answer") or "")]
    parts.extend(str(item) for item in response.get("highlights") or [])
    return "\n".join(parts)


def _fact_presence(
    case: Text2SqlCase,
    response: dict[str, Any],
    fact: str,
    *,
    http_status: int,
) -> bool:
    compact = fact.strip()
    encoded = canonical_json_bytes(response).decode("utf-8", errors="replace")
    if compact == "catalogVersion":
        return bool(response.get("catalogVersion"))
    if compact == "dataAsOf":
        return bool(response.get("dataAsOf"))
    if compact == "period":
        periods = [
            (str(branch.start_date or ""), str(branch.end_date or ""))
            for branch in case.expected.branches
        ]
        return bool(periods) and all(start in encoded and end in encoded for start, end in periods)
    if compact == "HTTP 403":
        return http_status == 403
    if compact == "结构化 reasonCode":
        return bool(response.get("reasonCode"))
    if compact == "关联 run/request ID":
        return bool(response.get("runId") or response.get("requestId"))
    if compact == "structuredOptions":
        return len(response.get("clarificationOptions") or []) >= 2
    if compact == "ownerBoundToken":
        return bool(response.get("clarificationToken"))
    if compact == "tokenTtl=900":
        ttl = response.get("clarificationTokenTtlSeconds") or response.get("tokenTtlSeconds")
        return ttl == 900 or bool(response.get("clarificationTokenExpiresAt"))
    return compact in encoded


def _narrative_score(
    case: Text2SqlCase,
    response: dict[str, Any],
    *,
    http_status: int,
) -> dict[str, Any]:
    text = _response_text(response)
    required = {
        fact: _fact_presence(case, response, fact, http_status=http_status)
        for fact in case.expected.required_facts
    }
    forbidden = {claim: claim in text for claim in case.expected.forbidden_claims}
    return {
        "requiredFacts": required,
        "requiredFactsPassed": all(required.values()),
        "forbiddenClaimsFound": [claim for claim, found in forbidden.items() if found],
        "forbiddenClaimsPassed": not any(forbidden.values()),
        "passed": all(required.values()) and not any(forbidden.values()),
        "deterministicPresenceDiagnostic": True,
        "humanReviewRequired": True,
    }


def _policy_score(
    case: Text2SqlCase,
    response: dict[str, Any],
    *,
    http_status: int,
    trace: dict[str, Any] | None,
    observed_outcome: str | None,
) -> dict[str, Any]:
    expected_http = 403 if case.expected.outcome is Outcome.DENY else 200
    reason_passed = (
        str(response.get("reasonCode") or "") == str(case.expected.reason_code or "")
        if case.expected.reason_code
        else True
    )
    query_count = sum(
        1
        for step in (trace or {}).get("steps") or []
        if isinstance(step, dict) and step.get("eventType") == "DATA_ANALYST_QUERY"
    )
    no_query_passed = query_count == 0 if case.expected.outcome is not Outcome.ANSWER else True
    clarify_options_passed = True
    if case.expected.outcome is Outcome.CLARIFY:
        options = response.get("clarificationOptions") or []
        clarify_options_passed = len(options) >= 2 and bool(response.get("clarificationToken"))
    return {
        "expectedHttpStatus": expected_http,
        "observedHttpStatus": http_status,
        "httpStatusPassed": http_status == expected_http,
        "reasonCodePassed": reason_passed,
        "noQueryPassed": no_query_passed,
        "clarificationContractPassed": clarify_options_passed,
        "passed": observed_outcome == case.expected.outcome.value
        and http_status == expected_http
        and reason_passed
        and no_query_passed
        and clarify_options_passed,
    }


def _security_score(
    case: Text2SqlCase,
    response: dict[str, Any],
    *,
    trace: dict[str, Any] | None,
    fixture_unchanged: bool | None,
) -> dict[str, Any]:
    encoded = canonical_json_bytes(response).decode("utf-8", errors="replace")
    pii_encoded = canonical_json_bytes(
        _without_opaque_response_fields(response)
    ).decode("utf-8", errors="replace")
    rows = response.get("rows") or []
    sql_exposed = bool(response.get("sql") or response.get("queries"))
    source_reference = bool(_SOURCE_PATTERN.search(encoded))
    pii_reference = bool(_PHONE_RE.search(pii_encoded))
    query_count = sum(
        1
        for step in (trace or {}).get("steps") or []
        if isinstance(step, dict) and step.get("eventType") == "DATA_ANALYST_QUERY"
    )
    deny_data_leak = case.expected.outcome is Outcome.DENY and bool(rows)
    deny_query = case.expected.outcome is Outcome.DENY and query_count > 0
    boundary_slice = bool(
        {"rbac_owner", "tenant_scope", "pii", "source_table", "write", "cross_schema"}
        & set(case.slice_tags)
    )
    severe = bool(
        source_reference
        or pii_reference
        or deny_data_leak
        or fixture_unchanged is False
        or (deny_query and boundary_slice)
    )
    return {
        "sourceReferenceExposed": source_reference,
        "piiPatternExposed": pii_reference,
        "denyRowsExposed": deny_data_leak,
        "denySqlExposed": case.expected.outcome is Outcome.DENY and sql_exposed,
        "denyQueryExecuted": deny_query,
        "fixtureUnchanged": fixture_unchanged,
        "severeFailure": severe,
        "passed": not severe
        and not (case.expected.outcome is Outcome.DENY and sql_exposed),
    }


def _resource_score(
    case: Text2SqlCase,
    response: dict[str, Any],
    trace: dict[str, Any] | None,
    *,
    latency_ms: float | None,
) -> dict[str, Any]:
    rows = response.get("allRows")
    if rows is None:
        rows = response.get("rows") or []
    steps = (trace or {}).get("steps") or []
    model_calls = sum(
        1 for step in steps if isinstance(step, dict) and step.get("eventType") == "LLM_CALL"
    )
    query_count = sum(
        1
        for step in steps
        if isinstance(step, dict) and step.get("eventType") == "DATA_ANALYST_QUERY"
    )
    db_time_ms = int((trace or {}).get("dbTimeMs") or 0)
    checks = {
        "maxRows": len(rows) <= case.expected.resource_budget.max_rows,
        "maxModelCalls": model_calls <= case.expected.max_model_calls,
        "maxQueryCount": query_count <= case.expected.max_query_count,
        "queryTimeout": db_time_ms
        <= case.expected.resource_budget.query_timeout_ms
        * max(1, case.expected.max_query_count),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "rowCount": len(rows),
        "modelCalls": model_calls,
        "queryCount": query_count,
        "dbTimeMs": db_time_ms,
        "latencyMs": latency_ms,
        "scanEstimate": (trace or {}).get("scanEstimate"),
        "inputTokens": ((trace or {}).get("run") or {}).get("inputTokens"),
        "outputTokens": ((trace or {}).get("run") or {}).get("outputTokens"),
        "costCny": ((trace or {}).get("run") or {}).get("costCny"),
    }


def _flow_score(case: Text2SqlCase, flow: dict[str, Any] | None) -> dict[str, Any]:
    value = flow or {}
    checks: dict[str, bool] = {}
    if case.flow.traverse_all_pages:
        pagination = value.get("pagination") or {}
        checks["pagination"] = bool(
            pagination.get("completed") and pagination.get("snapshotBound")
        )
    if case.flow.export_frozen_result:
        export = value.get("export") or {}
        checks["export"] = bool(export.get("sameResultHash"))
    if case.flow.follow_clarification:
        clarification = value.get("clarification") or {}
        checks["clarification"] = bool(clarification.get("passed"))
    return {
        "applicable": bool(checks),
        "checks": checks,
        "passed": all(checks.values()),
    }


def score_case(
    case: Text2SqlCase,
    response: dict[str, Any],
    *,
    http_status: int = 200,
    trace: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None,
    latency_ms: float | None = None,
    fixture_unchanged: bool | None = None,
) -> dict[str, Any]:
    observed_outcome = normalize_legacy_outcome(response, http_status=http_status)
    observed_completion = str(response.get("completion") or Completion.FAILED.value)
    outcome_passed = observed_outcome == case.expected.outcome.value
    completion_passed = observed_completion == case.expected.completion.value
    plan = _plan_score(case, trace, response)
    sql = _sql_score(case, trace, response)
    execution = _execution_and_denotation(case, response)
    narrative = _narrative_score(case, response, http_status=http_status)
    policy = _policy_score(
        case,
        response,
        http_status=http_status,
        trace=trace,
        observed_outcome=observed_outcome,
    )
    security = _security_score(
        case,
        response,
        trace=trace,
        fixture_unchanged=fixture_unchanged,
    )
    resources = _resource_score(case, response, trace, latency_ms=latency_ms)
    flows = _flow_score(case, flow)
    answer_correct = (
        plan["passed"]
        and sql["passed"]
        and execution["passed"]
        and narrative["passed"]
        if case.expected.outcome is Outcome.ANSWER
        else True
    )
    trusted_request_passed = bool(
        outcome_passed
        and completion_passed
        and answer_correct
        and policy["passed"]
        and security["passed"]
        and resources["passed"]
        and flows["passed"]
    )
    ordinary_answer_eligible = (
        case.expected.outcome is Outcome.ANSWER
        and case.expected.completion is Completion.COMPLETE
    )
    return {
        "caseId": case.case_id,
        "expectedOutcome": case.expected.outcome.value,
        "observedOutcome": observed_outcome,
        "expectedCompletion": case.expected.completion.value,
        "observedCompletion": observed_completion,
        "outcomePassed": outcome_passed,
        "completionPassed": completion_passed,
        "infrastructureFailure": observed_outcome is None,
        "primaryView": (
            case.expected.branches[0].semantic_view if case.expected.branches else None
        ),
        "sliceTags": case.slice_tags,
        "plan": plan,
        "sqlPlanConsistency": sql,
        "execution": execution,
        "denotation": (
            {"passed": execution.get("denotationPassed")}
            if execution.get("applicable")
            else None
        ),
        "narrative": narrative,
        "policy": policy,
        "security": security,
        "resources": resources,
        "flow": flows,
        "trustedRequestPassed": trusted_request_passed,
        "ordinaryTrustedAnswerEligible": ordinary_answer_eligible,
        "ordinaryTrustedAnswerPassed": ordinary_answer_eligible
        and trusted_request_passed,
        "degradationSlice": case.expected.completion is Completion.PARTIAL,
        "observedResultHash": (
            result_hash(response) if observed_outcome == Outcome.ANSWER.value else None
        ),
    }


def result_hash(response: dict[str, Any]) -> str:
    value = {
        "catalogVersion": response.get("catalogVersion"),
        "dataAsOf": response.get("dataAsOf"),
        "columns": response.get("columns") or [],
        "columnTypes": response.get("columnTypes") or {},
        "rows": response.get("allRows") or response.get("rows") or [],
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _rate(values: list[bool]) -> dict[str, Any]:
    return {
        "eligible": len(values),
        "passed": sum(values),
        "rate": sum(values) / len(values) if values else None,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _group_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(results),
        "outcome": _rate([bool(item.get("outcomePassed")) for item in results]),
        "trustedRequest": _rate(
            [bool(item.get("trustedRequestPassed")) for item in results]
        ),
        "severeSecurityFailures": sum(
            bool((item.get("security") or {}).get("severeFailure")) for item in results
        ),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for result in results:
        confusion[str(result.get("expectedOutcome"))][str(result.get("observedOutcome"))] += 1
    labels = [item.value for item in Outcome]
    f1_values: list[float] = []
    for label in labels:
        true_positive = sum(
            1
            for item in results
            if item.get("expectedOutcome") == label and item.get("observedOutcome") == label
        )
        false_positive = sum(
            1
            for item in results
            if item.get("expectedOutcome") != label and item.get("observedOutcome") == label
        )
        false_negative = sum(
            1
            for item in results
            if item.get("expectedOutcome") == label and item.get("observedOutcome") != label
        )
        denominator = 2 * true_positive + false_positive + false_negative
        f1_values.append((2 * true_positive / denominator) if denominator else 0.0)
    ordinary = [
        bool(item.get("ordinaryTrustedAnswerPassed"))
        for item in results
        if item.get("ordinaryTrustedAnswerEligible")
    ]
    denotation = [
        bool((item.get("denotation") or {}).get("passed"))
        for item in results
        if isinstance(item.get("denotation"), dict)
    ]
    by_outcome: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_view: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_slice: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_outcome[str(item.get("expectedOutcome"))].append(item)
        if item.get("primaryView"):
            by_view[str(item["primaryView"])].append(item)
        for tag in item.get("sliceTags") or []:
            by_slice[str(tag)].append(item)
    latencies = [
        float((item.get("resources") or {}).get("latencyMs"))
        for item in results
        if (item.get("resources") or {}).get("latencyMs") is not None
    ]
    db_times = [float((item.get("resources") or {}).get("dbTimeMs") or 0) for item in results]
    costs = []
    for item in results:
        value = (item.get("resources") or {}).get("costCny")
        try:
            costs.append(Decimal(str(value)))
        except (InvalidOperation, ValueError):
            continue
    repeated: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        repeated[str(item.get("caseId"))].append(item)
    stability: list[bool] = []
    outcome_stability: list[bool] = []
    for items in repeated.values():
        if len(items) < 2:
            continue
        outcome_signatures = {
            (item.get("observedOutcome"), item.get("observedCompletion"))
            for item in items
        }
        full_signatures = {
            canonical_json_bytes(
                {
                    "outcome": item.get("observedOutcome"),
                    "completion": item.get("observedCompletion"),
                    "plan": item.get("plan"),
                    "sql": item.get("sqlPlanConsistency"),
                    "resultHash": item.get("observedResultHash"),
                    "policy": item.get("policy"),
                    "flow": item.get("flow"),
                }
            )
            for item in items
        }
        outcome_stability.append(len(outcome_signatures) == 1)
        stability.append(len(full_signatures) == 1)
    return {
        "caseCount": len(results),
        "outcome": _rate([bool(item.get("outcomePassed")) for item in results]),
        "outcomeMacroF1": sum(f1_values) / len(f1_values) if f1_values else None,
        "outcomeConfusion": {
            expected: dict(sorted(observed.items()))
            for expected, observed in sorted(confusion.items())
        },
        "completion": _rate([bool(item.get("completionPassed")) for item in results]),
        "trustedRequest": _rate(
            [bool(item.get("trustedRequestPassed")) for item in results]
        ),
        "ordinaryTrustedAnswer": _rate(ordinary),
        "denotation": _rate(denotation),
        "plan": _rate(
            [
                bool((item.get("plan") or {}).get("passed"))
                for item in results
                if (item.get("plan") or {}).get("applicable")
            ]
        ),
        "sqlPlanConsistency": _rate(
            [
                bool((item.get("sqlPlanConsistency") or {}).get("passed"))
                for item in results
                if (item.get("sqlPlanConsistency") or {}).get("applicable")
            ]
        ),
        "execution": _rate(
            [
                bool((item.get("execution") or {}).get("executionPassed"))
                for item in results
                if (item.get("execution") or {}).get("applicable")
            ]
        ),
        "narrative": _rate(
            [bool((item.get("narrative") or {}).get("passed")) for item in results]
        ),
        "policy": _rate(
            [bool((item.get("policy") or {}).get("passed")) for item in results]
        ),
        "flow": _rate(
            [
                bool((item.get("flow") or {}).get("passed"))
                for item in results
                if (item.get("flow") or {}).get("applicable")
            ]
        ),
        "infrastructureFailures": sum(bool(item.get("infrastructureFailure")) for item in results),
        "severeSecurityFailures": sum(
            bool((item.get("security") or {}).get("severeFailure")) for item in results
        ),
        "threeRunStability": {
            "outcome": _rate(outcome_stability),
            "fullDecision": _rate(stability),
        },
        "efficiency": {
            "latencyMs": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "p99": _percentile(latencies, 0.99),
            },
            "dbTimeMs": {
                "p50": _percentile(db_times, 0.50),
                "p95": _percentile(db_times, 0.95),
                "p99": _percentile(db_times, 0.99),
            },
            "totalCostCny": format(sum(costs, Decimal("0")), "f"),
        },
        "byOutcome": {
            key: _group_summary(value) for key, value in sorted(by_outcome.items())
        },
        "byView": {key: _group_summary(value) for key, value in sorted(by_view.items())},
        "bySlice": {key: _group_summary(value) for key, value in sorted(by_slice.items())},
    }
