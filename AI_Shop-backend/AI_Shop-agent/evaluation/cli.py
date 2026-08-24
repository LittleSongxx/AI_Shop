from __future__ import annotations

# Runtime bootstrap intentionally precedes production imports; suppress the
# corresponding import-order lint rule for this small, explicit boundary.
# ruff: noqa: E402, I001

import argparse
import asyncio
import json
import secrets
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

# Must run before importing production modules: several production singletons
# read Settings during module import, and local start.sh ports are dynamic.
from evaluation.core.runtime import load_runtime_environment

load_runtime_environment()

from app.db.pool import close_pool, init_pool
from app.services.evaluation_fault_service import (
    cleanup_fault_capability,
    register_fault_capability,
    wait_for_fault_events,
)
from app.services.redis_service import redis_service
from evaluation.core.config import load_suite
from evaluation.core.contracts import (
    CaseResult,
    CaseStatus,
    Domain,
    EvaluationError,
    RunRecord,
    RUN_SCHEMA_VERSION_V3,
    Split,
)
from evaluation.core.datasets import (
    build_lock,
    canonical_dataset_sha256,
    load_split,
    validate_repository_datasets,
)
from evaluation.core.evidence import verify_evidence, write_run_evidence
from evaluation.core.auxiliary_evidence import write_auxiliary_evidence
from evaluation.core.lifecycle import (
    claim_final,
    freeze_final,
    lifecycle_status,
)
from evaluation.core.preflight import run_preflight
from evaluation.core.slices import aggregate_slice_metrics
from evaluation.core.fault_injection import (
    FAULT_EVIDENCE_PRODUCTION_BOUNDARY,
    FailureInjectionScope,
    assess_recovery,
    fault_evidence_level,
    load_fault_scenarios,
)
from evaluation.core.io import (
    EVIDENCE_ROOT,
    RUNS_ROOT,
    atomic_write_json,
    atomic_write_text,
    load_json,
    load_jsonl,
    utc_now,
)
from evaluation.core.metrics import aggregate_domain
from evaluation.core.fingerprints import environment_facts, source_fingerprint
from evaluation.quality_scorecard import (
    DEFAULT_CATALOG,
    DEFAULT_HOLDOUT,
    build_scorecard,
    write_scorecard,
)
from evaluation.search_paired_replay import (
    DEFAULT_BASELINE_EVIDENCE as DEFAULT_SEARCH_REPLAY_BASELINE,
    DEFAULT_CASE_IDS as DEFAULT_SEARCH_REPLAY_CASE_IDS,
    DEFAULT_V9_HOLDOUT as DEFAULT_SEARCH_REPLAY_HOLDOUT,
    load_replay_cases,
    run_search_paired_replay,
    write_paired_replay_evidence,
)
from evaluation.customer_service_gold import (
    DEFAULT_DATASET as DEFAULT_CUSTOMER_SERVICE_DATASET,
    DEFAULT_JSON_REPORT as DEFAULT_CUSTOMER_SERVICE_JSON_REPORT,
    DEFAULT_REPORT as DEFAULT_CUSTOMER_SERVICE_REPORT,
    load_gold_dataset,
    run_customer_service_gold,
)
from evaluation.customer_service_http import (
    build_http_agent_case,
    rebuild_customer_service_http_report,
    run_customer_service_http,
    write_customer_service_http_evidence,
)
from evaluation.customer_service_answer_review import (
    compare_answer_reviews,
    export_answer_adjudication_template,
    export_answer_review_sheet,
    merge_answer_reviews,
    render_answer_agreement_markdown,
    score_answer_review,
    seal_answer_review_sheet,
    validate_answer_review_sheet,
    verify_answer_review_evidence,
    verify_pending_answer_review_evidence,
    write_answer_review_evidence,
    write_pending_answer_review_evidence,
)
from evaluation.customer_service_review import (
    compare_human_reviews,
    export_review_sheet,
    merge_human_reviews,
    render_agreement_markdown,
    seal_review_sheet,
    validate_review_sheet,
)
from evaluation.customer_service_slot_replay import (
    build_slot_replay,
    write_slot_replay_evidence,
)
from evaluation.capacity_benchmark import (
    DEFAULT_CAPACITY_CASE_IDS,
    benchmark_capacity,
    load_capacity_cases,
    parse_concurrency_levels,
    write_capacity_evidence,
)
from evaluation.db_benchmark import benchmark_db_sizes, write_db_benchmark_evidence
from evaluation.repeat_runner import run_repeated_agent_cases
from evaluation.repeat_runner import trial_context
from evaluation.runner import _execute_case, run_evaluation


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _split(value: str) -> Split:
    try:
        return Split(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.cli",
        description="AI Shop trustworthy production-path evaluation",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="validate suite, datasets, locks, and disjointness")

    lock = commands.add_parser("lock", help="regenerate a visible dataset lock")
    lock.add_argument(
        "--split", type=_split, choices=[Split.DEVELOPMENT, Split.REGRESSION], required=True
    )

    preflight = commands.add_parser("preflight", help="fail-closed live dependency checks")
    preflight.add_argument(
        "--split", type=_split, choices=[Split.DEVELOPMENT, Split.REGRESSION], required=True
    )

    run = commands.add_parser("run", help="run one complete split")
    run.add_argument("--split", type=_split, required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--release-id")
    run.add_argument("--publish", action="store_true")
    run.add_argument("--confirm-final", action="store_true")

    freeze = commands.add_parser("freeze-final", help="freeze source before final reveal")
    freeze.add_argument("--release-id", required=True)

    claim = commands.add_parser("claim-final", help="claim one unseen final dataset")
    claim.add_argument("--release-id", required=True)
    claim.add_argument("--dataset", type=Path, required=True)

    status = commands.add_parser("status", help="show final lifecycle status")
    status.add_argument("--release-id")

    verify = commands.add_parser("verify", help="verify current published evidence")
    verify.add_argument("--path", type=Path)

    slices = commands.add_parser("slices", help="report independent slice metrics from a run")
    slices.add_argument("--split", type=_split, choices=list(Split), required=True)
    slices.add_argument("--run-id")

    repeat = commands.add_parser("repeat", help="run independent Agent pass^k trials")
    repeat.add_argument(
        "--split", type=_split, choices=[Split.DEVELOPMENT, Split.REGRESSION], required=True
    )
    repeat.add_argument("--k", type=int, default=5)
    repeat.add_argument("--run-id", required=True)

    fault = commands.add_parser("fault-test", help="execute the declared fault recovery matrix")
    fault.add_argument("--scenario-file", type=Path, required=True)
    fault.add_argument(
        "--split", type=_split, choices=[Split.DEVELOPMENT, Split.REGRESSION], default=Split.DEVELOPMENT
    )
    fault.add_argument("--run-id")

    benchmark = commands.add_parser("benchmark-db", help="benchmark batch and N+1 DB access")
    benchmark.add_argument("--sizes", default="1,10,50,100")
    benchmark.add_argument("--iterations", type=int, default=3)
    benchmark.add_argument("--run-id", help="immutable benchmark evidence ID")
    benchmark.add_argument(
        "--shared",
        action="store_true",
        help="explicitly use the application database (diagnostic only; not isolated)",
    )
    capacity = commands.add_parser(
        "benchmark-capacity",
        help="run an isolated-user, read-only local full-stack concurrency benchmark",
    )
    capacity.add_argument("--dataset", type=Path, required=True)
    capacity.add_argument("--run-id", required=True)
    capacity.add_argument("--output-id", help="immutable evidence package ID; defaults to run ID")
    capacity.add_argument(
        "--concurrency",
        action="append",
        default=None,
        help="comma-separated level(s); repeatable (default: 1,2,4,8)",
    )
    capacity.add_argument("--requests-per-level", type=int, default=8)
    capacity.add_argument(
        "--warmup-requests",
        type=int,
        default=4,
        help="read-only warm-up requests excluded from measured levels (default: 4)",
    )
    capacity.add_argument("--timeout-seconds", type=float, default=180.0)
    capacity.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="optional HUMAN_VERIFIED read-only case ID; repeatable",
    )
    scorecard = commands.add_parser(
        "scorecard",
        help="derive quality-first metrics and metric-specific badcases from immutable evidence",
    )
    scorecard.add_argument("--evidence", type=Path, default=None)
    scorecard.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    scorecard.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    scorecard.add_argument("--output", type=Path, required=True, help="Markdown scorecard path")
    scorecard.add_argument("--json-output", type=Path, help="optional structured JSON path")
    paired_replay = commands.add_parser(
        "search-paired-replay",
        help="compare current Search with immutable v9 hard-negative rankings",
    )
    paired_replay.add_argument(
        "--baseline-evidence", type=Path, default=DEFAULT_SEARCH_REPLAY_BASELINE
    )
    paired_replay.add_argument("--holdout", type=Path, default=DEFAULT_SEARCH_REPLAY_HOLDOUT)
    paired_replay.add_argument("--run-id", required=True)
    paired_replay.add_argument("--output-dir", type=Path, required=True)
    paired_replay.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="optional case ID filter; repeatable (default: known v9 hard negatives/tails)",
    )
    customer_service = commands.add_parser(
        "customer-service-gold",
        help="measure provisional customer-service intent, risk, slot, and handoff quality",
    )
    customer_service.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    customer_service.add_argument("--mode", choices=["rule"], default="rule")
    customer_service.add_argument("--output", type=Path, default=DEFAULT_CUSTOMER_SERVICE_REPORT)
    customer_service.add_argument(
        "--json-output", type=Path, default=DEFAULT_CUSTOMER_SERVICE_JSON_REPORT
    )
    slot_replay = commands.add_parser(
        "customer-service-slot-replay",
        help="compare the current deterministic slot extractor with immutable human evidence",
    )
    slot_replay.add_argument("--dataset", type=Path, required=True)
    slot_replay.add_argument("--baseline-report", type=Path, required=True)
    slot_replay.add_argument("--run-id", required=True)
    slot_replay.add_argument("--package-id", required=True)
    customer_http = commands.add_parser(
        "customer-service-http",
        help="run or review customer-service quality through the production HTTP Agent path",
    )
    customer_http_commands = customer_http.add_subparsers(
        dest="customer_http_command", required=True
    )
    customer_http_run = customer_http_commands.add_parser(
        "run", help="execute HUMAN_VERIFIED cases through /api/agent/sendMessage"
    )
    customer_http_run.add_argument("--dataset", type=Path, required=True)
    customer_http_run.add_argument("--run-id", required=True)
    customer_http_run.add_argument("--output", type=Path, required=True)
    customer_http_run.add_argument(
        "--case-id", action="append", default=[], help="optional case ID filter; repeatable"
    )
    customer_http_run.add_argument("--timeout-seconds", type=float, default=240.0)
    customer_http_run.add_argument("--review-a-output", type=Path)
    customer_http_run.add_argument("--review-b-output", type=Path)
    customer_http_rebuild = customer_http_commands.add_parser(
        "rebuild",
        help="rebuild metrics from preserved observations and seal an immutable benchmark",
    )
    customer_http_rebuild.add_argument("--source-report", type=Path, required=True)
    customer_http_rebuild.add_argument("--dataset", type=Path, required=True)
    customer_http_rebuild.add_argument("--output-dir", type=Path, required=True)
    customer_http_rebuild.add_argument("--review-a-output", type=Path)
    customer_http_rebuild.add_argument("--review-b-output", type=Path)
    customer_http_export = customer_http_commands.add_parser(
        "review-export", help="export a blind final-answer review sheet"
    )
    customer_http_export.add_argument("--report", type=Path, required=True)
    customer_http_export.add_argument("--annotator", required=True)
    customer_http_export.add_argument("--output", type=Path, required=True)
    customer_http_export.add_argument("--seed", type=int)
    customer_http_score = customer_http_commands.add_parser(
        "review-score", help="score one complete independent final-answer review sheet"
    )
    customer_http_score.add_argument("--report", type=Path, required=True)
    customer_http_score.add_argument("--review", type=Path, required=True)
    customer_http_score.add_argument("--output", type=Path, required=True)
    customer_http_validate = customer_http_commands.add_parser(
        "review-validate", help="validate an answer-review sheet and its HTTP source binding"
    )
    customer_http_validate.add_argument("--report", type=Path, required=True)
    customer_http_validate.add_argument("--review", type=Path, required=True)
    customer_http_validate.add_argument("--complete", action="store_true")
    customer_http_seal = customer_http_commands.add_parser(
        "review-seal", help="seal one completed answer-review sheet"
    )
    customer_http_seal.add_argument("--report", type=Path, required=True)
    customer_http_seal.add_argument("--review", type=Path, required=True)
    customer_http_seal.add_argument("--output", type=Path, required=True)
    customer_http_compare = customer_http_commands.add_parser(
        "review-compare", help="compare two sealed answer reviews before adjudication"
    )
    customer_http_compare.add_argument("--report", type=Path, required=True)
    customer_http_compare.add_argument("--review-a", type=Path, required=True)
    customer_http_compare.add_argument("--review-b", type=Path, required=True)
    customer_http_compare.add_argument("--output", type=Path, required=True)
    customer_http_compare.add_argument("--markdown-output", type=Path)
    customer_http_compare.add_argument("--adjudication-output", type=Path)
    customer_http_package = customer_http_commands.add_parser(
        "review-package",
        help="freeze completed dual reviews and export a separate adjudication input",
    )
    customer_http_package.add_argument("--report", type=Path, required=True)
    customer_http_package.add_argument("--review-a", type=Path, required=True)
    customer_http_package.add_argument("--review-b", type=Path, required=True)
    customer_http_package.add_argument("--output-dir", type=Path, required=True)
    customer_http_package.add_argument(
        "--adjudication-output",
        type=Path,
        help="optional writable third-reviewer JSONL output outside the evidence package",
    )
    customer_http_merge = customer_http_commands.add_parser(
        "review-merge", help="merge two sealed reviews and optional third-person adjudication"
    )
    customer_http_merge.add_argument("--report", type=Path, required=True)
    customer_http_merge.add_argument("--review-a", type=Path, required=True)
    customer_http_merge.add_argument("--review-b", type=Path, required=True)
    customer_http_merge.add_argument("--adjudication", type=Path)
    customer_http_merge.add_argument("--output-dir", type=Path, required=True)
    customer_http_verify = customer_http_commands.add_parser(
        "review-verify", help="verify an immutable answer-review evidence package"
    )
    customer_http_verify.add_argument("--evidence-dir", type=Path, required=True)
    customer_http_pending_verify = customer_http_commands.add_parser(
        "review-pending-verify",
        help="verify an immutable pending-adjudication answer-review package",
    )
    customer_http_pending_verify.add_argument("--evidence-dir", type=Path, required=True)
    customer_review = commands.add_parser(
        "customer-service-review",
        help="export, validate, or merge two blinded customer-service review sheets",
    )
    review_commands = customer_review.add_subparsers(dest="review_command", required=True)
    review_export = review_commands.add_parser("export", help="export one blinded reviewer sheet")
    review_export.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    review_export.add_argument("--annotator", required=True, help="stable reviewer identifier")
    review_export.add_argument("--output", type=Path, required=True)
    review_export.add_argument(
        "--seed",
        type=int,
        default=None,
        help="optional deterministic order seed; omitted uses a stable reviewer-specific seed",
    )
    review_validate = review_commands.add_parser("validate", help="validate a reviewer sheet")
    review_validate.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    review_validate.add_argument("--review", type=Path, required=True)
    review_validate.add_argument("--complete", action="store_true")
    review_compare = review_commands.add_parser(
        "compare",
        help="compare two sealed sheets and write pre-adjudication agreement evidence",
    )
    review_compare.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    review_compare.add_argument("--review-a", type=Path, required=True)
    review_compare.add_argument("--review-b", type=Path, required=True)
    review_compare.add_argument("--output", type=Path, required=True)
    review_compare.add_argument("--markdown-output", type=Path)
    review_seal = review_commands.add_parser(
        "seal", help="seal a completed open sheet into a new hashed artifact"
    )
    review_seal.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    review_seal.add_argument("--review", type=Path, required=True)
    review_seal.add_argument("--output", type=Path, required=True)
    review_merge = review_commands.add_parser("merge", help="merge two sheets into human-verified data")
    review_merge.add_argument("--dataset", type=Path, default=DEFAULT_CUSTOMER_SERVICE_DATASET)
    review_merge.add_argument("--review-a", type=Path, required=True)
    review_merge.add_argument("--review-b", type=Path, required=True)
    review_merge.add_argument("--adjudication", type=Path)
    review_merge.add_argument("--adjudicator", default="consensus")
    review_merge.add_argument("--output-dataset", type=Path, required=True)
    review_merge.add_argument("--evidence", type=Path, required=True)
    seal = commands.add_parser(
        "seal-auxiliary",
        help="copy a verified fault/repeat run into an immutable diagnostic package",
    )
    seal.add_argument("--kind", choices=["resilience", "repeated-agent"], required=True)
    seal.add_argument("--run-id", required=True)
    seal.add_argument("--package-id", required=True)
    seal.add_argument("--shadow-only", action="store_true")
    return parser


