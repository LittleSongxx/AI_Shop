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
            # Interpret Search v3 quality gate status
            gate_status = payload.get("qualityGates", {}).get("status")
            if gate_status == "FAILED_RETAINED":
                return StageResult(stage=stage, status="FAILED", result=payload)
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
                # Interpret RAG v5 quality gate and human review status
                retrieval_gate = retrieval_result.get("qualityGate", {}).get("status")
                generation_gate = generation_result.get("qualityGate", {}).get("status")
                human_review = generation_result.get("humanReviewStatus")
                if retrieval_gate == "FAILED_RETAINED" or generation_gate == "FAILED_RETAINED":
                    return StageResult(stage=stage, status="FAILED", result=payload)
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
                # Interpret Agent v2 quality gates
                gate_failures = report.get("gateFailures", [])
                if gate_failures:
                    return StageResult(
                        stage=stage,
                        status="FAILED",
                        result={"report": report, "resultDir": str(output_dir)},
                        error_message=f"Quality gates failed: {'; '.join(gate_failures)}"
                    )
                return StageResult(stage=stage, result={"report": report, "resultDir": str(output_dir)})
            raise ValueError(f"unsupported Agent v2 stage: {stage}")

        raise ValueError(f"no adapter implementation for suite {suite.suite_id}")
    except Exception as exc:
        failure = classify_exception(exc)
        return StageResult(
            stage=stage,
            status="BLOCKED" if failure in {FailureClass.SERVICE_UNAVAILABLE, FailureClass.DEPENDENCY_ERROR} else "FAILED",
            failure_class=failure,
            error_type=type(exc).__name__,
            error_message=str(exc)[:1000],
        )
