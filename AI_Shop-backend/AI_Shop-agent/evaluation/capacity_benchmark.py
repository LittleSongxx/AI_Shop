"""Read-only local full-stack capacity benchmark with explicit claim boundaries.

The runner exercises the production HTTP Agent path with independently reviewed
customer-service cases.  It is deliberately separate from quality gates: local
throughput and resource observations are useful bottleneck evidence, but they
are neither a production SLO nor a load-test certification.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from evaluation.adapters.agent import run_agent_case
from evaluation.core.contracts import CaseResult, CaseStatus, EvaluationCase
from evaluation.core.fingerprints import source_fingerprint
from evaluation.core.io import (
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import percentile
from evaluation.core.usage import merge_usage
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset
from evaluation.customer_service_http import build_http_agent_case
from evaluation.repeat_runner import trial_context

CAPACITY_BENCHMARK_ROOT = EVIDENCE_ROOT.parent / "benchmarks" / "capacity"
CAPACITY_SCHEMA = "aishop-capacity-benchmark/v1"
CAPACITY_EVIDENCE_SCHEMA = "aishop-capacity-benchmark-evidence/v1"
DEFAULT_CAPACITY_CASE_IDS = (
    "cs-gold-v1-001",  # deterministic product Search
    "cs-gold-v1-003",  # product consultation / generation
    "cs-gold-v1-004",  # deterministic order query
    "cs-gold-v1-028",  # general customer-service answer
)
_READ_ONLY_INTENTS = frozenset(
    {
        "PRODUCT_SEARCH",
        "PRODUCT_CONSULT",
        "QUERY_ORDER",
        "QUERY_LOGISTICS",
        "QUERY_FULFILLMENT",
        "QUERY_COUPON",
        "QUERY_COMMENT",
        "REFUND_STATUS",
        "CHAT",
    }
)
_CAPACITY_PROVIDER_CONTRACTS = {
    # This route resolves the exact order identifier through the Java runtime.
    # It intentionally does not need an LLM and has no workflow node with which
    # to prove the generic Agent adapter's deterministic-workflow exemption.
    "QUERY_ORDER": ("agent-runtime",),
}


class CapacityBenchmarkError(ValueError):
    """Raised when a capacity claim cannot be produced safely."""


def parse_concurrency_levels(raw: str | Sequence[str] | None) -> tuple[int, ...]:
    """Parse comma-separated and repeated CLI concurrency arguments."""

    chunks = ("1,2,4,8",) if raw is None else (raw,) if isinstance(raw, str) else raw
    try:
        values = tuple(
            int(value.strip())
            for chunk in chunks
            for value in str(chunk).split(",")
            if value.strip()
        )
    except ValueError as exc:
        raise CapacityBenchmarkError("concurrency values must be integers") from exc
    levels = tuple(sorted(set(values)))
    if not levels or any(value < 1 or value > 64 for value in levels):
        raise CapacityBenchmarkError("concurrency levels must be between 1 and 64")
    return levels


def load_capacity_cases(
    dataset_path: Path,
    *,
    case_ids: Sequence[str] = DEFAULT_CAPACITY_CASE_IDS,
) -> tuple[list[dict[str, Any]], list[EvaluationCase]]:
    rows = load_gold_dataset(dataset_path)
    selected = tuple(dict.fromkeys(str(value).strip() for value in case_ids if str(value).strip()))
    if not selected:
        raise CapacityBenchmarkError("at least one capacity case ID is required")
    by_id = {str(row["id"]): row for row in rows}
    unknown = sorted(set(selected) - set(by_id))
    if unknown:
        raise CapacityBenchmarkError(f"unknown capacity case IDs: {unknown}")
    chosen = [by_id[case_id] for case_id in selected]
    for row in chosen:
        case_id = str(row["id"])
        expected = row.get("expected") if isinstance(row.get("expected"), Mapping) else {}
        annotation = row.get("annotation") if isinstance(row.get("annotation"), Mapping) else {}
        intent = str(expected.get("intent") or "")
        if annotation.get("status") != HUMAN_STATUS:
            raise CapacityBenchmarkError(f"{case_id}: capacity case must be HUMAN_VERIFIED")
        if bool(expected.get("shouldHandoff")):
            raise CapacityBenchmarkError(f"{case_id}: handoff cases are excluded from capacity load")
        if intent not in _READ_ONLY_INTENTS:
            raise CapacityBenchmarkError(
                f"{case_id}: intent {intent or '<empty>'} is not approved for read-only capacity load"
            )
    cases: list[EvaluationCase] = []
    for row in chosen:
        expected = row.get("expected") if isinstance(row.get("expected"), Mapping) else {}
        intent = str(expected.get("intent") or "")
        case = build_http_agent_case(row)
        required = _CAPACITY_PROVIDER_CONTRACTS.get(intent)
        cases.append(replace(case, required_providers=required) if required else case)
    return chosen, cases


def _proc_cpu_ticks() -> tuple[int, int] | None:
    try:
        parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        values = [int(value) for value in parts[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _proc_memory() -> dict[str, float] | None:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition(":")
            if separator:
                values[key] = int(raw.strip().split()[0])
        total = int(values["MemTotal"])
        available = int(values["MemAvailable"])
    except (OSError, ValueError, KeyError, IndexError):
        return None
    used = max(0, total - available)
    return {
        "usedPercent": round(used / total * 100, 3) if total else 0.0,
        "availableMiB": round(available / 1024, 3),
    }


async def _sample_resources(stop: asyncio.Event, *, interval_seconds: float) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    previous = _proc_cpu_ticks()
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
        current = _proc_cpu_ticks()
        memory = _proc_memory()
        cpu_percent: float | None = None
        if previous is not None and current is not None:
            total_delta = current[0] - previous[0]
            idle_delta = current[1] - previous[1]
            if total_delta > 0:
                cpu_percent = round(max(0.0, total_delta - idle_delta) / total_delta * 100, 3)
        previous = current
        try:
            load_1m = round(float(os.getloadavg()[0]), 3)
        except (AttributeError, OSError):
            load_1m = None
        samples.append(
            {
                "observedAt": utc_now(),
                "hostCpuUsedPercent": cpu_percent,
                "hostMemoryUsedPercent": memory.get("usedPercent") if memory else None,
                "hostMemoryAvailableMiB": memory.get("availableMiB") if memory else None,
                "hostLoad1m": load_1m,
            }
        )
        if stop.is_set():
            return samples


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values]
    return {
        "sampleCount": len(rows),
        "p50": round(percentile(rows, 0.50), 3) if rows else None,
        "p95": round(percentile(rows, 0.95), 3) if rows else None,
        "p99": round(percentile(rows, 0.99), 3) if rows else None,
        "max": round(max(rows), 3) if rows else None,
    }


def _step_metrics(result: CaseResult) -> list[dict[str, Any]]:
    return [
        dict(step)
        for episode in result.output.get("episodes") or []
        if isinstance(episode, Mapping)
        for step in episode.get("steps") or []
        if isinstance(step, Mapping) and step.get("latencyMs") is not None
    ]


def _policy_facts(result: CaseResult) -> list[dict[str, Any]]:
    allowed = {
        "policy",
        "deterministicSocialReply",
        "llmSkipped",
        "ragSkipped",
        "sideEffectAllowed",
        "maxTokens",
        "disableThinking",
    }
    facts: list[dict[str, Any]] = []
    for episode in result.output.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        for step in episode.get("steps") or []:
            if not isinstance(step, Mapping) or str(step.get("eventType") or "") != "AGENT_POLICY":
                continue
            output = step.get("output")
            if not isinstance(output, Mapping):
                continue
            fact = {key: output[key] for key in allowed if key in output}
            if fact and fact not in facts:
                facts.append(fact)
    return facts


def _public_observation(
    result: CaseResult,
    *,
    case_id: str,
    trial_id: str,
    request_id: str,
    intent: str = "",
    required_providers: Sequence[str] = (),
) -> dict[str, Any]:
    answer = str(result.output.get("answer") or "")
    state_diff = result.state_diff or result.output.get("stateDiff") or {}
    return {
        "caseId": case_id,
        "trialId": trial_id,
        "requestId": request_id,
        "intent": intent,
        "requiredProviders": list(required_providers),
        "status": result.status.value,
        "latencyMs": round(float(result.latency_ms), 3),
        "providerCompleteness": int(result.metrics.get("providerCompleteness") or 0),
        "terminalStateCorrectness": int(
            result.metrics.get("terminalStateCorrectness") or 0
        ),
        "stateDiffMatched": bool(state_diff.get("matched")),
        "duplicateSideEffectCount": int(state_diff.get("duplicateSideEffectCount") or 0),
        "severeSafetyViolationCount": int(
            result.metrics.get("severeSafetyViolationCount") or 0
        ),
        "usage": dict(result.usage or {}),
        "executionPath": (
            "LLM"
            if int((result.usage or {}).get("providerCalls") or 0) > 0
            else "DETERMINISTIC"
        ),
        "providerFacts": dict(result.providers or {}),
        "policyFacts": _policy_facts(result),
        "answer": {
            "sha256": sha256_bytes(answer.encode("utf-8")),
            "chars": len(answer),
            "rawStored": False,
        },
        "tools": list(result.output.get("tools") or []),
        "steps": [
            {
                "eventType": str(step.get("eventType") or "UNKNOWN"),
                "status": str(step.get("status") or "UNKNOWN"),
                "latencyMs": float(step.get("latencyMs") or 0),
            }
            for step in _step_metrics(result)
        ],
        "error": result.error,
    }


def summarize_capacity_level(
    observations: Sequence[Mapping[str, Any]],
    *,
    concurrency: int,
    wall_seconds: float,
    resource_samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(observations)
    latencies = [float(row.get("latencyMs") or 0) for row in rows]
    passed = [
        row
        for row in rows
        if str(row.get("status")) == CaseStatus.PASSED.value
        and int(row.get("providerCompleteness") or 0) == 1
        and int(row.get("terminalStateCorrectness") or 0) == 1
        and bool(row.get("stateDiffMatched"))
        and int(row.get("duplicateSideEffectCount") or 0) == 0
        and int(row.get("severeSafetyViolationCount") or 0) == 0
    ]
    stages: dict[str, list[float]] = defaultdict(list)
    stage_errors: Counter[str] = Counter()
    for row in rows:
        for step in row.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            stage = str(step.get("eventType") or "UNKNOWN")
            stages[stage].append(float(step.get("latencyMs") or 0))
            if str(step.get("status") or "").upper() in {"ERROR", "FAILED", "CANCELLED"}:
                stage_errors[stage] += 1
    cpu = [
        float(row["hostCpuUsedPercent"])
        for row in resource_samples
        if row.get("hostCpuUsedPercent") is not None
    ]
    memory = [
        float(row["hostMemoryUsedPercent"])
        for row in resource_samples
        if row.get("hostMemoryUsedPercent") is not None
    ]

    def grouped_metrics(group_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        group = list(group_rows)
        group_passed = [row for row in group if row in passed]
        group_latencies = [float(row.get("latencyMs") or 0) for row in group]
        return {
            "requestCount": len(group),
            "successfulCount": len(group_passed),
            "successRate": round(len(group_passed) / len(group), 6) if group else 0.0,
            "latencyMs": _summary(group_latencies),
            "providerCalls": sum(
                int((row.get("usage") or {}).get("providerCalls") or 0)
                for row in group
                if isinstance(row.get("usage"), Mapping)
            ),
        }

    by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_path: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_case[str(row.get("caseId") or "unknown")].append(row)
        by_path[str(row.get("executionPath") or "UNKNOWN")].append(row)

    badcases = []
    for row in rows:
        if row in passed:
            continue
        reasons = []
        if str(row.get("status")) != CaseStatus.PASSED.value:
            reasons.append(f"status={row.get('status')}")
        if int(row.get("providerCompleteness") or 0) != 1:
            reasons.append("provider_incomplete")
        if int(row.get("terminalStateCorrectness") or 0) != 1:
            reasons.append("terminal_state_mismatch")
        if not bool(row.get("stateDiffMatched")):
            reasons.append("state_diff_mismatch")
        if int(row.get("duplicateSideEffectCount") or 0):
            reasons.append("duplicate_side_effect")
        if int(row.get("severeSafetyViolationCount") or 0):
            reasons.append("severe_safety_violation")
        badcases.append(
            {
                "trialId": str(row.get("trialId") or ""),
                "caseId": str(row.get("caseId") or ""),
                "reasons": reasons,
            }
        )
    return {
        "concurrency": concurrency,
        "requestedCount": len(rows),
        "completedCount": len(rows),
        "successfulCount": len(passed),
        "executionRate": round(len(rows) / len(rows), 6) if rows else 0.0,
        "successRate": round(len(passed) / len(rows), 6) if rows else 0.0,
        "errorCount": len(rows) - len(passed),
        "timeoutCount": sum(
            "timeout" in json.dumps(row.get("error") or {}, ensure_ascii=False).casefold()
            for row in rows
        ),
        "wallSeconds": round(max(0.0, wall_seconds), 6),
        "achievedQps": round(len(rows) / wall_seconds, 6) if wall_seconds > 0 else None,
        "latencyMs": {**_summary(latencies), "boundary": "LOCAL_FULL_STACK_NOT_PRODUCTION_SLO"},
        "usage": merge_usage(
            row.get("usage") if isinstance(row.get("usage"), Mapping) else {}
            for row in rows
        ),
        "caseMetrics": {
            case_id: grouped_metrics(group)
            for case_id, group in sorted(by_case.items())
        },
        "pathMetrics": {
            path: grouped_metrics(group)
            for path, group in sorted(by_path.items())
        },
        "stageMetrics": {
            stage: {
                "callCount": len(values),
                "latencyMs": _summary(values),
                "errorCount": stage_errors[stage],
            }
            for stage, values in sorted(stages.items())
        },
        "resources": {
            "scope": "LOCAL_HOST_SHARED_WITH_OTHER_PROCESSES",
            "sampleCount": len(resource_samples),
            "hostCpuUsedPercent": _summary(cpu),
            "hostMemoryUsedPercent": _summary(memory),
            "rawSamples": list(resource_samples),
        },
        "badcaseTrialIds": [
            str(row.get("trialId") or "") for row in rows if row not in passed
        ],
        "badcases": badcases,
    }


async def benchmark_capacity(
    dataset_path: Path,
    *,
    run_id: str,
    concurrencies: Sequence[int] = (1, 2, 4, 8),
    requests_per_level: int = 8,
    warmup_requests: int = 4,
    timeout_seconds: float = 180.0,
    case_ids: Sequence[str] = DEFAULT_CAPACITY_CASE_IDS,
    preflight: Mapping[str, Any],
    resource_sample_interval_seconds: float = 0.2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    levels = list(parse_concurrency_levels(tuple(str(value) for value in concurrencies)))
    if requests_per_level < max(levels):
        raise CapacityBenchmarkError("requests_per_level must be at least the maximum concurrency")
    if not 0 <= warmup_requests <= 1_000:
        raise CapacityBenchmarkError("warmup_requests must be between 0 and 1000")
    if timeout_seconds <= 0:
        raise CapacityBenchmarkError("timeout_seconds must be positive")
    if preflight.get("passed") is not True:
        raise CapacityBenchmarkError("capacity benchmark requires a passing production preflight")
    selected_rows, cases = load_capacity_cases(dataset_path, case_ids=case_ids)
    all_observations: list[dict[str, Any]] = []
    level_reports: dict[str, Any] = {}
    warmup_report: dict[str, Any] = {
        "requestCount": 0,
        "excludedFromMeasuredLevels": True,
        "status": "NOT_RUN",
    }
    for concurrency in levels:
        semaphore = asyncio.Semaphore(concurrency)

        async def execute(index: int, *, phase: str) -> dict[str, Any]:
            case = cases[index % len(cases)]
            source_row = selected_rows[index % len(selected_rows)]
            expected = (
                source_row.get("expected")
                if isinstance(source_row.get("expected"), Mapping)
                else {}
            )
            context = trial_context(
                f"{run_id}-{phase}-c{concurrency}", case.case_id, index + 1
            )
            async with semaphore:
                request_started = time.perf_counter()
                try:
                    result = await run_agent_case(
                        case,
                        user_id=context.evaluation_user_id,
                        timeout_seconds=timeout_seconds,
                        trial_context=context,
                    )
                except Exception as exc:
                    return {
                        "caseId": case.case_id,
                        "trialId": context.trial_id,
                        "requestId": context.request_id,
                        "intent": str(expected.get("intent") or ""),
                        "requiredProviders": list(case.required_providers),
                        "status": "ERROR",
                        "latencyMs": round(
                            (time.perf_counter() - request_started) * 1000, 3
                        ),
                        "providerCompleteness": 0,
                        "terminalStateCorrectness": 0,
                        "stateDiffMatched": False,
                        "duplicateSideEffectCount": 0,
                        "severeSafetyViolationCount": 0,
                        "usage": {
                            "providerCalls": 0,
                            "costCny": None,
                            "costStatus": "MISSING_USAGE",
                            "usageReported": False,
                            "missingReason": "capacity_request_error_before_usage",
                        },
                        "executionPath": "UNKNOWN",
                        "providerFacts": {},
                        "answer": {"sha256": None, "chars": 0, "rawStored": False},
                        "tools": [],
                        "steps": [],
                        "error": {"type": type(exc).__name__, "message": str(exc)[:300]},
                    }
            return _public_observation(
                result,
                case_id=case.case_id,
                trial_id=context.trial_id,
                request_id=context.request_id,
                intent=str(expected.get("intent") or ""),
                required_providers=case.required_providers,
            )

        if concurrency == levels[0] and warmup_requests:
            warmup_started = time.perf_counter()
            warmup_observations = await asyncio.gather(
                *(
                    execute(index, phase="warmup")
                    for index in range(warmup_requests)
                )
            )
            warmup_wall_seconds = time.perf_counter() - warmup_started
            warmup_report = {
                **summarize_capacity_level(
                    warmup_observations,
                    concurrency=concurrency,
                    wall_seconds=warmup_wall_seconds,
                    resource_samples=[],
                ),
                "requestCount": len(warmup_observations),
                "status": "COMPLETED",
                "excludedFromMeasuredLevels": True,
                "observationsStored": False,
            }

        stop = asyncio.Event()
        sampler = asyncio.create_task(
            _sample_resources(stop, interval_seconds=resource_sample_interval_seconds)
        )
        started = time.perf_counter()
        try:
            observations = await asyncio.gather(
                *(
                    execute(index, phase="measured")
                    for index in range(requests_per_level)
                )
            )
        finally:
            wall_seconds = time.perf_counter() - started
            stop.set()
            resource_samples = await sampler
        for observation in observations:
            observation["concurrency"] = concurrency
        all_observations.extend(observations)
        level_reports[str(concurrency)] = summarize_capacity_level(
            observations,
            concurrency=concurrency,
            wall_seconds=wall_seconds,
            resource_samples=resource_samples,
        )

    baseline_qps = float(level_reports[str(levels[0])].get("achievedQps") or 0)
    for concurrency in levels:
        report = level_reports[str(concurrency)]
        qps = float(report.get("achievedQps") or 0)
        report["throughputScalingEfficiency"] = (
            round(qps / (baseline_qps * concurrency / levels[0]), 6)
            if baseline_qps > 0
            else None
        )
    return (
        {
            "schemaVersion": CAPACITY_SCHEMA,
            "runId": run_id,
            "createdAt": utc_now(),
            "dataset": {
                "path": str(dataset_path.resolve()),
                "sha256": sha256_file(dataset_path),
                "selectedCaseIds": [str(row["id"]) for row in selected_rows],
                "caseCount": len(selected_rows),
                "annotationStatus": HUMAN_STATUS,
                "caseContracts": [
                    {
                        "caseId": case.case_id,
                        "intent": str((row.get("expected") or {}).get("intent") or ""),
                        "requiredProviders": list(case.required_providers),
                    }
                    for row, case in zip(selected_rows, cases, strict=True)
                ],
            },
            "configuration": {
                "concurrencies": levels,
                "requestsPerLevel": requests_per_level,
                "warmupRequests": warmup_requests,
                "timeoutSeconds": timeout_seconds,
                "resourceSampleIntervalSeconds": resource_sample_interval_seconds,
                "requestIsolation": [
                    "evaluationUserId",
                    "requestId",
                    "idempotencyKey",
                    "traceId",
                ],
            },
            "preflight": dict(preflight),
            "warmup": warmup_report,
            "levels": level_reports,
            "notProductionSlo": True,
            "normalQualityDenominatorExcluded": True,
            "claim": "LOCAL_FULL_STACK_READ_ONLY_CAPACITY_OBSERVATION",
            "limitations": [
                "Local single-host infrastructure and external Provider conditions; not a production SLO or capacity commitment.",
                "Host resource samples include unrelated local processes and are diagnostic only.",
                "The fixed four-case mix is an engineering probe, not production traffic distribution.",
                "Warm-up requests establish local connections/caches and are reported separately from measured levels.",
                "Answer content is hashed; this benchmark checks execution safety and capacity, not semantic answer quality.",
                "Unknown Provider price remains null and missing usage is never inferred from text length.",
            ],
        },
        all_observations,
    )


def _report_markdown(report: Mapping[str, Any]) -> str:
    warmup = report.get("warmup") or {}
    lines = [
        "# AI Shop 本地只读容量基准",
        "",
        f"> Run `{report.get('runId')}`；`notProductionSlo=true`；不进入质量门禁。",
        "",
        f"> Warm-up `{warmup.get('successfulCount', 0)}/{warmup.get('requestCount', 0)}`，"
        "独立于正式分母。",
        "",
        "| 并发 | 请求 | 安全执行成功率 | QPS | P50/P95/P99 (ms) | CPU P95 | 内存 P95 | usage |",
        "|---:|---:|---:|---:|---|---:|---:|---|",
    ]
    for concurrency, level in sorted(
        (report.get("levels") or {}).items(), key=lambda item: int(item[0])
    ):
        latency = level.get("latencyMs") or {}
        resources = level.get("resources") or {}
        cpu = resources.get("hostCpuUsedPercent") or {}
        memory = resources.get("hostMemoryUsedPercent") or {}
        usage = level.get("usage") or {}
        lines.append(
            f"| {concurrency} | {level.get('completedCount')} | {level.get('successRate')} | "
            f"{level.get('achievedQps')} | {latency.get('p50')}/{latency.get('p95')}/"
            f"{latency.get('p99')} | {cpu.get('p95')} | {memory.get('p95')} | "
            f"{usage.get('costStatus')} ({usage.get('missingUsageCalls', 0)} missing) |"
        )
    lines.extend(
        [
            "",
            "## 路径拆分",
            "",
            "| 并发 | 路径 | 请求 | 成功率 | P50/P95/P99 (ms) | Provider calls |",
            "|---:|---|---:|---:|---|---:|",
        ]
    )
    for concurrency, level in sorted(
        (report.get("levels") or {}).items(), key=lambda item: int(item[0])
    ):
        for path, metrics in sorted((level.get("pathMetrics") or {}).items()):
            latency = metrics.get("latencyMs") or {}
            lines.append(
                f"| {concurrency} | {path} | {metrics.get('requestCount')} | "
                f"{metrics.get('successRate')} | {latency.get('p50')}/"
                f"{latency.get('p95')}/{latency.get('p99')} | "
                f"{metrics.get('providerCalls')} |"
            )
    lines.extend(
        [
            "",
            "## 边界",
            "",
            "本报告描述本机、当前代码/依赖/Provider 和固定只读请求混合下的观察值。"
            "成功率只表示运行、状态与安全契约通过，不表示回答语义质量。"
            "它不能外推为生产 SLO、峰值容量或真实业务流量分布。",
            "",
        ]
    )
    return "\n".join(lines)


def write_capacity_evidence(
    report: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    benchmark_id: str,
) -> tuple[Path, str]:
    if not benchmark_id or any(char in benchmark_id for char in "/\\"):
        raise CapacityBenchmarkError("benchmark_id must be a non-empty path-safe identifier")
    root = CAPACITY_BENCHMARK_ROOT / benchmark_id
    if root.exists():
        raise FileExistsError(f"capacity evidence already exists: {root}")
    root.mkdir(parents=True)
    fingerprint = source_fingerprint()
    payload = {**dict(report), "sourceFingerprint": fingerprint}
    atomic_write_json(root / "report.json", payload, overwrite=False)
    atomic_write_jsonl(root / "observations.jsonl", observations, overwrite=False)
    atomic_write_text(root / "report.md", _report_markdown(payload), overwrite=False)
    inventory = {
        path.name: {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in sorted(root.iterdir())
        if path.is_file()
    }
    manifest = {
        "schemaVersion": CAPACITY_EVIDENCE_SCHEMA,
        "benchmarkSchemaVersion": CAPACITY_SCHEMA,
        "benchmarkId": benchmark_id,
        "runId": report.get("runId"),
        "createdAt": report.get("createdAt"),
        "datasetSha256": (report.get("dataset") or {}).get("sha256"),
        "sourceSha256": (fingerprint.get("source") or {}).get("sha256"),
        "providerConfigurationSha256": fingerprint.get("providerConfigurationSha256"),
        "notProductionSlo": True,
        "normalQualityDenominatorExcluded": True,
        "preflightPassed": (report.get("preflight") or {}).get("passed") is True,
        "files": inventory,
    }
    atomic_write_json(root / "evidence-manifest.json", manifest, overwrite=False)
    sums = {
        path.name: sha256_file(path)
        for path in sorted(root.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    }
    atomic_write_text(
        root / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        overwrite=False,
    )
    verify_capacity_evidence(root)
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return root, sha256_file(root / "SHA256SUMS")


def verify_capacity_evidence(root: Path) -> dict[str, Any]:
    if not root.is_dir() or not (root / "SHA256SUMS").is_file():
        raise CapacityBenchmarkError(f"invalid capacity evidence root: {root}")
    expected: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise CapacityBenchmarkError(f"invalid capacity SHA256SUMS line: {line!r}")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise CapacityBenchmarkError("capacity evidence file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise CapacityBenchmarkError(f"capacity evidence hash mismatch: {name}")
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "evidence-manifest.json").read_text(encoding="utf-8"))
    if report.get("schemaVersion") != CAPACITY_SCHEMA:
        raise CapacityBenchmarkError("capacity report schema is invalid")
    if manifest.get("schemaVersion") != CAPACITY_EVIDENCE_SCHEMA:
        raise CapacityBenchmarkError("capacity evidence schema is invalid")
    if report.get("notProductionSlo") is not True or manifest.get("notProductionSlo") is not True:
        raise CapacityBenchmarkError("capacity evidence must declare notProductionSlo=true")
    if report.get("normalQualityDenominatorExcluded") is not True:
        raise CapacityBenchmarkError("capacity evidence must be excluded from quality denominators")
    if manifest.get("preflightPassed") is not True:
        raise CapacityBenchmarkError("capacity evidence requires a passing preflight")
    if manifest.get("runId") != report.get("runId"):
        raise CapacityBenchmarkError("capacity run ID differs between report and manifest")
    if manifest.get("datasetSha256") != (report.get("dataset") or {}).get("sha256"):
        raise CapacityBenchmarkError("capacity dataset hash differs between report and manifest")
    return {
        "valid": True,
        "benchmarkId": manifest.get("benchmarkId"),
        "runId": report.get("runId"),
        "sha256SumsSha256": sha256_file(root / "SHA256SUMS"),
        "contentSha256": sha256_bytes(canonical_json_bytes(report)),
    }
