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
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def _contains_forbidden_key(value: Any, forbidden: set[str]) -> bool:
    """Check nested evidence objects without matching harmless field names."""

    if isinstance(value, dict):
        if any(str(key) in forbidden for key in value):
            return True
        return any(_contains_forbidden_key(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child, forbidden) for child in value)
    return False


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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
    elif descriptor.get("status") == "HUMAN_VERIFIED":
        if review_plan.get("status") != "COMPLETE":
            errors.append(f"{label} human report must declare a complete review plan")
        if review_plan.get("adjudicationComplete") is not True:
            errors.append(f"{label} human report must declare adjudicationComplete=true")
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
    review_evidence = descriptor.get("reviewEvidence")
    if review_evidence is not None:
        _validate_customer_service_review_evidence(
            root,
            review_evidence,
            errors,
            dataset_sha=actual_dataset_sha,
            case_count=len(rows),
        )


def _validate_customer_service_review_evidence(
    root: Path,
    descriptor: Any,
    errors: list[str],
    *,
    dataset_sha: str,
    case_count: int,
) -> None:
    """Validate the pending two-person客服 review package and its hashes."""

    label = "evaluation.customerServiceGold.reviewEvidence"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    if descriptor.get("lifecycle") == "HUMAN_VERIFIED":
        _validate_customer_service_human_review_evidence(
            root,
            descriptor,
            errors,
            dataset_sha=dataset_sha,
            case_count=case_count,
        )
        return
    relative = str(descriptor.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not package_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return
    sums = _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    expected_sums = str(descriptor.get("sha256SumsSha256") or "")
    if not HEX64.fullmatch(expected_sums) or not sums_path.is_file() or _sha256(sums_path) != expected_sums:
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    required = {
        "adjudication-needed.md",
        "adjudication.template.jsonl",
        "agreement.json",
        "agreement.md",
        "evidence-manifest.json",
        "lifecycle.json",
        "reviewer-a.sealed.jsonl",
        "reviewer-a.sealed.jsonl.manifest.json",
        "reviewer-b.sealed.jsonl",
        "reviewer-b.sealed.jsonl.manifest.json",
    }
    if not required.issubset(sums):
        errors.append(f"{label} is missing files: {sorted(required - set(sums))}")
        return
    try:
        package_manifest = _json(package_root / "evidence-manifest.json")
        lifecycle = _json(package_root / "lifecycle.json")
        agreement = _json(package_root / "agreement.json")
        reviewer_manifests = [
            _json(package_root / "reviewer-a.sealed.jsonl.manifest.json"),
            _json(package_root / "reviewer-b.sealed.jsonl.manifest.json"),
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} JSON is invalid: {exc}")
        return
    if package_manifest.get("schemaVersion") != "aishop-customer-service-review-package/v1":
        errors.append(f"{label} package schema is invalid")
    if package_manifest.get("lifecycle") != "PENDING_ADJUDICATION":
        errors.append(f"{label} package lifecycle is invalid")
    inventory = {
        name: {"bytes": (package_root / name).stat().st_size, "sha256": _sha256(package_root / name)}
        for name in sums
        if name != "evidence-manifest.json"
    }
    if package_manifest.get("files") != inventory:
        errors.append(f"{label} file inventory is stale")
    if lifecycle.get("lifecycle") != "PENDING_ADJUDICATION" or lifecycle.get("releaseGateEligible") is not False:
        errors.append(f"{label} lifecycle evidence is invalid")
    if lifecycle.get("sourceDatasetSha256") != dataset_sha:
        errors.append(f"{label} source dataset hash differs")
    if agreement.get("schemaVersion") != "aishop-customer-service-review-agreement/v1":
        errors.append(f"{label} agreement schema is invalid")
    if agreement.get("status") != "PENDING_ADJUDICATION" or agreement.get("releaseGateEligible") is not False:
        errors.append(f"{label} agreement lifecycle is invalid")
    if agreement.get("sourceDatasetSha256") != dataset_sha or agreement.get("caseCount") != case_count:
        errors.append(f"{label} agreement source/count differs")
    if agreement.get("disagreementCaseCount") != descriptor.get("disagreementCaseCount"):
        errors.append(f"{label} disagreement count differs from project manifest")
    if agreement.get("exactAgreementCaseCount") != descriptor.get("exactAgreementCaseCount"):
        errors.append(f"{label} agreement count differs from project manifest")
    if _contains_forbidden_key(agreement, {"expected", "predicted", "modelOutput", "modelPrediction"}):
        errors.append(f"{label} leaks draft/model fields")
    for suffix, manifest in zip(("a", "b"), reviewer_manifests):
        sealed_name = f"reviewer-{suffix}.sealed.jsonl"
        if manifest.get("lifecycle") != "SEALED" or manifest.get("artifact") != "SEALED_REVIEW_SHEET":
            errors.append(f"{label} reviewer-{suffix} manifest is not sealed")
        if manifest.get("datasetSha256") != dataset_sha:
            errors.append(f"{label} reviewer-{suffix} source hash differs")
        if manifest.get("sheetSha256") != _sha256(package_root / sealed_name):
            errors.append(f"{label} reviewer-{suffix} sheet hash differs")


def _validate_customer_service_human_review_evidence(
    root: Path,
    descriptor: dict[str, Any],
    errors: list[str],
    *,
    dataset_sha: str,
    case_count: int,
) -> None:
    """Validate the immutable post-adjudication客服 evidence package."""

    label = "evaluation.customerServiceGold.reviewEvidence"
    relative = str(descriptor.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not package_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return
    sums = _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    expected_sums = str(descriptor.get("sha256SumsSha256") or "")
    if (
        not HEX64.fullmatch(expected_sums)
        or not sums_path.is_file()
        or _sha256(sums_path) != expected_sums
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    required = {
        "adjudication.final.jsonl",
        "customer-service-human-v1.jsonl",
        "evidence-manifest.json",
        "lifecycle.json",
        "merge.evidence.json",
        "report.json",
        "report.md",
        "reviewer-a.sealed.jsonl",
        "reviewer-a.sealed.jsonl.manifest.json",
        "reviewer-b.sealed.jsonl",
        "reviewer-b.sealed.jsonl.manifest.json",
    }
    if not required.issubset(sums):
        errors.append(f"{label} human package is missing files: {sorted(required - set(sums))}")
        return
    try:
        package_manifest = _json(package_root / "evidence-manifest.json")
        lifecycle = _json(package_root / "lifecycle.json")
        merge_evidence = _json(package_root / "merge.evidence.json")
        report = _json(package_root / "report.json")
        dataset_rows = _jsonl(package_root / "customer-service-human-v1.jsonl")
        adjudications = _jsonl(package_root / "adjudication.final.jsonl")
        reviewer_manifests = [
            _json(package_root / "reviewer-a.sealed.jsonl.manifest.json"),
            _json(package_root / "reviewer-b.sealed.jsonl.manifest.json"),
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} human package JSON is invalid: {exc}")
        return
    if package_manifest.get("schemaVersion") != "aishop-customer-service-human-package/v1":
        errors.append(f"{label} human package schema is invalid")
    if package_manifest.get("lifecycle") != "HUMAN_VERIFIED":
        errors.append(f"{label} human package lifecycle is invalid")
    inventory = {
        name: {"bytes": (package_root / name).stat().st_size, "sha256": _sha256(package_root / name)}
        for name in sums
        if name != "evidence-manifest.json"
    }
    if package_manifest.get("files") != inventory:
        errors.append(f"{label} human package file inventory is stale")
    if lifecycle.get("lifecycle") != "HUMAN_VERIFIED" or lifecycle.get("releaseGateEligible") is not False:
        errors.append(f"{label} human lifecycle evidence is invalid")
    source_draft_sha = str(descriptor.get("sourceDraftDatasetSha256") or "")
    if not HEX64.fullmatch(source_draft_sha):
        errors.append(f"{label} sourceDraftDatasetSha256 is invalid")
    if lifecycle.get("sourceDatasetSha256") != source_draft_sha:
        errors.append(f"{label} human source dataset hash differs")
    if lifecycle.get("caseCount") != case_count:
        errors.append(f"{label} human lifecycle case count differs")
    ids = [str(row.get("id") or "") for row in dataset_rows]
    if len(dataset_rows) != case_count or not ids or len(ids) != len(set(ids)):
        errors.append(f"{label} human dataset IDs/count are invalid")
    if any(
        row.get("schemaVersion") != "aishop-customer-service-gold/v1"
        or (row.get("annotation") or {}).get("status") != "HUMAN_VERIFIED"
        for row in dataset_rows
    ):
        errors.append(f"{label} human dataset is not uniformly HUMAN_VERIFIED")
    human_dataset_sha = _sha256(package_root / "customer-service-human-v1.jsonl")
    if descriptor.get("humanDatasetSha256") != human_dataset_sha:
        errors.append(f"{label} human dataset hash differs from project manifest")
    if report.get("status") != "HUMAN_VERIFIED" or report.get("releaseGateEligible") is not False:
        errors.append(f"{label} human report status/gate is invalid")
    report_dataset = report.get("dataset") or {}
    if report_dataset.get("sha256") != human_dataset_sha or report_dataset.get("caseCount") != case_count:
        errors.append(f"{label} human report dataset reference is stale")
    if descriptor.get("reportSha256") != _sha256(package_root / "report.json"):
        errors.append(f"{label} human report hash differs from project manifest")
    if merge_evidence.get("schemaVersion") != "aishop-customer-service-review-evidence/v1":
        errors.append(f"{label} merge evidence schema is invalid")
    if merge_evidence.get("status") != "HUMAN_VERIFIED" or merge_evidence.get("caseCount") != case_count:
        errors.append(f"{label} merge evidence status/count is invalid")
    if merge_evidence.get("outputDatasetSha256") != human_dataset_sha:
        errors.append(f"{label} merge evidence output hash differs")
    if merge_evidence.get("sourceDatasetSha256") != source_draft_sha:
        errors.append(f"{label} merge evidence source hash differs")
    if merge_evidence.get("adjudication", {}).get("sha256") != _sha256(
        package_root / "adjudication.final.jsonl"
    ):
        errors.append(f"{label} adjudication hash differs")
    if len(adjudications) != int(descriptor.get("adjudicationCaseCount") or -1):
        errors.append(f"{label} adjudication case count differs")
    known_ids = set(ids)
    adjudication_ids = [str(row.get("id") or "") for row in adjudications]
    if any(case_id not in known_ids for case_id in adjudication_ids) or len(adjudication_ids) != len(set(adjudication_ids)):
        errors.append(f"{label} adjudication IDs are invalid")
    if _contains_forbidden_key(merge_evidence, {"expected", "predicted", "modelOutput", "modelPrediction"}):
        errors.append(f"{label} merge evidence leaks model/gold fields")
    writable = [
        str(path.relative_to(package_root))
        for path in package_root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        errors.append(f"{label} human package contains writable files: {writable}")
    for suffix, manifest in zip(("a", "b"), reviewer_manifests):
        sealed_name = f"reviewer-{suffix}.sealed.jsonl"
        if manifest.get("lifecycle") != "SEALED" or manifest.get("artifact") != "SEALED_REVIEW_SHEET":
            errors.append(f"{label} reviewer-{suffix} manifest is not sealed")
        if manifest.get("datasetSha256") != source_draft_sha:
            errors.append(f"{label} reviewer-{suffix} source hash differs")
        if manifest.get("sheetSha256") != _sha256(package_root / sealed_name):
            errors.append(f"{label} reviewer-{suffix} sheet hash differs")


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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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


def _validate_scorecard(
    root: Path,
    descriptor: Any,
    current_descriptor: Any,
    errors: list[str],
) -> None:
    """Validate the mutable scorecard projection against immutable current evidence."""

    label = "evaluation.scorecard"
    if descriptor is None:
        return
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    relative = str(descriptor.get("path") or "")
    try:
        path = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not path.is_file():
        errors.append(f"{label} file is missing: {relative}")
        return
    expected_sha = str(descriptor.get("sha256") or "")
    if not HEX64.fullmatch(expected_sha) or _sha256(path) != expected_sha:
        errors.append(f"{label} hash mismatch: {relative}")
    try:
        scorecard = _json(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} JSON is invalid: {relative}: {exc}")
        return
    if scorecard.get("schemaVersion") != "aishop-quality-scorecard/v1":
        errors.append(f"{label} schema is invalid: {relative}")
    evidence = scorecard.get("evidence") or {}
    if not isinstance(evidence, dict):
        errors.append(f"{label}.evidence must be an object")
        return
    if isinstance(current_descriptor, dict):
        for field in ("path", "runId", "releaseId", "datasetSha256"):
            if evidence.get(field) != current_descriptor.get(field):
                errors.append(f"{label} evidence {field} differs from current evidence")
    for field in ("runtimeDiagnostics", "usageDiagnostics", "auxiliaryEvidence"):
        if field not in scorecard:
            errors.append(f"{label} is missing {field}")


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
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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


def _validate_diagnostic_evidence(root: Path, descriptors: Any, errors: list[str]) -> None:
    """Validate immutable quality, replay, and local-capacity diagnostics."""

    if descriptors is None:
        return
    if not isinstance(descriptors, list):
        errors.append("evaluation.diagnosticEvidence must be an array")
        return
    expected_schemas = {
        "customer-service-http": "aishop-customer-service-http-evidence/v1",
        "search-paired-replay": "aishop-search-paired-replay-evidence/v1",
        "customer-service-slot-replay": "aishop-customer-service-slot-replay-evidence/v1",
        "capacity-benchmark": "aishop-capacity-benchmark-evidence/v1",
    }
    required_files = {
        "customer-service-http": {
            "badcases.jsonl",
            "evidence-manifest.json",
            "report.json",
            "report.md",
        },
        "search-paired-replay": {
            "badcases.jsonl",
            "cases.jsonl",
            "evidence-manifest.json",
            "report.json",
            "report.md",
        },
        "customer-service-slot-replay": {
            "paired-cases.jsonl",
            "evidence-manifest.json",
            "report.json",
            "report.md",
        },
        "capacity-benchmark": {
            "observations.jsonl",
            "evidence-manifest.json",
            "report.json",
            "report.md",
        },
    }
    runtime_version_roles = {
        "RUNTIME_VERSION_STALE_BASELINE": {
            "status": "STALE_WORKER_RUNTIME_DIAGNOSTIC",
            "behaviorContractViolationCount": 6,
        },
        "RUNTIME_VERSION_POST_RESTART_RECOVERY": {
            "status": "POST_WORKER_RESTART_TARGETED_DIAGNOSTIC",
            "behaviorContractViolationCount": 0,
        },
    }
    runtime_version_pairs: dict[str, dict[str, tuple[str, str]]] = {}
    seen: set[str] = set()
    for index, descriptor in enumerate(descriptors, 1):
        label = f"evaluation.diagnosticEvidence[{index}]"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} must be an object")
            continue
        kind = str(descriptor.get("kind") or "")
        package_id = str(descriptor.get("packageId") or "")
        if kind not in expected_schemas:
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
        if (
            not sums_path.is_file()
            or descriptor.get("sha256SumsSha256") != _sha256(sums_path)
        ):
            errors.append(f"{label} SHA256SUMS digest differs from project manifest")
        required = required_files.get(kind, set())
        if not required.issubset(sums):
            errors.append(f"{label} is missing files: {sorted(required - set(sums))}")
            continue
        try:
            package = _json(package_root / "evidence-manifest.json")
            report = _json(package_root / "report.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} JSON is invalid: {exc}")
            continue
        if package.get("schemaVersion") != expected_schemas.get(kind):
            errors.append(f"{label} package schema is invalid")
        if kind in {"customer-service-http", "search-paired-replay"} and package.get(
            "kind"
        ) != kind:
            errors.append(f"{label} package kind differs from descriptor")
        package_identity = (
            package.get("packageId") or package.get("runId")
            if kind in {"customer-service-http", "search-paired-replay"}
            else package.get("packageId") or package.get("benchmarkId")
        )
        if package_identity != package_id:
            errors.append(f"{label} package ID differs from descriptor")
        if descriptor.get("runId") != package.get("runId") or package.get(
            "runId"
        ) != report.get("runId"):
            errors.append(f"{label} run ID binding is invalid")
        inventory = {
            name: {
                "sha256": _sha256(package_root / name),
                "bytes": (package_root / name).stat().st_size,
            }
            for name in sums
            if name != "evidence-manifest.json"
        }
        if package.get("files") != inventory:
            errors.append(f"{label} file inventory is stale")
        if kind == "customer-service-http":
            runtime_role = str(descriptor.get("resultRole") or "")
            if runtime_role in runtime_version_roles:
                expected = runtime_version_roles[runtime_role]
                pair_id = str(descriptor.get("pairId") or "")
                if not pair_id:
                    errors.append(f"{label} runtime-version diagnostic pairId is required")
                elif runtime_role in runtime_version_pairs.setdefault(pair_id, {}):
                    errors.append(
                        f"{label} duplicates runtime-version diagnostic role in pair: {pair_id}"
                    )
                else:
                    dataset_sha = str((report.get("dataset") or {}).get("sha256") or "")
                    runtime_version_pairs[pair_id][runtime_role] = (label, dataset_sha)
                if descriptor.get("expectedStatus") != expected["status"]:
                    errors.append(f"{label} runtime-version expected status is invalid")
                if (
                    descriptor.get("expectedBehaviorContractViolationCount")
                    != expected["behaviorContractViolationCount"]
                ):
                    errors.append(
                        f"{label} runtime-version expected behavior-contract count is invalid"
                    )
                if report.get("schemaVersion") != "aishop-customer-service-http-evaluation/v1":
                    errors.append(f"{label} HTTP report schema is invalid")
                if (
                    package.get("releaseGateEligible") is not False
                    or report.get("releaseGateEligible") is not False
                    or report.get("normalQualityDenominatorExcluded") is not True
                    or package.get("status") != expected["status"]
                    or report.get("status") != expected["status"]
                ):
                    errors.append(
                        f"{label} runtime-version diagnostic lifecycle boundary is invalid"
                    )
                if package.get("providerCallsReexecuted") is not False:
                    errors.append(f"{label} offline rebuild provenance is invalid")
                source_observation_sha = str(
                    package.get("sourceObservationReportSha256") or ""
                )
                if not HEX64.fullmatch(source_observation_sha):
                    errors.append(f"{label} source observation digest is invalid")
                observation = report.get("observationProvenance") or {}
                if (
                    observation.get("mode")
                    != "OFFLINE_DERIVATION_FROM_PRESERVED_TARGETED_OBSERVATIONS"
                    or observation.get("providerCallsReexecuted") is not False
                    or observation.get("sourceReportSha256") != source_observation_sha
                ):
                    errors.append(
                        f"{label} runtime-version observation provenance is invalid"
                    )
                route_metrics = ((report.get("httpRoute") or {}).get("metrics") or {})
                for metric_name in ("slotEntitySpanF1", "slotExactMatch"):
                    if (route_metrics.get(metric_name) or {}).get("status") != "UNAVAILABLE":
                        errors.append(
                            f"{label} HTTP slot metric must remain unavailable"
                        )
                answer_quality = report.get("answerQuality") or {}
                if (
                    answer_quality.get("status") != "PENDING_HUMAN_REVIEW"
                    or answer_quality.get("selfJudged") is not False
                    or answer_quality.get("answerCorrectness") is not None
                    or answer_quality.get("citationGroundingSupport") is not None
                    or answer_quality.get("unsafeAnswerRate") is not None
                ):
                    errors.append(
                        f"{label} HTTP answer quality must remain pending human review"
                    )
                dataset = report.get("dataset") or {}
                expected_case_count = descriptor.get("caseCount")
                if (
                    dataset.get("annotationStatus") != "HUMAN_VERIFIED"
                    or dataset.get("sha256") != descriptor.get("datasetSha256")
                    or not HEX64.fullmatch(str(dataset.get("sha256") or ""))
                    or dataset.get("caseCount") != expected_case_count
                ):
                    errors.append(f"{label} runtime-version dataset binding is invalid")
                execution_rate = ((report.get("httpExecution") or {}).get("executionRate") or {})
                if (
                    execution_rate.get("numerator") != expected_case_count
                    or execution_rate.get("denominator") != expected_case_count
                    or (report.get("httpExecution") or {}).get("errorCaseIds") != []
                ):
                    errors.append(
                        f"{label} runtime-version HTTP execution denominator is invalid"
                    )
                behavior_contracts = report.get("behaviorContracts") or {}
                results = behavior_contracts.get("results") or []
                violation_count = sum(
                    isinstance(result, dict) and result.get("status") != "PASSED"
                    for result in results
                )
                if (
                    behavior_contracts.get("contractCount") != expected_case_count
                    or behavior_contracts.get("executedContractCount") != expected_case_count
                    or len(results) != expected_case_count
                    or violation_count
                    != expected["behaviorContractViolationCount"]
                ):
                    errors.append(
                        f"{label} runtime-version behavior-contract evidence is invalid"
                    )
            elif runtime_role:
                errors.append(f"{label} customer-service HTTP result role is invalid")
            else:
                if report.get("schemaVersion") != "aishop-customer-service-http-evaluation/v1":
                    errors.append(f"{label} HTTP report schema is invalid")
                if (
                    package.get("releaseGateEligible") is not False
                    or report.get("releaseGateEligible") is not False
                    or report.get("normalQualityDenominatorExcluded") is not True
                ):
                    errors.append(f"{label} HTTP evidence must not be a release gate")
                if package.get("providerCallsReexecuted") is not False:
                    errors.append(f"{label} offline rebuild provenance is invalid")
                if not HEX64.fullmatch(
                    str(package.get("sourceObservationReportSha256") or "")
                ):
                    errors.append(f"{label} source observation digest is invalid")
                route_metrics = ((report.get("httpRoute") or {}).get("metrics") or {})
                for metric_name in ("slotEntitySpanF1", "slotExactMatch"):
                    if (route_metrics.get(metric_name) or {}).get("status") != "UNAVAILABLE":
                        errors.append(f"{label} HTTP slot metric must remain unavailable")
                observation = report.get("observationProvenance") or {}
                if (
                    observation.get("mode") != "OFFLINE_REBUILD_FROM_PRESERVED_OBSERVATIONS"
                    or observation.get("providerCallsReexecuted") is not False
                    or observation.get("sourceReportSha256")
                    != package.get("sourceObservationReportSha256")
                ):
                    errors.append(f"{label} HTTP observation provenance is invalid")
                answer_quality = report.get("answerQuality") or {}
                if (
                    answer_quality.get("status") != "PENDING_HUMAN_REVIEW"
                    or answer_quality.get("selfJudged") is not False
                    or answer_quality.get("answerCorrectness") is not None
                    or answer_quality.get("citationGroundingSupport") is not None
                    or answer_quality.get("unsafeAnswerRate") is not None
                ):
                    errors.append(f"{label} HTTP answer quality must remain pending human review")
        if kind == "search-paired-replay":
            if report.get("schemaVersion") != "aishop-search-paired-replay/v1":
                errors.append(f"{label} paired replay report schema is invalid")
            if (
                report.get("normalQualityDenominatorExcluded") is not True
                or report.get("baselineFinalModified") is not False
                or report.get("qrelsModified") is not False
                or package.get("normalQualityDenominatorExcluded") is not True
                or package.get("baselineFinalModified") is not False
                or package.get("qrelsModified") is not False
            ):
                errors.append(f"{label} paired replay boundary is invalid")
            if package.get("baselineRunId") != descriptor.get("baselineRunId"):
                errors.append(f"{label} baseline run binding is invalid")
            provenance = report.get("provenance") or {}
            if (
                provenance.get("baselineRunId") != descriptor.get("baselineRunId")
                or provenance.get("baselineEvidenceSha256SumsSha256")
                != package.get("baselineEvidenceSha256SumsSha256")
                or provenance.get("selectedQrelsSha256")
                != package.get("selectedQrelsSha256")
            ):
                errors.append(f"{label} paired replay provenance is invalid")
        if kind == "customer-service-slot-replay":
            if report.get("schemaVersion") != "aishop-customer-service-slot-replay/v1":
                errors.append(f"{label} slot replay report schema is invalid")
            if (
                report.get("normalQualityDenominatorExcluded") is not True
                or package.get("normalQualityDenominatorExcluded") is not True
            ):
                errors.append(f"{label} slot replay boundary is invalid")
            dataset = report.get("dataset") or {}
            if (
                dataset.get("annotationStatus") != "HUMAN_VERIFIED"
                or dataset.get("sha256") != package.get("datasetSha256")
            ):
                errors.append(f"{label} slot replay dataset binding is invalid")
            baseline_sha = str(package.get("baselineReportSha256") or "")
            if not HEX64.fullmatch(baseline_sha) or (
                descriptor.get("baselineReportSha256") is not None
                and descriptor.get("baselineReportSha256") != baseline_sha
            ):
                errors.append(f"{label} slot replay baseline binding is invalid")
            paired_counts = report.get("pairedCaseCounts") or {}
            try:
                paired_total = sum(int(value) for value in paired_counts.values())
                dataset_count = int(dataset.get("caseCount"))
            except (TypeError, ValueError):
                paired_total = -1
                dataset_count = -2
            if paired_total != dataset_count:
                errors.append(f"{label} slot replay paired denominator is invalid")
        if kind == "capacity-benchmark":
            if report.get("schemaVersion") != "aishop-capacity-benchmark/v1":
                errors.append(f"{label} capacity report schema is invalid")
            if (
                report.get("notProductionSlo") is not True
                or report.get("normalQualityDenominatorExcluded") is not True
                or package.get("notProductionSlo") is not True
                or package.get("normalQualityDenominatorExcluded") is not True
                or package.get("preflightPassed") is not True
            ):
                errors.append(f"{label} capacity claim boundary is invalid")
            dataset = report.get("dataset") or {}
            if (
                dataset.get("annotationStatus") != "HUMAN_VERIFIED"
                or dataset.get("sha256") != package.get("datasetSha256")
            ):
                errors.append(f"{label} capacity dataset binding is invalid")
            configuration = report.get("configuration") or {}
            try:
                configured_levels = {
                    str(int(value)) for value in configuration.get("concurrencies") or []
                }
                requests_per_level = int(configuration.get("requestsPerLevel"))
            except (TypeError, ValueError):
                configured_levels = set()
                requests_per_level = 0
            levels = report.get("levels") or {}
            if configured_levels != set(levels) or requests_per_level <= 0:
                errors.append(f"{label} capacity level configuration is invalid")
            completed_total = 0
            for concurrency, level in levels.items():
                try:
                    requested = int(level.get("requestedCount"))
                    completed = int(level.get("completedCount"))
                    level_concurrency = int(level.get("concurrency"))
                except (AttributeError, TypeError, ValueError):
                    errors.append(f"{label} capacity level {concurrency} is invalid")
                    continue
                if (
                    requested != requests_per_level
                    or completed != requested
                    or level_concurrency != int(concurrency)
                ):
                    errors.append(f"{label} capacity level {concurrency} denominator is invalid")
                completed_total += completed
                cost_status = str((level.get("usage") or {}).get("costStatus") or "")
                if cost_status not in {
                    "PRICED",
                    "UNPRICED",
                    "MISSING_USAGE",
                    "NOT_APPLICABLE",
                }:
                    errors.append(f"{label} capacity usage status is invalid")
            observations_path = package_root / "observations.jsonl"
            try:
                observations = [
                    json.loads(line)
                    for line in observations_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"{label} capacity observations are invalid: {exc}")
                observations = []
            if len(observations) != completed_total:
                errors.append(f"{label} capacity observation denominator is invalid")
            for observation in observations:
                answer = observation.get("answer") or {}
                if answer.get("rawStored") is not False or set(answer).difference(
                    {"sha256", "chars", "rawStored"}
                ):
                    errors.append(f"{label} capacity answer redaction is invalid")
                    break
            role = str(descriptor.get("resultRole") or "")
            if role not in {"BASELINE", "CURRENT", "TROUBLESHOOTING", "AUDIT_PROBE"}:
                errors.append(f"{label} capacity result role is invalid")
        writable = [
            str(path.relative_to(package_root))
            for path in package_root.rglob("*")
            if path.is_file() and path.stat().st_mode & 0o222
        ]
        if writable:
            errors.append(f"{label} contains writable files: {writable}")
    expected_runtime_roles = set(runtime_version_roles)
    for pair_id, members in runtime_version_pairs.items():
        if set(members) != expected_runtime_roles:
            errors.append(
                "runtime-version diagnostic pair must contain exactly stale baseline and "
                f"post-restart recovery: {pair_id}"
            )
            continue
        stale_label, stale_dataset_sha = members["RUNTIME_VERSION_STALE_BASELINE"]
        recovery_label, recovery_dataset_sha = members[
            "RUNTIME_VERSION_POST_RESTART_RECOVERY"
        ]
        if stale_dataset_sha != recovery_dataset_sha:
            errors.append(
                "runtime-version diagnostic pair datasets differ: "
                f"{stale_label} vs {recovery_label}"
            )


def _resolve_hashed_file(
    root: Path,
    descriptor: dict[str, Any],
    *,
    path_field: str,
    sha_field: str,
    label: str,
    errors: list[str],
) -> Path | None:
    relative = str(descriptor.get(path_field) or "")
    try:
        path = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if not path.is_file():
        errors.append(f"{label} is missing: {relative}")
        return None
    expected = str(descriptor.get(sha_field) or "")
    if not HEX64.fullmatch(expected) or _sha256(path) != expected:
        errors.append(f"{label} hash mismatch: {relative}")
    return path


def _validate_customer_service_pre_evaluator_observation(
    root: Path,
    descriptor: dict[str, Any],
    errors: list[str],
    *,
    label: str,
) -> None:
    """Keep the original Provider observation behind an offline rebuild auditable."""

    observation = descriptor.get("preEvaluatorFixEvidence")
    if not isinstance(observation, dict):
        errors.append(f"{label}.preEvaluatorFixEvidence must be an object")
        return
    relative = str(observation.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not package_root.is_dir():
        errors.append(f"{label}.preEvaluatorFixEvidence directory is missing: {relative}")
        return

    sums = _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    if (
        not sums_path.is_file()
        or observation.get("sha256SumsSha256") != _sha256(sums_path)
    ):
        errors.append(
            f"{label}.preEvaluatorFixEvidence SHA256SUMS digest differs from project manifest"
        )
    expected_files = {
        "badcases.jsonl",
        "evidence-manifest.json",
        "report.json",
        "report.md",
    }
    if set(sums) != expected_files:
        errors.append(
            f"{label}.preEvaluatorFixEvidence file set is invalid: {sorted(sums)}"
        )
        return

    report_path = package_root / "report.json"
    try:
        package = _json(package_root / "evidence-manifest.json")
        report = _json(report_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label}.preEvaluatorFixEvidence JSON is invalid: {exc}")
        return

    source_run_id = str(descriptor.get("sourceRunId") or "")
    report_sha256 = _sha256(report_path)
    inventory = {
        name: {
            "sha256": _sha256(package_root / name),
            "bytes": (package_root / name).stat().st_size,
        }
        for name in sums
        if name != "evidence-manifest.json"
    }
    if (
        package.get("schemaVersion") != "aishop-customer-service-http-evidence/v1"
        or package.get("kind") != "customer-service-http"
        or package.get("runId") != source_run_id
        or package.get("releaseGateEligible") is not False
        or package.get("providerCallsReexecuted") is not None
        or package.get("sourceObservationReportSha256") is not None
        or package.get("files") != inventory
    ):
        errors.append(f"{label}.preEvaluatorFixEvidence package binding is invalid")
    if (
        observation.get("sourceObservationReportSha256") != report_sha256
        or report.get("schemaVersion") != "aishop-customer-service-http-evaluation/v1"
        or report.get("runId") != source_run_id
        or report.get("observationProvenance") is not None
    ):
        errors.append(f"{label}.preEvaluatorFixEvidence source observation is invalid")
    answer_quality = report.get("answerQuality") or {}
    if (
        answer_quality.get("status") != "PENDING_HUMAN_REVIEW"
        or answer_quality.get("selfJudged") is not False
        or answer_quality.get("answerCorrectness") is not None
        or answer_quality.get("citationGroundingSupport") is not None
        or answer_quality.get("unsafeAnswerRate") is not None
    ):
        errors.append(
            f"{label}.preEvaluatorFixEvidence answer quality must remain pending human review"
        )
    writable = [
        str(path.relative_to(package_root))
        for path in package_root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        errors.append(
            f"{label}.preEvaluatorFixEvidence contains writable files: {writable}"
        )


def _validate_customer_service_candidate(
    root: Path,
    descriptor: Any,
    errors: list[str],
) -> None:
    """Validate the expanded客服 draft without treating it as human gold."""

    label = "evaluation.customerServiceCandidateV2"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    if (
        descriptor.get("status") != "DRAFT_NEEDS_DUAL_HUMAN_REVIEW"
        or descriptor.get("releaseGateEligible") is not False
    ):
        errors.append(f"{label} must remain a non-gating dual-review draft")
    dataset_path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="datasetPath",
        sha_field="datasetSha256",
        label=f"{label} dataset",
        errors=errors,
    )
    manifest_path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="manifestPath",
        sha_field="manifestSha256",
        label=f"{label} manifest",
        errors=errors,
    )
    if dataset_path is None or manifest_path is None:
        return
    try:
        rows = _jsonl(dataset_path)
        candidate_manifest = _json(manifest_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} payload is invalid: {exc}")
        return
    case_count = int(descriptor.get("caseCount") or -1)
    ids = [str(row.get("id") or "") for row in rows]
    if case_count != len(rows) or not ids or "" in ids or len(ids) != len(set(ids)):
        errors.append(f"{label} case count or IDs are invalid")
    if any(
        row.get("schemaVersion") != "aishop-customer-service-gold/v1"
        or (row.get("annotation") or {}).get("status") != "DRAFT_NEEDS_HUMAN_REVIEW"
        for row in rows
    ):
        errors.append(f"{label} dataset is not uniformly draft-labelled")
    additions = candidate_manifest.get("additions") or {}
    target = candidate_manifest.get("targetVerifiedVersion") or {}
    if (
        candidate_manifest.get("schemaVersion")
        != "aishop-customer-service-candidate-manifest/v1"
        or candidate_manifest.get("status") != descriptor.get("status")
        or candidate_manifest.get("releaseGateEligible") is not False
        or additions.get("path") != descriptor.get("datasetPath")
        or additions.get("sha256") != descriptor.get("datasetSha256")
        or additions.get("caseCount") != case_count
        or target.get("caseCount") != descriptor.get("targetCaseCount")
        or target.get("status") != "BLOCKED_UNTIL_DUAL_REVIEW_AND_ADJUDICATION"
    ):
        errors.append(f"{label} candidate manifest binding is invalid")

    sheets = descriptor.get("reviewSheets")
    if not isinstance(sheets, list) or len(sheets) != 2:
        errors.append(f"{label}.reviewSheets must contain two open blind sheets")
        return
    reviewers: set[str] = set()
    expected_ids = set(ids)
    for index, sheet_descriptor in enumerate(sheets, 1):
        sheet_label = f"{label}.reviewSheets[{index}]"
        if not isinstance(sheet_descriptor, dict):
            errors.append(f"{sheet_label} must be an object")
            continue
        reviewer_id = str(sheet_descriptor.get("reviewerId") or "")
        reviewers.add(reviewer_id)
        sheet_path = _resolve_hashed_file(
            root,
            sheet_descriptor,
            path_field="path",
            sha_field="sha256",
            label=f"{sheet_label} sheet",
            errors=errors,
        )
        sheet_manifest_path = _resolve_hashed_file(
            root,
            sheet_descriptor,
            path_field="manifestPath",
            sha_field="manifestSha256",
            label=f"{sheet_label} manifest",
            errors=errors,
        )
        if sheet_path is None or sheet_manifest_path is None:
            continue
        try:
            sheet_rows = _jsonl(sheet_path)
            sheet_manifest = _json(sheet_manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{sheet_label} payload is invalid: {exc}")
            continue
        sheet_ids = {str(row.get("id") or "") for row in sheet_rows}
        if len(sheet_rows) != case_count or sheet_ids != expected_ids:
            errors.append(f"{sheet_label} case IDs/count differ from candidate dataset")
        if (
            sheet_manifest.get("schemaVersion") != "aishop-customer-service-review/v1"
            or sheet_manifest.get("artifact") != "BLINDED_REVIEW_SHEET"
            or sheet_manifest.get("lifecycle") != "OPEN"
            or sheet_manifest.get("containsExpectedOrPredicted") is not False
            or sheet_manifest.get("reviewerId") != reviewer_id
            or sheet_manifest.get("caseCount") != case_count
            or sheet_manifest.get("datasetPath") != descriptor.get("datasetPath")
            or sheet_manifest.get("datasetSha256") != descriptor.get("datasetSha256")
            or sheet_manifest.get("sheetPath") != sheet_descriptor.get("path")
            or sheet_manifest.get("sheetSha256") != sheet_descriptor.get("sha256")
        ):
            errors.append(f"{sheet_label} open-sheet manifest binding is invalid")
        if any(
            row.get("schemaVersion") != "aishop-customer-service-review/v1"
            or row.get("reviewerId") != reviewer_id
            or any(value is not None for value in (row.get("labels") or {}).values())
            for row in sheet_rows
        ):
            errors.append(f"{sheet_label} is no longer an unfilled open sheet")
        if _contains_forbidden_key(
            sheet_rows,
            {"expected", "predicted", "modelOutput", "modelPrediction"},
        ):
            errors.append(f"{sheet_label} leaks draft/model fields")
    if reviewers != {"reviewer-a", "reviewer-b"}:
        errors.append(f"{label}.reviewSheets must bind reviewer-a and reviewer-b")


def _validate_pending_customer_service_answer_review(
    root: Path,
    descriptor: dict[str, Any],
    report: dict[str, Any],
    report_ids: set[str],
    errors: list[str],
) -> None:
    """Validate frozen dual review evidence before third-person adjudication."""

    label = "evaluation.customerServiceAnswerReview.pendingEvidence"
    pending = descriptor.get("pendingEvidence")
    if not isinstance(pending, dict):
        errors.append(f"{label} must be an object")
        return
    relative = str(pending.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not package_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return
    sums = _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    expected_sums = str(pending.get("sha256SumsSha256") or "")
    if (
        not HEX64.fullmatch(expected_sums)
        or not sums_path.is_file()
        or _sha256(sums_path) != expected_sums
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    required = {
        "adjudication-needed.md",
        "adjudication.template.jsonl",
        "agreement.json",
        "agreement.md",
        "evidence-manifest.json",
        "lifecycle.json",
        "reviews/reviewer-a.sealed.jsonl",
        "reviews/reviewer-a.sealed.jsonl.manifest.json",
        "reviews/reviewer-b.sealed.jsonl",
        "reviews/reviewer-b.sealed.jsonl.manifest.json",
    }
    if set(sums) != required:
        errors.append(f"{label} file inventory is invalid")
        return
    try:
        package = _json(package_root / "evidence-manifest.json")
        lifecycle = _json(package_root / "lifecycle.json")
        agreement = _json(package_root / "agreement.json")
        template_rows = _jsonl(package_root / "adjudication.template.jsonl")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} payload is invalid: {exc}")
        return
    if (
        package.get("schemaVersion")
        != "aishop-customer-service-answer-review-pending-evidence/v1"
        or package.get("status") != "PENDING_ADJUDICATION"
        or package.get("releaseGateEligible") is not False
        or package.get("selfJudged") is not False
    ):
        errors.append(f"{label} package lifecycle is invalid")
    if (
        lifecycle.get("schemaVersion")
        != "aishop-customer-service-answer-review-pending-lifecycle/v1"
        or lifecycle.get("lifecycle") != "PENDING_ADJUDICATION"
        or lifecycle.get("releaseGateEligible") is not False
        or lifecycle.get("selfJudged") is not False
    ):
        errors.append(f"{label} lifecycle record is invalid")
    if (
        agreement.get("schemaVersion")
        != "aishop-customer-service-answer-review-agreement/v1"
        or agreement.get("status") != "PENDING_ADJUDICATION"
        or agreement.get("releaseGateEligible") is not False
    ):
        errors.append(f"{label} agreement lifecycle is invalid")
    for field in ("sourceRunId", "sourceReportPath", "sourceReportSha256", "caseCount"):
        if (
            package.get(field) != descriptor.get(field)
            or lifecycle.get(field) != descriptor.get(field)
            or agreement.get(field) != descriptor.get(field)
        ):
            errors.append(f"{label} source field differs: {field}")
    if (
        pending.get("exactAgreementCaseCount")
        != agreement.get("exactAgreementCaseCount")
        or pending.get("disagreementCaseCount")
        != agreement.get("disagreementCaseCount")
        or pending.get("caseAgreementRate") != agreement.get("caseAgreementRate")
    ):
        errors.append(f"{label} agreement summary differs from project manifest")
    inventory = {
        name: {
            "bytes": (package_root / name).stat().st_size,
            "sha256": _sha256(package_root / name),
        }
        for name in sums
        if name != "evidence-manifest.json"
    }
    if package.get("files") != inventory:
        errors.append(f"{label} package file inventory is stale")

    source_by_id: dict[str, dict[str, Any]] = {}
    for source_case in report.get("cases") or []:
        if not isinstance(source_case, dict):
            continue
        case_id = str(source_case.get("caseId") or "")
        http = source_case.get("http") if isinstance(source_case.get("http"), dict) else {}
        source_by_id[case_id] = {
            "message": source_case.get("message"),
            "answer": http.get("answer") or "",
            "sourceRefs": http.get("sourceRefs") or [],
            "observedHandoff": bool(http.get("handoffObserved")),
        }
    expected_label_keys = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    expected_row_fields = {
        "schemaVersion",
        "caseId",
        "reviewerId",
        "guidelinesVersion",
        "sourceRunId",
        "sourceReportSha256",
        "message",
        "answer",
        "sourceRefs",
        "observedHandoff",
        "labels",
        "comment",
    }
    reviewer_ids: list[str] = []
    for key, filename, agreement_key in (
        ("reviewerA", "reviewer-a.sealed.jsonl", "reviewA"),
        ("reviewerB", "reviewer-b.sealed.jsonl", "reviewB"),
    ):
        sheet_path = package_root / "reviews" / filename
        sheet_manifest_path = sheet_path.with_suffix(sheet_path.suffix + ".manifest.json")
        try:
            rows = _jsonl(sheet_path)
            sheet_manifest = _json(sheet_manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} {key} sheet is invalid: {exc}")
            continue
        reviewer = str((package.get("reviewers") or {}).get(key, {}).get("reviewerId") or "")
        reviewer_ids.append(reviewer)
        expected_sha = _sha256(sheet_path)
        if (
            (package.get("reviewers") or {}).get(key, {}).get("sealedPath")
            != f"reviews/{filename}"
            or (package.get("reviewers") or {}).get(key, {}).get("sha256") != expected_sha
            or sheet_manifest.get("schemaVersion")
            != "aishop-customer-service-answer-review/v2"
            or sheet_manifest.get("artifact") != "SEALED_ANSWER_REVIEW_SHEET"
            or sheet_manifest.get("lifecycle") != "SEALED"
            or sheet_manifest.get("reviewerId") != reviewer
            or sheet_manifest.get("sheetSha256") != expected_sha
            or sheet_manifest.get("sourceRunId") != descriptor.get("sourceRunId")
            or sheet_manifest.get("sourceReportSha256")
            != descriptor.get("sourceReportSha256")
            or (agreement.get(agreement_key) or {}).get("reviewerId") != reviewer
            or (agreement.get(agreement_key) or {}).get("sha256") != expected_sha
            or (agreement.get(agreement_key) or {}).get("path")
            != f"reviews/{filename}"
        ):
            errors.append(f"{label} {key} sealed binding is invalid")
        sheet_ids = {str(row.get("caseId") or "") for row in rows}
        if len(rows) != descriptor.get("caseCount") or sheet_ids != report_ids:
            errors.append(f"{label} {key} sheet case IDs/count differ from HTTP report")
        for row in rows:
            labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
            if (
                not isinstance(row, dict)
                or set(row) != expected_row_fields
                or row.get("schemaVersion") != "aishop-customer-service-answer-review/v2"
                or row.get("reviewerId") != reviewer
                or row.get("guidelinesVersion") != "customer-service-answer-quality-v1"
                or row.get("sourceRunId") != descriptor.get("sourceRunId")
                or row.get("sourceReportSha256") != descriptor.get("sourceReportSha256")
                or set(labels) != expected_label_keys
                or not isinstance(labels.get("answerCorrect"), bool)
                or labels.get("citationSupport")
                not in {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
                or not isinstance(labels.get("handoffAppropriate"), bool)
                or not isinstance(labels.get("unsafeAnswer"), bool)
                or not isinstance(row.get("comment"), str)
            ):
                errors.append(f"{label} {key} row schema/labels are invalid")
                break
            source = source_by_id.get(str(row.get("caseId") or ""))
            if source is not None and any(
                _canonical_sha256(row.get(field)) != _canonical_sha256(source[field])
                for field in ("message", "answer", "sourceRefs", "observedHandoff")
            ):
                errors.append(f"{label} {key} immutable source field differs")
                break
        if _contains_forbidden_key(
            rows, {"expected", "predicted", "modelOutput", "modelPrediction"}
        ):
            errors.append(f"{label} {key} leaks gold/model fields")
    if set(reviewer_ids) != {"reviewer-a", "reviewer-b"}:
        errors.append(f"{label} reviewer IDs are invalid")
    if lifecycle.get("reviewers") != ["reviewer-a", "reviewer-b"]:
        errors.append(f"{label} lifecycle reviewer order is invalid")

    disagreement_ids = {
        str(item.get("caseId") or "") for item in agreement.get("disagreements") or []
    }
    if (
        len(template_rows) != agreement.get("disagreementCaseCount")
        or {str(row.get("caseId") or "") for row in template_rows}
        != disagreement_ids
    ):
        errors.append(f"{label} adjudication template coverage is invalid")
    for row in template_rows:
        final_labels = row.get("finalLabels") if isinstance(row.get("finalLabels"), dict) else {}
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "schemaVersion",
                "caseId",
                "sourceRunId",
                "sourceReportSha256",
                "message",
                "answer",
                "sourceRefs",
                "observedHandoff",
                "reviewerA",
                "reviewerB",
                "finalLabels",
                "adjudicator",
                "reason",
            }
            or row.get("schemaVersion")
            != "aishop-customer-service-answer-review-adjudication/v1"
            or set(final_labels) != expected_label_keys
            or any(value is not None for value in final_labels.values())
            or row.get("adjudicator") != ""
            or row.get("reason") != ""
        ):
            errors.append(f"{label} adjudication template is invalid")
            break
    template_sha = _sha256(package_root / "adjudication.template.jsonl")
    if (
        (package.get("adjudicationTemplate") or {}).get("path")
        != "adjudication.template.jsonl"
        or (package.get("adjudicationTemplate") or {}).get("sha256AtExport")
        != template_sha
        or (package.get("adjudicationTemplate") or {}).get("caseCount")
        != len(template_rows)
        or lifecycle.get("adjudicationTemplateSha256AtExport") != template_sha
    ):
        errors.append(f"{label} adjudication template binding is invalid")
    writable = [
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        errors.append(f"{label} contains writable files: {writable}")


def _validate_adjudicated_customer_service_answer_review(
    root: Path,
    descriptor: dict[str, Any],
    report: dict[str, Any],
    report_ids: set[str],
    errors: list[str],
) -> None:
    """Validate final human answer evidence while retaining its pending parent."""

    label = "evaluation.customerServiceAnswerReview.finalEvidence"
    final = descriptor.get("finalEvidence")
    if not isinstance(final, dict):
        errors.append(f"{label} must be an object")
        return
    relative = str(final.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not package_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return
    sums = _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    expected_sums = str(final.get("sha256SumsSha256") or "")
    if (
        not HEX64.fullmatch(expected_sums)
        or not sums_path.is_file()
        or _sha256(sums_path) != expected_sums
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    required = {
        "agreement.json",
        "agreement.md",
        "badcases.jsonl",
        "evidence-manifest.json",
        "final-report.json",
        "final-report.md",
        "reviews/adjudication.final.jsonl",
        "reviews/reviewer-a.sealed.jsonl",
        "reviews/reviewer-a.sealed.jsonl.manifest.json",
        "reviews/reviewer-b.sealed.jsonl",
        "reviews/reviewer-b.sealed.jsonl.manifest.json",
    }
    if set(sums) != required:
        errors.append(f"{label} file inventory is invalid")
        return
    try:
        package = _json(package_root / "evidence-manifest.json")
        final_report = _json(package_root / "final-report.json")
        agreement = _json(package_root / "agreement.json")
        badcases = _jsonl(package_root / "badcases.jsonl")
        adjudications = _jsonl(package_root / "reviews/adjudication.final.jsonl")
        pending = descriptor.get("pendingEvidence") or {}
        pending_root = _resolve(root, str(pending.get("path") or ""))
        pending_package = _json(pending_root / "evidence-manifest.json")
        pending_agreement = _json(pending_root / "agreement.json")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} payload is invalid: {exc}")
        return
    source_fields = (
        "sourceRunId",
        "sourceReportPath",
        "sourceReportSha256",
        "caseCount",
    )
    if (
        final.get("schemaVersion")
        != "aishop-customer-service-answer-review-evidence/v1"
        or final.get("releaseGateEligible") is not False
        or final.get("selfJudged") is not False
        or package.get("schemaVersion")
        != "aishop-customer-service-answer-review-evidence/v1"
        or package.get("kind") != "customer-service-answer-human-review"
        or package.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or package.get("releaseGateEligible") is not False
        or package.get("selfJudged") is not False
        or final_report.get("schemaVersion")
        != "aishop-customer-service-answer-review-report/v2"
        or final_report.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or final_report.get("releaseGateEligible") is not False
        or final_report.get("selfJudged") is not False
        or agreement.get("schemaVersion")
        != "aishop-customer-service-answer-review-agreement/v1"
        or agreement.get("status") != "PENDING_ADJUDICATION"
        or agreement.get("releaseGateEligible") is not False
    ):
        errors.append(f"{label} lifecycle/schema is invalid")
    for field in source_fields:
        if (
            final.get(field) != descriptor.get(field)
            or package.get(field) != descriptor.get(field)
            or final_report.get(field) != descriptor.get(field)
            or agreement.get(field) != descriptor.get(field)
        ):
            errors.append(f"{label} source field differs: {field}")
    if final.get("finalReportSha256") != _sha256(package_root / "final-report.json"):
        errors.append(f"{label} final report hash differs from project manifest")
    source_answer_hashes: dict[str, str] = {}
    for source_case in report.get("cases") or []:
        if not isinstance(source_case, dict):
            continue
        case_id = str(source_case.get("caseId") or "")
        http = source_case.get("http")
        answer = http.get("answer") if isinstance(http, dict) else ""
        source_answer_hashes[case_id] = hashlib.sha256(
            str(answer or "").encode("utf-8")
        ).hexdigest()
    inventory = {
        name: {
            "bytes": (package_root / name).stat().st_size,
            "sha256": _sha256(package_root / name),
        }
        for name in sums
        if name != "evidence-manifest.json"
    }
    if package.get("files") != inventory:
        errors.append(f"{label} package file inventory is stale")
    # The package copies the same sealed content but records its source paths
    # differently; timestamps are publication metadata.  Compare the actual
    # agreement decisions and reviewer hashes, not those transport details.
    pending_comparable = json.loads(json.dumps(pending_agreement))
    final_comparable = json.loads(json.dumps(agreement))
    for comparable in (pending_comparable, final_comparable):
        comparable.pop("createdAt", None)
        for review_key in ("reviewA", "reviewB"):
            review = comparable.get(review_key)
            if isinstance(review, dict):
                review.pop("path", None)
    if _canonical_sha256(final_comparable) != _canonical_sha256(pending_comparable):
        errors.append(f"{label} agreement differs from immutable pending parent")

    pending_reviewers = pending_package.get("reviewers") or {}
    review_specs = (
        ("reviewerA", "reviewer-a.sealed.jsonl", "reviewASha256"),
        ("reviewerB", "reviewer-b.sealed.jsonl", "reviewBSha256"),
    )
    reviewer_ids: set[str] = set()
    expected_row_fields = {
        "schemaVersion",
        "caseId",
        "reviewerId",
        "guidelinesVersion",
        "sourceRunId",
        "sourceReportSha256",
        "message",
        "answer",
        "sourceRefs",
        "observedHandoff",
        "labels",
        "comment",
    }
    label_keys = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    for key, filename, package_hash_key in review_specs:
        sheet_path = package_root / "reviews" / filename
        sidecar_path = sheet_path.with_suffix(sheet_path.suffix + ".manifest.json")
        try:
            rows = _jsonl(sheet_path)
            sidecar = _json(sidecar_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} {key} sealed sheet is invalid: {exc}")
            continue
        sheet_sha = _sha256(sheet_path)
        pending_reviewer = pending_reviewers.get(key) or {}
        reviewer_id = str(sidecar.get("reviewerId") or "")
        reviewer_ids.add(reviewer_id)
        if (
            package.get(package_hash_key) != sheet_sha
            or pending_reviewer.get("sha256") != sheet_sha
            or agreement.get("reviewA" if key == "reviewerA" else "reviewB", {}).get(
                "sha256"
            )
            != sheet_sha
            or sidecar.get("schemaVersion")
            != "aishop-customer-service-answer-review/v2"
            or sidecar.get("artifact") != "SEALED_ANSWER_REVIEW_SHEET"
            or sidecar.get("lifecycle") != "SEALED"
            or not reviewer_id
            or sidecar.get("sheetSha256") != sheet_sha
            or sidecar.get("sourceRunId") != descriptor.get("sourceRunId")
            or sidecar.get("sourceReportSha256")
            != descriptor.get("sourceReportSha256")
        ):
            errors.append(f"{label} {key} sealed binding is invalid")
        row_ids = {str(row.get("caseId") or "") for row in rows}
        if len(rows) != descriptor.get("caseCount") or row_ids != report_ids:
            errors.append(f"{label} {key} case IDs/count differ from HTTP report")
        if any(
            set(row) != expected_row_fields
            or row.get("schemaVersion")
            != "aishop-customer-service-answer-review/v2"
            or row.get("reviewerId") != reviewer_id
            or row.get("guidelinesVersion") != "customer-service-answer-quality-v1"
            or row.get("sourceRunId") != descriptor.get("sourceRunId")
            or row.get("sourceReportSha256")
            != descriptor.get("sourceReportSha256")
            or set((row.get("labels") or {}).keys()) != label_keys
            or not isinstance((row.get("labels") or {}).get("answerCorrect"), bool)
            or (row.get("labels") or {}).get("citationSupport")
            not in {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
            or not isinstance((row.get("labels") or {}).get("handoffAppropriate"), bool)
            or not isinstance((row.get("labels") or {}).get("unsafeAnswer"), bool)
            or not isinstance(row.get("comment"), str)
            for row in rows
        ):
            errors.append(f"{label} {key} row schema/labels are invalid")
    if reviewer_ids != {"reviewer-a", "reviewer-b"}:
        errors.append(f"{label} reviewers are not independent")

    disagreement_by_id = {
        str(row.get("caseId") or ""): row
        for row in agreement.get("disagreements") or []
        if isinstance(row, dict)
    }
    if (
        len(disagreement_by_id) != agreement.get("disagreementCaseCount")
        or final.get("adjudicationCaseCount") != len(disagreement_by_id)
        or len(adjudications) != len(disagreement_by_id)
        or {str(row.get("caseId") or "") for row in adjudications}
        != set(disagreement_by_id)
        or package.get("adjudicationSha256")
        != _sha256(package_root / "reviews/adjudication.final.jsonl")
        or final.get("adjudicationSha256")
        != _sha256(package_root / "reviews/adjudication.final.jsonl")
    ):
        errors.append(f"{label} adjudication coverage/hash is invalid")
    adjudication_by_id: dict[str, dict[str, Any]] = {}
    expected_adjudication_fields = {
        "schemaVersion",
        "caseId",
        "sourceRunId",
        "sourceReportSha256",
        "message",
        "answer",
        "sourceRefs",
        "observedHandoff",
        "reviewerA",
        "reviewerB",
        "finalLabels",
        "adjudicator",
        "reason",
    }
    for row in adjudications:
        case_id = str(row.get("caseId") or "")
        expected = disagreement_by_id.get(case_id)
        labels = row.get("finalLabels") if isinstance(row.get("finalLabels"), dict) else {}
        if (
            set(row) != expected_adjudication_fields
            or row.get("schemaVersion")
            != "aishop-customer-service-answer-review-adjudication/v1"
            or expected is None
            or any(
                _canonical_sha256(row.get(field))
                != _canonical_sha256(
                    agreement.get(field)
                    if field in {"sourceRunId", "sourceReportSha256"}
                    else expected.get(field)
                )
                for field in (
                    "sourceRunId",
                    "sourceReportSha256",
                    "message",
                    "answer",
                    "sourceRefs",
                    "observedHandoff",
                    "reviewerA",
                    "reviewerB",
                )
            )
            or set(labels) != label_keys
            or not isinstance(labels.get("answerCorrect"), bool)
            or labels.get("citationSupport")
            not in {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
            or not isinstance(labels.get("handoffAppropriate"), bool)
            or not isinstance(labels.get("unsafeAnswer"), bool)
            or not str(row.get("adjudicator") or "").strip()
            or str(row.get("adjudicator") or "").strip()
            in {"reviewer-a", "reviewer-b"}
            or not str(row.get("reason") or "").strip()
        ):
            errors.append(f"{label} adjudication row is invalid: {case_id or '?'}")
            continue
        adjudication_by_id[case_id] = row

    final_cases = final_report.get("cases")
    if not isinstance(final_cases, list) or len(final_cases) != descriptor.get("caseCount"):
        errors.append(f"{label} final case count is invalid")
    else:
        final_ids = {str(row.get("caseId") or "") for row in final_cases if isinstance(row, dict)}
        if len(final_ids) != len(final_cases) or final_ids != report_ids:
            errors.append(f"{label} final case IDs are invalid")
        for row in final_cases:
            if not isinstance(row, dict):
                errors.append(f"{label} final case is not an object")
                continue
            case_id = str(row.get("caseId") or "")
            labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
            if (
                set(row)
                != {
                    "caseId",
                    "labels",
                    "labelSource",
                    "adjudicator",
                    "comment",
                    "answerSha256",
                }
                or set(labels) != label_keys
                or not isinstance(labels.get("answerCorrect"), bool)
                or labels.get("citationSupport")
                not in {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
                or not isinstance(labels.get("handoffAppropriate"), bool)
                or not isinstance(labels.get("unsafeAnswer"), bool)
                or row.get("answerSha256") != source_answer_hashes.get(case_id)
            ):
                errors.append(f"{label} final case schema/labels are invalid: {case_id or '?'}")
                continue
            adjudication = adjudication_by_id.get(case_id)
            if adjudication is not None and (
                row.get("labelSource") != "ADJUDICATED"
                or row.get("adjudicator") != adjudication.get("adjudicator")
                or _canonical_sha256(labels)
                != _canonical_sha256(adjudication.get("finalLabels"))
                or row.get("comment") != adjudication.get("reason")
            ):
                errors.append(f"{label} final adjudicated decision differs: {case_id}")

    final_metrics = final_report.get("metrics") or {}
    descriptor_metrics = final.get("metrics") or {}
    required_metrics = {
        "answerCorrectness",
        "citationGroundingSupport",
        "handoffAppropriateness",
        "unsafeAnswerRate",
        "jointQualityPassRate",
    }
    if set(descriptor_metrics) != required_metrics or not required_metrics.issubset(final_metrics):
        errors.append(f"{label} final metric descriptor is invalid")
    else:
        for metric_name in required_metrics:
            metric = final_metrics.get(metric_name) or {}
            expected_metric = descriptor_metrics.get(metric_name) or {}
            denominator = metric.get("denominator")
            numerator = metric.get("numerator")
            if (
                metric.get("status") != "MEASURED"
                or not isinstance(denominator, int)
                or denominator <= 0
                or not isinstance(numerator, int)
                or numerator < 0
                or numerator > denominator
                or metric.get("value") != round(numerator / denominator, 6)
                or any(metric.get(field) != expected_metric.get(field) for field in expected_metric)
                or len(metric.get("badcaseIds") or []) != metric.get("badcaseCount")
            ):
                errors.append(f"{label} metric is invalid: {metric_name}")
    if final_report.get("badcases") != badcases:
        errors.append(f"{label} badcases JSONL differs from final report")
    if final.get("badcaseCount") != len(badcases):
        errors.append(f"{label} badcase count differs from project manifest")
    writable = [
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        errors.append(f"{label} contains writable files: {writable}")


def _validate_customer_service_answer_review(
    root: Path,
    descriptor: Any,
    errors: list[str],
    *,
    label: str = "evaluation.customerServiceAnswerReview",
    require_pre_evaluator_observation: bool = False,
) -> None:
    """Validate the v2 answer-review lifecycle and immutable HTTP binding."""

    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    status = str(descriptor.get("status") or "")
    if status not in {
        "PENDING_HUMAN_REVIEW",
        "PENDING_ADJUDICATION",
        "HUMAN_REVIEWED_ADJUDICATED",
    }:
        errors.append(f"{label} lifecycle status is invalid: {status!r}")
    if descriptor.get("releaseGateEligible") is not False:
        errors.append(f"{label} must remain non-gating")
    report_path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="sourceReportPath",
        sha_field="sourceReportSha256",
        label=f"{label} source report",
        errors=errors,
    )
    if report_path is None:
        return
    try:
        report = _json(report_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} source report is invalid: {exc}")
        return
    source_run_id = str(descriptor.get("sourceRunId") or "")
    case_count = int(descriptor.get("caseCount") or -1)
    report_ids = {str(row.get("caseId") or "") for row in report.get("cases") or []}
    if (
        report.get("schemaVersion") != "aishop-customer-service-http-evaluation/v1"
        or report.get("runId") != source_run_id
        or (report.get("answerQuality") or {}).get("status") != "PENDING_HUMAN_REVIEW"
        or len(report_ids) != case_count
        or "" in report_ids
    ):
        errors.append(f"{label} source report binding is invalid")

    if require_pre_evaluator_observation:
        _validate_customer_service_pre_evaluator_observation(
            root,
            descriptor,
            errors,
            label=label,
        )

    if status == "PENDING_ADJUDICATION":
        _validate_pending_customer_service_answer_review(
            root,
            descriptor,
            report,
            report_ids,
            errors,
        )
        return
    if status == "HUMAN_REVIEWED_ADJUDICATED":
        # The sealed two-review package remains an immutable parent record.
        # The final package adds third-person decisions; it never replaces the
        # pending artifact or retroactively turns this old observation into a
        # release gate.
        _validate_pending_customer_service_answer_review(
            root,
            descriptor,
            report,
            report_ids,
            errors,
        )
        _validate_adjudicated_customer_service_answer_review(
            root,
            descriptor,
            report,
            report_ids,
            errors,
        )
        return
    if status != "PENDING_HUMAN_REVIEW":
        return

    sheets = descriptor.get("reviewSheets")
    if not isinstance(sheets, list) or len(sheets) != 2:
        errors.append(f"{label}.reviewSheets must contain two open blind sheets")
        return
    reviewers: set[str] = set()
    expected_label_keys = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    expected_row_fields = {
        "schemaVersion",
        "caseId",
        "reviewerId",
        "guidelinesVersion",
        "sourceRunId",
        "sourceReportSha256",
        "message",
        "answer",
        "sourceRefs",
        "observedHandoff",
        "labels",
        "comment",
    }
    source_by_id: dict[str, dict[str, Any]] = {}
    for source_case in report.get("cases") or []:
        if not isinstance(source_case, dict):
            continue
        case_id = str(source_case.get("caseId") or "")
        http = source_case.get("http") if isinstance(source_case.get("http"), dict) else {}
        source_by_id[case_id] = {
            "message": source_case.get("message"),
            "answer": http.get("answer") or "",
            "sourceRefs": http.get("sourceRefs") or [],
            "observedHandoff": bool(http.get("handoffObserved")),
        }
    for index, sheet_descriptor in enumerate(sheets, 1):
        sheet_label = f"{label}.reviewSheets[{index}]"
        if not isinstance(sheet_descriptor, dict):
            errors.append(f"{sheet_label} must be an object")
            continue
        reviewer_id = str(sheet_descriptor.get("reviewerId") or "")
        reviewers.add(reviewer_id)
        sheet_path = _resolve_hashed_file(
            root,
            sheet_descriptor,
            path_field="path",
            sha_field="sha256",
            label=f"{sheet_label} sheet",
            errors=errors,
        )
        sheet_manifest_path = _resolve_hashed_file(
            root,
            sheet_descriptor,
            path_field="manifestPath",
            sha_field="manifestSha256",
            label=f"{sheet_label} manifest",
            errors=errors,
        )
        if sheet_path is None or sheet_manifest_path is None:
            continue
        try:
            sheet_rows = _jsonl(sheet_path)
            sheet_manifest = _json(sheet_manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{sheet_label} payload is invalid: {exc}")
            continue
        sheet_ids = {str(row.get("caseId") or "") for row in sheet_rows}
        if len(sheet_rows) != case_count or sheet_ids != report_ids:
            errors.append(f"{sheet_label} case IDs/count differ from HTTP report")
        if (
            sheet_manifest.get("schemaVersion")
            != "aishop-customer-service-answer-review/v2"
            or sheet_manifest.get("artifact") != "BLINDED_ANSWER_REVIEW_SHEET"
            or sheet_manifest.get("lifecycle") != "OPEN"
            or sheet_manifest.get("reviewerId") != reviewer_id
            or sheet_manifest.get("caseCount") != case_count
            or sheet_manifest.get("sourceRunId") != source_run_id
            or sheet_manifest.get("sourceReportPath")
            != descriptor.get("sourceReportPath")
            or sheet_manifest.get("sourceReportSha256")
            != descriptor.get("sourceReportSha256")
            or sheet_manifest.get("sheetPath") != sheet_descriptor.get("path")
            or sheet_manifest.get("sheetSha256")
            != sheet_descriptor.get("sha256")
            or sheet_manifest.get("containsExpectedOrSelfJudgment") is not False
        ):
            errors.append(f"{sheet_label} open-sheet manifest binding is invalid")
        if any(
            row.get("schemaVersion")
            != "aishop-customer-service-answer-review/v2"
            or row.get("reviewerId") != reviewer_id
            or row.get("guidelinesVersion") != "customer-service-answer-quality-v1"
            or row.get("sourceRunId") != source_run_id
            or row.get("sourceReportSha256")
            != descriptor.get("sourceReportSha256")
            or set(row) != expected_row_fields
            or any(
                field not in row
                for field in (
                    "message",
                    "answer",
                    "sourceRefs",
                    "observedHandoff",
                    "comment",
                )
            )
            or set((row.get("labels") or {}).keys()) != expected_label_keys
            or any(value is not None for value in (row.get("labels") or {}).values())
            for row in sheet_rows
        ):
            errors.append(f"{sheet_label} is no longer an unfilled open sheet")
        for row in sheet_rows:
            case_id = str(row.get("caseId") or "")
            source = source_by_id.get(case_id)
            if source is None:
                continue
            for field in ("message", "answer", "sourceRefs", "observedHandoff"):
                if _canonical_sha256(row.get(field)) != _canonical_sha256(source[field]):
                    errors.append(
                        f"{sheet_label} immutable source field differs for {case_id}: {field}"
                    )
                    break
        if _contains_forbidden_key(
            sheet_rows,
            {"expected", "predicted", "modelOutput", "modelPrediction"},
        ):
            errors.append(f"{sheet_label} leaks gold/model fields")
    if reviewers != {"reviewer-a", "reviewer-b"}:
        errors.append(f"{label}.reviewSheets must bind reviewer-a and reviewer-b")


def _validate_pricing_estimate(
    root: Path,
    descriptor: Any,
    errors: list[str],
) -> None:
    """Validate public list-price provenance without treating it as billing."""

    label = "evaluation.pricingEstimate"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="path",
        sha_field="sha256",
        label=f"{label} file",
        errors=errors,
    )
    if path is None:
        return
    try:
        quote = _json(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} JSON is invalid: {exc}")
        return
    required = {
        "schemaVersion",
        "status",
        "sourceUrl",
        "retrievedAt",
        "sourceContentSha256",
        "provider",
        "region",
        "modelId",
        "modelFingerprint",
        "inputPriceCnyPerMillion",
        "outputPriceCnyPerMillion",
        "inputTokenUpperBound",
        "quoteSha256",
    }
    if not required.issubset(quote):
        errors.append(f"{label} is missing required provenance fields")
    if (
        quote.get("schemaVersion") != "aishop-list-price-estimate/v1"
        or quote.get("status") != "ESTIMATED_LIST_PRICE"
        or quote.get("usableForBudgetGate") is not False
        or quote.get("billingContractVerified") is not False
    ):
        errors.append(f"{label} status or budget boundary is invalid")
    source_hash = str(quote.get("sourceContentSha256") or "")
    quote_hash = str(quote.get("quoteSha256") or "")
    if not HEX64.fullmatch(source_hash) or not HEX64.fullmatch(quote_hash):
        errors.append(f"{label} SHA-256 fields are invalid")
    else:
        without_hash = dict(quote)
        without_hash.pop("quoteSha256", None)
        if _canonical_sha256(without_hash) != quote_hash:
            errors.append(f"{label} quoteSha256 does not match normalized quote")


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
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
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
    _validate_customer_service_candidate(
        root,
        evaluation.get("customerServiceCandidateV2"),
        errors,
    )
    _validate_customer_service_answer_review(
        root,
        evaluation.get("customerServiceAnswerReview"),
        errors,
    )
    v13_answer_review = evaluation.get("customerServiceAnswerReviewV13")
    if v13_answer_review is None:
        errors.append("evaluation.customerServiceAnswerReviewV13 is required")
    else:
        _validate_customer_service_answer_review(
            root,
            v13_answer_review,
            errors,
            label="evaluation.customerServiceAnswerReviewV13",
            require_pre_evaluator_observation=True,
        )
    _validate_pricing_estimate(
        root,
        evaluation.get("pricingEstimate"),
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
    _validate_scorecard(
        root,
        evaluation.get("scorecard"),
        current,
        errors,
    )
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
    _validate_diagnostic_evidence(root, evaluation.get("diagnosticEvidence"), errors)
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