async def _preflight(split: Split) -> dict[str, Any]:
    await init_pool()
    await redis_service.ensure_connected()
    try:
        return await run_preflight(load_split(split))
    finally:
        await close_pool()


def _result_from_public(row: dict[str, Any]) -> CaseResult:
    return CaseResult(
        case_id=str(row.get("case_id") or ""),
        domain=Domain(str(row.get("domain") or "agent")),
        status=CaseStatus(str(row.get("status") or "ERROR")),
        metrics=dict(row.get("metrics") or {}),
        latency_ms=float(row.get("latency_ms") or 0),
        output=dict(row.get("output") or {}),
        providers=dict(row.get("providers") or {}),
        assertions=list(row.get("assertions") or []),
        error=row.get("error"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        usage=dict(row.get("usage") or {}),
        slice=row.get("slice"),
        trial_id=row.get("trial_id"),
        state_diff=row.get("state_diff"),
        fault_scenario=row.get("fault_scenario"),
        semantic_judgment=row.get("semantic_judgment"),
    )


def _find_run_for_split(split: Split, run_id: str | None) -> tuple[str, Path]:
    if run_id:
        root = RUNS_ROOT / run_id
        if not root.is_dir():
            raise ValueError(f"run evidence does not exist: {run_id}")
        return run_id, root
    candidates: list[Path] = []
    for root in RUNS_ROOT.iterdir() if RUNS_ROOT.is_dir() else []:
        if not root.is_dir() or not (root / "summary.json").is_file():
            continue
        try:
            summary = load_json(root / "summary.json")
        except (OSError, ValueError):
            continue
        if summary.get("split") == split.value:
            candidates.append(root)
    if not candidates:
        raise ValueError(f"no run evidence found for split {split.value}")
    root = max(candidates, key=lambda item: item.stat().st_mtime_ns)
    return root.name, root


async def _slices(args: argparse.Namespace) -> int:
    run_id, root = _find_run_for_split(args.split, args.run_id)
    verify_evidence(root)
    rows = [_result_from_public(row) for row in load_jsonl(root / "cases.jsonl")]
    by_domain: dict[str, list[CaseResult]] = defaultdict(list)
    for row in rows:
        by_domain[row.domain.value].append(row)
    _print(
        {
            "runId": run_id,
            "split": args.split.value,
            "sliceMetrics": {
                "search": aggregate_slice_metrics(by_domain.get("search", [])),
                "rag": aggregate_slice_metrics(by_domain.get("rag", [])),
                "agent": aggregate_slice_metrics(by_domain.get("agent", [])),
            },
            "weightedTotal": None,
            "policy": "NO_WEIGHTED_TOTAL; EVERY_SLICE_IS_REPORTED_INDEPENDENTLY",
        }
    )
    return 0


async def _repeat(args: argparse.Namespace) -> int:
    if args.k < 1 or args.k > 32:
        raise ValueError("--k must be between 1 and 32")
    validate_repository_datasets()
    cases = load_split(args.split)
    await init_pool()
    try:
        preflight = await run_preflight(cases)

        async def execute(case, context):
            return await _execute_case(case, args.run_id, context)

        trials, repeated = await run_repeated_agent_cases(
            cases,
            run_id=args.run_id,
            k=args.k,
            execute=execute,
        )
    finally:
        await close_pool()
    first: dict[str, CaseResult] = {}
    for trial in trials:
        first.setdefault(trial.case_id, trial)
    primary = [first[case.case_id] for case in cases if case.domain is Domain.AGENT]
    suite = load_suite()
    summary = {
        "schemaVersion": "aishop-evaluation-summary/v3",
        "runId": args.run_id,
        "split": args.split.value,
        "datasetSha256": canonical_dataset_sha256(cases),
        "executionMode": "LOCAL_FULL_STACK",
        "domains": {
            "agent": aggregate_domain(
                primary,
                suite["domains"]["agent"],
                suite["statisticalPolicy"],
            )
        },
        "repeatedAgentMetrics": repeated,
        "usageMetrics": {"source": "repeated-trials", "trialCount": len(trials)},
        "completedAt": utc_now(),
    }
    gates = {
        "passed": bool(repeated.get("hardGate", {}).get("passed")),
        "domainOutcomes": {"agent": bool(repeated.get("hardGate", {}).get("passed"))},
        "repeatedAgent": repeated.get("hardGate"),
        "preflight": preflight,
        "policy": "REPEATED_AGENT_EVIDENCE_IS_FAIL_CLOSED",
    }
    run = RunRecord(
        run_id=args.run_id,
        split=args.split,
        dataset_sha256=summary["datasetSha256"],
        source_fingerprint=source_fingerprint(),
        environment={**environment_facts(), "preflight": preflight},
        cases=primary,
        trials=trials,
        summary=summary,
        gates=gates,
        schema_version=RUN_SCHEMA_VERSION_V3,
        framework_schema_version="aishop-evaluation/v3",
    )
    root, digest = write_run_evidence(run)
    _print(
        {
            "runId": args.run_id,
            "k": args.k,
            "passed": gates["passed"],
            "evidence": str(root),
            "sha256SumsSha256": digest,
        }
    )
    return 0 if gates["passed"] else 2


async def _fault_test(args: argparse.Namespace) -> int:
    scenarios = load_fault_scenarios(args.scenario_file)
    validate_repository_datasets()
    cases = load_split(args.split)
    by_id = {case.case_id: case for case in cases}
    selected: list[tuple[Any, Any]] = []
    for scenario in scenarios:
        case = by_id.get(scenario.case_id or "")
        if case is None:
            # A scenario without a case ID is still useful for contract
            # validation, but cannot be silently mapped to an arbitrary case.
            selected.append((scenario, None))
        else:
            selected.append((scenario, case))
    run_id = args.run_id or f"fault-{utc_now().replace(':', '').replace('-', '').replace('.', '')}"

    def _trace_rows(result: CaseResult) -> list[dict[str, Any]]:
        output = result.output if isinstance(result.output, dict) else {}
        trace = output.get("trace") or output.get("traces") or []
        if isinstance(trace, dict):
            trace = [trace]
        return [row for row in trace if isinstance(row, dict)]

    def _fallback_used(result: CaseResult) -> bool | None:
        output = result.output if isinstance(result.output, dict) else {}
        if result.status is CaseStatus.ERROR or not output:
            return None
        if "fallbackUsed" in output and isinstance(output.get("fallbackUsed"), bool):
            return bool(output["fallbackUsed"])
        if any(bool(output.get(key)) for key in ("fallback", "degraded")):
            return True
        if any(
            bool(row.get(key))
            for row in _trace_rows(result)
            for key in ("fallback", "fallbackUsed", "partialFailure", "deadlineExceeded")
        ):
            return True
        source = str(output.get("resultSource") or "").casefold()
        if any(token in source for token in ("fallback", "partial", "unavailable", "degraded")):
            return True
        # A partial response is an observed boundary outcome, not a terminal
        # state inferred from the scenario declaration.
        fault_events = output.get("faultEvents") or []
        if any(
            isinstance(event, dict) and event.get("mode") == "partial"
            for event in fault_events
        ):
            return True
        return False

    def _terminal_state(result: CaseResult) -> tuple[str | None, str]:
        output = result.output if isinstance(result.output, dict) else {}
        explicit = str(output.get("terminalState") or output.get("terminal") or "").upper()
        if explicit in {"SUCCEEDED", "FAILED", "DEGRADED", "FALLBACK", "INCONCLUSIVE", "MANUAL_REVIEW"}:
            return explicit, "RESPONSE_FIELD"
        episodes = output.get("episodes") or []
        statuses = {
            str(episode.get("status") or "").upper()
            for episode in episodes
            if isinstance(episode, dict) and not episode.get("parentRunId")
        }
        terminal_statuses = statuses.intersection(
            {"SUCCEEDED", "FAILED", "DEGRADED", "FALLBACK", "INCONCLUSIVE", "MANUAL_REVIEW"}
        )
        if len(terminal_statuses) == 1:
            return next(iter(terminal_statuses)), "AUTHORITATIVE_EPISODE"
        fallback_used = _fallback_used(result)
        if result.status is not CaseStatus.ERROR and output and fallback_used is True:
            return "DEGRADED", "OBSERVED_FALLBACK_RESPONSE"
        if result.status in {CaseStatus.PASSED, CaseStatus.FAILED} and output:
            return "SUCCEEDED", "OBSERVED_RESPONSE_COMPLETION"
        return None, "UNOBSERVED"

    def _metric_violation(result: CaseResult, *names: str) -> bool | None:
        if "hardConstraintBypassCount" in names:
            output = result.output if isinstance(result.output, dict) else {}
            evidence = output.get("hardConstraintEvidence")
            if isinstance(evidence, dict) and evidence.get("status") == "NOT_APPLICABLE":
                return "NOT_APPLICABLE"
        for name in names:
            if name not in result.metrics:
                continue
            value = result.metrics[name]
            if isinstance(value, (bool, int, float)):
                return bool(value)
        return None

    def _recovery_public(result: CaseResult) -> dict[str, Any]:
        return {
            "status": result.status.value,
            "error": result.error,
            "metrics": dict(result.metrics),
            "providers": dict(result.providers),
            "trialId": result.trial_id,
            "stateDiff": result.state_diff,
        }

    await init_pool()
    results: list[CaseResult] = []
    try:
        preflight_cases = [case for _scenario, case in selected if case is not None]
        preflight = await run_preflight(preflight_cases) if preflight_cases else {"passed": False}
        for scenario, case in selected:
            if case is None:
                continue
            scenario_nonce = f"{run_id}-{scenario.scenario_id}-{secrets.token_hex(6)}"
            if case.domain.value == "agent":
                fault_context = trial_context(f"{scenario_nonce}-fault", case.case_id, 1)
            else:
                fault_context = None
            registered = None
            fault_events: list[dict[str, Any]] = []
            if (
                case.domain.value == "agent"
                and scenario.target
                in {"redis-checkpoint", "worker-deadline", "mcp-tool"}
            ):
                assert fault_context is not None
                registered = await register_fault_capability(
                    scenario,
                    user_id=fault_context.evaluation_user_id,
                    request_id=fault_context.request_id,
                    trial_id=fault_context.trial_id,
                )
                fault_context = replace(
                    fault_context,
                    fault_capability=registered.token,
                    fault_evidence_id=registered.evidence_id,
                )
                try:
                    result = await _execute_case(
                        case,
                        scenario_nonce,
                        fault_context,
                    )
                    fault_events = await wait_for_fault_events(
                        registered.evidence_id,
                        scenario_id=scenario.scenario_id,
                    )
                finally:
                    await cleanup_fault_capability(registered)
            else:
                with FailureInjectionScope(scenario) as scope:
                    result = await _execute_case(case, scenario_nonce, fault_context)
                fault_events = scope.events
            if fault_events and isinstance(result.output, dict):
                # Keep the raw injected boundary event beside the response so a
                # reviewer can distinguish a successful fallback from a normal
                # provider execution.  Do not synthesize a fault when the
                # declared scenario was not actually reached.
                result.output["faultEvents"] = fault_events

            # Always leave the injection scope before issuing the recovery
            # request.  A fresh nonce/context prevents durable request,
            # checkpoint, Redis-token, and order-state collisions.
            recovery_nonce = f"{run_id}-{scenario.scenario_id}-recovery-{secrets.token_hex(6)}"
            if case.domain.value == "agent":
                recovery_context = trial_context(f"{recovery_nonce}", case.case_id, 1)
            else:
                recovery_context = None
            recovery_result = await _execute_case(case, recovery_nonce, recovery_context)
            recovery_safety = _metric_violation(
                recovery_result,
                "unsafeAnswerCount",
                "severeSafetyViolationCount",
            )
            recovery_ok = (
                recovery_result.status is CaseStatus.PASSED
                and not recovery_result.error
                and int(recovery_result.metrics.get("providerCompleteness") or 0) == 1
                and recovery_safety is False
                and int((recovery_result.state_diff or {}).get("duplicateSideEffectCount") or 0) == 0
                and (
                    case.domain.value != "agent"
                    or bool((recovery_result.state_diff or {}).get("matched"))
                )
            )
            terminal, terminal_source = _terminal_state(result)
            evidence_level = fault_evidence_level(scenario.target)
            unsafe_answer = _metric_violation(
                result,
                "unsafeAnswerCount",
                "severeSafetyViolationCount",
            )
            hard_constraint_bypass = _metric_violation(
                result,
                "hardConstraintBypassCount",
                "constraintViolationCount",
            )
            request_outcome_observed = (
                result.status in {CaseStatus.PASSED, CaseStatus.FAILED}
                and isinstance(result.output, dict)
                and bool(result.output)
                and terminal is not None
            )
            recovery = assess_recovery(
                scenario,
                {
                    "failureTrace": fault_events,
                    "fallbackUsed": _fallback_used(result),
                    "unsafeAnswer": unsafe_answer,
                    "hardConstraintBypass": hard_constraint_bypass,
                    "terminalState": terminal,
                    "terminalStateSource": terminal_source,
                    "faultEvidenceLevel": evidence_level,
                    "requestOutcomeObserved": request_outcome_observed,
                    "caseStatus": result.status.value,
                    "nextRequestRecovered": recovery_ok,
                },
            )
            hard_gate_eligible = bool(recovery["hardGateEligible"])
            contract_passed = bool(recovery["passed"])
            result.fault_scenario = {
                **scenario.public(),
                "faultEvents": fault_events,
                "faultEvidenceLevel": evidence_level,
                # A fault request is a resilience observation, never a normal
                # quality observation.  Keep this marker on the case itself so
                # downstream readers cannot accidentally put it in a quality
                # denominator just because the API returned a response.
                "normalQualityDenominatorExcluded": True,
                "observedTerminalState": terminal,
                "terminalStateSource": terminal_source,
                "requestOutcomeObserved": request_outcome_observed,
                "unsafeAnswerObserved": unsafe_answer,
                "hardConstraintBypassObserved": hard_constraint_bypass,
                "nextRequest": _recovery_public(recovery_result),
                "recovery": recovery,
                "contractPassed": contract_passed,
                "hardGateEligible": hard_gate_eligible,
                "hardGatePassed": contract_passed if hard_gate_eligible else None,
                # Compatibility projection for existing evidence readers. It
                # means the declared contract passed, not that a shadow case
                # contributed to the hard gate.
                "recoveryPassed": contract_passed,
            }
            result.trial_id = scenario.scenario_id
            results.append(result)
    finally:
        await close_pool()
    suite = load_suite()
    by_domain: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_domain[result.domain.value].append(result)

    def _fault_domain_summary(rows: list[CaseResult], domain: str) -> dict[str, Any]:
        """Render fault evidence without treating injected failures as quality cases.

        ``verify_evidence`` still needs the domain ``caseCount`` to account for
        every case file row.  The quality metrics themselves are therefore
        computed from the (normally empty) non-fault subset and the explicit
        denominator fields make that distinction machine-checkable.
        """

        normal_rows = [row for row in rows if not row.fault_scenario]
        normal = aggregate_domain(
            normal_rows,
            suite["domains"][domain],
            suite["statisticalPolicy"],
        )
        all_statuses: dict[str, int] = {}
        for row in rows:
            all_statuses[row.status.value] = all_statuses.get(row.status.value, 0) + 1
        normal["caseCount"] = len(rows)
        normal["statusCounts"] = all_statuses
        normal["normalQualityCaseCount"] = len(normal_rows)
        normal["faultCaseCount"] = len(rows) - len(normal_rows)
        normal["faultCasesExcludedFromNormalDenominator"] = True
        normal["normalQuality"] = {
            "caseCount": len(normal_rows),
            "statusCounts": {
                status: sum(1 for row in normal_rows if row.status.value == status)
                for status in ("PASSED", "FAILED", "ERROR")
            },
            "metrics": normal["metrics"],
            "gate": {
                "status": "NOT_RUN" if not normal_rows else "REPORTED_SEPARATELY",
                "passed": None if not normal_rows else all(
                    row.status is CaseStatus.PASSED for row in normal_rows
                ),
                "reason": "FAULT_CASES_EXCLUDED_FROM_NORMAL_DENOMINATOR",
            },
        }
        return normal

    hard_rows = [
        row
        for row in results
        if bool((row.fault_scenario or {}).get("hardGateEligible"))
    ]
    shadow_rows = [
        row
        for row in results
        if not bool((row.fault_scenario or {}).get("hardGateEligible"))
    ]
    declared_hard_count = sum(scenario.gate_mode == "HARD" for scenario in scenarios)
    hard_gate_passed = (
        declared_hard_count > 0
        and len(hard_rows) == declared_hard_count
        and all(bool((row.fault_scenario or {}).get("hardGatePassed")) for row in hard_rows)
    )
    all_contracts_passed = (
        len(results) == len(scenarios)
        and bool(results)
        and all(bool((row.fault_scenario or {}).get("contractPassed")) for row in results)
    )
    preflight_passed = bool(preflight.get("passed"))
    summary = {
        "schemaVersion": "aishop-evaluation-summary/v3",
        "runId": run_id,
        "split": args.split.value,
        "datasetSha256": canonical_dataset_sha256([case for _scenario, case in selected if case]),
        "executionMode": "LOCAL_FULL_STACK_MIXED_BOUNDARY_FAULT_INJECTION",
        "domains": {
            domain: _fault_domain_summary(rows, domain)
            for domain, rows in by_domain.items()
        },
        "resilienceMetrics": {
            "scenarioCount": len(scenarios),
            "executedCount": len(results),
            "missingScenarioCount": len(scenarios) - len(results),
            "contractPassedCount": sum(
                bool((row.fault_scenario or {}).get("contractPassed")) for row in results
            ),
            "allContractsPassed": len(results) == len(scenarios)
            and bool(results)
            and all(bool((row.fault_scenario or {}).get("contractPassed")) for row in results),
            "hardScenarioCount": declared_hard_count,
            "hardExecutedCount": len(hard_rows),
            "hardPassedCount": sum(
                bool((row.fault_scenario or {}).get("hardGatePassed")) for row in hard_rows
            ),
            "hardGatePassed": hard_gate_passed,
            "shadowScenarioCount": len(scenarios) - declared_hard_count,
            "shadowExecutedCount": len(shadow_rows),
            "productionBoundaryCount": sum(
                (row.fault_scenario or {}).get("faultEvidenceLevel")
                == FAULT_EVIDENCE_PRODUCTION_BOUNDARY
                for row in results
            ),
            "harnessBoundaryCount": sum(
                (row.fault_scenario or {}).get("faultEvidenceLevel") == "HARNESS_BOUNDARY"
                for row in results
            ),
            "unsupportedCount": sum(
                (row.fault_scenario or {}).get("faultEvidenceLevel") == "UNSUPPORTED"
                for row in results
            ),
            "preflight": preflight,
        },
        "completedAt": utc_now(),
    }
    gates = {
        "passed": hard_gate_passed and all_contracts_passed and preflight_passed,
        "domainOutcomes": {
            domain: all(
                bool((row.fault_scenario or {}).get("hardGatePassed"))
                for row in domain_rows
                if bool((row.fault_scenario or {}).get("hardGateEligible"))
            )
            for domain, domain_rows in by_domain.items()
            if any(
                bool((row.fault_scenario or {}).get("hardGateEligible"))
                for row in domain_rows
            )
        },
        "preflightPassed": preflight_passed,
        "allRecoveryContractsPassed": all_contracts_passed,
        "policy": (
            "ONLY_PRODUCTION_BOUNDARY_HARD_SCENARIOS_GATE; "
            "HARNESS_AND_UNSUPPORTED_SCENARIOS_ARE_SHADOW; "
            "FAULT_CASES_ARE_EXCLUDED_FROM_NORMAL_QUALITY_DENOMINATORS"
        ),
    }
    run = RunRecord(
        run_id=run_id,
        split=args.split,
        dataset_sha256=summary["datasetSha256"],
        source_fingerprint=source_fingerprint(),
        environment={**environment_facts(), "preflight": preflight},
        cases=results,
        summary=summary,
        gates=gates,
        schema_version=RUN_SCHEMA_VERSION_V3,
        framework_schema_version="aishop-evaluation/v3",
    )
    root, digest = write_run_evidence(run)
    _print({"runId": run_id, "passed": gates["passed"], "evidence": str(root), "sha256SumsSha256": digest})
    return 0 if gates["passed"] else 2


async def _main(args: argparse.Namespace) -> int:
    if args.command == "validate":
        suite = load_suite()
        locks = validate_repository_datasets()
        _print(
            {
                "valid": True,
                "suiteId": suite["suiteId"],
                "locks": locks,
                "finalLifecycle": lifecycle_status(),
            }
        )
        return 0
    if args.command == "lock":
        _print(build_lock(args.split))
        return 0
    if args.command == "preflight":
        _print(await _preflight(args.split))
        return 0
    if args.command == "run":
        run, evidence_root = await run_evaluation(
            split=args.split,
            run_id=args.run_id,
            release_id=args.release_id,
            publish=args.publish,
            confirm_final=args.confirm_final,
        )
        _print(
            {
                "runId": run.run_id,
                "split": run.split.value,
                "passed": bool(run.gates.get("passed")),
                "domainOutcomes": run.gates.get("domainOutcomes"),
                "evidence": str(evidence_root),
            }
        )
        return 0 if run.gates.get("passed") else 2
    if args.command == "freeze-final":
        _print(freeze_final(args.release_id))
        return 0
    if args.command == "claim-final":
        _print(claim_final(args.release_id, args.dataset))
        return 0
    if args.command == "status":
        _print(lifecycle_status(args.release_id))
        return 0
    if args.command == "verify":
        _print(verify_evidence(args.path) if args.path else verify_evidence())
        return 0
    if args.command == "scorecard":
        scorecard = build_scorecard(
            args.evidence or EVIDENCE_ROOT,
            holdout_path=args.holdout,
            catalog_path=args.catalog,
        )
        markdown_path, json_path = write_scorecard(
            scorecard,
            args.output,
            args.json_output,
            evidence_path=args.evidence or EVIDENCE_ROOT,
        )
        _print(
            {
                "schemaVersion": scorecard.get("schemaVersion"),
                "runId": (scorecard.get("evidence") or {}).get("runId"),
                "markdown": str(markdown_path),
                "json": str(json_path),
                "badcaseCount": len(scorecard.get("badcases") or []),
            }
        )
        return 0
    if args.command == "search-paired-replay":
        selected_ids = tuple(args.case_id) or DEFAULT_SEARCH_REPLAY_CASE_IDS
        _all_cases, selected = load_replay_cases(
            args.holdout,
            case_ids=selected_ids,
        )
        # Search can invoke rerank conditionally even when the historical case
        # declaration required only embedding. Probe both providers before any
        # paired query is consumed.
        preflight_cases = [
            replace(
                case,
                required_providers=tuple(
                    sorted(set(case.required_providers) | {"embedding", "rerank"})
                ),
            )
            for case in selected
        ]
        await init_pool()
        try:
            preflight = await run_preflight(preflight_cases)
            report = await run_search_paired_replay(
                baseline_evidence=args.baseline_evidence,
                holdout_path=args.holdout,
                run_id=args.run_id,
                preflight=preflight,
                case_ids=selected_ids,
            )
        finally:
            await close_pool()
        verification = write_paired_replay_evidence(report, args.output_dir)
        _print(
            {
                "schemaVersion": report.get("schemaVersion"),
                "runId": report.get("runId"),
                "status": report.get("status"),
                "caseCount": report.get("caseCount"),
                "metrics": report.get("metrics"),
                "badcaseCount": len(report.get("badcases") or []),
                "verification": verification,
            }
        )
        return 0
    if args.command == "customer-service-gold":
        report = await run_customer_service_gold(
            args.dataset,
            mode=args.mode,
            output_path=args.output,
            json_output_path=args.json_output,
        )
        _print(
            {
                "schemaVersion": report.get("schemaVersion"),
                "status": report.get("status"),
                "releaseGateEligible": report.get("releaseGateEligible"),
                "dataset": report.get("dataset"),
                "metrics": report.get("metrics"),
                "canonicalSlotDiagnostics": report.get("canonicalSlotDiagnostics"),
                "markdown": str(args.output),
                "json": str(args.json_output),
                "badcaseCount": len(report.get("badcases") or []),
            }
        )
        return 0
    if args.command == "customer-service-slot-replay":
        report, paired_cases = await build_slot_replay(
            args.dataset,
            baseline_report_path=args.baseline_report,
            run_id=args.run_id,
        )
        root, digest = write_slot_replay_evidence(
            report,
            paired_cases,
            package_id=args.package_id,
        )
        _print(
            {
                "schemaVersion": report.get("schemaVersion"),
                "runId": report.get("runId"),
                "metrics": report.get("metrics"),
                "pairedCaseCounts": report.get("pairedCaseCounts"),
                "evidence": str(root),
                "sha256SumsSha256": digest,
            }
        )
        return 0
    if args.command == "customer-service-http":
        if args.customer_http_command == "run":
            rows = load_gold_dataset(args.dataset)
            selected = {str(value) for value in args.case_id}
            if selected:
                rows = [row for row in rows if str(row["id"]) in selected]
            cases = [build_http_agent_case(row) for row in rows]
            await init_pool()
            try:
                preflight = await run_preflight(cases)
                report = await run_customer_service_http(
                    args.dataset,
                    run_id=args.run_id,
                    preflight=preflight,
                    timeout_seconds=args.timeout_seconds,
                    case_ids=args.case_id,
                )
            finally:
                await close_pool()
            atomic_write_json(args.output, report, overwrite=False)
            reviews: dict[str, Any] = {}
            if args.review_a_output:
                reviews["reviewA"] = export_answer_review_sheet(
                    args.output,
                    args.review_a_output,
                    reviewer_id="reviewer-a",
                )
            if args.review_b_output:
                reviews["reviewB"] = export_answer_review_sheet(
                    args.output,
                    args.review_b_output,
                    reviewer_id="reviewer-b",
                )
            _print(
                {
                    "schemaVersion": report.get("schemaVersion"),
                    "runId": report.get("runId"),
                    "status": report.get("status"),
                    "dataset": report.get("dataset"),
                    "httpExecution": report.get("httpExecution"),
                    "handoffDecision": report.get("handoffDecision"),
                    "answerQuality": report.get("answerQuality"),
                    "output": str(args.output),
                    "reviews": reviews,
                }
            )
            return 0
        if args.customer_http_command == "rebuild":
            report = rebuild_customer_service_http_report(
                args.source_report,
                args.dataset,
            )
            verification = write_customer_service_http_evidence(
                report, args.output_dir
            )
            report_path = args.output_dir / "report.json"
            reviews: dict[str, Any] = {}
            if args.review_a_output:
                reviews["reviewA"] = export_answer_review_sheet(
                    report_path,
                    args.review_a_output,
                    reviewer_id="reviewer-a",
                )
            if args.review_b_output:
                reviews["reviewB"] = export_answer_review_sheet(
                    report_path,
                    args.review_b_output,
                    reviewer_id="reviewer-b",
                )
            _print(
                {
                    "schemaVersion": report.get("schemaVersion"),
                    "runId": report.get("runId"),
                    "status": report.get("status"),
                    "runtimeMetrics": report.get("runtimeMetrics"),
                    "citationContractDiagnostic": report.get(
                        "citationContractDiagnostic"
                    ),
                    "verification": verification,
                    "reviews": reviews,
                }
            )
            return 0
        if args.customer_http_command == "review-export":
            manifest = export_answer_review_sheet(
                args.report,
                args.output,
                reviewer_id=args.annotator,
                seed=args.seed,
            )
            _print(manifest)
            return 0
        if args.customer_http_command == "review-score":
            report = score_answer_review(args.report, args.review)
            atomic_write_json(args.output, report, overwrite=False)
            _print(
                {
                    "status": report.get("status"),
                    "caseCount": report.get("caseCount"),
                    "metrics": report.get("metrics"),
                    "badcaseCount": len(report.get("badcases") or []),
                    "output": str(args.output),
                }
            )
            return 0
        if args.customer_http_command == "review-validate":
            manifest = validate_answer_review_sheet(
                args.report,
                args.review,
                require_complete=args.complete,
            )
            _print({"valid": True, "manifest": manifest})
            return 0
        if args.customer_http_command == "review-seal":
            manifest = seal_answer_review_sheet(
                args.report,
                args.review,
                args.output,
            )
            _print(manifest)
            return 0
        if args.customer_http_command == "review-compare":
            agreement = compare_answer_reviews(
                args.report,
                args.review_a,
                args.review_b,
            )
            atomic_write_json(args.output, agreement, overwrite=False)
            if args.markdown_output:
                atomic_write_text(
                    args.markdown_output,
                    render_answer_agreement_markdown(agreement),
                    overwrite=False,
                )
            adjudication = None
            if args.adjudication_output:
                adjudication = export_answer_adjudication_template(
                    agreement, args.adjudication_output
                )
            _print(
                {
                    "status": agreement["status"],
                    "caseCount": agreement["caseCount"],
                    "exactAgreementCaseCount": agreement[
                        "exactAgreementCaseCount"
                    ],
                    "disagreementCaseCount": agreement["disagreementCaseCount"],
                    "caseAgreementRate": agreement["caseAgreementRate"],
                    "fieldStats": agreement["fieldStats"],
                    "output": str(args.output),
                    "markdownOutput": (
                        str(args.markdown_output) if args.markdown_output else None
                    ),
                    "adjudication": adjudication,
                }
            )
            return 0
        if args.customer_http_command == "review-package":
            agreement = compare_answer_reviews(
                args.report,
                args.review_a,
                args.review_b,
            )
            verification = write_pending_answer_review_evidence(
                args.report,
                agreement,
                review_a_path=args.review_a,
                review_b_path=args.review_b,
                output_dir=args.output_dir,
                adjudication_output=args.adjudication_output,
            )
            _print(
                {
                    "status": agreement["status"],
                    "caseCount": agreement["caseCount"],
                    "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
                    "disagreementCaseCount": agreement["disagreementCaseCount"],
                    "caseAgreementRate": agreement["caseAgreementRate"],
                    "fieldStats": agreement["fieldStats"],
                    "verification": verification,
                }
            )
            return 0
        if args.customer_http_command == "review-merge":
            final_report, agreement = merge_answer_reviews(
                args.report,
                args.review_a,
                args.review_b,
                adjudication_path=args.adjudication,
            )
            verification = write_answer_review_evidence(
                final_report,
                agreement,
                review_a_path=args.review_a,
                review_b_path=args.review_b,
                adjudication_path=args.adjudication,
                output_dir=args.output_dir,
            )
            _print(
                {
                    "status": final_report["status"],
                    "caseCount": final_report["caseCount"],
                    "agreement": final_report["agreement"],
                    "metrics": final_report["metrics"],
                    "badcaseCount": len(final_report["badcases"]),
                    "verification": verification,
                }
            )
            return 0
        if args.customer_http_command == "review-verify":
            _print(verify_answer_review_evidence(args.evidence_dir))
            return 0
        if args.customer_http_command == "review-pending-verify":
            _print(verify_pending_answer_review_evidence(args.evidence_dir))
            return 0
        raise AssertionError(
            f"unhandled customer-service HTTP command: {args.customer_http_command}"
        )
    if args.command == "customer-service-review":
        if args.review_command == "export":
            manifest = export_review_sheet(
                args.dataset,
                args.output,
                reviewer_id=args.annotator,
                seed=args.seed,
            )
            _print(manifest)
            return 0
        if args.review_command == "validate":
            manifest = validate_review_sheet(
                args.dataset,
                args.review,
                require_complete=args.complete,
            )
            _print({"valid": True, "manifest": manifest})
            return 0
        if args.review_command == "compare":
            agreement = compare_human_reviews(args.dataset, args.review_a, args.review_b)
            atomic_write_json(args.output, agreement, overwrite=False)
            if args.markdown_output:
                atomic_write_text(
                    args.markdown_output,
                    render_agreement_markdown(agreement),
                    overwrite=False,
                )
            _print(
                {
                    "status": agreement["status"],
                    "caseCount": agreement["caseCount"],
                    "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
                    "disagreementCaseCount": agreement["disagreementCaseCount"],
                    "caseAgreementRate": agreement["caseAgreementRate"],
                    "fieldStats": agreement["fieldStats"],
                    "slotStats": agreement["slotStats"],
                    "output": str(args.output),
                    "markdownOutput": str(args.markdown_output) if args.markdown_output else None,
                }
            )
            return 0
        if args.review_command == "seal":
            manifest = seal_review_sheet(args.dataset, args.review, args.output)
            _print(manifest)
            return 0
        if args.review_command == "merge":
            evidence = merge_human_reviews(
                args.dataset,
                args.review_a,
                args.review_b,
                output_dataset_path=args.output_dataset,
                evidence_path=args.evidence,
                adjudication_path=args.adjudication,
                default_adjudicator=args.adjudicator,
            )
            _print(evidence)
            return 0
        raise AssertionError(f"unhandled customer-service review command: {args.review_command}")
    if args.command == "slices":
        return await _slices(args)
    if args.command == "repeat":
        return await _repeat(args)
    if args.command == "fault-test":
        return await _fault_test(args)
    if args.command == "benchmark-capacity":
        concurrencies = parse_concurrency_levels(args.concurrency)
        selected_ids = tuple(args.case_id) or DEFAULT_CAPACITY_CASE_IDS
        _rows, cases = load_capacity_cases(args.dataset, case_ids=selected_ids)
        await init_pool()
        try:
            await redis_service.ensure_connected()
            preflight = await run_preflight(cases)
            report, observations = await benchmark_capacity(
                args.dataset,
                run_id=args.run_id,
                concurrencies=concurrencies,
                requests_per_level=args.requests_per_level,
                warmup_requests=args.warmup_requests,
                timeout_seconds=args.timeout_seconds,
                case_ids=selected_ids,
                preflight=preflight,
            )
        finally:
            await close_pool()
        root, digest = write_capacity_evidence(
            report,
            observations,
            benchmark_id=args.output_id or args.run_id,
        )
        _print(
            {
                "runId": args.run_id,
                "levels": report["levels"],
                "warmup": report["warmup"],
                "notProductionSlo": True,
                "evidence": str(root),
                "sha256SumsSha256": digest,
            }
        )
        warmup = report.get("warmup") or {}
        warmup_ok = (
            int(warmup.get("requestCount") or 0) == 0
            or float(warmup.get("successRate") or 0) == 1.0
        )
        return 0 if warmup_ok and all(
            float(level.get("successRate") or 0) == 1.0
            for level in report["levels"].values()
        ) else 2
    if args.command == "benchmark-db":
        sizes = [int(value.strip()) for value in str(args.sizes).split(",") if value.strip()]
        await init_pool()
        try:
            benchmark = await benchmark_db_sizes(
                sizes,
                iterations=args.iterations,
                isolated=not args.shared,
            )
        finally:
            await close_pool()
        benchmark_id = args.run_id or f"db-{utc_now().replace(':', '').replace('-', '').replace('.', '')}"
        root, digest = write_db_benchmark_evidence(benchmark, benchmark_id=benchmark_id)
        _print({**benchmark, "evidence": str(root), "sha256SumsSha256": digest})
        return 0
    if args.command == "seal-auxiliary":
        run_root = RUNS_ROOT / args.run_id
        root, digest = write_auxiliary_evidence(
            run_root,
            kind=args.kind,
            package_id=args.package_id,
            shadow_only=args.shadow_only,
        )
        _print(
            {
                "kind": args.kind,
                "packageId": args.package_id,
                "evidence": str(root),
                "sha256SumsSha256": digest,
            }
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(args)))
    except (EvaluationError, ValueError, FileExistsError) as exc:
        print(f"evaluation failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
