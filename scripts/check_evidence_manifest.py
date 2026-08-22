#!/usr/bin/env python3
"""Validate the single AI evaluation evidence chain used by AI Shop."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "evidence-manifest.json"
PROJECT_SCHEMA = "aishop-project-evidence/v2"
SUITE_SCHEMA = "aishop-evaluation/v2"  # compatibility alias used by contract tests
SUITE_SCHEMAS = {"aishop-evaluation/v2", "aishop-evaluation/v3"}
LOCK_SCHEMA = "aishop-evaluation-dataset-lock/v2"  # compatibility alias
LOCK_SCHEMAS = {"aishop-evaluation-dataset-lock/v2", "aishop-evaluation-dataset-lock/v3"}
EVIDENCE_SCHEMA = "aishop-evaluation-evidence/v2"  # compatibility alias
EVIDENCE_SCHEMAS = {"aishop-evaluation-evidence/v2", "aishop-evaluation-evidence/v3"}
RUN_SCHEMA = "aishop-evaluation-run/v2"  # compatibility alias
RUN_SCHEMAS = {"aishop-evaluation-run/v2", "aishop-evaluation-run/v3"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LEGACY_TOKENS = (
    "benchmarks/eval.py",
    "aishop-eval/v1",
    "search-v3",
    "rag-v5",
    "FAILED_RETAINED",
)


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _resolve(root: Path, relative: str) -> Path:
    if not relative:
        raise ValueError("repository-relative path is empty")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {relative}") from exc
    return candidate


def _validate_suite(root: Path, descriptor: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        errors.append("evaluation.suite must be an object")
        return {}
    relative = str(descriptor.get("path") or "")
    try:
        path = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return {}
    if not path.is_file():
        errors.append(f"evaluation suite is missing: {relative}")
        return {}
    expected_sha = str(descriptor.get("sha256") or "")
    if not HEX64.fullmatch(expected_sha) or _sha256(path) != expected_sha:
        errors.append(f"evaluation suite hash mismatch: {relative}")
    try:
        suite = _json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid evaluation suite {relative}: {exc}")
        return {}
    if suite.get("schemaVersion") not in SUITE_SCHEMAS:
        errors.append(
            "evaluation suite schema must be one of "
            + ", ".join(sorted(SUITE_SCHEMAS))
        )
    if set((suite.get("domains") or {}).keys()) != {"search", "rag", "agent"}:
        errors.append("evaluation suite must contain exactly search, rag, and agent")
    if set((suite.get("splitMinimums") or {}).keys()) != {
        "development",
        "regression",
        "final",
    }:
        errors.append("evaluation suite must predeclare all three split minimums")
    return suite


def _validate_lock(
    root: Path,
    descriptor: Any,
    suite: dict[str, Any],
    errors: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(descriptor, dict):
        errors.append("evaluation.datasetLocks entries must be objects")
        return []
    split = str(descriptor.get("split") or "")
    relative = str(descriptor.get("path") or "")
    try:
        path = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return []
    if not path.is_file():
        errors.append(f"dataset lock is missing: {relative}")
        return []
    expected_sha = str(descriptor.get("sha256") or "")
    if not HEX64.fullmatch(expected_sha) or _sha256(path) != expected_sha:
        errors.append(f"dataset lock hash mismatch: {relative}")
    try:
        lock = _json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid dataset lock {relative}: {exc}")
        return []
    if lock.get("schemaVersion") not in LOCK_SCHEMAS:
        errors.append(f"dataset lock schema is invalid: {relative}")
    if lock.get("split") != split or split not in {"development", "regression"}:
        errors.append(f"dataset lock split is invalid: {relative}")
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        errors.append(f"dataset lock has no files: {relative}")
        return []

    rows: list[dict[str, Any]] = []
    for dataset_relative, facts in sorted(files.items()):
        try:
            dataset_path = _resolve(root, str(dataset_relative))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not dataset_path.is_file():
            errors.append(f"locked dataset file is missing: {dataset_relative}")
            continue
        if not isinstance(facts, dict):
            errors.append(f"locked dataset facts are invalid: {dataset_relative}")
            continue
        if _sha256(dataset_path) != facts.get("sha256"):
            errors.append(f"locked dataset file hash mismatch: {dataset_relative}")
        if dataset_path.stat().st_size != facts.get("bytes"):
            errors.append(f"locked dataset file size mismatch: {dataset_relative}")
        try:
            rows.extend(_jsonl(dataset_path))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid dataset file {dataset_relative}: {exc}")

    ids = [str(row.get("id") or "") for row in rows]
    if not ids or "" in ids or len(ids) != len(set(ids)):
        errors.append(f"dataset case IDs are empty or duplicated: {split}")
    if any(row.get("split") != split for row in rows):
        errors.append(f"dataset contains rows from another split: {split}")
    counts = {
        domain: sum(row.get("domain") == domain for row in rows)
        for domain in ("search", "rag", "agent")
    }
    if lock.get("caseCount") != len(rows) or lock.get("domainCounts") != counts:
        errors.append(f"dataset lock counts differ from current rows: {split}")
    canonical_rows = sorted(rows, key=lambda row: str(row.get("id") or ""))
    if lock.get("canonicalDatasetSha256") != _canonical_sha256(canonical_rows):
        errors.append(f"dataset canonical hash mismatch: {split}")
    minimums = (suite.get("splitMinimums") or {}).get(split) or {}
    for domain, minimum in minimums.items():
        if counts.get(domain, 0) < int(minimum):
            errors.append(f"{split}.{domain} is below its predeclared minimum")
    return rows


def _validate_customer_service_gold(
    root: Path,
    descriptor: Any,
    errors: list[str],
) -> None:
    """Cross-check the independent客服 quality dataset and generated report."""

    label = "evaluation.customerServiceGold"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    dataset_relative = str(descriptor.get("datasetPath") or "")
    report_relative = str(descriptor.get("reportPath") or "")
    try:
        dataset_path = _resolve(root, dataset_relative)
        report_path = _resolve(root, report_relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not dataset_path.is_file():
        errors.append(f"{label} dataset is missing: {dataset_relative}")
        return
    if not report_path.is_file():
        errors.append(f"{label} report is missing: {report_relative}")
        return
    expected_dataset_sha = str(descriptor.get("datasetSha256") or "")
    actual_dataset_sha = _sha256(dataset_path)
    if not HEX64.fullmatch(expected_dataset_sha) or expected_dataset_sha != actual_dataset_sha:
        errors.append(f"{label} dataset hash mismatch: {dataset_relative}")
    expected_report_sha = str(descriptor.get("reportSha256") or "")
    actual_report_sha = _sha256(report_path)
    if not HEX64.fullmatch(expected_report_sha) or expected_report_sha != actual_report_sha:
        errors.append(f"{label} report hash mismatch: {report_relative}")
    try:
        rows = _jsonl(dataset_path)
        report = _json(report_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{label} payload is invalid: {exc}")
        return
    case_count = descriptor.get("caseCount")
    if case_count != len(rows):
        errors.append(f"{label} caseCount differs from dataset: {case_count!r} != {len(rows)}")
    ids = [str(row.get("id") or "") for row in rows]
    if not ids or "" in ids or len(ids) != len(set(ids)):
        errors.append(f"{label} dataset IDs are empty or duplicated")
    if any(row.get("schemaVersion") != "aishop-customer-service-gold/v1" for row in rows):
        errors.append(f"{label} dataset contains an unsupported schema")
    if report.get("schemaVersion") != "aishop-customer-service-evidence/v1":
        errors.append(f"{label} report schema is invalid")
    review_plan = report.get("humanReviewPlan")
    if not isinstance(review_plan, dict):
        errors.append(f"{label} humanReviewPlan is missing")
    elif descriptor.get("status") == "PROVISIONAL_NOT_HUMAN_GOLD":
        if review_plan.get("status") != "PENDING_INDEPENDENT_REVIEW":
            errors.append(f"{label} provisional report must keep human review pending")
        if review_plan.get("requiredAnnotators") != 2:
            errors.append(f"{label} provisional report must require two annotators")
        if review_plan.get("blindedFirstPass") is not True:
            errors.append(f"{label} provisional report must require a blinded first pass")
    report_dataset = report.get("dataset") or {}
    if report_dataset.get("caseCount") != len(rows):
        errors.append(f"{label} report dataset caseCount is stale")
    if report_dataset.get("sha256") != actual_dataset_sha:
        errors.append(f"{label} report dataset hash is stale")
    if report.get("status") != descriptor.get("status"):
        errors.append(f"{label} status differs from project manifest")
    if report.get("releaseGateEligible") is not False or descriptor.get("releaseGateEligible") is not False:
        errors.append(f"{label} draft gold must remain releaseGateEligible=false")
    known_ids = set(ids)
    for badcase in report.get("badcases") or []:
        if not isinstance(badcase, dict) or str(badcase.get("caseId") or "") not in known_ids:
            errors.append(f"{label} contains a badcase ID absent from dataset")
            break


def _parse_sums(root: Path, errors: list[str]) -> dict[str, str]:
    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        errors.append(f"published evidence lacks SHA256SUMS: {root}")
        return {}
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or not HEX64.fullmatch(digest) or not name or name in expected:
            errors.append(f"invalid SHA256SUMS line: {line!r}")
            continue
        try:
            _resolve(root, name)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        errors.append("published evidence file set differs from SHA256SUMS")
    for name, digest in expected.items():
        path = root / name
        if path.is_file() and _sha256(path) != digest:
            errors.append(f"published evidence hash mismatch: {name}")
    return expected


def _validate_evidence_package(
    root: Path,
    descriptor: Any,
    errors: list[str],
    *,
    label: str,
    require_lifecycle: bool = True,
) -> None:
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    relative = str(descriptor.get("path") or "")
    try:
        evidence_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not evidence_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return
    sums = _parse_sums(evidence_root, errors)
    sums_path = evidence_root / "SHA256SUMS"
    expected_sums = descriptor.get("sha256SumsSha256")
    if expected_sums is not None and (
        not sums_path.is_file() or expected_sums != _sha256(sums_path)
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    required = {
        "bad-cases.jsonl",
        "cases.jsonl",
        "environment.json",
        "evidence-manifest.json",
        "gates.json",
        "report.md",
        "source-fingerprint.json",
        "summary.json",
    }
    if require_lifecycle:
        required.add("lifecycle.json")
    if not required.issubset(sums):
        errors.append(f"{label} is missing files: {sorted(required - set(sums))}")
        return
    try:
        manifest = _json(evidence_root / "evidence-manifest.json")
        summary = _json(evidence_root / "summary.json")
        gates = _json(evidence_root / "gates.json")
        lifecycle = (
            _json(evidence_root / "lifecycle.json")
            if (evidence_root / "lifecycle.json").is_file()
            else {}
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"{label} JSON is invalid: {exc}")
        return
    run = manifest.get("run") or {}
    if manifest.get("schemaVersion") not in EVIDENCE_SCHEMAS:
        errors.append(f"{label} manifest schema is invalid")
    if run.get("schemaVersion") not in RUN_SCHEMAS or run.get("split") != "final":
        errors.append(f"{label} is not a supported final run")
    if descriptor.get("runId") is not None and run.get("runId") != descriptor.get("runId"):
        errors.append(f"{label} runId differs from project manifest")
    if (
        descriptor.get("datasetSha256") is not None
        and run.get("datasetSha256") != descriptor.get("datasetSha256")
    ):
        errors.append(f"{label} dataset hash differs from project manifest")
    source_fingerprint = run.get("sourceFingerprint") or {}
    expected_source = descriptor.get("sourceSha256")
    if expected_source is not None and (
        (source_fingerprint.get("source") or {}).get("sha256") != expected_source
    ):
        errors.append(f"{label} source hash differs from project manifest")
    expected_provider = descriptor.get("providerConfigurationSha256")
    if expected_provider is not None and (
        source_fingerprint.get("providerConfigurationSha256") != expected_provider
    ):
        errors.append(f"{label} provider configuration hash differs from project manifest")
    if (
        descriptor.get("qualityGatePassed") is not None
        and bool(gates.get("passed")) != descriptor.get("qualityGatePassed")
    ):
        errors.append(f"{label} gate outcome differs from project manifest")
    if summary != run.get("summary") or gates != run.get("gates"):
        errors.append(f"{label} standalone summary/gates differ from the run manifest")
    if require_lifecycle:
        if lifecycle.get("status") != "EXECUTED":
            errors.append(f"{label} lifecycle is not EXECUTED")
        expected_outcome = "PASSED" if gates.get("passed") else "FAILED"
        if (lifecycle.get("run") or {}).get("outcome") != expected_outcome:
            errors.append(f"{label} lifecycle outcome differs from hard gates")
    expected_release = descriptor.get("releaseId")
    if expected_release is not None and lifecycle.get("releaseId") != expected_release:
        errors.append(f"{label} releaseId differs from project manifest")
    expected_hash = descriptor.get("evidenceSha256")
    lifecycle_hash = (lifecycle.get("run") or {}).get("evidenceSha256")
    if expected_hash is not None and lifecycle_hash not in {None, expected_hash}:
        errors.append(f"{label} lifecycle evidence hash differs from project manifest")
    # An archive is immutable evidence, not merely a copied directory. Reject
    # writable files so a later command cannot silently alter its contents.
    if label.startswith(("evaluation.archive", "evaluation.failedFinalAttempt")):
        writable = [
            str(path.relative_to(evidence_root))
            for path in evidence_root.rglob("*")
            if path.is_file() and path.stat().st_mode & 0o222
        ]
        if writable:
            errors.append(f"{label} contains writable files: {writable}")


def _validate_current_evidence(
    root: Path,
    descriptor: Any,
    errors: list[str],
) -> None:
    if not isinstance(descriptor, dict):
        errors.append("evaluation.currentEvidence must be an object when final is published")
        return
    _validate_evidence_package(
        root,
        descriptor,
        errors,
        label="evaluation.currentEvidence",
        require_lifecycle=True,
    )


def _validate_archives(root: Path, descriptors: Any, errors: list[str]) -> None:
    if descriptors is None:
        return
    if not isinstance(descriptors, list):
        errors.append("evaluation.archives must be an array")
        return
    seen: set[str] = set()
    for index, descriptor in enumerate(descriptors, 1):
        if not isinstance(descriptor, dict):
            errors.append(f"evaluation.archives[{index}] must be an object")
            continue
        release_id = str(descriptor.get("releaseId") or "")
        if not release_id or release_id in seen:
            errors.append(f"evaluation.archives contains duplicate/empty releaseId: {release_id!r}")
        seen.add(release_id)
        _validate_evidence_package(
            root,
            descriptor,
            errors,
            label=f"evaluation.archive[{release_id or index}]",
            require_lifecycle=True,
        )


def _validate_failed_final_attempts(
    root: Path,
    descriptors: Any,
    errors: list[str],
) -> None:
    """Keep one-shot failed finals visible, hashed, and immutable.

    A failed final must never replace ``currentEvidence``. It still belongs in
    the project evidence chain so deleting or rewriting the failed attempt is
    detectable instead of silently erasing an unfavorable result.
    """

    if descriptors is None:
        return
    if not isinstance(descriptors, list):
        errors.append("evaluation.failedFinalAttempts must be an array")
        return
    seen_releases: set[str] = set()
    seen_runs: set[str] = set()
    seen_paths: set[str] = set()
    for index, descriptor in enumerate(descriptors, 1):
        if not isinstance(descriptor, dict):
            errors.append(f"evaluation.failedFinalAttempts[{index}] must be an object")
            continue
        release_id = str(descriptor.get("releaseId") or "")
        run_id = str(descriptor.get("runId") or "")
        relative = str(descriptor.get("path") or "")
        for value, seen, field in (
            (release_id, seen_releases, "releaseId"),
            (run_id, seen_runs, "runId"),
            (relative, seen_paths, "path"),
        ):
            if not value or value in seen:
                errors.append(
                    "evaluation.failedFinalAttempts contains "
                    f"duplicate/empty {field}: {value!r}"
                )
            seen.add(value)
        if descriptor.get("qualityGatePassed") is not False:
            errors.append(
                f"evaluation.failedFinalAttempt[{release_id or index}] "
                "must declare qualityGatePassed=false"
            )
        if descriptor.get("outcome") != "FAILED":
            errors.append(
                f"evaluation.failedFinalAttempt[{release_id or index}] "
                "must declare outcome=FAILED"
            )
        _validate_evidence_package(
            root,
            descriptor,
            errors,
            label=f"evaluation.failedFinalAttempt[{release_id or index}]",
            require_lifecycle=True,
        )


def _validate_visible_runs(
    root: Path,
    descriptors: Any,
    locked_dataset_hashes: dict[str, str],
    errors: list[str],
) -> None:
    """Cross-check the development/regression evidence named by the project manifest."""

    if not isinstance(descriptors, dict) or set(descriptors) != {
        "development",
        "regression",
    }:
        errors.append("visibleRuns must contain exactly development and regression")
        return
    seen_run_ids: set[str] = set()
    seen_paths: set[str] = set()
    for split in ("development", "regression"):
        descriptor = descriptors.get(split)
        label = f"visibleRuns.{split}"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} must be an object")
            continue
        relative = str(descriptor.get("path") or "")
        try:
            run_root = _resolve(root, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not run_root.is_dir():
            errors.append(f"{label} directory is missing: {relative}")
            continue
        if relative in seen_paths:
            errors.append(f"visibleRuns contains duplicate path: {relative}")
        seen_paths.add(relative)

        sums = _parse_sums(run_root, errors)
        sums_path = run_root / "SHA256SUMS"
        expected_sums = descriptor.get("sha256SumsSha256")
        if expected_sums is not None and (
            not sums_path.is_file() or expected_sums != _sha256(sums_path)
        ):
            errors.append(f"{label} SHA256SUMS digest differs from project manifest")
        required = {
            "bad-cases.jsonl",
            "cases.jsonl",
            "environment.json",
            "evidence-manifest.json",
            "gates.json",
            "report.md",
            "source-fingerprint.json",
            "summary.json",
        }
        if not required.issubset(sums):
            errors.append(f"{label} is missing files: {sorted(required - set(sums))}")
            continue
        try:
            package = _json(run_root / "evidence-manifest.json")
            summary = _json(run_root / "summary.json")
            gates = _json(run_root / "gates.json")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label} JSON is invalid: {exc}")
            continue
        run = package.get("run") or {}
        run_id = str(run.get("runId") or "")
        if package.get("schemaVersion") not in EVIDENCE_SCHEMAS:
            errors.append(f"{label} manifest schema is invalid")
        if run.get("schemaVersion") not in RUN_SCHEMAS or run.get("split") != split:
            errors.append(f"{label} is not a supported {split} run")
        if not run_id or run_id in seen_run_ids:
            errors.append(f"visibleRuns contains duplicate/empty runId: {run_id!r}")
        seen_run_ids.add(run_id)
        if descriptor.get("runId") != run_id:
            errors.append(f"{label} runId differs from project manifest")
        dataset_hash = str(run.get("datasetSha256") or "")
        if descriptor.get("datasetSha256") != dataset_hash:
            errors.append(f"{label} dataset hash differs from project manifest")
        if locked_dataset_hashes.get(split) != dataset_hash:
            errors.append(f"{label} dataset hash differs from the current {split} lock")
        if descriptor.get("qualityGatePassed") is not None and (
            bool(gates.get("passed")) != descriptor.get("qualityGatePassed")
        ):
            errors.append(f"{label} gate outcome differs from project manifest")
        if summary != run.get("summary") or gates != run.get("gates"):
            errors.append(f"{label} standalone summary/gates differ from the run manifest")
        expected_source = descriptor.get("sourceSha256")
        actual_source = (
            ((run.get("sourceFingerprint") or {}).get("source") or {}).get("sha256")
        )
        if expected_source is not None and expected_source != actual_source:
            errors.append(f"{label} source hash differs from project manifest")


def _validate_benchmarks(root: Path, descriptors: Any, errors: list[str]) -> None:
    """Validate immutable non-quality benchmark packages when declared."""

    if descriptors is None:
        return
    if not isinstance(descriptors, list):
        errors.append("evaluation.benchmarks must be an array")
        return
    seen: set[str] = set()
    for index, descriptor in enumerate(descriptors, 1):
        if not isinstance(descriptor, dict):
            errors.append(f"evaluation.benchmarks[{index}] must be an object")
            continue
        relative = str(descriptor.get("path") or "")
        try:
            benchmark_root = _resolve(root, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not benchmark_root.is_dir():
            errors.append(f"evaluation benchmark directory is missing: {relative}")
            continue
        benchmark_id = str(descriptor.get("benchmarkId") or "")
        if not benchmark_id or benchmark_id in seen:
            errors.append(f"evaluation.benchmarks contains duplicate/empty benchmarkId: {benchmark_id!r}")
        seen.add(benchmark_id)
        sums = _parse_sums(benchmark_root, errors)
        sums_path = benchmark_root / "SHA256SUMS"
        expected_sums = descriptor.get("sha256SumsSha256")
        if expected_sums is not None and (
            not sums_path.is_file() or expected_sums != _sha256(sums_path)
        ):
            errors.append(f"evaluation benchmark SHA256SUMS digest differs: {relative}")
        required = {"benchmark.json", "evidence-manifest.json", "report.md"}
        if not required.issubset(sums):
            errors.append(f"evaluation benchmark is missing files: {sorted(required - set(sums))}")
            continue
        try:
            payload = _json(benchmark_root / "benchmark.json")
            package = _json(benchmark_root / "evidence-manifest.json")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"evaluation benchmark JSON is invalid: {relative}: {exc}")
            continue
        if payload.get("benchmarkId") != benchmark_id:
            errors.append(f"evaluation benchmark ID differs from descriptor: {relative}")
        if payload.get("notProductionSlo") is not True:
            errors.append(f"evaluation benchmark must declare notProductionSlo=true: {relative}")
        package_schema = package.get("schemaVersion")
        if package_schema not in {
            "aishop-db-benchmark-evidence/v1",
            "aishop-db-benchmark-evidence/v2",
        }:
            errors.append(f"evaluation benchmark schema is invalid: {relative}")
        if package.get("benchmarkId") != benchmark_id:
            errors.append(f"evaluation benchmark manifest ID differs: {relative}")
        inventory = {
            name: {"sha256": _sha256(benchmark_root / name), "bytes": (benchmark_root / name).stat().st_size}
            for name in sums
            if name != "evidence-manifest.json"
        }
        if package.get("files") != inventory:
            errors.append(f"evaluation benchmark file inventory is stale: {relative}")
        if package_schema == "aishop-db-benchmark-evidence/v2":
            if payload.get("schemaVersion") != "aishop-db-benchmark/v2":
                errors.append(f"evaluation benchmark v2 payload schema is invalid: {relative}")
            if package.get("benchmarkSchemaVersion") != payload.get("schemaVersion"):
                errors.append(f"evaluation benchmark v2 schema link is invalid: {relative}")
            source_facts = payload.get("sourceFingerprint") or {}
            if package.get("sourceSha256") != (source_facts.get("source") or {}).get(
                "sha256"
            ):
                errors.append(f"evaluation benchmark source fingerprint differs: {relative}")
            if package.get("providerConfigurationSha256") != source_facts.get(
                "providerConfigurationSha256"
            ):
                errors.append(f"evaluation benchmark provider fingerprint differs: {relative}")
            rollback = payload.get("rollbackProbe") or {}
            if rollback.get("passed") is not True or (
                rollback.get("beforeCount"),
                rollback.get("insideTransactionCount"),
                rollback.get("afterRollbackCount"),
                rollback.get("committedWrites"),
            ) != (0, 1, 0, 0):
                errors.append(f"evaluation benchmark rollback evidence is invalid: {relative}")
            if package.get("rollbackProbePassed") is not True:
                errors.append(f"evaluation benchmark manifest omits rollback result: {relative}")
            database_facts = payload.get("databaseFacts") or {}
            if package.get("dedicatedBenchmarkDatabase") != database_facts.get(
                "dedicatedBenchmarkDatabase"
            ):
                errors.append(f"evaluation benchmark isolation claim differs: {relative}")
            for size, row in (payload.get("rows") or {}).items():
                label = f"{relative} size={size}"
                try:
                    candidate_count = int(row.get("candidateCount"))
                    unique_count = int(row.get("uniqueCandidateCount"))
                except (TypeError, ValueError):
                    errors.append(f"evaluation benchmark candidate counts are invalid: {label}")
                    continue
                if candidate_count != unique_count:
                    errors.append(f"evaluation benchmark candidates are not unique: {label}")
                equivalence = row.get("resultEquivalence") or {}
                if equivalence.get("offerSnapshot") is not True or equivalence.get(
                    "decisionFeature"
                ) is not True:
                    errors.append(f"evaluation benchmark result sets differ: {label}")
                for measurement_name in (
                    "batchOfferSnapshot",
                    "nPlusOneOfferSnapshot",
                    "batchDecisionFeature",
                    "nPlusOneDecisionFeature",
                ):
                    measurement = row.get(measurement_name) or {}
                    if measurement.get("counterSource") != (
                        "COUNTED_CURSOR_EXECUTE_AND_POOL_ACQUIRE_CALLS"
                    ):
                        errors.append(
                            f"evaluation benchmark has unverified query counts: "
                            f"{label} {measurement_name}"
                        )
                    if measurement.get("stableResult") is not True:
                        errors.append(
                            f"evaluation benchmark result is unstable: "
                            f"{label} {measurement_name}"
                        )
        writable = [
            str(path.relative_to(benchmark_root))
            for path in benchmark_root.rglob("*")
            if path.is_file() and path.stat().st_mode & 0o222
        ]
        if writable:
            errors.append(f"evaluation benchmark contains writable files: {writable}")


def _validate_auxiliary_evidence(root: Path, descriptors: Any, errors: list[str]) -> None:
    """Validate immutable resilience/repeated-Agent packages.

    These packages intentionally live outside the normal quality denominator.
    They may reference a source ``.runs`` directory that has since been
    garbage-collected; the package's own source run ID and SHA256SUMS digest
    are the durable provenance boundary.
    """

    if descriptors is None:
        return
    if not isinstance(descriptors, list):
        errors.append("evaluation.auxiliaryEvidence must be an array")
        return
    seen: set[str] = set()
    for index, descriptor in enumerate(descriptors, 1):
        label = f"evaluation.auxiliaryEvidence[{index}]"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} must be an object")
            continue
        kind = str(descriptor.get("kind") or "")
        package_id = str(descriptor.get("packageId") or "")
        if kind not in {"resilience", "repeated-agent"}:
            errors.append(f"{label} kind is invalid: {kind!r}")
        if not package_id or package_id in seen:
            errors.append(f"{label} contains duplicate/empty packageId: {package_id!r}")
        seen.add(package_id)
        relative = str(descriptor.get("path") or "")
        try:
            package_root = _resolve(root, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not package_root.is_dir():
            errors.append(f"{label} directory is missing: {relative}")
            continue
        sums = _parse_sums(package_root, errors)
        sums_path = package_root / "SHA256SUMS"
        expected_sums = descriptor.get("sha256SumsSha256")
        if expected_sums is not None and (
            not sums_path.is_file() or expected_sums != _sha256(sums_path)
        ):
            errors.append(f"{label} SHA256SUMS digest differs from project manifest")
        required = {
            "bad-cases.jsonl",
            "cases.jsonl",
            "evidence-manifest.json",
            "environment.json",
            "gates.json",
            "report.md",
            "source-fingerprint.json",
            "summary.json",
        }
        if not required.issubset(sums):
            errors.append(f"{label} is missing files: {sorted(required - set(sums))}")
            continue
        try:
            package = _json(package_root / "evidence-manifest.json")
            summary = _json(package_root / "summary.json")
            gates = _json(package_root / "gates.json")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label} JSON is invalid: {exc}")
            continue
        if package.get("schemaVersion") != "aishop-auxiliary-evidence/v1":
            errors.append(f"{label} schema is invalid")
        if package.get("kind") != kind or package.get("packageId") != package_id:
            errors.append(f"{label} kind/package ID differs from descriptor")
        if package.get("normalQualityDenominatorExcluded") is not True:
            errors.append(f"{label} must be excluded from normal quality denominator")
        source_digest = str(package.get("sourceRunSha256SumsSha256") or "")
        if not HEX64.fullmatch(source_digest):
            errors.append(f"{label} source run SHA256SUMS digest is invalid")
        if not isinstance(package.get("sourceRunId"), str) or not package.get("sourceRunId"):
            errors.append(f"{label} source run ID is missing")
        if descriptor.get("sourceRunId") is not None and descriptor.get("sourceRunId") != package.get("sourceRunId"):
            errors.append(f"{label} source run ID differs from descriptor")
        if descriptor.get("sourceRunSha256SumsSha256") is not None and descriptor.get("sourceRunSha256SumsSha256") != source_digest:
            errors.append(f"{label} source run digest differs from descriptor")
        inventory = {
            name: {"sha256": _sha256(package_root / name), "bytes": (package_root / name).stat().st_size}
            for name in sums
            if name != "evidence-manifest.json"
        }
        if package.get("files") != inventory:
            errors.append(f"{label} file inventory is stale")
        writable = [
            str(path.relative_to(package_root))
            for path in package_root.rglob("*")
            if path.is_file() and path.stat().st_mode & 0o222
        ]
        if writable:
            errors.append(f"{label} contains writable files: {writable}")
        # The copied run must still be internally self-consistent, even though
        # its lifecycle may be absent because diagnostics are not final runs.
        if summary.get("runId") != package.get("sourceRunId"):
            errors.append(f"{label} summary runId does not match source run ID")
        run_manifest = package.get("run") or {}
        if run_manifest and run_manifest.get("runId") != package.get("sourceRunId"):
            errors.append(f"{label} copied run manifest does not match source run ID")
        if not isinstance(gates.get("passed"), bool):
            errors.append(f"{label} gates.passed must be boolean")


def _validate_claim_documents(root: Path, documents: Any, errors: list[str]) -> None:
    if not isinstance(documents, list) or not documents:
        errors.append("claimDocuments must be a non-empty array")
        return
    for relative in documents:
        if not isinstance(relative, str):
            errors.append("claimDocuments entries must be paths")
            continue
        try:
            path = _resolve(root, relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"claim document is missing: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for token in LEGACY_TOKENS:
            if token in content:
                errors.append(f"claim document contains legacy token {token!r}: {relative}")
        for raw_target in MARKDOWN_LINK.findall(content):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target).split("#", 1)[0]
            if target and not (path.parent / target).resolve().exists():
                errors.append(f"broken local link in {relative}: {raw_target}")


def validate_repository(
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    require_current: bool = False,
) -> list[str]:
    root = REPO_ROOT
    errors: list[str] = []
    try:
        manifest = _json(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"invalid project evidence manifest: {exc}"]
    if manifest.get("schemaVersion") != PROJECT_SCHEMA:
        errors.append(f"project evidence schema must be {PROJECT_SCHEMA}")
    evaluation = manifest.get("evaluation")
    if not isinstance(evaluation, dict):
        return [*errors, "evaluation must be an object"]
    suite = _validate_suite(root, evaluation.get("suite"), errors)
    lock_descriptors = evaluation.get("datasetLocks")
    all_rows: list[dict[str, Any]] = []
    locked_dataset_hashes: dict[str, str] = {}
    if not isinstance(lock_descriptors, list) or {
        str(row.get("split") or "") for row in lock_descriptors if isinstance(row, dict)
    } != {"development", "regression"}:
        errors.append("evaluation.datasetLocks must contain development and regression")
    else:
        for descriptor in lock_descriptors:
            rows = _validate_lock(root, descriptor, suite, errors)
            all_rows.extend(rows)
            split = str(descriptor.get("split") or "")
            if rows and split in {"development", "regression"}:
                locked_dataset_hashes[split] = _canonical_sha256(
                    sorted(rows, key=lambda row: str(row.get("id") or ""))
                )
    _validate_customer_service_gold(
        root,
        evaluation.get("customerServiceGold"),
        errors,
    )
    ids = [str(row.get("id") or "") for row in all_rows]
    inputs = [
        _canonical_sha256({"domain": row.get("domain"), "input": row.get("input")})
        for row in all_rows
    ]
    if len(ids) != len(set(ids)) or len(inputs) != len(set(inputs)):
        errors.append("development and regression cases are not disjoint")

    status = manifest.get("status")
    current = evaluation.get("currentEvidence")
    if status == "NO_PUBLISHED_FINAL":
        if current is not None:
            errors.append("NO_PUBLISHED_FINAL must not point to current evidence")
        if require_current:
            errors.append("a published final evidence package is required")
    elif status == "PUBLISHED_FINAL":
        _validate_current_evidence(root, current, errors)
    else:
        errors.append("status must be NO_PUBLISHED_FINAL or PUBLISHED_FINAL")
    _validate_archives(root, evaluation.get("archives"), errors)
    _validate_failed_final_attempts(
        root,
        evaluation.get("failedFinalAttempts"),
        errors,
    )
    _validate_visible_runs(
        root,
        manifest.get("visibleRuns"),
        locked_dataset_hashes,
        errors,
    )
    _validate_benchmarks(root, evaluation.get("benchmarks"), errors)
    _validate_auxiliary_evidence(root, evaluation.get("auxiliaryEvidence"), errors)
    if isinstance(current, dict):
        current_path = str(current.get("path") or "")
        for archive in evaluation.get("archives") or []:
            if isinstance(archive, dict) and str(archive.get("path") or "") == current_path:
                errors.append("current evidence path must not also be listed as an archive")
        for attempt in evaluation.get("failedFinalAttempts") or []:
            if isinstance(attempt, dict) and str(attempt.get("path") or "") == current_path:
                errors.append("current evidence path must not also be a failed final attempt")
    _validate_claim_documents(root, manifest.get("claimDocuments"), errors)

    obsolete_paths = (
        "AI_Shop-backend/AI_Shop-agent/benchmarks",
        "AI_Shop-backend/AI_Shop-agent/app/evaluation",
        "docs/evidence",
    )
    for relative in obsolete_paths:
        if _resolve(root, relative).exists():
            errors.append(f"obsolete evaluation path still exists: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    errors = validate_repository(args.manifest, require_current=args.require_current)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "schemaVersion": PROJECT_SCHEMA,
                "currentEvidenceRequired": args.require_current,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
