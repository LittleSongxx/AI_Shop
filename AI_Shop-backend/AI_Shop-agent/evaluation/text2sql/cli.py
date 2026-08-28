from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from evaluation.text2sql.answer_review import (
    adjudicate_answer_reviews,
    compare_answer_reviews,
    create_answer_review_packages,
    seal_answer_review,
    validate_answer_review,
)
from evaluation.text2sql.cases_v0 import write_candidates
from evaluation.text2sql.catalog import verify_catalog, write_catalog
from evaluation.text2sql.comparison import compare_baselines
from evaluation.text2sql.dataset import (
    DEFAULT_CATALOG,
    DEFAULT_DATASET,
    load_cases,
    validate_v0,
    verify_lock,
    write_lock,
)
from evaluation.text2sql.final_report import build_final_report
from evaluation.text2sql.fixture import (
    bootstrap,
    down,
    fingerprint,
    materialize_oracles,
    reset,
    source_data_fingerprint,
    up,
    verify,
)
from evaluation.text2sql.freeze import freeze_inputs
from evaluation.text2sql.io import read_json
from evaluation.text2sql.mutations import run_mutation_diagnostic
from evaluation.text2sql.review import (
    DEFAULT_WORKSPACE,
    adjudicate_gold,
    compare_reviews,
    create_open_packages,
    materialize_adjudication,
    materialize_review,
    seal_review,
    validate_review,
)
from evaluation.text2sql.runner import RunConfig, run
from evaluation.text2sql.runtime import (
    DEFAULT_SMOKE_EVIDENCE,
    build_admin,
    smoke,
    start,
    stop,
)
from evaluation.text2sql.sessions import seed_admin_sessions


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.text2sql.cli",
        description="Independent DEVELOPMENT/PROVISIONAL Text2SQL V0 evaluator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    catalog = sub.add_parser("catalog-generate")
    catalog.add_argument("--output", type=_path, default=DEFAULT_CATALOG)
    catalog.add_argument("--overwrite", action="store_true")
    catalog_verify = sub.add_parser("catalog-verify")
    catalog_verify.add_argument("--catalog", type=_path, default=DEFAULT_CATALOG)

    generate = sub.add_parser("dataset-generate")
    generate.add_argument("--output", type=_path, default=DEFAULT_DATASET)
    generate.add_argument("--overwrite", action="store_true")
    dataset_validate = sub.add_parser("dataset-validate")
    dataset_validate.add_argument("--dataset", type=_path, default=DEFAULT_DATASET)
    dataset_lock = sub.add_parser("dataset-lock")
    dataset_lock.add_argument("--overwrite", action="store_true")
    sub.add_parser("dataset-verify-lock")

    fixture_up = sub.add_parser("fixture-up")
    fixture_up.set_defaults(action=lambda _: up())
    fixture_bootstrap = sub.add_parser("fixture-bootstrap")
    fixture_bootstrap.add_argument("--state", choices=("base", "boundary", "empty"), default="base")
    fixture_reset = sub.add_parser("fixture-reset")
    fixture_reset.add_argument("--state", choices=("base", "boundary", "empty"), required=True)
    fixture_verify = sub.add_parser("fixture-verify")
    fixture_verify.set_defaults(action=lambda _: verify())
    fixture_fingerprint = sub.add_parser("fixture-fingerprint")
    fixture_fingerprint.set_defaults(action=lambda _: fingerprint())
    fixture_data_fingerprint = sub.add_parser("fixture-data-fingerprint")
    fixture_data_fingerprint.set_defaults(action=lambda _: source_data_fingerprint())
    fixture_down = sub.add_parser("fixture-down")
    fixture_down.set_defaults(action=lambda _: down())
    runtime_build = sub.add_parser("runtime-build-admin")
    runtime_build.set_defaults(action=lambda _: build_admin())
    runtime_start = sub.add_parser("runtime-start")
    runtime_start.add_argument("--rebuild-admin", action="store_true")
    runtime_stop = sub.add_parser("runtime-stop")
    runtime_stop.set_defaults(action=lambda _: stop())
    runtime_smoke = sub.add_parser("runtime-smoke")
    runtime_smoke.add_argument("--output", type=_path, default=DEFAULT_SMOKE_EVIDENCE)
    runtime_smoke.set_defaults(action=lambda args: smoke(args.output))
    session_seed = sub.add_parser("runtime-seed-sessions")
    session_seed.set_defaults(
        action=lambda _: {"tokens": seed_admin_sessions(load_cases()), "seeded": True}
    )

    materialize = sub.add_parser("dataset-materialize")
    materialize.add_argument("--dataset", type=_path, default=DEFAULT_DATASET)
    materialize.add_argument("--overwrite", action="store_true")
    mutation = sub.add_parser("mutation-run")
    mutation.add_argument("output", type=_path)

    review_open = sub.add_parser("review-open")
    review_open.add_argument("--output", type=_path, default=DEFAULT_WORKSPACE)
    review_open.add_argument("--overwrite", action="store_true")
    review_validate = sub.add_parser("review-validate")
    review_validate.add_argument("directory", type=_path)
    review_validate.add_argument("--complete", action="store_true")
    review_materialize = sub.add_parser("review-materialize")
    review_materialize.add_argument("directory", type=_path)
    adjudication_materialize = sub.add_parser("review-adjudication-materialize")
    adjudication_materialize.add_argument("directory", type=_path)
    review_seal = sub.add_parser("review-seal")
    review_seal.add_argument("directory", type=_path)
    review_seal.add_argument("output", type=_path)
    review_compare = sub.add_parser("review-compare")
    review_compare.add_argument("sealed_a", type=_path)
    review_compare.add_argument("sealed_b", type=_path)
    review_compare.add_argument("output", type=_path)
    adjudicate = sub.add_parser("review-adjudicate")
    adjudicate.add_argument("sealed_a", type=_path)
    adjudicate.add_argument("sealed_b", type=_path)
    adjudicate.add_argument("comparison", type=_path)
    adjudicate.add_argument("output", type=_path)
    baseline = sub.add_parser("baseline-run")
    baseline.add_argument("--phase", choices=("pre-foundation", "post-foundation"), required=True)
    baseline.add_argument("--dataset", type=_path, required=True)
    baseline.add_argument("--output", type=_path, required=True)
    baseline_compare = sub.add_parser("baseline-compare")
    baseline_compare.add_argument("--pre", type=_path, required=True)
    baseline_compare.add_argument("--post", type=_path, required=True)
    baseline_compare.add_argument("--dataset", type=_path, required=True)
    baseline_compare.add_argument("--output", type=_path, required=True)
    answer_review_open = sub.add_parser("answer-review-open")
    answer_review_open.add_argument("--pre", type=_path, required=True)
    answer_review_open.add_argument("--post", type=_path, required=True)
    answer_review_open.add_argument("--dataset", type=_path, required=True)
    answer_review_open.add_argument("--output", type=_path, required=True)
    answer_review_validate = sub.add_parser("answer-review-validate")
    answer_review_validate.add_argument("directory", type=_path)
    answer_review_validate.add_argument("--review-file", type=_path)
    answer_review_validate.add_argument("--complete", action="store_true")
    answer_review_seal = sub.add_parser("answer-review-seal")
    answer_review_seal.add_argument("directory", type=_path)
    answer_review_seal.add_argument("output", type=_path)
    answer_review_seal.add_argument("--review-file", type=_path)
    answer_review_compare = sub.add_parser("answer-review-compare")
    answer_review_compare.add_argument("sealed_a", type=_path)
    answer_review_compare.add_argument("sealed_b", type=_path)
    answer_review_compare.add_argument("control", type=_path)
    answer_review_compare.add_argument("output", type=_path)
    answer_review_adjudicate = sub.add_parser("answer-review-adjudicate")
    answer_review_adjudicate.add_argument("sealed_a", type=_path)
    answer_review_adjudicate.add_argument("sealed_b", type=_path)
    answer_review_adjudicate.add_argument("comparison", type=_path)
    answer_review_adjudicate.add_argument("control", type=_path)
    answer_review_adjudicate.add_argument("output", type=_path)
    final_report = sub.add_parser("final-report")
    final_report.add_argument("--pre", type=_path, required=True)
    final_report.add_argument("--post", type=_path, required=True)
    final_report.add_argument("--paired", type=_path, required=True)
    final_report.add_argument("--gold", type=_path, required=True)
    final_report.add_argument("--answer-review", type=_path, required=True)
    final_report.add_argument("--verification", type=_path, required=True)
    final_report.add_argument("--output", type=_path, required=True)
    freeze = sub.add_parser("freeze-inputs")
    freeze.add_argument("--dataset", type=_path, required=True)
    freeze.add_argument("--output", type=_path, required=True)

    return parser


