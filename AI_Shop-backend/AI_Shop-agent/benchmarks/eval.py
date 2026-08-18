#!/usr/bin/env python3
"""The single public entrypoint for AI_Shop evaluation suites."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.eval_runtime.adapters import run_stage  # noqa: E402
from benchmarks.eval_runtime.contracts import FailureClass, RunPhase  # noqa: E402
from benchmarks.eval_runtime.evidence import EvidenceError, EvidenceStore  # noqa: E402
from benchmarks.eval_runtime.lifecycle import LifecycleError, RunLifecycle  # noqa: E402
from benchmarks.eval_runtime.preflight import build_suite_preflight  # noqa: E402
from benchmarks.eval_runtime.registry import SuiteDefinition, list_suites, load_suite  # noqa: E402
from benchmarks.mature_eval.common import sha256_file  # noqa: E402


class EvaluationCommandError(ValueError):
    """A user-facing contract or lifecycle error."""


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _validate_run_id(suite: SuiteDefinition, run_id: str) -> str:
    value = str(run_id or "").strip()
    if not value or not suite.run_id_pattern.fullmatch(value):
        raise EvaluationCommandError(
            f"run-id does not match {suite.suite_id} contract: {value!r}"
        )
    return value


def _run_root(suite: SuiteDefinition, run_id: str) -> Path:
    return suite.result_root / run_id


def _store(suite: SuiteDefinition, run_id: str) -> EvidenceStore:
    return EvidenceStore(
        suite.result_root.parent,
        suite=suite.result_root.name,
        run_id=run_id,
    )


def _lifecycle(suite: SuiteDefinition, run_id: str) -> RunLifecycle:
    return RunLifecycle(
        _run_root(suite, run_id) / "lifecycle.json",
        suite=suite.suite_id,
        run_id=run_id,
    )


def _suite_lock_sha(suite: SuiteDefinition) -> str | None:
    raw = suite.contract.get("suiteLock")
    if not raw:
        return None
    path = PROJECT_ROOT / str(raw)
    return sha256_file(path) if path.is_file() else None


def _validate_contract(suite: SuiteDefinition, options: dict[str, Any]) -> dict[str, Any]:
    """Delegate dataset validation to the domain owner, without live calls."""

    if suite.adapter == "search-v3":
        from benchmarks import run_search_v3_eval

        result = run_search_v3_eval.prepare(
            argparse.Namespace(index=options.get("index", "aishop_eval_search_v3"))
        )
        return {**result, "suite": suite.suite_id, "suiteLockSha256": _suite_lock_sha(suite)}

    if suite.adapter == "rag-v5":
        from benchmarks import run_rag_v5_eval

        result = run_rag_v5_eval.prepare(argparse.Namespace())
        return {**result, "suite": suite.suite_id, "suiteLockSha256": _suite_lock_sha(suite)}

    if suite.adapter == "agent-v2":
        from benchmarks import run_task_success_v2_eval

        dataset = Path(options.get("dataset") or run_task_success_v2_eval.DEFAULT_DATASET)
        lock = Path(options.get("lock") or run_task_success_v2_eval.DEFAULT_LOCK)
        cases = run_task_success_v2_eval.load_cases(dataset)
        contract = run_task_success_v2_eval.validate_contract(cases, dataset, lock)
        return {
            "suite": suite.suite_id,
            "suiteLockSha256": _suite_lock_sha(suite),
            "caseCount": len(cases),
            "contract": contract,
        }

    raise EvaluationCommandError(f"no validation adapter for suite {suite.suite_id}")


def _common_options(args: argparse.Namespace) -> dict[str, Any]:
    values = vars(args).copy()
    for key in ("command", "suite", "stage", "run_id"):
        values.pop(key, None)
    return values


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    suite = load_suite(args.suite)
    result = _validate_contract(suite, _common_options(args))
    return {
        "command": "validate",
        "status": "VALID",
        **result,
        "suite": suite.suite_id,
    }


async def command_preflight(args: argparse.Namespace) -> dict[str, Any]:
    suite = load_suite(args.suite)
    run_id = _validate_run_id(suite, args.run_id)
    # Validation happens before creating a run claim; preflight itself never
    # reads fresh data or touches the global fresh lock.
    validation = _validate_contract(suite, _common_options(args))
    lifecycle = _lifecycle(suite, run_id)
    if lifecycle.phase != RunPhase.VALIDATED:
        raise EvaluationCommandError(
            f"preflight requires VALIDATED run, found {lifecycle.phase.value}"
        )
    result = await build_suite_preflight(
        suite.suite_id,
        run_id,
        api_base_url=args.api_base_url,
        require_java=(suite.adapter == "search-v3"),
    )
    store = _store(suite, run_id)
    store.write_json("validation.json", validation)
    store.write_json("preflight.json", result.to_dict())
    if result.passed:
        lifecycle.transition(RunPhase.PREFLIGHTED, details={"preflight": "PASS"})
    else:
        lifecycle.transition(RunPhase.BLOCKED, details={"preflight": "FAIL"})
        store.append_event(
            stage="preflight",
            status="BLOCKED",
            failure_class=FailureClass.DEPENDENCY_ERROR.value,
            checks=result.to_dict()["checks"],
        )
    payload = {
        "command": "preflight",
        "suite": suite.suite_id,
        "runId": run_id,
        "status": result.to_dict()["status"],
        "preflight": result.to_dict(),
    }
    if not result.passed:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise EvaluationCommandError("preflight blocked; see JSON output above")
    return payload


def _stage_phase(suite: SuiteDefinition, stage: str) -> RunPhase | None:
    if stage in {"known", "retrieval-known", "generation-known"}:
        return RunPhase.KNOWN_COLLECTED
    if stage in {"final", "retrieval-final", "generation-final", "execute"}:
        return RunPhase.FINAL_COLLECTED
    if stage == "package":
        return RunPhase.PACKAGED
    if stage == "deterministic":
        return RunPhase.PACKAGED
    return None


def _require_stage_prerequisites(suite: SuiteDefinition, stage: str, lifecycle: RunLifecycle) -> None:
    if suite.adapter != "rag-v5":
        return
    if stage == "retrieval-known":
        lifecycle.require(RunPhase.PREFLIGHTED)
    elif stage == "retrieval-final":
        lifecycle.require(RunPhase.FROZEN)
    elif stage == "generation-known":
        if not (_run_root(suite, lifecycle.run_id) / "stage-retrieval-known.json").is_file():
            raise EvaluationCommandError("retrieval-known must complete before generation-known")
    elif stage == "generation-final":
        if not (_run_root(suite, lifecycle.run_id) / "stage-retrieval-final.json").is_file():
            raise EvaluationCommandError("retrieval-final must complete before generation-final")
    elif stage == "package":
        if not (_run_root(suite, lifecycle.run_id) / "stage-generation-final.json").is_file():
            raise EvaluationCommandError("generation-final must complete before package")


async def command_run(args: argparse.Namespace) -> dict[str, Any]:
    suite = load_suite(args.suite)
    run_id = _validate_run_id(suite, args.run_id)
    if args.stage not in suite.stages:
        raise EvaluationCommandError(
            f"stage {args.stage!r} is not registered for {suite.suite_id}: {suite.stages}"
        )
    if suite.contract.get("legacy"):
        # Legacy deterministic registrations are compatibility-only and never
        # participate in the formal suite list or fresh holdout lifecycle.
        if args.stage != "deterministic":
            raise EvaluationCommandError("legacy suite supports deterministic replay only")
    lifecycle = _lifecycle(suite, run_id)
    _require_stage_prerequisites(suite, args.stage, lifecycle)
    if args.stage not in {"validate", "preflight"} and lifecycle.phase in {
        RunPhase.BLOCKED,
        RunPhase.FAILED_RETAINED,
        RunPhase.PACKAGED,
    }:
        raise EvaluationCommandError(
            f"run is terminal at {lifecycle.phase.value}; create a new run"
        )
    preflight_path = _run_root(suite, run_id) / "preflight.json"
    requires_preflight = not suite.contract.get("legacy")
    if requires_preflight and not preflight_path.is_file():
        raise EvaluationCommandError("preflight must pass before execution")
    if requires_preflight and preflight_path.is_file():
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        if preflight.get("status") != "READY":
            raise EvaluationCommandError("preflight is blocked; formal execution is not allowed")
    options = _common_options(args)
    stage_result = await run_stage(suite, stage=args.stage, run_id=run_id, options=options)
    store = _store(suite, run_id)
    store.write_json(f"stage-{args.stage}.json", stage_result.to_dict())
    if stage_result.status in {"BLOCKED", "FAILED", "REVIEW_PENDING"}:
        store.append_event(
            stage=args.stage,
            status=stage_result.status,
            failure_class=stage_result.failure_class.value,
            errorType=stage_result.error_type,
            errorMessage=stage_result.error_message,
        )
        if args.stage in {"final", "retrieval-final", "generation-final"}:
            lifecycle.transition(RunPhase.FAILED_RETAINED, details={"stage": args.stage})
        elif stage_result.status == "REVIEW_PENDING":
            lifecycle.transition(RunPhase.REVIEW_PENDING, details={"stage": args.stage})
        else:
            lifecycle.transition(RunPhase.BLOCKED, details={"stage": args.stage})
    else:
        target = _stage_phase(suite, args.stage)
        if target is not None:
            if suite.adapter == "rag-v5":
                if args.stage == "retrieval-known":
                    lifecycle.transition(RunPhase.KNOWN_COLLECTED, details={"stage": args.stage})
                    lifecycle.transition(RunPhase.FROZEN, details={"stage": args.stage})
                elif args.stage == "retrieval-final":
                    lifecycle.transition(RunPhase.FINAL_COLLECTED, details={"stage": args.stage})
                elif args.stage == "generation-known":
                    lifecycle.history.append({"stage": args.stage, "status": "COMPLETE"})
                    lifecycle._persist()
                elif args.stage == "generation-final":
                    lifecycle.history.append({"stage": args.stage, "status": "COMPLETE"})
                    lifecycle._persist()
                elif args.stage == "package":
                    lifecycle.transition(RunPhase.PACKAGED, details={"stage": args.stage})
            elif target == RunPhase.KNOWN_COLLECTED and lifecycle.phase == RunPhase.PREFLIGHTED:
                lifecycle.transition(RunPhase.KNOWN_COLLECTED, details={"stage": args.stage})
                lifecycle.transition(RunPhase.FROZEN, details={"stage": args.stage})
            elif target == RunPhase.FINAL_COLLECTED and lifecycle.phase in {
                RunPhase.FROZEN,
                RunPhase.FINAL_COLLECTED,
            }:
                if lifecycle.phase == RunPhase.FROZEN:
                    lifecycle.transition(RunPhase.FINAL_COLLECTED, details={"stage": args.stage})
            elif target == RunPhase.PACKAGED and lifecycle.phase == RunPhase.FINAL_COLLECTED:
                # For RAG, check if still pending human review
                if stage_result.status == "REVIEW_PENDING":
                    lifecycle.transition(RunPhase.REVIEW_PENDING, details={"stage": args.stage})
                else:
                    lifecycle.transition(RunPhase.PACKAGED, details={"stage": args.stage})
    return {
        "command": "run",
        "suite": suite.suite_id,
        "runId": run_id,
        "stage": args.stage,
        "status": stage_result.status,
        "result": stage_result.result,
        "lifecycle": lifecycle.snapshot(),
    }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    suite = load_suite(args.suite)
    run_id = _validate_run_id(suite, args.run_id)
    lifecycle = _lifecycle(suite, run_id)
    root = _run_root(suite, run_id)
    artifacts = sorted(path.name for path in root.glob("*") if path.is_file())
    return {"command": "status", "suite": suite.suite_id, "runId": run_id, "lifecycle": lifecycle.snapshot(), "artifacts": artifacts}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.set_defaults(handler=lambda _args: {"suites": [item.suite_id for item in list_suites()]})

    validate = subparsers.add_parser("validate")
    validate.add_argument("--suite", required=True)
    validate.add_argument("--index", default="aishop_eval_search_v3")
    validate.add_argument("--dataset", type=Path)
    validate.add_argument("--lock", type=Path)
    validate.set_defaults(handler=command_validate)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--suite", required=True)
    preflight.add_argument("--run-id", required=True)
    preflight.add_argument("--index", default="aishop_eval_search_v3")
    preflight.add_argument("--dataset", type=Path)
    preflight.add_argument("--lock", type=Path)
    preflight.add_argument("--api-base-url", default="http://127.0.0.1:7050")
    preflight.set_defaults(handler=command_preflight)

    run = subparsers.add_parser("run")
    run.add_argument("--suite", required=True)
    run.add_argument("--stage", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--index", default="aishop_eval_search_v3")
    run.add_argument("--release-version", type=int, default=0)
    run.add_argument("--candidate-size", type=int, default=20)
    run.add_argument("--top-k", type=int, default=10)
    run.add_argument("--contextual-mode", choices=("original", "context_prefix"), default="context_prefix")
    run.add_argument("--knowledge-index", default="aishop_eval_rag_context_v5")
    run.add_argument("--finalize-holdout", action="store_true")
    run.add_argument("--dataset", type=Path)
    run.add_argument("--lock", type=Path)
    run.add_argument("--bindings", type=Path)
    run.add_argument("--api-base-url", default="http://127.0.0.1:7050")
    run.add_argument("--gateway-base-url", default="http://127.0.0.1:8080")
    run.add_argument("--internal-token")
    run.add_argument("--fixture-snapshot-id")
    run.add_argument("--timeout", type=float, default=180.0)
    run.add_argument("--expected-orchestration-mode", default="adaptive")
    run.add_argument("--subset", action="append")
    run.set_defaults(handler=command_run)

    status = subparsers.add_parser("status")
    status.add_argument("--suite", required=True)
    status.add_argument("--run-id", required=True)
    status.set_defaults(handler=command_status)
    return parser


async def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    handler = args.handler
    result = handler(args)
    if asyncio.iscoroutine(result):
        return await result
    return result


def main() -> None:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(_dispatch(args))
        _json_print(result)
        # Exit with non-zero if stage is blocked, failed, or pending review
        if args.command == "run":
            status = result.get("status")
            if status in {"BLOCKED", "FAILED", "REVIEW_PENDING"}:
                print(f"Evaluation stage {status.lower()}", file=sys.stderr)
                raise SystemExit(1)
    except (EvaluationCommandError, EvidenceError, LifecycleError, ValueError, OSError) as exc:
        print(f"Evaluation command failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
