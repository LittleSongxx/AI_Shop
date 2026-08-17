#!/usr/bin/env python3
"""Run the frozen task-success suite through the real AI_Shop serving stack.

This runner deliberately has no simulator and no in-process graph shortcut. A
case enters through ``POST /api/agent/sendMessage`` and is graded only from the
persisted Episode, pending-action row, and API result. Missing dependencies,
credentials, fixture bindings, or provider evidence fail closed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.pool import close_pool, init_pool  # noqa: E402
from app.services.episode_query_service import episode_query_service  # noqa: E402
from app.services.message_service import agent_message_service  # noqa: E402
from app.services.pending_action_store import pending_action_store  # noqa: E402
from app.utils.biz_payload import extract_act_token_id  # noqa: E402

SUITE = "task-success-live-v1"
EXECUTION_MODE = "LIVE_FULL_STACK"
DEFAULT_DATASET = Path(__file__).with_name("task_success_v1.jsonl")
DEFAULT_LOCK = Path(__file__).with_name("task_success_v1.lock.json")
RESULTS_ROOT = Path(__file__).with_name("results") / SUITE
TERMINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "HANDOFF", "DEGRADED"})
_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_SECRET_KEYS = frozenset({"authToken", "token", "actionToken"})


class EvaluationContractError(ValueError):
    """The frozen data, bindings, or live-stack contract is invalid."""


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationContractError(
                f"dataset line {line_number} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(value, dict):
            raise EvaluationContractError(f"dataset line {line_number} must be a JSON object")
        cases.append(value)
    return cases


def validate_contract(
    cases: list[dict[str, Any]], dataset: Path, lock_path: Path
) -> dict[str, Any]:
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationContractError(f"cannot read dataset lock: {lock_path}") from exc
    errors: list[str] = []
    digest = dataset_sha256(dataset)
    if lock.get("schemaVersion") != 2:
        errors.append("lock schemaVersion must be 2")
    if lock.get("suite") != SUITE:
        errors.append(f"lock suite must be {SUITE}")
    if lock.get("datasetSha256") != digest:
        errors.append("dataset SHA-256 differs from the frozen lock")
    if int(lock.get("caseCount") or 0) != len(cases):
        errors.append("dataset case count differs from the frozen lock")
    if not 30 <= len(cases) <= 50:
        errors.append("the interview task suite must contain 30 to 50 cases")

    ids = [str(case.get("id") or "") for case in cases]
    if not all(ids) or len(ids) != len(set(ids)):
        errors.append("case IDs must be non-empty and unique")
    subsets: set[str] = set()
    for case in cases:
        case_id = str(case.get("id") or "<missing-id>")
        subset = str(case.get("subset") or "")
        subsets.add(subset)
        if case.get("schemaVersion") != 1:
            errors.append(f"{case_id}: schemaVersion must be 1")
        if not subset:
            errors.append(f"{case_id}: subset is required")
        input_data = case.get("input")
        expected = case.get("expected")
        if not isinstance(input_data, dict):
            errors.append(f"{case_id}: input must be an object")
            continue
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: expected must be an object")
            continue
        if not str(input_data.get("message") or "").strip():
            errors.append(f"{case_id}: input.message is required")
        if not str(input_data.get("authToken") or "").strip():
            errors.append(f"{case_id}: input.authToken placeholder is required")
        if not str(input_data.get("expectedUserId") or "").strip():
            errors.append(f"{case_id}: input.expectedUserId placeholder is required")
        if not any(
            key in expected
            for key in (
                "terminalStatuses",
                "apiErrorCode",
                "requiredTools",
                "requiredEvents",
            )
        ):
            errors.append(f"{case_id}: expected has no observable success contract")

    required_subsets = set(lock.get("requiredSubsets") or [])
    missing_subsets = sorted(required_subsets - subsets)
    if missing_subsets:
        errors.append(f"required subsets are missing: {missing_subsets}")
    thresholds = lock.get("thresholds")
    if not isinstance(thresholds, dict):
        errors.append("lock thresholds must be an object")
    if errors:
        raise EvaluationContractError("task-success contract invalid:\n- " + "\n- ".join(errors))
    return {**lock, "datasetSha256": digest}


def load_bindings(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationContractError(f"cannot read bindings file: {path}") from exc
        if not isinstance(raw, dict):
            raise EvaluationContractError("bindings file must be a JSON object")
        for key, value in raw.items():
            if isinstance(value, (str, int, float)):
                values[str(key)] = str(value)
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def resolve_placeholders(value: Any, bindings: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: resolve_placeholders(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(item, bindings) for item in value]
    if not isinstance(value, str):
        return value
    missing = sorted(
        {name for name in _PLACEHOLDER.findall(value) if not str(bindings.get(name) or "").strip()}
    )
    if missing:
        raise EvaluationContractError(f"missing fixture bindings: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda match: bindings[match.group(1)], value)


def recursive_subset_mismatches(expected: Any, actual: Any, path: str = "$") -> list[str]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        mismatches: list[str] = []
        for key, expected_value in expected.items():
            if key not in actual:
                mismatches.append(f"{path}.{key}: missing")
            else:
                mismatches.extend(
                    recursive_subset_mismatches(expected_value, actual[key], f"{path}.{key}")
                )
        return mismatches
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected array, got {type(actual).__name__}"]
        if len(expected) > len(actual):
            return [f"{path}: expected at least {len(expected)} items, got {len(actual)}"]
        mismatches: list[str] = []
        for index, expected_value in enumerate(expected):
            mismatches.extend(
                recursive_subset_mismatches(expected_value, actual[index], f"{path}[{index}]")
            )
        return mismatches
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


def _ordered_subsequence(required: Iterable[str], actual: list[str]) -> bool:
    position = 0
    for expected in required:
        try:
            position = actual.index(expected, position) + 1
        except ValueError:
            return False
    return True


def _assertion(
    name: str,
    passed: bool,
    *,
    expected: Any = None,
    actual: Any = None,
    category: str = "QUALITY",
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "category": category,
        "expected": expected,
        "actual": actual,
        "detail": detail,
    }


def _tool_steps(episode: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [step for step in episode.get("steps") or [] if step.get("eventType") == "TOOL_CALL"]


def _orchestration_fact(episode: Mapping[str, Any]) -> dict[str, Any]:
    steps = [
        step
        for step in episode.get("steps") or []
        if step.get("eventType") == "ORCHESTRATION_DECISION"
    ]
    output = steps[-1].get("output") if steps else None
    return output if isinstance(output, dict) else {}


def _provider_facts(episode: Mapping[str, Any]) -> dict[str, Any]:
    llm_steps = [step for step in episode.get("steps") or [] if step.get("eventType") == "LLM_CALL"]
    model_names = sorted(
        {str(step.get("modelName")) for step in llm_steps if step.get("modelName")}
    )
    failed = [step for step in llm_steps if str(step.get("status") or "").upper() != "OK"]
    return {
        "llmCallCount": len(llm_steps),
        "modelNames": model_names,
        "failedLlmCalls": len(failed),
        "complete": not failed and all(step.get("modelName") for step in llm_steps),
    }


def _count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw_count in value.items():
        if isinstance(raw_count, bool):
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            result[str(key)] = count
    return result


def _merge_counts(target: dict[str, int], incoming: Mapping[str, int]) -> None:
    for key, count in incoming.items():
        target[key] = target.get(key, 0) + count


def _rag_runtime_records(step: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    output = step.get("output")
    trace = output.get("trace") if isinstance(output, dict) else None
    if not isinstance(trace, dict):
        return []
    retrievals = trace.get("retrievals")
    candidates = retrievals if isinstance(retrievals, list) and retrievals else [trace]
    records: list[Mapping[str, Any]] = []
    for candidate in candidates:
        runtime = candidate.get("runtime") if isinstance(candidate, dict) else None
        if isinstance(runtime, dict):
            records.append(runtime)
    return records


def _rag_provider_facts(episode: Mapping[str, Any]) -> dict[str, Any]:
    rag_steps = [
        step for step in episode.get("steps") or [] if step.get("eventType") == "RAG_RETRIEVAL"
    ]
    calls: dict[str, int] = {}
    successes: dict[str, int] = {}
    failures: dict[str, int] = {}
    cache_hits: dict[str, int] = {}
    fallbacks: set[str] = set()
    routes: set[str] = set()
    rerank_skips: set[str] = set()
    runtime_record_count = 0
    missing_runtime_events = 0

    for step in rag_steps:
        records = _rag_runtime_records(step)
        if not records:
            missing_runtime_events += 1
            continue
        runtime_record_count += len(records)
        for runtime in records:
            _merge_counts(calls, _count_map(runtime.get("providerCalls")))
            _merge_counts(successes, _count_map(runtime.get("providerSuccesses")))
            _merge_counts(failures, _count_map(runtime.get("providerFailures")))
            _merge_counts(cache_hits, _count_map(runtime.get("providerCacheHits")))
            fallbacks.update(str(item) for item in runtime.get("fallbacks") or [] if item)
            if runtime.get("route"):
                routes.add(str(runtime["route"]))
            observations = runtime.get("observations")
            if isinstance(observations, dict) and observations.get("rerankSkipped"):
                rerank_skips.add(str(observations["rerankSkipped"]))

    tracked_providers = ("embedding", "rerank")
    balanced_attempts = all(
        failures.get(provider, 0) == 0 and calls.get(provider, 0) == successes.get(provider, 0)
        for provider in tracked_providers
    )
    vector_calls = calls.get("elasticsearchVector", 0)
    embedding_evidence = successes.get("embedding", 0) + cache_hits.get("embedding", 0)
    vector_has_embedding = vector_calls == 0 or embedding_evidence >= vector_calls
    complete = bool(rag_steps) and (
        missing_runtime_events == 0 and balanced_attempts and vector_has_embedding and not fallbacks
    )
    return {
        "retrievalEventCount": len(rag_steps),
        "runtimeRecordCount": runtime_record_count,
        "missingRuntimeEventCount": missing_runtime_events,
        "providerCalls": dict(sorted(calls.items())),
        "providerSuccesses": dict(sorted(successes.items())),
        "providerFailures": dict(sorted(failures.items())),
        "providerCacheHits": dict(sorted(cache_hits.items())),
        "fallbacks": sorted(fallbacks),
        "routes": sorted(routes),
        "rerankSkips": sorted(rerank_skips),
        "complete": complete,
    }


def evaluate_episode(
    case: Mapping[str, Any],
    episode: Mapping[str, Any],
    *,
    pending: Mapping[str, Any] | None = None,
    expected_configured_mode: str | None = None,
) -> dict[str, Any]:
    expected = case["expected"]
    steps = list(episode.get("steps") or [])
    events = [str(step.get("eventType") or "") for step in steps]
    tool_steps = _tool_steps(episode)
    tools = [str(step.get("toolName") or "") for step in tool_steps]
    conversation = episode.get("conversation") or {}
    assistant = str(conversation.get("assistantMessage") or "")
    source_refs = conversation.get("sourceRefs") or []
    orchestration = _orchestration_fact(episode)
    provider = _provider_facts(episode)
    rag_provider = _rag_provider_facts(episode)
    provider["rag"] = rag_provider
    assertions: list[dict[str, Any]] = []

    terminal = [str(value) for value in expected.get("terminalStatuses") or []]
    if terminal:
        assertions.append(
            _assertion(
                "terminal_status",
                str(episode.get("status")) in terminal,
                expected=terminal,
                actual=episode.get("status"),
                category="EXECUTION",
            )
        )

    required_events = [str(value) for value in expected.get("requiredEvents") or []]
    for event in required_events:
        assertions.append(
            _assertion(
                f"event:{event}",
                event in events,
                expected=True,
                actual=event in events,
                category="TRACE",
            )
        )

    required_tools = [str(value) for value in expected.get("requiredTools") or []]
    assertions.append(
        _assertion(
            "tool_sequence",
            _ordered_subsequence(required_tools, tools),
            expected=required_tools,
            actual=tools,
            category="TOOL_SELECTION",
        )
    )
    forbidden_tools = [str(value) for value in expected.get("forbiddenTools") or []]
    forbidden_seen = sorted(set(forbidden_tools) & set(tools))
    if forbidden_tools:
        assertions.append(
            _assertion(
                "forbidden_tools",
                not forbidden_seen,
                expected=[],
                actual=forbidden_seen,
                category="SAFETY",
            )
        )
    if expected.get("noWriteTools") is True:
        writes = [name for name in tools if name.startswith("PROPOSE_")]
        assertions.append(
            _assertion(
                "no_write_tools",
                not writes,
                expected=[],
                actual=writes,
                category="SAFETY",
            )
        )

    for index, contract in enumerate(expected.get("requiredToolArgs") or []):
        tool_name = str(contract.get("tool") or "")
        subset = contract.get("subset") or {}
        candidates = [step for step in tool_steps if step.get("toolName") == tool_name]
        candidate_mismatches: list[list[str]] = []
        for step in candidates:
            observable = step.get("input") or {}
            args = observable.get("args") if isinstance(observable, dict) else None
            mismatches = recursive_subset_mismatches(subset, args)
            candidate_mismatches.append(mismatches)
        passed = bool(candidates) and any(not item for item in candidate_mismatches)
        assertions.append(
            _assertion(
                f"tool_args:{index}:{tool_name}",
                passed,
                expected="recursive subset match",
                actual="matched" if passed else "missing or mismatched",
                category="TOOL_PARAMETERS",
            )
        )

    biz_types = [str(value) for value in expected.get("bizTypes") or []]
    if biz_types:
        assertions.append(
            _assertion(
                "business_response_type",
                conversation.get("bizType") in biz_types,
                expected=biz_types,
                actual=conversation.get("bizType"),
                category="FINAL_STATE",
            )
        )
    minimum_refs = expected.get("sourceRefsMin")
    if minimum_refs is not None:
        assertions.append(
            _assertion(
                "source_references",
                len(source_refs) >= int(minimum_refs),
                expected=f">={minimum_refs}",
                actual=len(source_refs),
                category="GROUNDING",
            )
        )
    for text in expected.get("assistantContains") or []:
        assertions.append(
            _assertion(
                f"assistant_contains:{text}",
                str(text) in assistant,
                expected=True,
                actual=str(text) in assistant,
                category="FINAL_STATE",
            )
        )
    for text in expected.get("assistantNotContains") or []:
        assertions.append(
            _assertion(
                f"assistant_excludes:{text}",
                str(text) not in assistant,
                expected=False,
                actual=str(text) in assistant,
                category="SAFETY",
            )
        )

    adaptive_modes = [str(value) for value in expected.get("orchestrationModes") or []]
    if adaptive_modes and expected_configured_mode in {None, "adaptive"}:
        assertions.append(
            _assertion(
                "adaptive_orchestration",
                orchestration.get("mode") in adaptive_modes,
                expected=adaptive_modes,
                actual=orchestration.get("mode"),
                category="ORCHESTRATION",
            )
        )
    if expected_configured_mode and orchestration:
        assertions.append(
            _assertion(
                "configured_orchestration_mode",
                orchestration.get("configuredMode") == expected_configured_mode,
                expected=expected_configured_mode,
                actual=orchestration.get("configuredMode"),
                category="ORCHESTRATION",
            )
        )

    if expected.get("modelRequired") is True:
        assertions.append(
            _assertion(
                "real_model_observed",
                provider["llmCallCount"] > 0 and provider["complete"],
                expected="at least one successful named LLM call",
                actual=provider,
                category="PROVIDER",
            )
        )
    if provider["llmCallCount"]:
        assertions.append(
            _assertion(
                "llm_provider_complete",
                provider["complete"],
                expected=True,
                actual=provider["complete"],
                category="PROVIDER",
            )
        )

    rag_expected = "RAG_RETRIEVAL" in required_events or "SEARCH_KNOWLEDGE" in required_tools
    rag_relevant = rag_expected or rag_provider["retrievalEventCount"] > 0
    if rag_relevant:
        provider["complete"] = provider["complete"] and rag_provider["complete"]
        assertions.append(
            _assertion(
                "rag_provider_trace_complete",
                rag_provider["complete"],
                expected=(
                    "runtime trace for every retrieval, successful attempted "
                    "embedding/rerank calls, and no provider fallback"
                ),
                actual=rag_provider,
                category="PROVIDER",
            )
        )

    max_steps = expected.get("maxTraceSteps")
    if max_steps is not None:
        assertions.append(
            _assertion(
                "step_budget",
                len(steps) <= int(max_steps),
                expected=f"<={max_steps}",
                actual=len(steps),
                category="BUDGET",
            )
        )
    max_tokens = expected.get("maxTokens")
    total_tokens = int(episode.get("inputTokens") or 0) + int(episode.get("outputTokens") or 0)
    if max_tokens is not None:
        assertions.append(
            _assertion(
                "token_budget",
                total_tokens <= int(max_tokens),
                expected=f"<={max_tokens}",
                actual=total_tokens,
                category="BUDGET",
            )
        )
    max_cost = expected.get("maxCostCny")
    if max_cost is not None:
        assertions.append(
            _assertion(
                "cost_budget",
                float(episode.get("costCny") or 0) <= float(max_cost),
                expected=f"<={max_cost}",
                actual=float(episode.get("costCny") or 0),
                category="BUDGET",
            )
        )

    pending_statuses = [str(value) for value in expected.get("pendingStatuses") or []]
    if pending_statuses:
        actual_pending_status = (pending or {}).get("statusName")
        assertions.append(
            _assertion(
                "pending_action_state",
                actual_pending_status in pending_statuses,
                expected=pending_statuses,
                actual=actual_pending_status,
                category="FINAL_STATE",
            )
        )
    action_types = [str(value) for value in expected.get("actionTypes") or []]
    if action_types:
        actual_action = (pending or {}).get("actionType")
        assertions.append(
            _assertion(
                "pending_action_type",
                actual_action in action_types,
                expected=action_types,
                actual=actual_action,
                category="FINAL_STATE",
            )
        )

    return {
        "assertions": assertions,
        "taskSuccess": all(item["passed"] for item in assertions),
        "tools": tools,
        "events": events,
        "orchestration": orchestration or {"mode": "api_fast_path"},
        "provider": provider,
        "metrics": {
            "traceSteps": len(steps),
            "inputTokens": int(episode.get("inputTokens") or 0),
            "outputTokens": int(episode.get("outputTokens") or 0),
            "costCny": float(episode.get("costCny") or 0),
            "latencyMs": episode.get("latencyMs"),
            "ttftMs": episode.get("ttftMs"),
        },
    }


def evaluate_api_rejection(case: Mapping[str, Any], envelope: Mapping[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    code = envelope.get("code")
    info = str(envelope.get("info") or "")
    assertions = [
        _assertion(
            "api_rejection_code",
            code == expected.get("apiErrorCode"),
            expected=expected.get("apiErrorCode"),
            actual=code,
            category="SAFETY",
        )
    ]
    if expected.get("apiErrorContains"):
        text = str(expected["apiErrorContains"])
        assertions.append(
            _assertion(
                "api_rejection_reason",
                text in info,
                expected=text,
                actual="matched" if text in info else "not matched",
                category="SAFETY",
            )
        )
    return {
        "assertions": assertions,
        "taskSuccess": all(item["passed"] for item in assertions),
        "tools": [],
        "events": [],
        "orchestration": {"mode": "input_guard"},
        "provider": {
            "llmCallCount": 0,
            "modelNames": [],
            "failedLlmCalls": 0,
            "complete": True,
            "rag": {
                "retrievalEventCount": 0,
                "runtimeRecordCount": 0,
                "missingRuntimeEventCount": 0,
                "providerCalls": {},
                "providerSuccesses": {},
                "providerFailures": {},
                "providerCacheHits": {},
                "fallbacks": [],
                "routes": [],
                "rerankSkips": [],
                "complete": True,
            },
        },
        "metrics": {
            "traceSteps": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "costCny": 0.0,
            "latencyMs": None,
            "ttftMs": None,
        },
    }


async def _poll_episode(run_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, Any] | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await episode_query_service.detail(run_id)
        if last and str(last.get("status") or "").upper() in TERMINAL_RUN_STATUSES:
            return last
        await asyncio.sleep(0.25)
    status = (last or {}).get("status")
    raise TimeoutError(f"run did not reach a terminal state; last status={status!r}")


async def _poll_pending(
    token: str, allowed_statuses: set[str], timeout_seconds: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, Any] | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await pending_action_store.get(token)
        if last and str(last.get("statusName") or "") in allowed_statuses:
            return last
        await asyncio.sleep(0.25)
    status = (last or {}).get("statusName")
    raise TimeoutError(f"pending action did not reach expected state; last status={status!r}")


def _action_token(message: Mapping[str, Any]) -> str | None:
    raw = message.get("bizData")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and value.get("token"):
            return str(value["token"])
    return extract_act_token_id(str(message.get("assistantMessage") or ""))


async def preflight(client: httpx.AsyncClient, api_base_url: str) -> dict[str, Any]:
    ready_response = await client.get(f"{api_base_url}/health/ready")
    dependencies_response = await client.get(f"{api_base_url}/health/dependencies")
    if ready_response.status_code != 200:
        raise EvaluationContractError(
            f"Agent readiness failed closed: HTTP {ready_response.status_code}"
        )
    if dependencies_response.status_code != 200:
        raise EvaluationContractError(
            f"Agent dependency probe failed: HTTP {dependencies_response.status_code}"
        )
    ready = ready_response.json()
    dependencies = dependencies_response.json()
    failures: list[str] = []
    if ready.get("ready") is not True:
        failures.append("full serving stack is not ready")
    for key in ("llm", "embeddingProductionReady", "rerank", "javaGateway", "mcp"):
        if dependencies.get(key) is not True:
            failures.append(f"provider/dependency {key} is not production-ready")
    if failures:
        raise EvaluationContractError("live preflight failed:\n- " + "\n- ".join(failures))
    return {"readiness": ready, "dependencies": dependencies}


class LiveTaskEvaluator:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_base_url: str,
        timeout_seconds: float,
        expected_configured_mode: str | None,
    ) -> None:
        self.client = client
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.expected_configured_mode = expected_configured_mode

    async def execute(self, case: Mapping[str, Any]) -> dict[str, Any]:
        case_id = str(case["id"])
        subset = str(case["subset"])
        input_data = case["input"]
        expected = case["expected"]
        token = str(input_data["authToken"])
        expected_user_id = str(input_data["expectedUserId"])
        base = {
            "caseId": case_id,
            "subset": subset,
            "executionMode": EXECUTION_MODE,
        }
        try:
            response = await self.client.post(
                f"{self.api_base_url}/api/agent/sendMessage",
                data={"message": str(input_data["message"])},
                headers={"token": token},
            )
            response.raise_for_status()
            envelope = response.json()
            if expected.get("apiErrorCode") is not None:
                result = evaluate_api_rejection(case, envelope)
                return {**base, **result, "runId": None, "error": None}
            if envelope.get("status") != "success" or not isinstance(envelope.get("data"), dict):
                raise RuntimeError(f"sendMessage rejected: code={envelope.get('code')!r}")
            submitted = envelope["data"]
            if str(submitted.get("userId") or "") != expected_user_id:
                raise RuntimeError("auth token resolved to a different fixture user")
            run_id = str(submitted.get("runId") or "")
            message_id = int(submitted.get("messageId") or 0)
            if not run_id or not message_id:
                raise RuntimeError("sendMessage did not return runId/messageId")
            episode = await _poll_episode(run_id, self.timeout_seconds)
            message = await agent_message_service.admin_get_message(message_id)
            pending: dict[str, Any] | None = None
            action_token = _action_token(message or {})
            pending_statuses = {str(value) for value in expected.get("pendingStatuses") or []}
            if expected.get("confirmAction") is True:
                if not action_token:
                    raise RuntimeError(
                        "case expected confirmation but no durable action token exists"
                    )
                confirm_response = await self.client.post(
                    f"{self.api_base_url}/api/agent/confirmAction",
                    data={"actionToken": action_token},
                    headers={"token": token},
                )
                confirm_response.raise_for_status()
                confirm_envelope = confirm_response.json()
                if confirm_envelope.get("status") != "success":
                    raise RuntimeError(
                        f"confirmAction failed: code={confirm_envelope.get('code')!r}"
                    )
                allowed = pending_statuses or {"CONFIRMED", "FAILED"}
                pending = await _poll_pending(action_token, allowed, self.timeout_seconds)
                await asyncio.sleep(0.3)
                episode = await episode_query_service.detail(run_id) or episode
            elif action_token:
                pending = await pending_action_store.get(action_token)
            result = evaluate_episode(
                case,
                episode,
                pending=pending,
                expected_configured_mode=self.expected_configured_mode,
            )
            return {**base, **result, "runId": run_id, "error": None}
        except Exception as exc:
            return {
                **base,
                "runId": None,
                "taskSuccess": False,
                "assertions": [],
                "tools": [],
                "events": [],
                "orchestration": {},
                "provider": {
                    "llmCallCount": 0,
                    "modelNames": [],
                    "failedLlmCalls": 0,
                    "complete": False,
                    "rag": {
                        "retrievalEventCount": 0,
                        "runtimeRecordCount": 0,
                        "missingRuntimeEventCount": 0,
                        "providerCalls": {},
                        "providerSuccesses": {},
                        "providerFailures": {},
                        "providerCacheHits": {},
                        "fallbacks": [],
                        "routes": [],
                        "rerankSkips": [],
                        "complete": False,
                    },
                },
                "metrics": {
                    "traceSteps": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "costCny": 0.0,
                    "latencyMs": None,
                    "ttftMs": None,
                },
                "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
            }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile) + 0.999999) - 1))
    return round(float(ordered[index]), 3)


def aggregate(results: list[dict[str, Any]], preflight_facts: Mapping[str, Any]) -> dict[str, Any]:
    total = len(results)
    successful = sum(result.get("taskSuccess") is True for result in results)
    assertions = [item for result in results for item in result.get("assertions") or []]
    tool_assertions = [item for item in assertions if item.get("category") == "TOOL_SELECTION"]
    parameter_assertions = [
        item for item in assertions if item.get("category") == "TOOL_PARAMETERS"
    ]
    final_assertions = [item for item in assertions if item.get("category") == "FINAL_STATE"]
    safety_failures = [
        item
        for item in assertions
        if item.get("category") == "SAFETY" and item.get("passed") is not True
    ]
    execution_complete = sum(result.get("error") is None for result in results)
    provider_graded = [
        result
        for result in results
        if result.get("error") is None
        and result.get("orchestration", {}).get("mode") != "input_guard"
    ]
    provider_complete = sum(
        result.get("provider", {}).get("complete") is True for result in provider_graded
    )
    latencies = [
        float(result["metrics"]["latencyMs"])
        for result in results
        if result.get("metrics", {}).get("latencyMs") is not None
    ]
    ttfts = [
        float(result["metrics"]["ttftMs"])
        for result in results
        if result.get("metrics", {}).get("ttftMs") is not None
    ]
    total_tokens = sum(
        int(result.get("metrics", {}).get("inputTokens") or 0)
        + int(result.get("metrics", {}).get("outputTokens") or 0)
        for result in results
    )
    total_cost = sum(float(result.get("metrics", {}).get("costCny") or 0) for result in results)

    def rate(items: list[dict[str, Any]]) -> float:
        if not items:
            return 1.0
        return round(sum(item.get("passed") is True for item in items) / len(items), 4)

    return {
        "caseCount": total,
        "passedCount": successful,
        "failedCount": total - successful,
        "taskSuccessRate": round(successful / total, 4) if total else 0.0,
        "executionCompletenessRate": round(execution_complete / total, 4) if total else 0.0,
        "toolSelectionAccuracy": rate(tool_assertions),
        "toolParameterAccuracy": rate(parameter_assertions),
        "finalStateAccuracy": rate(final_assertions),
        "criticalSafetyViolationCount": len(safety_failures),
        "providerCompletenessRate": round(provider_complete / len(provider_graded), 4)
        if provider_graded
        else 1.0,
        "latencyMs": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "ttftMs": {
            "p50": _percentile(ttfts, 0.50),
            "p95": _percentile(ttfts, 0.95),
        },
        "totalTokens": total_tokens,
        "totalCostCny": round(total_cost, 6),
        "observedModels": sorted(
            {
                model
                for result in results
                for model in result.get("provider", {}).get("modelNames") or []
            }
        ),
        "preflightPassed": bool(preflight_facts),
    }


def threshold_failures(summary: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[str]:
    comparisons = (
        ("taskSuccessRate", "taskSuccessRateMin"),
        ("executionCompletenessRate", "executionCompletenessRateMin"),
        ("providerCompletenessRate", "providerCompletenessRateMin"),
    )
    failures: list[str] = []
    for metric, threshold_key in comparisons:
        minimum = float(thresholds.get(threshold_key, 0))
        actual = float(summary.get(metric) or 0)
        if actual < minimum:
            failures.append(f"{metric}={actual:.4f} < {minimum:.4f}")
    maximum_safety = int(thresholds.get("criticalSafetyViolationCountMax", 0))
    actual_safety = int(summary.get("criticalSafetyViolationCount") or 0)
    if actual_safety > maximum_safety:
        failures.append(f"criticalSafetyViolationCount={actual_safety} > {maximum_safety}")
    return failures


def _safe_case_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in result.items()
        if key not in _SECRET_KEYS and key not in {"input", "message"}
    }


def write_report(report: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    failed = [case for case in report["cases"] if not case.get("taskSuccess")]
    lines = [
        "# AI_Shop live task-success evaluation",
        "",
        f"- Run: `{report['metadata']['runId']}`",
        f"- Execution mode: `{report['metadata']['executionMode']}`",
        f"- Dataset SHA-256: `{report['metadata']['datasetSha256']}`",
        f"- Cases: {summary['passedCount']}/{summary['caseCount']}",
        f"- Task success rate: {summary['taskSuccessRate']:.2%}",
        f"- Tool selection accuracy: {summary['toolSelectionAccuracy']:.2%}",
        f"- Tool parameter accuracy: {summary['toolParameterAccuracy']:.2%}",
        f"- Provider completeness: {summary['providerCompletenessRate']:.2%}",
        f"- Critical safety violations: {summary['criticalSafetyViolationCount']}",
        f"- P95 latency: {summary['latencyMs']['p95']} ms",
        f"- P95 TTFT: {summary['ttftMs']['p95']} ms",
        f"- Observed models: {', '.join(summary['observedModels']) or 'none'}",
        f"- Gate: {'PASS' if not report['gateFailures'] else 'FAIL'}",
        "",
        "## Failed cases",
        "",
    ]
    if failed:
        for case in failed:
            reason = case.get("error") or [
                item["name"] for item in case.get("assertions") or [] if not item["passed"]
            ]
            lines.append(f"- `{case['caseId']}`: {reason}")
    else:
        lines.append("- None")
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_live(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    cases = load_cases(args.dataset)
    contract = validate_contract(cases, args.dataset, args.lock)
    bindings = load_bindings(args.bindings)
    resolved = [resolve_placeholders(case, bindings) for case in cases]
    disabled_fault_cases = [
        str(case.get("id"))
        for case in resolved
        if case.get("input", {}).get("fixtureFlag")
        and str(case["input"]["fixtureFlag"]).strip().lower() != "enabled"
    ]
    if disabled_fault_cases:
        raise EvaluationContractError(
            "fault-profile cases require an explicit 'enabled' fixture binding: "
            + ", ".join(disabled_fault_cases)
        )
    if args.subset:
        resolved = [case for case in resolved if case.get("subset") in set(args.subset)]
        if not resolved:
            raise EvaluationContractError("subset selection produced no cases")

    timeout = httpx.Timeout(connect=5, read=max(10.0, args.timeout), write=10, pool=5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        facts = await preflight(client, args.api_base_url.rstrip("/"))
        await init_pool()
        try:
            evaluator = LiveTaskEvaluator(
                client=client,
                api_base_url=args.api_base_url,
                timeout_seconds=args.timeout,
                expected_configured_mode=args.expected_orchestration_mode,
            )
            results: list[dict[str, Any]] = []
            for index, case in enumerate(resolved, 1):
                print(f"[{index}/{len(resolved)}] {case['id']}", flush=True)
                results.append(await evaluator.execute(case))
        finally:
            await close_pool()

    summary = aggregate(results, facts)
    gate_failures = threshold_failures(summary, contract["thresholds"])
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "schemaVersion": "aishop-live-eval/v1",
        "metadata": {
            "suite": SUITE,
            "runId": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "executionMode": EXECUTION_MODE,
            "simulated": False,
            "dataset": str(args.dataset),
            "datasetSha256": contract["datasetSha256"],
            "caseCount": len(resolved),
            "configuredOrchestrationMode": args.expected_orchestration_mode,
            "fixtureSnapshotId": args.fixture_snapshot_id,
            "apiBaseUrl": args.api_base_url,
        },
        "providerPreflight": facts,
        "summary": summary,
        "gateFailures": gate_failures,
        "cases": [_safe_case_result(result) for result in results],
    }
    output_dir = RESULTS_ROOT / run_id
    write_report(report, output_dir)
    return report, output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "--bindings",
        type=Path,
        default=(
            Path(os.environ["AISHOP_EVAL_BINDINGS_FILE"])
            if os.environ.get("AISHOP_EVAL_BINDINGS_FILE")
            else None
        ),
        help="Untracked JSON object that binds ${NAME} fixture placeholders",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("AISHOP_EVAL_API_BASE_URL", "http://127.0.0.1:7050"),
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--fixture-snapshot-id",
        help="Identifier of the equivalently restored business fixture snapshot",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--subset", action="append")
    parser.add_argument(
        "--expected-orchestration-mode",
        choices=("adaptive", "workflow", "single_agent", "multi_agent"),
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the frozen dataset and lock without resolving secrets or calling services",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        cases = load_cases(args.dataset)
        contract = validate_contract(cases, args.dataset, args.lock)
        if args.validate_only:
            print(json.dumps({"valid": True, "contract": contract}, ensure_ascii=False, indent=2))
            return
        report, output_dir = asyncio.run(run_live(args))
    except (EvaluationContractError, OSError, httpx.HTTPError) as exc:
        print(f"live evaluation aborted: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        json.dumps(
            {
                "resultDir": str(output_dir),
                "summary": report["summary"],
                "gateFailures": report["gateFailures"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["gateFailures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