def _dispatch(args: argparse.Namespace) -> Any:
    action: Callable[[argparse.Namespace], Any] | None = getattr(args, "action", None)
    if action is not None:
        return action(args)
    command = args.command
    if command == "catalog-generate":
        catalog = write_catalog(args.output, overwrite=args.overwrite)
        return {"output": str(args.output), **verify_catalog(catalog)}
    if command == "catalog-verify":
        return verify_catalog(read_json(args.catalog))
    if command == "dataset-generate":
        cases = write_candidates(args.output, overwrite=args.overwrite)
        return {"output": str(args.output), "summary": validate_v0(cases)}
    if command == "dataset-validate":
        return validate_v0(load_cases(args.dataset))
    if command == "dataset-lock":
        return write_lock(load_cases(), overwrite=args.overwrite)
    if command == "dataset-verify-lock":
        return verify_lock()
    if command == "fixture-bootstrap":
        return bootstrap(state=args.state)
    if command == "fixture-reset":
        return reset(args.state)
    if command == "runtime-start":
        return start(rebuild_admin=args.rebuild_admin)
    if command == "dataset-materialize":
        return materialize_oracles(args.dataset, overwrite=args.overwrite)
    if command == "mutation-run":
        return run_mutation_diagnostic(args.output)
    if command == "review-open":
        return create_open_packages(args.output, overwrite=args.overwrite)
    if command == "review-validate":
        return validate_review(args.directory, require_complete=args.complete)
    if command == "review-materialize":
        return materialize_review(args.directory)
    if command == "review-adjudication-materialize":
        return materialize_adjudication(args.directory)
    if command == "review-seal":
        return seal_review(args.directory, args.output)
    if command == "review-compare":
        return compare_reviews(args.sealed_a, args.sealed_b, args.output)
    if command == "review-adjudicate":
        return adjudicate_gold(
            args.sealed_a,
            args.sealed_b,
            args.comparison,
            args.output,
        )
    if command == "baseline-run":
        return run(
            RunConfig(
                phase=args.phase,
                dataset=args.dataset,
                output=args.output,
            )
        )
    if command == "baseline-compare":
        return compare_baselines(args.pre, args.post, args.dataset, args.output)
    if command == "answer-review-open":
        return create_answer_review_packages(args.pre, args.post, args.dataset, args.output)
    if command == "answer-review-validate":
        return validate_answer_review(
            args.directory,
            review_file=args.review_file,
            require_complete=args.complete,
        )
    if command == "answer-review-seal":
        return seal_answer_review(args.directory, args.output, review_file=args.review_file)
    if command == "answer-review-compare":
        return compare_answer_reviews(args.sealed_a, args.sealed_b, args.control, args.output)
    if command == "answer-review-adjudicate":
        return adjudicate_answer_reviews(
            args.sealed_a,
            args.sealed_b,
            args.comparison,
            args.control,
            args.output,
        )
    if command == "final-report":
        return build_final_report(
            pre=args.pre,
            post=args.post,
            paired=args.paired,
            gold=args.gold,
            answer_review=args.answer_review,
            verification=args.verification,
            output=args.output,
        )
    if command == "freeze-inputs":
        return freeze_inputs(args.dataset, args.output)
    raise AssertionError(f"unhandled command: {command}")


def main() -> None:
    args = _parser().parse_args()
    _emit(_dispatch(args))


if __name__ == "__main__":
    main()
