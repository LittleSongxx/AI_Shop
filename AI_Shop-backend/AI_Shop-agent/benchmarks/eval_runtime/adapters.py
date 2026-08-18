"""Compatibility adapters around existing domain runners.

The adapters intentionally keep domain scoring in the mature runners while the
unified CLI owns validation, preflight and lifecycle metadata.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from .contracts import FailureClass, StageResult, classify_exception
from .registry import SuiteDefinition


def _namespace(**values: Any) -> Namespace:
    return Namespace(**values)


def _result_payload(result: Any) -> dict[str, Any]:
    return result if isinstance(result, dict) else {"value": result}


def _provider_violation(payload: Any, *, path: str = "result") -> str | None:
    """Return the first fail-closed provider/fallback violation in a payload."""

    if isinstance(payload, list):
        for index, item in enumerate(payload):
            violation = _provider_violation(item, path=f"{path}[{index}]")
            if violation:
                return violation
        return None
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        normalized = str(key).replace("_", "").casefold()
        current = f"{path}.{key}"
        if normalized in {"fallbackused", "fallback"} and value is True:
            return f"{current}=true"
        if normalized == "fallbackcount":
            try:
                if int(value or 0) > 0:
                    return f"{current}={value}"
            except (TypeError, ValueError):
                return f"{current}=invalid"
        if normalized == "executionmode" and str(value).strip().lower() in {
            "deterministic",
            "replay",
            "fallback",
        }:
            return f"{current}={value}"
        if normalized in {"providercomplete", "providercompleteness"}:
            if value is False:
                return f"{current}=false"
            if isinstance(value, dict):
                status = str(value.get("status") or "").upper()
                if value.get("passed") is False or value.get("complete") is False:
                    return f"{current}=incomplete"
                if status in {"BLOCKED", "FAILED", "INCOMPLETE", "NOT_COLLECTED"}:
                    return f"{current}.status={status}"
                if any(item is False for item in value.values() if isinstance(item, bool)):
                    return f"{current}=incomplete"
        if normalized == "provider" and isinstance(value, dict):
            if value.get("complete") is False:
                return f"{current}.complete=false"
        violation = _provider_violation(value, path=current)
        if violation:
            return violation
    return None


def _blocked_provider_result(
    suite: SuiteDefinition,
    *,
    stage: str,
    payload: dict[str, Any],
) -> StageResult | None:
    if suite.contract.get("providerPolicy") != "FAIL_CLOSED_NO_FALLBACK":
        return None
    violation = _provider_violation(payload)
    if not violation:
        return None
    return StageResult(
        stage=stage,
        status="BLOCKED",
        result=payload,
        failure_class=FailureClass.PROVIDER_ERROR,
        error_type="ProviderEvidenceViolation",
        error_message=f"formal live provider evidence is incomplete: {violation}",
    )


async def run_stage(suite: SuiteDefinition, *, stage: str, run_id: str, options: dict[str, Any]) -> StageResult:
    """Dispatch one stage to the existing versioned domain implementation."""

    try:
        if suite.adapter == "search-v3":
            module = importlib.import_module("benchmarks.run_search_v3_eval")
            args = _namespace(run_id=run_id, index=options.get("index", "aishop_eval_search_v3"), finalize_holdout=options.get("finalize_holdout", False))
            if stage == "validate":
                result = module.prepare(args)
            elif stage == "known":
                result = await module.collect_known(args)
            elif stage == "final":
                result = await module.collect_final(args)
            elif stage == "package":
                result = module.package(args)
            else:
                raise ValueError(f"unsupported Search v3 stage: {stage}")
            payload = _result_payload(result)
            provider_block = _blocked_provider_result(
                suite, stage=stage, payload=payload
            )
            if provider_block:
                return provider_block
            # Interpret Search v3 quality gate status
            gate_status = payload.get("qualityGates", {}).get("status")
            if gate_status == "FAILED_RETAINED":
                return StageResult(
                    stage=stage,
                    status="FAILED",
                    result=payload,
                    failure_class=FailureClass.QUALITY_FAIL,
                )
            return StageResult(stage=stage, result=payload)

        if suite.adapter == "rag-v5":
            retrieval = importlib.import_module("benchmarks.run_rag_v5_eval")
            generation = importlib.import_module("benchmarks.run_rag_generation_v5")
            release = int(options.get("release_version", 0))
            args = _namespace(
                run_id=run_id,
                release_version=release,
                top_k=int(options.get("top_k", 10)),
                candidate_size=int(options.get("candidate_size", 20)),
                contextual_mode=options.get("contextual_mode", "context_prefix"),
                knowledge_index=options.get("knowledge_index", "aishop_eval_rag_context_v5"),
                finalize_holdout=options.get("finalize_holdout", False),
            )
            module = retrieval if stage.startswith("retrieval") else generation
            if stage.endswith("known"):
                result = await module.collect_known(args)
            elif stage.endswith("final"):
                result = await module.collect_final(args)
            elif stage == "package":
                retrieval_result = retrieval.package(args)
                generation_result = generation.package(args)
                payload = {
                    "retrieval": retrieval_result,
                    "generation": generation_result,
                }
                provider_block = _blocked_provider_result(
                    suite, stage=stage, payload=payload
                )
                if provider_block:
                    return provider_block
                # Interpret RAG v5 quality gate and human review status
                retrieval_gate = retrieval_result.get("qualityGate", {}).get("status")
                generation_gate = generation_result.get("qualityGate", {}).get("status")
                human_review = generation_result.get("humanReviewStatus")
                if retrieval_gate == "FAILED_RETAINED" or generation_gate == "FAILED_RETAINED":
                    return StageResult(
                        stage=stage,
                        status="FAILED",
                        result=payload,
                        failure_class=FailureClass.QUALITY_FAIL,
                    )
                if human_review == "HUMAN_REVIEW_PENDING":
                    return StageResult(stage=stage, status="REVIEW_PENDING", result=payload)
                return StageResult(stage=stage, result=payload)

        if suite.adapter == "legacy-deterministic":
            command = str(suite.contract.get("command") or "")
            if not command:
                raise ValueError("legacy deterministic suite has no command")
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, *command.split(), "--run-id", run_id],
                cwd=Path(__file__).resolve().parents[2],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode:
                raise RuntimeError(
                    f"legacy deterministic runner exited {completed.returncode}: "
                    f"{completed.stderr[-500:]}"
                )
            try:
                output = json.loads(completed.stdout)
            except json.JSONDecodeError:
                output = {"stdout": completed.stdout[-2000:]}
            if isinstance(output, dict) and (
                output.get("gateFailures")
                or output.get("failures")
                or output.get("qualityGate", {}).get("passed") is False
            ):
                raise RuntimeError("legacy deterministic evaluation gate failed")
            return StageResult(stage=stage, result=output)

        if suite.adapter == "agent-v2":
            module = importlib.import_module("benchmarks.run_task_success_v2_eval")
            if stage == "validate":
                cases = module.load_cases(options.get("dataset") or module.DEFAULT_DATASET)
                contract = module.validate_contract(
                    cases,
                    options.get("dataset") or module.DEFAULT_DATASET,
                    options.get("lock") or module.DEFAULT_LOCK,
                )
                return StageResult(stage=stage, result={"contract": contract})
            if stage == "preflight":
                return StageResult(stage=stage, result={"delegated": True})
            if stage == "execute":
                args = _namespace(
                    dataset=options.get("dataset") or module.DEFAULT_DATASET,
                    lock=options.get("lock") or module.DEFAULT_LOCK,
                    bindings=options.get("bindings"),
                    api_base_url=options.get("api_base_url", "http://127.0.0.1:7050"),
                    gateway_base_url=options.get("gateway_base_url", "http://127.0.0.1:8080"),
                    internal_token=options.get("internal_token"),
                    fixture_snapshot_id=options.get("fixture_snapshot_id"),
                    timeout=float(options.get("timeout", 180.0)),
                    subset=options.get("subset"),
                    expected_orchestration_mode=options.get("expected_orchestration_mode", "adaptive"),
                    run_id=run_id,
                )
                report, output_dir = await module.run_live(args)
                payload = {"report": report, "resultDir": str(output_dir)}
                provider_block = _blocked_provider_result(
                    suite, stage=stage, payload=payload
                )
                if provider_block:
                    return provider_block
                # Interpret Agent v2 quality gates
                gate_failures = report.get("gateFailures", [])
                if gate_failures:
                    return StageResult(
                        stage=stage,
                        status="FAILED",
                        result=payload,
                        failure_class=FailureClass.QUALITY_FAIL,
                        error_message=f"Quality gates failed: {'; '.join(gate_failures)}",
                    )
                return StageResult(stage=stage, result=payload)
            raise ValueError(f"unsupported Agent v2 stage: {stage}")

        if suite.adapter == "visual-v1":
            module = importlib.import_module("benchmarks.run_visual_relevance")
            if stage == "validate":
                cases = module.load_cases()
                return StageResult(
                    stage=stage,
                    result={"contract": module.validate_contract(cases)},
                )
            if stage == "execute":
                if options.get("predictions"):
                    raise ValueError(
                        "formal visual-v1 execution does not accept replay predictions"
                    )
                payload = await module.run_live(options.get("limit"))
                provider_block = _blocked_provider_result(
                    suite, stage=stage, payload=payload
                )
                if provider_block:
                    return provider_block
                failures = module.gate_failures(
                    payload["report"], module.json.loads(module.LOCK_PATH.read_text())
                    .get("thresholds")
                    or {},
                )
                if failures:
                    return StageResult(
                        stage=stage,
                        status="FAILED",
                        result=payload,
                        failure_class=FailureClass.QUALITY_FAIL,
                        error_message="; ".join(failures),
                    )
                return StageResult(stage=stage, result=payload)
            if stage == "package":
                return StageResult(stage=stage, result={"packaged": True})
            raise ValueError(f"unsupported Visual v1 stage: {stage}")

        if suite.adapter == "text2sql-v1":
            module = importlib.import_module("benchmarks.text2sql_eval")
            dataset = Path(options.get("dataset") or module.DATASET_PATH)
            lock = Path(options.get("lock") or module.LOCK_PATH)
            if stage == "validate":
                cases = module.load_cases(dataset)
                return StageResult(
                    stage=stage,
                    result={"contract": module.validate_contract(cases, dataset, lock)},
                )
            if stage == "execute":
                predictions_path = options.get("predictions")
                if not predictions_path:
                    return StageResult(
                        stage=stage,
                        status="BLOCKED",
                        failure_class=FailureClass.PROVIDER_ERROR,
                        error_type="ProviderEvidenceMissing",
                        error_message=(
                            "Text2SQL real provider predictions were not collected; "
                            "deterministic output is not accepted as live evidence"
                        ),
                    )
                cases = module.load_cases(dataset)
                predictions = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
                report = module.evaluate_predictions(cases, predictions, lock_path=lock)
                payload = {"contract": module.validate_contract(cases, dataset, lock), "report": report}
                if report.get("providerCompleteness", 0.0) < 1.0:
                    return StageResult(
                        stage=stage,
                        status="BLOCKED",
                        result=payload,
                        failure_class=FailureClass.PROVIDER_ERROR,
                        error_type="ProviderEvidenceIncomplete",
                        error_message="Text2SQL provider trace completeness is below 1.0",
                    )
                if report.get("gateFailures"):
                    return StageResult(
                        stage=stage,
                        status="FAILED",
                        result=payload,
                        failure_class=FailureClass.QUALITY_FAIL,
                        error_message="; ".join(report["gateFailures"]),
                    )
                return StageResult(stage=stage, result=payload)
            if stage == "package":
                return StageResult(stage=stage, result={"packaged": True})
            raise ValueError(f"unsupported Text2SQL v1 stage: {stage}")

        raise ValueError(f"no adapter implementation for suite {suite.suite_id}")
    except Exception as exc:
        failure = classify_exception(exc)
        blocking_failures = {
            FailureClass.SERVICE_UNAVAILABLE,
            FailureClass.DEPENDENCY_ERROR,
            FailureClass.PROVIDER_ERROR,
            FailureClass.RATE_LIMITED,
            FailureClass.CIRCUIT_OPEN,
            FailureClass.TIMEOUT,
            FailureClass.BUDGET_EXCEEDED,
        }
        return StageResult(
            stage=stage,
            status="BLOCKED" if failure in blocking_failures else "FAILED",
            failure_class=failure,
            error_type=type(exc).__name__,
            error_message=str(exc)[:1000],
        )
