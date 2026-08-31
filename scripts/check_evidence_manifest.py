#!/usr/bin/env python3
"""Validate the single AI evaluation evidence chain used by AI Shop."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
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


def _is_badcase_jsonl(path: Path) -> bool:
    """Recognize all badcase JSONL filename conventions used by evidence packages."""

    if path.suffix != ".jsonl":
        return False
    normalized = re.sub(r"[^a-z]", "", path.stem.casefold())
    return "badcase" in normalized


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
    live_execution_roles = {
        "LIVE_EXECUTED_OBSERVATION": {
            "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
            "behaviorContractCount": 10,
            "behaviorContractViolationCount": 2,
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
            elif runtime_role in live_execution_roles:
                expected = live_execution_roles[runtime_role]
                if descriptor.get("expectedStatus") != expected["status"]:
                    errors.append(f"{label} live execution expected status is invalid")
                if report.get("schemaVersion") != "aishop-customer-service-http-evaluation/v1":
                    errors.append(f"{label} live HTTP report schema is invalid")
                if (
                    package.get("releaseGateEligible") is not False
                    or report.get("releaseGateEligible") is not False
                    or report.get("normalQualityDenominatorExcluded") is not True
                    or package.get("status") != expected["status"]
                    or report.get("status") != expected["status"]
                ):
                    errors.append(f"{label} live HTTP lifecycle boundary is invalid")
                if package.get("providerCallsReexecuted") is not None:
                    errors.append(f"{label} live execution provenance must remain null")
                if package.get("sourceObservationReportSha256") is not None:
                    errors.append(f"{label} live execution source observation must remain null")
                dataset = report.get("dataset") or {}
                if (
                    dataset.get("annotationStatus") != "HUMAN_VERIFIED"
                    or dataset.get("sha256") != descriptor.get("datasetSha256")
                    or dataset.get("caseCount") != descriptor.get("caseCount")
                ):
                    errors.append(f"{label} live execution dataset binding is invalid")
                execution_rate = (
                    (report.get("httpExecution") or {}).get("executionRate") or {}
                )
                if (
                    execution_rate.get("numerator") != descriptor.get("caseCount")
                    or execution_rate.get("denominator") != descriptor.get("caseCount")
                    or (report.get("httpExecution") or {}).get("errorCaseIds") != []
                ):
                    errors.append(f"{label} live HTTP execution denominator is invalid")
                behavior_contracts = report.get("behaviorContracts") or {}
                results = behavior_contracts.get("results") or []
                violation_count = sum(
                    isinstance(result, dict) and result.get("status") != "PASSED"
                    for result in results
                )
                if (
                    behavior_contracts.get("contractCount")
                    != expected["behaviorContractCount"]
                    or behavior_contracts.get("executedContractCount")
                    != expected["behaviorContractCount"]
                    or len(results) != expected["behaviorContractCount"]
                    or violation_count != expected["behaviorContractViolationCount"]
                    or descriptor.get("behaviorContractViolationCount")
                    != expected["behaviorContractViolationCount"]
                ):
                    errors.append(f"{label} live behavior-contract evidence is invalid")
                route_metrics = ((report.get("httpRoute") or {}).get("metrics") or {})
                for metric_name in ("slotEntitySpanF1", "slotExactMatch"):
                    if (route_metrics.get(metric_name) or {}).get("status") != "UNAVAILABLE":
                        errors.append(f"{label} live HTTP slot metric must remain unavailable")
                answer_quality = report.get("answerQuality") or {}
                if (
                    answer_quality.get("status") != "PENDING_HUMAN_REVIEW"
                    or answer_quality.get("selfJudged") is not False
                    or answer_quality.get("answerCorrectness") is not None
                    or answer_quality.get("citationGroundingSupport") is not None
                    or answer_quality.get("unsafeAnswerRate") is not None
                ):
                    errors.append(f"{label} live HTTP answer quality must remain pending human review")
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


def _answer_review_ratio_metric(
    numerator: int,
    denominator: int,
    *,
    badcase_ids: list[str],
    lower_is_better: bool = False,
) -> dict[str, Any]:
    """Recompute a review metric without trusting the packaged report."""

    unique_badcases = list(dict.fromkeys(badcase_ids))
    if denominator <= 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "numerator": numerator,
            "denominator": denominator,
            "confidenceInterval95": None,
            "badcaseCount": len(unique_badcases),
            "badcaseIds": unique_badcases,
            "lowerIsBetter": lower_is_better,
        }
    z = 1.959963984540054
    proportion = numerator / denominator
    scale = 1 + (z * z / denominator)
    center = (proportion + z * z / (2 * denominator)) / scale
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / denominator
            + z * z / (4 * denominator * denominator)
        )
        / scale
    )
    return {
        "status": "MEASURED",
        "value": round(proportion, 6),
        "numerator": numerator,
        "denominator": denominator,
        "confidenceInterval95": {
            "lower": round(max(0.0, center - margin), 6),
            "upper": round(min(1.0, center + margin), 6),
            "method": "wilson",
            "confidenceLevel": 0.95,
        },
        "badcaseCount": len(unique_badcases),
        "badcaseIds": unique_badcases,
        "lowerIsBetter": lower_is_better,
    }


def _answer_review_field_stats(
    left_values: list[Any], right_values: list[Any]
) -> dict[str, Any]:
    """Recompute categorical agreement and Cohen kappa inputs."""

    left = [json.dumps(value, ensure_ascii=False, sort_keys=True) for value in left_values]
    right = [json.dumps(value, ensure_ascii=False, sort_keys=True) for value in right_values]
    count = len(left)
    if count <= 0 or count != len(right):
        return {}
    agreement_count = sum(a == b for a, b in zip(left, right, strict=True))
    left_counts = {value: left.count(value) for value in set(left)}
    right_counts = {value: right.count(value) for value in set(right)}
    expected = sum(
        left_counts.get(value, 0) * right_counts.get(value, 0)
        for value in set(left_counts) | set(right_counts)
    ) / (count * count)
    observed = agreement_count / count
    kappa = None if expected >= 1.0 else (observed - expected) / (1.0 - expected)
    return {
        "agreementCount": agreement_count,
        "caseCount": count,
        "agreementRate": round(observed, 6),
        "expectedAgreement": round(expected, 6),
        "cohenKappa": None if kappa is None else round(kappa, 6),
    }


def _validate_targeted_customer_service_answer_review(
    root: Path,
    descriptor: Any,
    errors: list[str],
) -> None:
    """Validate an agreed targeted review and its immutable HTTP parent."""

    label = "evaluation.customerServiceAnswerReviewV25Targeted"
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    if (
        descriptor.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or descriptor.get("agreementStatus") != "AGREED_NO_ADJUDICATION"
        or descriptor.get("resultRole") != "TARGETED_REGRESSION_OBSERVATION"
        or descriptor.get("releaseGateEligible") is not False
        or descriptor.get("normalQualityDenominatorExcluded") is not True
    ):
        errors.append(f"{label} lifecycle boundary is invalid")

    source_descriptor = descriptor.get("sourceExecutionEvidence")
    final_descriptor = descriptor.get("finalEvidence")
    if not isinstance(source_descriptor, dict) or not isinstance(final_descriptor, dict):
        errors.append(f"{label} source/final evidence descriptors are required")
        return
    try:
        source_root = _resolve(root, str(source_descriptor.get("path") or ""))
        final_root = _resolve(root, str(final_descriptor.get("path") or ""))
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not source_root.is_dir() or not final_root.is_dir():
        errors.append(f"{label} source or final evidence directory is missing")
        return
    if not (source_root / "SHA256SUMS").is_file() or not (
        final_root / "SHA256SUMS"
    ).is_file():
        errors.append(f"{label} source or final evidence lacks SHA256SUMS")
        return

    source_sums = _parse_sums(source_root, errors)
    final_sums = _parse_sums(final_root, errors)
    if (
        source_descriptor.get("sha256SumsSha256")
        != _sha256(source_root / "SHA256SUMS")
        or final_descriptor.get("sha256SumsSha256")
        != _sha256(final_root / "SHA256SUMS")
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    if set(source_sums) != {
        "badcases.jsonl",
        "evidence-manifest.json",
        "report.json",
        "report.md",
    }:
        errors.append(f"{label} source execution inventory is invalid")
        return
    required_final = {
        "agreement.json",
        "agreement.md",
        "badcases.jsonl",
        "evidence-manifest.json",
        "final-report.json",
        "final-report.md",
        "reviews/reviewer-a.sealed.jsonl",
        "reviews/reviewer-a.sealed.jsonl.manifest.json",
        "reviews/reviewer-b.sealed.jsonl",
        "reviews/reviewer-b.sealed.jsonl.manifest.json",
    }
    if set(final_sums) != required_final:
        errors.append(f"{label} agreed evidence inventory is invalid")
        return

    try:
        source_package = _json(source_root / "evidence-manifest.json")
        source_report = _json(source_root / "report.json")
        package = _json(final_root / "evidence-manifest.json")
        final_report = _json(final_root / "final-report.json")
        agreement = _json(final_root / "agreement.json")
        badcases = _jsonl(final_root / "badcases.jsonl")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} payload is invalid: {exc}")
        return

    source_run_id = str(descriptor.get("sourceRunId") or "")
    source_report_path = str(descriptor.get("sourceReportPath") or "")
    source_report_sha = str(descriptor.get("sourceReportSha256") or "")
    case_count = descriptor.get("caseCount")
    if not isinstance(case_count, int) or isinstance(case_count, bool) or case_count <= 0:
        errors.append(f"{label} caseCount must be a positive integer")
        return
    source_cases = source_report.get("cases") or []
    source_ids = {
        str(row.get("caseId") or "")
        for row in source_cases
        if isinstance(row, dict)
    }
    source_inventory = {
        name: {
            "sha256": _sha256(source_root / name),
            "bytes": (source_root / name).stat().st_size,
        }
        for name in source_sums
        if name != "evidence-manifest.json"
    }
    if (
        source_descriptor.get("schemaVersion")
        != "aishop-customer-service-http-evidence/v1"
        or source_descriptor.get("reportSha256") != _sha256(source_root / "report.json")
        or source_package.get("schemaVersion")
        != "aishop-customer-service-http-evidence/v1"
        or source_package.get("kind") != "customer-service-http"
        or source_package.get("runId") != source_run_id
        or source_package.get("status") != "TARGETED_REGRESSION_OBSERVATION"
        or source_package.get("releaseGateEligible") is not False
        or source_package.get("sourceObservationReportSha256") != source_report_sha
        or source_package.get("files") != source_inventory
        or source_report.get("schemaVersion")
        != "aishop-customer-service-http-evaluation/v1"
        or source_report.get("runId") != source_run_id
        or source_report.get("status") != "TARGETED_REGRESSION_OBSERVATION"
        or source_report.get("releaseGateEligible") is not False
        or source_report.get("normalQualityDenominatorExcluded") is not True
        or (source_report.get("answerQuality") or {}).get("status")
        != "PENDING_HUMAN_REVIEW"
        or len(source_cases) != case_count
        or len(source_ids) != case_count
        or "" in source_ids
    ):
        errors.append(f"{label} source execution binding is invalid")

    final_inventory = {
        name: {
            "sha256": _sha256(final_root / name),
            "bytes": (final_root / name).stat().st_size,
        }
        for name in final_sums
        if name != "evidence-manifest.json"
    }
    source_fields = {
        "sourceRunId": source_run_id,
        "sourceReportPath": source_report_path,
        "sourceReportSha256": source_report_sha,
        "caseCount": case_count,
    }
    if (
        final_descriptor.get("schemaVersion")
        != "aishop-customer-service-answer-review-evidence/v1"
        or final_descriptor.get("finalReportSha256")
        != _sha256(final_root / "final-report.json")
        or final_descriptor.get("agreementSha256")
        != _sha256(final_root / "agreement.json")
        or final_descriptor.get("selfJudged") is not False
        or final_descriptor.get("releaseGateEligible") is not False
        or final_descriptor.get("normalQualityDenominatorExcluded") is not True
        or package.get("schemaVersion")
        != "aishop-customer-service-answer-review-evidence/v1"
        or package.get("kind") != "customer-service-answer-human-review"
        or package.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or package.get("releaseGateEligible") is not False
        or package.get("selfJudged") is not False
        or package.get("normalQualityDenominatorExcluded") is not True
        or package.get("adjudicationSha256") is not None
        or package.get("files") != final_inventory
        or final_report.get("schemaVersion")
        != "aishop-customer-service-answer-review-report/v2"
        or final_report.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or final_report.get("releaseGateEligible") is not False
        or final_report.get("selfJudged") is not False
        or final_report.get("normalQualityDenominatorExcluded") is not True
        or agreement.get("schemaVersion")
        != "aishop-customer-service-answer-review-agreement/v1"
        or agreement.get("status") != "AGREED_NO_ADJUDICATION"
        or agreement.get("releaseGateEligible") is not False
        or any(
            payload.get(field) != expected
            for payload in (package, final_report, agreement)
            for field, expected in source_fields.items()
        )
    ):
        errors.append(f"{label} agreed evidence lifecycle/source binding is invalid")

    source_evaluation = final_report.get("sourceEvaluation") or {}
    review_evidence = final_report.get("reviewEvidence") or {}
    if (
        source_evaluation
        != {
            "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
            "releaseGateEligible": False,
            "normalQualityDenominatorExcluded": True,
        }
        or review_evidence.get("adjudicationPath") is not None
        or review_evidence.get("adjudicationSha256") is not None
    ):
        errors.append(f"{label} denominator/adjudication boundary is invalid")

    expected_reviewer_ids = descriptor.get("reviewerIds")
    if (
        not isinstance(expected_reviewer_ids, list)
        or len(expected_reviewer_ids) != 2
        or len(set(expected_reviewer_ids)) != 2
    ):
        errors.append(f"{label} reviewer IDs are invalid")
        expected_reviewer_ids = []
    label_fields = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    row_fields = {
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
    review_rows: list[dict[str, dict[str, Any]]] = []
    reviewer_ids: list[str] = []
    for suffix, agreement_key, manifest_hash_key in (
        ("a", "reviewA", "reviewASha256"),
        ("b", "reviewB", "reviewBSha256"),
    ):
        sheet_path = final_root / "reviews" / f"reviewer-{suffix}.sealed.jsonl"
        sidecar_path = sheet_path.with_suffix(sheet_path.suffix + ".manifest.json")
        try:
            rows = _jsonl(sheet_path)
            sidecar = _json(sidecar_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} reviewer-{suffix} sheet is invalid: {exc}")
            continue
        reviewer_id = str(sidecar.get("reviewerId") or "")
        reviewer_ids.append(reviewer_id)
        sheet_sha = _sha256(sheet_path)
        if (
            sidecar.get("schemaVersion") != "aishop-customer-service-answer-review/v2"
            or sidecar.get("artifact") != "SEALED_ANSWER_REVIEW_SHEET"
            or sidecar.get("lifecycle") != "SEALED"
            or sidecar.get("caseCount") != case_count
            or sidecar.get("sheetSha256") != sheet_sha
            or sidecar.get("sourceRunId") != source_run_id
            or sidecar.get("sourceReportSha256") != source_report_sha
            or package.get(manifest_hash_key) != sheet_sha
            or (agreement.get(agreement_key) or {}).get("reviewerId") != reviewer_id
            or (agreement.get(agreement_key) or {}).get("sha256") != sheet_sha
        ):
            errors.append(f"{label} reviewer-{suffix} sealed binding is invalid")
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
            case_id = str(row.get("caseId") or "")
            if (
                set(row) != row_fields
                or not case_id
                or case_id in by_id
                or row.get("schemaVersion")
                != "aishop-customer-service-answer-review/v2"
                or row.get("reviewerId") != reviewer_id
                or row.get("sourceRunId") != source_run_id
                or row.get("sourceReportSha256") != source_report_sha
                or row.get("guidelinesVersion")
                != "customer-service-answer-quality-v1"
                or set(labels) != label_fields
                or not isinstance(labels.get("answerCorrect"), bool)
                or labels.get("citationSupport")
                not in {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
                or not isinstance(labels.get("handoffAppropriate"), bool)
                or not isinstance(labels.get("unsafeAnswer"), bool)
                or not isinstance(row.get("comment"), str)
            ):
                errors.append(f"{label} reviewer-{suffix} row is invalid")
                break
            by_id[case_id] = row
        if len(by_id) != case_count or set(by_id) != source_ids:
            errors.append(f"{label} reviewer-{suffix} coverage is invalid")
        review_rows.append(by_id)
    if set(reviewer_ids) != set(expected_reviewer_ids):
        errors.append(f"{label} reviewers are not the registered independent pair")
    if len(review_rows) != 2 or set(review_rows[0]) != set(review_rows[1]):
        errors.append(f"{label} sealed sheets cover different cases")
        return

    field_values: dict[str, tuple[list[Any], list[Any]]] = {
        field: ([], []) for field in label_fields
    }
    disagreement_ids: list[str] = []
    for case_id, left in review_rows[0].items():
        right = review_rows[1][case_id]
        if any(
            _canonical_sha256(left.get(field)) != _canonical_sha256(right.get(field))
            for field in ("message", "answer", "sourceRefs", "observedHandoff")
        ):
            errors.append(f"{label} immutable review source differs: {case_id}")
        fields_differ = False
        for field in label_fields:
            field_values[field][0].append(left["labels"][field])
            field_values[field][1].append(right["labels"][field])
            fields_differ |= left["labels"][field] != right["labels"][field]
        if fields_differ:
            disagreement_ids.append(case_id)
    expected_field_stats = {
        field: _answer_review_field_stats(*field_values[field]) for field in label_fields
    }
    exact_count = case_count - len(disagreement_ids)
    if (
        disagreement_ids
        or agreement.get("disagreements") != []
        or agreement.get("exactAgreementCaseCount") != exact_count
        or agreement.get("disagreementCaseCount") != 0
        or agreement.get("caseAgreementRate") != 1.0
        or agreement.get("fieldStats") != expected_field_stats
        or final_descriptor.get("exactAgreementCaseCount") != exact_count
        or final_descriptor.get("disagreementCaseCount") != 0
        or final_descriptor.get("adjudicationCaseCount") != 0
    ):
        errors.append(f"{label} no-adjudication agreement summary is invalid")

    final_cases = final_report.get("cases")
    if not isinstance(final_cases, list) or len(final_cases) != case_count:
        errors.append(f"{label} final cases are invalid")
        return
    answer_ok = citation_supported = citation_eligible = handoff_ok = unsafe = joint = 0
    metric_badcases = {
        metric: []
        for metric in (
            "answerCorrectness",
            "citationGroundingSupport",
            "handoffAppropriateness",
            "unsafeAnswerRate",
            "jointQualityPassRate",
        )
    }
    expected_badcases: list[dict[str, Any]] = []
    seen_final_ids: set[str] = set()
    for final_case in final_cases:
        case_id = str(final_case.get("caseId") or "") if isinstance(final_case, dict) else ""
        left = review_rows[0].get(case_id)
        right = review_rows[1].get(case_id)
        if left is None or right is None or case_id in seen_final_ids:
            errors.append(f"{label} final case ID is invalid: {case_id or '?'}")
            continue
        seen_final_ids.add(case_id)
        labels = left["labels"]
        comment = " | ".join(
            value for value in (left["comment"], right["comment"]) if value
        )
        if (
            final_case.get("labels") != labels
            or final_case.get("labelSource") != "REVIEWER_AGREEMENT"
            or final_case.get("adjudicator") is not None
            or final_case.get("comment") != comment
            or final_case.get("answerSha256")
            != hashlib.sha256(str(left.get("answer") or "").encode("utf-8")).hexdigest()
        ):
            errors.append(f"{label} final agreed decision is invalid: {case_id}")
        is_answer_ok = labels["answerCorrect"] is True
        citation_label = str(labels["citationSupport"])
        is_citation_eligible = citation_label != "NOT_APPLICABLE"
        is_citation_ok = citation_label in {"SUPPORTED", "NOT_APPLICABLE"}
        is_handoff_ok = labels["handoffAppropriate"] is True
        is_unsafe = labels["unsafeAnswer"] is True
        is_joint = is_answer_ok and is_citation_ok and is_handoff_ok and not is_unsafe
        answer_ok += int(is_answer_ok)
        citation_supported += int(citation_label == "SUPPORTED")
        citation_eligible += int(is_citation_eligible)
        handoff_ok += int(is_handoff_ok)
        unsafe += int(is_unsafe)
        joint += int(is_joint)
        failures: list[str] = []
        for metric, passed in (
            ("answerCorrectness", is_answer_ok),
            ("citationGroundingSupport", is_citation_ok),
            ("handoffAppropriateness", is_handoff_ok),
            ("unsafeAnswerRate", not is_unsafe),
            ("jointQualityPassRate", is_joint),
        ):
            if not passed:
                metric_badcases[metric].append(case_id)
                failures.append(metric)
        if failures:
            expected_badcases.append(
                {
                    "caseId": case_id,
                    "message": left.get("message"),
                    "answerSha256": hashlib.sha256(
                        str(left.get("answer") or "").encode("utf-8")
                    ).hexdigest(),
                    "failedMetrics": failures,
                    "labels": labels,
                    "comment": comment,
                }
            )
    expected_metrics = {
        "answerCorrectness": _answer_review_ratio_metric(
            answer_ok,
            case_count,
            badcase_ids=metric_badcases["answerCorrectness"],
        ),
        "citationGroundingSupport": _answer_review_ratio_metric(
            citation_supported,
            citation_eligible,
            badcase_ids=metric_badcases["citationGroundingSupport"],
        ),
        "handoffAppropriateness": _answer_review_ratio_metric(
            handoff_ok,
            case_count,
            badcase_ids=metric_badcases["handoffAppropriateness"],
        ),
        "unsafeAnswerRate": _answer_review_ratio_metric(
            unsafe,
            case_count,
            badcase_ids=metric_badcases["unsafeAnswerRate"],
            lower_is_better=True,
        ),
        "jointQualityPassRate": _answer_review_ratio_metric(
            joint,
            case_count,
            badcase_ids=metric_badcases["jointQualityPassRate"],
        ),
        "citationUndecidableCount": sum(
            row["labels"]["citationSupport"] == "UNDECIDABLE"
            for row in review_rows[0].values()
        ),
    }
    descriptor_metrics = final_descriptor.get("metrics") or {}
    required_metric_names = {
        "answerCorrectness",
        "citationGroundingSupport",
        "handoffAppropriateness",
        "unsafeAnswerRate",
        "jointQualityPassRate",
    }
    expected_final_agreement = {
        "exactAgreementCaseCount": exact_count,
        "disagreementCaseCount": 0,
        "caseAgreementRate": 1.0,
        "fieldStats": expected_field_stats,
    }
    if (
        set(seen_final_ids) != source_ids
        or final_report.get("agreement") != expected_final_agreement
        or final_report.get("metrics") != expected_metrics
        or final_report.get("badcases") != expected_badcases
        or badcases != expected_badcases
        or final_descriptor.get("badcaseCount") != len(expected_badcases)
        or set(descriptor_metrics) != required_metric_names
        or any(
            any(metric.get(field) != expected.get(field) for field in expected)
            for name, expected in descriptor_metrics.items()
            for metric in [expected_metrics.get(name) or {}]
        )
    ):
        errors.append(f"{label} final metrics or badcases are invalid")


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


def _checksum_bound_package(
    root: Path,
    descriptor: Any,
    errors: list[str],
    *,
    label: str,
) -> Path | None:
    """Validate a newly declared non-release package and return its root."""

    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return None
    if (
        descriptor.get("releaseGateEligible") is not False
        or descriptor.get("finalUnseenEligible") is not False
    ):
        errors.append(f"{label} must remain non-release and non-unseen")
    relative = str(descriptor.get("path") or "")
    try:
        package_root = _resolve(root, relative)
    except ValueError as exc:
        errors.append(str(exc))
        return None
    if not package_root.is_dir():
        errors.append(f"{label} directory is missing: {relative}")
        return None
    _parse_sums(package_root, errors)
    sums_path = package_root / "SHA256SUMS"
    if (
        not sums_path.is_file()
        or descriptor.get("sha256SumsSha256") != _sha256(sums_path)
    ):
        errors.append(f"{label} SHA256SUMS digest differs from project manifest")
    return package_root


def _validate_current_customer_service_v2(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the fail-closed v2 label, paired, and v43 evidence chain."""

    v2 = evaluation.get("customerServiceV2")
    v2_root = _checksum_bound_package(
        root,
        v2,
        errors,
        label="evaluation.customerServiceV2",
    )
    if v2_root is not None and isinstance(v2, dict):
        dataset_path = _resolve_hashed_file(
            root,
            v2,
            path_field="datasetPath",
            sha_field="datasetSha256",
            label="evaluation.customerServiceV2 dataset",
            errors=errors,
        )
        audit_path = _resolve_hashed_file(
            root,
            v2,
            path_field="provenanceAuditPath",
            sha_field="provenanceAuditSha256",
            label="evaluation.customerServiceV2 provenance audit",
            errors=errors,
        )
        if dataset_path is not None:
            try:
                rows = _jsonl(dataset_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceV2 dataset is invalid: {exc}")
            else:
                if (
                    len(rows) != v2.get("caseCount")
                    or len({str(row.get("id") or "") for row in rows}) != len(rows)
                    or any(
                        (row.get("annotation") or {}).get("status") != "HUMAN_VERIFIED"
                        for row in rows
                    )
                ):
                    errors.append(
                        "evaluation.customerServiceV2 dataset count, IDs, or row declarations are invalid"
                    )
        if audit_path is not None:
            try:
                audit = _json(audit_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceV2 provenance audit is invalid: {exc}")
            else:
                combined = (audit.get("caseCounts") or {}).get("combined")
                source_hash = (audit.get("sourceHashes") or {}).get("combined")
                if (
                    v2.get("status") != "HUMAN_VERIFIED_PROVENANCE_REVIEW_REQUIRED"
                    or v2.get("qualityClaimStatus") != "DEVELOPMENT_DIAGNOSTIC_ONLY"
                    or v2.get("hashAndLabelChainValid") is not True
                    or v2.get("independentReviewClaimVerified") is not False
                    or audit.get("status") != v2.get("status")
                    or audit.get("releaseGateEligible") is not False
                    or audit.get("finalUnseenEligible") is not False
                    or audit.get("hashAndLabelChainValid") is not True
                    or audit.get("independentReviewClaimVerified") is not False
                    or combined != v2.get("caseCount")
                    or source_hash != v2.get("datasetSha256")
                ):
                    errors.append(
                        "evaluation.customerServiceV2 provenance status or dataset binding is invalid"
                    )

    label_descriptor = evaluation.get("customerServiceV2LabelAudit")
    label_root = _checksum_bound_package(
        root,
        label_descriptor,
        errors,
        label="evaluation.customerServiceV2LabelAudit",
    )
    if label_root is not None and isinstance(label_descriptor, dict):
        audit_path = _resolve_hashed_file(
            root,
            label_descriptor,
            path_field="auditPath",
            sha_field="auditSha256",
            label="evaluation.customerServiceV2LabelAudit report",
            errors=errors,
        )
        taxonomy_path = _resolve_hashed_file(
            root,
            label_descriptor,
            path_field="taxonomyPath",
            sha_field="taxonomySha256",
            label="evaluation.customerServiceV2LabelAudit taxonomy",
            errors=errors,
        )
        if audit_path is not None:
            try:
                audit = _json(audit_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceV2LabelAudit report is invalid: {exc}")
            else:
                summary = audit.get("summary") or {}
                gates = audit.get("gates") or {}
                if (
                    audit.get("status") != label_descriptor.get("status")
                    or summary.get("findingCount") != label_descriptor.get("findingCount")
                    or summary.get("blockingFindingCount")
                    != label_descriptor.get("blockingFindingCount")
                    or summary.get("affectedCaseCount")
                    != label_descriptor.get("affectedCaseCount")
                    or gates.get("releaseGateEligible") is not False
                    or gates.get("finalUnseenEligible") is not False
                    or gates.get("labelConsistencyPassed") is not False
                ):
                    errors.append(
                        "evaluation.customerServiceV2LabelAudit status, counts, or gates are invalid"
                    )
        if taxonomy_path is not None:
            try:
                taxonomy = _json(taxonomy_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceV2LabelAudit taxonomy is invalid: {exc}")
            else:
                if taxonomy.get("contractVersion") != "customer-service-taxonomy-v2.1":
                    errors.append(
                        "evaluation.customerServiceV2LabelAudit taxonomy version is invalid"
                    )

    paired_descriptor = evaluation.get("customerServiceV2RoutingPaired")
    paired_root = _checksum_bound_package(
        root,
        paired_descriptor,
        errors,
        label="evaluation.customerServiceV2RoutingPaired",
    )
    if paired_root is not None and isinstance(paired_descriptor, dict):
        report_path = _resolve_hashed_file(
            root,
            paired_descriptor,
            path_field="reportPath",
            sha_field="reportSha256",
            label="evaluation.customerServiceV2RoutingPaired report",
            errors=errors,
        )
        if report_path is not None:
            try:
                report = _json(report_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceV2RoutingPaired report is invalid: {exc}")
            else:
                design = report.get("comparisonDesign") or {}
                gates = report.get("gates") or {}
                if (
                    report.get("status") != paired_descriptor.get("status")
                    or design.get("caseCount") != paired_descriptor.get("caseCount")
                    or design.get("knownSetExposure") != "FULLY_EXPOSED_DEVELOPMENT_SET"
                    or gates.get("developmentFixValidated")
                    != paired_descriptor.get("developmentFixValidated")
                    or gates.get("releaseGateEligible") is not False
                    or gates.get("finalUnseenEligible") is not False
                    or gates.get("labelConsistencyPassed") is not False
                    or gates.get("provenancePassed") is not False
                ):
                    errors.append(
                        "evaluation.customerServiceV2RoutingPaired design or fail-closed gates are invalid"
                    )

    http_descriptor = evaluation.get("customerServiceHttpV43")
    http_root = _checksum_bound_package(
        root,
        http_descriptor,
        errors,
        label="evaluation.customerServiceHttpV43",
    )
    if http_root is not None and isinstance(http_descriptor, dict):
        report_path = _resolve_hashed_file(
            root,
            http_descriptor,
            path_field="reportPath",
            sha_field="reportSha256",
            label="evaluation.customerServiceHttpV43 report",
            errors=errors,
        )
        if report_path is not None:
            try:
                report = _json(report_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.customerServiceHttpV43 report is invalid: {exc}")
            else:
                execution = report.get("httpExecution") or {}
                capture = execution.get("observationCaptureRate") or {}
                production = execution.get("executionRate") or {}
                contracts = report.get("behaviorContracts") or {}
                answer = report.get("answerQuality") or {}
                validity = report.get("evidenceValidity") or {}
                if (
                    report.get("status") != http_descriptor.get("status")
                    or report.get("qualityClaimStatus")
                    != http_descriptor.get("qualityClaimStatus")
                    or report.get("releaseGateEligible") is not False
                    or len(report.get("cases") or []) != http_descriptor.get("caseCount")
                    or capture.get("numerator")
                    != http_descriptor.get("observationCaptureCount")
                    or production.get("numerator")
                    != http_descriptor.get("productionExecutionCount")
                    or execution.get("metricDefinition")
                    != "PRODUCTION_EPISODE_ADAPTER_STATUS_PASSED_V2"
                    or contracts.get("executedContractCount")
                    != http_descriptor.get("behaviorContractPassedCount")
                    or contracts.get("contractCount")
                    != http_descriptor.get("behaviorContractCount")
                    or contracts.get("violationCount") != 0
                    or answer.get("status") != http_descriptor.get("answerQualityStatus")
                    or (answer.get("reviewCoverage") or {}).get("numerator")
                    != http_descriptor.get("humanAnswerReviewCount")
                    or answer.get("answerCorrectness") is not None
                    or answer.get("citationGroundingSupport") is not None
                    or answer.get("unsafeAnswerRate") is not None
                    or validity.get("status") != "BLOCKED_HUMAN_READJUDICATION"
                    or validity.get("blocking") is not True
                ):
                    errors.append(
                        "evaluation.customerServiceHttpV43 execution, contracts, validity, or answer gate is invalid"
                    )

    audit_descriptor = evaluation.get("currentEvaluationAudit")
    if not isinstance(audit_descriptor, dict):
        errors.append("evaluation.currentEvaluationAudit must be an object")
        return
    markdown_path = _resolve_hashed_file(
        root,
        audit_descriptor,
        path_field="markdownPath",
        sha_field="markdownSha256",
        label="evaluation.currentEvaluationAudit markdown",
        errors=errors,
    )
    json_path = _resolve_hashed_file(
        root,
        audit_descriptor,
        path_field="jsonPath",
        sha_field="jsonSha256",
        label="evaluation.currentEvaluationAudit JSON",
        errors=errors,
    )
    if json_path is not None:
        try:
            audit = _json(json_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"evaluation.currentEvaluationAudit JSON is invalid: {exc}")
        else:
            required = audit.get("requiredCurrentStatuses") or {}
            if (
                markdown_path is None
                or audit.get("schemaVersion") != "aishop-evaluation-system-audit/v1"
                or required.get("qualityClaimStatus")
                != audit_descriptor.get("status")
                or required.get("releaseGateEligible") is not False
                or required.get("finalUnseenEligible") is not False
                or audit_descriptor.get("releaseGateEligible") is not False
                or audit_descriptor.get("finalUnseenEligible") is not False
            ):
                errors.append(
                    "evaluation.currentEvaluationAudit status or claim boundary is invalid"
                )


def _validate_customer_service_v54_remediation(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the v54 execution, human adjudication, and final metric chain."""

    label = "evaluation.customerServiceBadcaseRemediationV54"
    descriptor = evaluation.get("customerServiceBadcaseRemediationV54")
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    if (
        descriptor.get("status") != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED"
        or descriptor.get("releaseGateEligible") is not False
        or descriptor.get("finalUnseenEligible") is not False
    ):
        errors.append(f"{label} lifecycle or claim boundary is invalid")

    handoff_markdown = _resolve_hashed_file(
        root,
        descriptor,
        path_field="handoffMarkdownPath",
        sha_field="handoffMarkdownSha256",
        label=f"{label} handoff markdown",
        errors=errors,
    )
    handoff_json_path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="handoffJsonPath",
        sha_field="handoffJsonSha256",
        label=f"{label} handoff JSON",
        errors=errors,
    )

    full_descriptor = descriptor.get("fullExecution")
    full_root = _checksum_bound_package(
        root,
        full_descriptor,
        errors,
        label=f"{label}.fullExecution",
    )
    source_report: dict[str, Any] | None = None
    if full_root is not None and isinstance(full_descriptor, dict):
        source_report_path = full_root / "report.json"
        try:
            source_report = _json(source_report_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label}.fullExecution report is invalid: {exc}")
        else:
            execution = source_report.get("httpExecution") or {}
            contracts = source_report.get("behaviorContracts") or {}
            answer = source_report.get("answerQuality") or {}
            diagnostics = source_report.get("qualityDiagnostics") or {}
            if (
                _sha256(source_report_path) != full_descriptor.get("reportSha256")
                or len(source_report.get("cases") or [])
                != full_descriptor.get("caseCount")
                or (execution.get("executionRate") or {}).get("numerator")
                != full_descriptor.get("executionPassedCount")
                or contracts.get("executedContractCount")
                != full_descriptor.get("behaviorContractPassedCount")
                or contracts.get("contractCount")
                != full_descriptor.get("behaviorContractCount")
                or diagnostics.get("fixtureCleanupFailureCount")
                != full_descriptor.get("fixtureCleanupFailureCount")
                or diagnostics.get("hardConstraintViolationCount")
                != full_descriptor.get("hardConstraintViolationCount")
                or answer.get("status")
                != full_descriptor.get("answerQualityStatus")
                or (answer.get("reviewCoverage") or {}).get("numerator")
                != full_descriptor.get("humanAnswerReviewCount")
                or answer.get("answerCorrectness") is not None
                or answer.get("citationGroundingSupport") is not None
                or answer.get("unsafeAnswerRate") is not None
            ):
                errors.append(f"{label}.fullExecution source boundary is invalid")

    final_descriptor = descriptor.get("finalAnswerQuality")
    final_root = _checksum_bound_package(
        root,
        final_descriptor,
        errors,
        label=f"{label}.finalAnswerQuality",
    )
    final_report: dict[str, Any] | None = None
    if final_root is not None and isinstance(final_descriptor, dict):
        final_report_path = final_root / "final-report.json"
        try:
            final_report = _json(final_report_path)
            final_manifest = _json(final_root / "evidence-manifest.json")
            final_badcases = _jsonl(final_root / "badcases.jsonl")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label}.finalAnswerQuality payload is invalid: {exc}")
        else:
            metrics = final_report.get("metrics") or {}
            agreement = final_report.get("agreement") or {}
            expected_metrics = {
                "answerCorrectness": (
                    final_descriptor.get("answerCorrectCount"),
                    final_descriptor.get("caseCount"),
                ),
                "citationGroundingSupport": (
                    final_descriptor.get("citationSupportedCount"),
                    final_descriptor.get("citationEligibleCount"),
                ),
                "handoffAppropriateness": (
                    final_descriptor.get("handoffAppropriateCount"),
                    final_descriptor.get("caseCount"),
                ),
                "unsafeAnswerRate": (
                    final_descriptor.get("unsafeAnswerCount"),
                    final_descriptor.get("caseCount"),
                ),
                "jointQualityPassRate": (
                    final_descriptor.get("jointQualityPassedCount"),
                    final_descriptor.get("caseCount"),
                ),
            }
            metric_binding_valid = all(
                (metrics.get(name) or {}).get("numerator") == numerator
                and (metrics.get(name) or {}).get("denominator") == denominator
                for name, (numerator, denominator) in expected_metrics.items()
            )
            if (
                _sha256(final_report_path)
                != final_descriptor.get("finalReportSha256")
                or final_report.get("status") != final_descriptor.get("status")
                or final_report.get("annotationStatus")
                != final_descriptor.get("annotationStatus")
                or final_report.get("evidenceTier")
                != final_descriptor.get("evidenceTier")
                or final_report.get("humanDecisionAuthority") is not True
                or final_report.get("caseCount")
                != final_descriptor.get("caseCount")
                or agreement.get("exactAgreementCaseCount")
                != final_descriptor.get("exactAgreementCaseCount")
                or agreement.get("disagreementCaseCount")
                != final_descriptor.get("adjudicatedCaseCount")
                or not metric_binding_valid
                or len(final_badcases) != final_descriptor.get("badcaseCount")
                or final_manifest.get("annotationStatus")
                != final_descriptor.get("annotationStatus")
                or final_manifest.get("evidenceTier")
                != final_descriptor.get("evidenceTier")
                or final_manifest.get("humanDecisionAuthority") is not True
                or final_manifest.get("releaseGateEligible") is not False
                or final_descriptor.get("releaseGateEligible") is not False
                or final_descriptor.get("finalUnseenEligible") is not False
            ):
                errors.append(f"{label}.finalAnswerQuality metrics or provenance are invalid")
            if source_report is not None and (
                final_report.get("sourceReportSha256")
                != full_descriptor.get("reportSha256")
                or final_report.get("sourceRunId") != source_report.get("runId")
            ):
                errors.append(f"{label} final review differs from the frozen v54 source")

    handoff = descriptor.get("humanReviewHandoff") or {}
    adjudicator = handoff.get("adjudicatorC") or {}
    archive_descriptor = adjudicator.get("returnArchive")
    archive_root = _checksum_bound_package(
        root,
        archive_descriptor,
        errors,
        label=f"{label}.humanReviewHandoff.adjudicatorC.returnArchive",
    )
    if archive_root is not None and isinstance(archive_descriptor, dict):
        try:
            archive_manifest = _json(archive_root / "evidence-manifest.json")
            archive_audit = _json(archive_root / "validation-audit.json")
            clarification = _json(archive_root / "human-approval-clarification.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} adjudication return archive is invalid: {exc}")
        else:
            raw_return = archive_audit.get("returnedAdjudication") or {}
            if (
                archive_manifest.get("status")
                != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED"
                or archive_manifest.get("evidenceTier")
                != archive_descriptor.get("evidenceTier")
                or archive_manifest.get("readOnly") is not True
                or archive_audit.get("status") != archive_manifest.get("status")
                or archive_audit.get("humanDecisionAuthority") is not True
                or (archive_audit.get("validation") or {}).get("frozenFieldChanges")
                != []
                or (archive_audit.get("validation") or {}).get("caseCount")
                != adjudicator.get("caseCount")
                or raw_return.get("sha256") != adjudicator.get("adjudicationSha256")
                or clarification.get("humanDecisionAuthority") is not True
                or clarification.get("aiAssistanceUsed") is not True
                or clarification.get("pureHumanUnaidedClaim") is not False
                or clarification.get("evidenceTier")
                != archive_descriptor.get("evidenceTier")
            ):
                errors.append(f"{label} adjudication return provenance is invalid")
            if final_root is not None:
                final_manifest = _json(final_root / "evidence-manifest.json")
                if final_manifest.get("rawReturnArchiveSha256SumsSha256") != (
                    archive_descriptor.get("sha256SumsSha256")
                ):
                    errors.append(f"{label} final review is not bound to the return archive")

    returned_zip_path = None
    try:
        returned_zip_path = _resolve(root, str(adjudicator.get("returnedZipPath") or ""))
    except ValueError as exc:
        errors.append(str(exc))
    if (
        returned_zip_path is None
        or not returned_zip_path.is_file()
        or _sha256(returned_zip_path) != adjudicator.get("returnedZipSha256")
    ):
        errors.append(f"{label} returned adjudication ZIP binding is invalid")

    if handoff_json_path is not None:
        try:
            handoff_json = _json(handoff_json_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} handoff JSON is invalid: {exc}")
        else:
            handoff_final = handoff_json.get("finalAnswerQuality") or {}
            if (
                handoff_markdown is None
                or handoff_json.get("status") != descriptor.get("status")
                or handoff_final.get("sha256SumsSha256")
                != (final_descriptor or {}).get("sha256SumsSha256")
                or handoff_final.get("badcaseCount")
                != (final_descriptor or {}).get("badcaseCount")
                or handoff_final.get("metrics", {})
                .get("jointQualityPassRate", {})
                .get("numerator")
                != (final_descriptor or {}).get("jointQualityPassedCount")
            ):
                errors.append(f"{label} handoff conclusion differs from final evidence")


def _validate_customer_service_v56_regression(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the v3-knowledge regression chain without inventing quality labels."""

    label = "evaluation.customerServiceV3KnowledgeRegressionV56"
    descriptor = evaluation.get("customerServiceV3KnowledgeRegressionV56")
    if not isinstance(descriptor, dict):
        errors.append(f"{label} must be an object")
        return
    lifecycle = (
        descriptor.get("status"),
        descriptor.get("qualityClaimStatus"),
    )
    pending_adjudication = lifecycle == (
        "DUAL_REVIEW_COMPLETE_PENDING_HUMAN_ADJUDICATION",
        "DEVELOPMENT_DIAGNOSTIC_PENDING_HUMAN_ADJUDICATION",
    )
    adjudicated = lifecycle == (
        "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED",
        "DEVELOPMENT_DIAGNOSTIC_HUMAN_REVIEWED_ADJUDICATED",
    )
    if (
        lifecycle
        not in {
            (
                "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
                "DEVELOPMENT_DIAGNOSTIC_PENDING_HUMAN_REVIEW",
            ),
            (
                "DUAL_REVIEW_COMPLETE_PENDING_HUMAN_ADJUDICATION",
                "DEVELOPMENT_DIAGNOSTIC_PENDING_HUMAN_ADJUDICATION",
            ),
            (
                "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED",
                "DEVELOPMENT_DIAGNOSTIC_HUMAN_REVIEWED_ADJUDICATED",
            ),
        }
        or descriptor.get("releaseGateEligible") is not False
        or descriptor.get("finalUnseenEligible") is not False
    ):
        errors.append(f"{label} lifecycle or claim boundary is invalid")

    source_human = descriptor.get("sourceHumanConclusion")
    source_human_root = _checksum_bound_package(
        root,
        source_human,
        errors,
        label=f"{label}.sourceHumanConclusion",
    )
    if source_human_root is not None and isinstance(source_human, dict):
        try:
            source_manifest = _json(source_human_root / "evidence-manifest.json")
            source_badcases = _jsonl(source_human_root / "badcases.jsonl")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} source human conclusion is invalid: {exc}")
        else:
            if (
                source_manifest.get("evidenceTier")
                != source_human.get("evidenceTier")
                or source_manifest.get("humanDecisionAuthority") is not True
                or source_human.get("humanDecisionAuthority") is not True
                or len(source_badcases) != source_human.get("remainingBadcaseCount")
            ):
                errors.append(f"{label} source human authority or badcase count is invalid")

    handoff_markdown = _resolve_hashed_file(
        root,
        descriptor,
        path_field="handoffMarkdownPath",
        sha_field="handoffMarkdownSha256",
        label=f"{label} handoff markdown",
        errors=errors,
    )
    handoff_json_path = _resolve_hashed_file(
        root,
        descriptor,
        path_field="handoffJsonPath",
        sha_field="handoffJsonSha256",
        label=f"{label} handoff JSON",
        errors=errors,
    )

    knowledge = descriptor.get("knowledgeV3")
    if not isinstance(knowledge, dict):
        errors.append(f"{label}.knowledgeV3 must be an object")
    else:
        catalog_path = _resolve_hashed_file(
            root,
            knowledge,
            path_field="catalogPath",
            sha_field="catalogSha256",
            label=f"{label} knowledge catalog",
            errors=errors,
        )
        metadata_path = _resolve_hashed_file(
            root,
            knowledge,
            path_field="factMetadataPath",
            sha_field="factMetadataSha256",
            label=f"{label} fact metadata",
            errors=errors,
        )
        if catalog_path is not None and metadata_path is not None:
            try:
                catalog = _json(catalog_path)
                metadata = _json(metadata_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"{label} knowledge overlay is invalid: {exc}")
            else:
                fact_ids = {
                    str(item.get("factId") or "")
                    for item in metadata.get("facts") or []
                    if isinstance(item, dict)
                }
                if (
                    catalog.get("schemaVersion")
                    != "aishop-knowledge-catalog-overlay/v1"
                    or catalog.get("catalogVersion") != 3
                    or metadata.get("schemaVersion")
                    != "aishop-fact-metadata-overlay/v1"
                    or metadata.get("canonicalCatalogSha256")
                    != knowledge.get("catalogSha256")
                    or metadata.get("factCount") != knowledge.get("factCount")
                    or not set(knowledge.get("newFactIds") or []).issubset(fact_ids)
                ):
                    errors.append(f"{label} knowledge overlay contract is invalid")

    behavior = descriptor.get("behaviorContract")
    if not isinstance(behavior, dict):
        errors.append(f"{label}.behaviorContract must be an object")
        behavior_payload: dict[str, Any] = {}
    else:
        behavior_path = _resolve_hashed_file(
            root,
            behavior,
            path_field="path",
            sha_field="sha256",
            label=f"{label} behavior contract",
            errors=errors,
        )
        try:
            behavior_payload = _json(behavior_path) if behavior_path else {}
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} behavior contract is invalid: {exc}")
            behavior_payload = {}
        target_ids = {
            "cs-gold-v1-036",
            "cs-gold-v1-048",
            "cs-candidate-v2-075",
            "cs-candidate-v2-090",
            "cs-candidate-v2-092",
            "cs-candidate-v2-110",
            "cs-candidate-v2-116",
        }
        contracts = behavior_payload.get("contracts") or []
        bound_targets = {
            str(item.get("caseId") or "")
            for item in contracts
            if isinstance(item, dict) and str(item.get("caseId") or "") in target_ids
        }
        if (
            behavior_payload.get("schemaVersion")
            != "aishop-customer-service-http-behavior-contracts/v1"
            or behavior_payload.get("annotationStatus")
            != "HUMAN_APPROVED_AI_ASSISTED"
            or behavior_payload.get("humanDecisionAuthority") is not True
            or len(contracts) != behavior.get("contractCount")
            or bound_targets != target_ids
            or len(bound_targets) != behavior.get("targetBadcaseContractCount")
        ):
            errors.append(f"{label} behavior contract coverage is invalid")

    targeted = descriptor.get("targetedExecution")
    targeted_root = _checksum_bound_package(
        root,
        targeted,
        errors,
        label=f"{label}.targetedExecution",
    )
    if targeted_root is not None and isinstance(targeted, dict):
        try:
            targeted_report = _json(targeted_root / "report.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} targeted report is invalid: {exc}")
        else:
            execution = targeted_report.get("httpExecution") or {}
            contracts = targeted_report.get("behaviorContracts") or {}
            answer = targeted_report.get("answerQuality") or {}
            if (
                _sha256(targeted_root / "report.json") != targeted.get("reportSha256")
                or len(targeted_report.get("cases") or []) != targeted.get("caseCount")
                or (execution.get("executionRate") or {}).get("numerator")
                != targeted.get("executionPassedCount")
                or contracts.get("executedContractCount")
                != targeted.get("applicableBehaviorContractPassedCount")
                or contracts.get("violationCount") != 0
                or (contracts.get("provenance") or {}).get("sha256")
                != (behavior or {}).get("sha256")
                or targeted_report.get("normalQualityDenominatorExcluded") is not True
                or targeted.get("normalQualityDenominatorExcluded") is not True
                or answer.get("status") != targeted.get("answerQualityStatus")
                or (answer.get("reviewCoverage") or {}).get("numerator") != 0
            ):
                errors.append(f"{label} targeted execution boundary is invalid")

    full = descriptor.get("fullExecution")
    full_root = _checksum_bound_package(
        root,
        full,
        errors,
        label=f"{label}.fullExecution",
    )
    full_report: dict[str, Any] | None = None
    if full_root is not None and isinstance(full, dict):
        try:
            full_report = _json(full_root / "report.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} full report is invalid: {exc}")
        else:
            execution = full_report.get("httpExecution") or {}
            contracts = full_report.get("behaviorContracts") or {}
            citation = full_report.get("citationContractDiagnostic") or {}
            diagnostics = full_report.get("qualityDiagnostics") or {}
            answer = full_report.get("answerQuality") or {}
            preflight = full_report.get("preflight") or {}
            if (
                _sha256(full_root / "report.json") != full.get("reportSha256")
                or full_report.get("status")
                != "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW"
                or len(full_report.get("cases") or []) != full.get("caseCount")
                or (execution.get("executionRate") or {}).get("numerator")
                != full.get("executionPassedCount")
                or (execution.get("observationCaptureRate") or {}).get("numerator")
                != full.get("observationCapturedCount")
                or contracts.get("executedContractCount")
                != full.get("behaviorContractPassedCount")
                or contracts.get("contractCount") != full.get("behaviorContractCount")
                or contracts.get("violationCount") != 0
                or (contracts.get("provenance") or {}).get("sha256")
                != (behavior or {}).get("sha256")
                or citation.get("invalidCaseCount")
                != full.get("citationContractInvalidCount")
                or diagnostics.get("fixtureCleanupFailureCount")
                != full.get("fixtureCleanupFailureCount")
                or diagnostics.get("hardConstraintViolationCount")
                != full.get("hardConstraintViolationCount")
                or answer.get("status") != full.get("answerQualityStatus")
                or (answer.get("reviewCoverage") or {}).get("numerator")
                != full.get("humanAnswerReviewCount")
                or answer.get("selfJudged") is not False
                or full.get("selfJudged") is not False
                or answer.get("answerCorrectness") is not None
                or answer.get("citationGroundingSupport") is not None
                or answer.get("unsafeAnswerRate") is not None
                or preflight.get("passed") is not True
            ):
                errors.append(f"{label} full execution or pending-review gate is invalid")

    review = descriptor.get("humanReviewHandoff")
    if not isinstance(review, dict):
        errors.append(f"{label}.humanReviewHandoff must be an object")
        review = {}
    delivery_path = _resolve_hashed_file(
        root,
        review,
        path_field="deliveryManifestPath",
        sha_field="deliveryManifestSha256",
        label=f"{label} review delivery manifest",
        errors=errors,
    )
    try:
        delivery = _json(delivery_path) if delivery_path else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"{label} review delivery manifest is invalid: {exc}")
        delivery = {}
    reviewer_orders: list[list[str]] = []
    for reviewer_key, reviewer_id in (("reviewerA", "reviewer-a"), ("reviewerB", "reviewer-b")):
        item = review.get(reviewer_key)
        if not isinstance(item, dict):
            errors.append(f"{label}.humanReviewHandoff.{reviewer_key} must be an object")
            continue
        zip_path = _resolve_hashed_file(
            root,
            item,
            path_field="zipPath",
            sha_field="zipSha256",
            label=f"{label} {reviewer_id} ZIP",
            errors=errors,
        )
        sheet_path = _resolve_hashed_file(
            root,
            item,
            path_field="sheetPath",
            sha_field="sheetSha256",
            label=f"{label} {reviewer_id} sheet",
            errors=errors,
        )
        manifest_path = _resolve_hashed_file(
            root,
            item,
            path_field="manifestPath",
            sha_field="manifestSha256",
            label=f"{label} {reviewer_id} manifest",
            errors=errors,
        )
        if zip_path is None or sheet_path is None or manifest_path is None:
            continue
        try:
            rows = _jsonl(sheet_path)
            sheet_manifest = _json(manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} {reviewer_id} review sheet is invalid: {exc}")
            continue
        reviewer_orders.append([str(row.get("caseId") or "") for row in rows])
        labels_blank = all(
            row.get("reviewerId") == reviewer_id
            and row.get("comment") == ""
            and isinstance(row.get("labels"), dict)
            and all(value is None for value in row["labels"].values())
            for row in rows
        )
        delivery_package = (delivery.get("packages") or {}).get(reviewer_key) or {}
        if (
            len(rows) != review.get("caseCountPerReviewer")
            or len({str(row.get("caseId") or "") for row in rows}) != len(rows)
            or not labels_blank
            or sheet_manifest.get("lifecycle") != "OPEN"
            or sheet_manifest.get("reviewerId") != reviewer_id
            or sheet_manifest.get("containsExpectedOrSelfJudgment") is not False
            or sheet_manifest.get("messageProjection") != review.get("messageProjection")
            or sheet_manifest.get("sheetSha256") != item.get("sheetSha256")
            or sheet_manifest.get("sourceReportSha256") != (full or {}).get("reportSha256")
            or delivery_package.get("path") != item.get("zipPath")
            or delivery_package.get("sha256") != item.get("zipSha256")
        ):
            errors.append(f"{label} {reviewer_id} blinding or source binding is invalid")
    if (
        delivery.get("status") != "OPEN_INDEPENDENT_A_B_REVIEW"
        or delivery.get("caseCountPerReviewer") != review.get("caseCountPerReviewer")
        or (delivery.get("adjudication") or {}).get("sendNow") is not False
        or len(reviewer_orders) != 2
        or reviewer_orders[0] == reviewer_orders[1]
        or set(reviewer_orders[0]) != set(reviewer_orders[1])
    ):
        errors.append(f"{label} dual-review handoff or randomization is invalid")

    if pending_adjudication or adjudicated:
        returned = review.get("returnedReview")
        if not isinstance(returned, dict):
            errors.append(f"{label} returned review binding must be an object")
            returned = {}
        raw_a = _resolve_hashed_file(
            root,
            returned,
            path_field="reviewerARawZipPath",
            sha_field="reviewerARawZipSha256",
            label=f"{label} reviewer-a raw return ZIP",
            errors=errors,
        )
        raw_b = _resolve_hashed_file(
            root,
            returned,
            path_field="reviewerBRawZipPath",
            sha_field="reviewerBRawZipSha256",
            label=f"{label} reviewer-b raw return ZIP",
            errors=errors,
        )
        archive_descriptor = review.get("roundOneReturnArchive")
        archive_root = _checksum_bound_package(
            root,
            archive_descriptor,
            errors,
            label=f"{label}.roundOneReturnArchive",
        )
        pending_descriptor = review.get("pendingAdjudicationEvidence")
        pending_root = _checksum_bound_package(
            root,
            pending_descriptor,
            errors,
            label=f"{label}.pendingAdjudicationEvidence",
        )
        adjudicator = review.get("adjudicatorC")
        if not isinstance(adjudicator, dict):
            errors.append(f"{label}.adjudicatorC must be an object")
            adjudicator = {}
        adjudicator_zip = _resolve_hashed_file(
            root,
            adjudicator,
            path_field="zipPath",
            sha_field="zipSha256",
            label=f"{label} adjudicator-c ZIP",
            errors=errors,
        )
        adjudication_delivery_path = _resolve_hashed_file(
            root,
            adjudicator,
            path_field="deliveryManifestPath",
            sha_field="deliveryManifestSha256",
            label=f"{label} adjudication delivery manifest",
            errors=errors,
        )
        try:
            archive_manifest = (
                _json(archive_root / "evidence-manifest.json")
                if archive_root is not None
                else {}
            )
            archive_audit = (
                _json(archive_root / "intake-audit.json")
                if archive_root is not None
                else {}
            )
            pending_manifest = (
                _json(pending_root / "evidence-manifest.json")
                if pending_root is not None
                else {}
            )
            pending_agreement = (
                _json(pending_root / "agreement.json")
                if pending_root is not None
                else {}
            )
            adjudication_delivery = (
                _json(adjudication_delivery_path)
                if adjudication_delivery_path is not None
                else {}
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} returned-review or adjudication evidence is invalid: {exc}")
            archive_manifest = {}
            archive_audit = {}
            pending_manifest = {}
            pending_agreement = {}
            adjudication_delivery = {}
        expected_counts = {
            "caseCount": 120,
            "exactAgreementCaseCount": 118,
            "disagreementCaseCount": 2,
            "caseAgreementRate": 0.983333,
        }
        archive_agreement = archive_audit.get("agreement") or {}
        delivery_source = adjudication_delivery.get("sourceReview") or {}
        delivery_adjudicator = adjudication_delivery.get("adjudicatorC") or {}
        expected_review_status = (
            "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED"
            if adjudicated
            else "DUAL_REVIEW_COMPLETE_PENDING_HUMAN_ADJUDICATION"
        )
        if (
            review.get("status") != expected_review_status
            or review.get("adjudicationSendNow") is not pending_adjudication
            or review.get("caseCountPerReviewer") != expected_counts["caseCount"]
            or any(
                review.get(key) != expected_counts[key]
                for key in (
                    "exactAgreementCaseCount",
                    "disagreementCaseCount",
                    "caseAgreementRate",
                )
            )
            or returned.get("evidenceTier") != "HUMAN_APPROVED_AI_ASSISTED"
            or returned.get("humanDecisionAuthority") is not True
            or returned.get("pureHumanUnaidedClaim") is not False
            or raw_a is None
            or raw_b is None
            or archive_manifest.get("schemaVersion")
            != "aishop-v56-answer-review-return-archive/v1"
            or archive_manifest.get("status")
            != "HUMAN_APPROVED_AI_ASSISTED_RETURNS_ARCHIVED_NORMALIZED_AND_SEALED"
            or archive_manifest.get("evidenceTier") != "HUMAN_APPROVED_AI_ASSISTED"
            or archive_manifest.get("humanDecisionAuthority") is not True
            or archive_manifest.get("readOnly") is not True
            or any(archive_agreement.get(key) != value for key, value in expected_counts.items())
            or pending_manifest.get("schemaVersion")
            != "aishop-customer-service-answer-review-pending-evidence/v1"
            or pending_manifest.get("status") != "PENDING_ADJUDICATION"
            or pending_manifest.get("sourceReportSha256") != (full or {}).get("reportSha256")
            or pending_agreement.get("status") != "PENDING_ADJUDICATION"
            or any(pending_agreement.get(key) != value for key, value in expected_counts.items())
            or pending_agreement.get("sourceReportSha256") != (full or {}).get("reportSha256")
            or adjudication_delivery.get("status") != "OPEN_HUMAN_ADJUDICATION"
            or delivery_source.get("pendingEvidencePath")
            != (pending_descriptor or {}).get("path")
            or delivery_source.get("pendingEvidenceSha256SumsSha256")
            != (pending_descriptor or {}).get("sha256SumsSha256")
            or any(delivery_source.get(key) != value for key, value in expected_counts.items())
            or delivery_adjudicator.get("zipPath") != adjudicator.get("zipPath")
            or delivery_adjudicator.get("zipSha256") != adjudicator.get("zipSha256")
            or delivery_adjudicator.get("caseCount") != 2
        ):
            errors.append(f"{label} completed dual review or pending adjudication binding is invalid")
        if adjudicator_zip is not None and pending_root is not None:
            try:
                with zipfile.ZipFile(adjudicator_zip) as bundle:
                    members = {
                        item.filename
                        for item in bundle.infolist()
                        if not item.is_dir()
                    }
                    template_bytes = bundle.read("adjudication.open.jsonl")
                template_path = pending_root / "adjudication.template.jsonl"
                if (
                    members
                    != {
                        "_ORIGINAL-SHA256SUMS",
                        "_PACKAGE-README.md",
                        "adjudication.open.jsonl",
                        "adjudicator-attestation.template.json",
                        "仲裁说明.md",
                    }
                    or template_bytes != template_path.read_bytes()
                    or _sha256(template_path)
                    != delivery_adjudicator.get("adjudicationTemplateSha256")
                ):
                    errors.append(f"{label} adjudicator-c ZIP inventory or template differs")
            except (OSError, KeyError, zipfile.BadZipFile) as exc:
                errors.append(f"{label} adjudicator-c ZIP is invalid: {exc}")
        if adjudicated:
            returned_zip = _resolve_hashed_file(
                root,
                adjudicator,
                path_field="returnedZipPath",
                sha_field="returnedZipSha256",
                label=f"{label} returned adjudication ZIP",
                errors=errors,
            )
            return_archive_descriptor = adjudicator.get("returnArchive")
            return_archive_root = _checksum_bound_package(
                root,
                return_archive_descriptor,
                errors,
                label=f"{label}.adjudicatorC.returnArchive",
            )
            final_descriptor = descriptor.get("finalAnswerQuality")
            final_root = _checksum_bound_package(
                root,
                final_descriptor,
                errors,
                label=f"{label}.finalAnswerQuality",
            )
            try:
                return_manifest = (
                    _json(return_archive_root / "evidence-manifest.json")
                    if return_archive_root is not None
                    else {}
                )
                return_audit = (
                    _json(return_archive_root / "validation-audit.json")
                    if return_archive_root is not None
                    else {}
                )
                clarification = (
                    _json(return_archive_root / "human-approval-clarification.json")
                    if return_archive_root is not None
                    else {}
                )
                final_manifest = (
                    _json(final_root / "evidence-manifest.json")
                    if final_root is not None
                    else {}
                )
                final_report = (
                    _json(final_root / "final-report.json")
                    if final_root is not None
                    else {}
                )
                final_badcases = (
                    _jsonl(final_root / "badcases.jsonl")
                    if final_root is not None
                    else []
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"{label} final human evidence is invalid: {exc}")
                return_manifest = {}
                return_audit = {}
                clarification = {}
                final_manifest = {}
                final_report = {}
                final_badcases = []
            validation = return_audit.get("validation") or {}
            human_approval = (final_report.get("reviewEvidence") or {}).get(
                "humanApproval"
            ) or {}
            metrics = final_report.get("metrics") or {}
            answer_metric = metrics.get("answerCorrectness") or {}
            citation_metric = metrics.get("citationGroundingSupport") or {}
            handoff_metric = metrics.get("handoffAppropriateness") or {}
            unsafe_metric = metrics.get("unsafeAnswerRate") or {}
            joint_metric = metrics.get("jointQualityPassRate") or {}
            archived_zip = (
                return_archive_root / "raw-return/original-return.zip"
                if return_archive_root is not None
                else None
            )
            archived_adjudication = (
                return_archive_root / "raw-return/adjudication.open.jsonl"
                if return_archive_root is not None
                else None
            )
            archived_attestation = (
                return_archive_root
                / "raw-return/adjudicator-attestation.template.json"
                if return_archive_root is not None
                else None
            )
            if (
                returned_zip is None
                or return_manifest.get("schemaVersion")
                != "aishop-v56-answer-adjudication-return-evidence/v1"
                or return_manifest.get("status")
                != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED"
                or return_manifest.get("evidenceTier")
                != "HUMAN_APPROVED_AI_ASSISTED"
                or return_manifest.get("humanDecisionAuthority") is not True
                or clarification.get("status")
                != "PROJECT_OWNER_CONFIRMED_HUMAN_ADJUDICATION_AI_ASSISTED_EDITING"
                or clarification.get("humanDecisionAuthority") is not True
                or clarification.get("aiAssistanceUsed") is not True
                or clarification.get("pureHumanUnaidedClaim") is not False
                or validation.get("caseCount") != 2
                or validation.get("frozenFieldChanges") != []
                or validation.get("finalLabelsComplete") is not True
                or validation.get("reasonsComplete") is not True
                or validation.get("adjudicatorId") != adjudicator.get("adjudicatorId")
                or archived_zip is None
                or not archived_zip.is_file()
                or _sha256(archived_zip) != adjudicator.get("returnedZipSha256")
                or archived_adjudication is None
                or not archived_adjudication.is_file()
                or _sha256(archived_adjudication)
                != adjudicator.get("adjudicationSha256")
                or archived_attestation is None
                or not archived_attestation.is_file()
                or _sha256(archived_attestation)
                != adjudicator.get("rawAttestationSha256")
                or final_manifest.get("schemaVersion")
                != "aishop-customer-service-answer-review-evidence/v1"
                or final_manifest.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
                or final_manifest.get("annotationStatus")
                != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED"
                or final_manifest.get("evidenceTier")
                != "HUMAN_APPROVED_AI_ASSISTED"
                or final_manifest.get("humanDecisionAuthority") is not True
                or final_report.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
                or final_report.get("annotationStatus")
                != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED"
                or final_report.get("humanDecisionAuthority") is not True
                or final_report.get("sourceReportSha256")
                != (full or {}).get("reportSha256")
                or final_report.get("caseCount") != 120
                or final_report.get("normalQualityDenominatorExcluded") is not True
                or _sha256(final_root / "final-report.json")
                != (final_descriptor or {}).get("finalReportSha256")
                or human_approval.get("humanDecisionAuthority") is not True
                or human_approval.get("pureHumanUnaidedClaim") is not False
                or human_approval.get("rawReturnZipSha256")
                != adjudicator.get("returnedZipSha256")
                or human_approval.get("rawAdjudicationSha256")
                != adjudicator.get("adjudicationSha256")
                or len(final_badcases) != 0
                or answer_metric.get("numerator") != 120
                or answer_metric.get("denominator") != 120
                or citation_metric.get("numerator") != 67
                or citation_metric.get("denominator") != 67
                or handoff_metric.get("numerator") != 120
                or handoff_metric.get("denominator") != 120
                or unsafe_metric.get("numerator") != 0
                or unsafe_metric.get("denominator") != 120
                or joint_metric.get("numerator") != 120
                or joint_metric.get("denominator") != 120
                or (final_descriptor or {}).get("answerCorrectCount") != 120
                or (final_descriptor or {}).get("citationSupportedCount") != 67
                or (final_descriptor or {}).get("citationEligibleCount") != 67
                or (final_descriptor or {}).get("handoffAppropriateCount") != 120
                or (final_descriptor or {}).get("unsafeAnswerCount") != 0
                or (final_descriptor or {}).get("jointQualityPassedCount") != 120
                or (final_descriptor or {}).get("badcaseCount") != 0
            ):
                errors.append(f"{label} final metrics or human provenance is invalid")
    elif (
        review.get("status") != "OPEN_INDEPENDENT_A_B_REVIEW"
        or review.get("adjudicationSendNow") is not False
    ):
        errors.append(f"{label} open dual-review lifecycle is invalid")

    if handoff_json_path is not None:
        try:
            handoff_json = _json(handoff_json_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"{label} handoff JSON is invalid: {exc}")
        else:
            handoff_full = handoff_json.get("fullExecution") or {}
            handoff_review = handoff_json.get("humanReviewHandoff") or {}
            handoff_final = handoff_json.get("finalAnswerQuality") or {}
            claim_key = (
                "latestHumanCertifiedSameSetMetricsAreV56"
                if adjudicated
                else (
                    "latestHumanCertifiedMetricsRemainV54UntilV56AdjudicationCompletes"
                    if pending_adjudication
                    else "latestHumanCertifiedMetricsRemainV54UntilV56ReviewCompletes"
                )
            )
            if (
                handoff_markdown is None
                or handoff_json.get("status") != descriptor.get("status")
                or handoff_full.get("reportSha256") != (full or {}).get("reportSha256")
                or handoff_full.get("answerQualityStatus")
                != (full or {}).get("answerQualityStatus")
                or handoff_review.get("deliveryManifestSha256")
                != review.get("deliveryManifestSha256")
                or handoff_review.get("status") != review.get("status")
                or handoff_review.get("disagreementCaseCount")
                != review.get("disagreementCaseCount")
                or (handoff_json.get("claimBoundary") or {}).get(claim_key) is not True
                or (
                    adjudicated
                    and (
                        handoff_final.get("sha256SumsSha256")
                        != (descriptor.get("finalAnswerQuality") or {}).get(
                            "sha256SumsSha256"
                        )
                        or handoff_final.get("finalReportSha256")
                        != (descriptor.get("finalAnswerQuality") or {}).get(
                            "finalReportSha256"
                        )
                        or handoff_final.get("jointQualityPassedCount") != 120
                        or handoff_final.get("badcaseCount") != 0
                    )
                )
            ):
                errors.append(f"{label} handoff conclusion differs from frozen evidence")


def _validate_evaluation_archive_and_handoff(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the indexed archive, recovered intake, and editable handoff export."""

    catalog_descriptor = evaluation.get("evaluationAssetCatalog")
    if not isinstance(catalog_descriptor, dict):
        errors.append("evaluation.evaluationAssetCatalog must be an object")
    else:
        catalog_json_path = _resolve_hashed_file(
            root,
            catalog_descriptor,
            path_field="jsonPath",
            sha_field="jsonSha256",
            label="evaluation.evaluationAssetCatalog JSON",
            errors=errors,
        )
        catalog_markdown_path = _resolve_hashed_file(
            root,
            catalog_descriptor,
            path_field="markdownPath",
            sha_field="markdownSha256",
            label="evaluation.evaluationAssetCatalog markdown",
            errors=errors,
        )
        if catalog_json_path is not None:
            try:
                catalog = _json(catalog_json_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.evaluationAssetCatalog JSON is invalid: {exc}")
            else:
                summary = catalog.get("summary") or {}
                policy = catalog.get("archivePolicy") or {}
                catalog_inventory_valid = True

                def validate_file_records(
                    records: Any,
                    expected_paths: set[str],
                    *,
                    check_rows: bool,
                ) -> None:
                    nonlocal catalog_inventory_valid
                    if not isinstance(records, list):
                        catalog_inventory_valid = False
                        return
                    record_paths = {
                        str(item.get("path") or "")
                        for item in records
                        if isinstance(item, dict)
                    }
                    if record_paths != expected_paths or len(record_paths) != len(records):
                        catalog_inventory_valid = False
                        return
                    for item in records:
                        try:
                            path = _resolve(root, str(item.get("path") or ""))
                        except ValueError:
                            catalog_inventory_valid = False
                            continue
                        if (
                            not path.is_file()
                            or path.stat().st_size != item.get("bytes")
                            or _sha256(path) != item.get("sha256")
                        ):
                            catalog_inventory_valid = False
                            continue
                        if check_rows and "rowCount" in item:
                            try:
                                row_count = sum(
                                    1
                                    for line in path.read_text(encoding="utf-8").splitlines()
                                    if line.strip()
                                )
                            except (OSError, UnicodeDecodeError):
                                catalog_inventory_valid = False
                            else:
                                if row_count != item.get("rowCount"):
                                    catalog_inventory_valid = False

                dataset_root = (
                    root / "AI_Shop-backend/AI_Shop-agent/evaluation/datasets"
                )
                dataset_paths = {
                    path.relative_to(root).as_posix()
                    for path in dataset_root.rglob("*")
                    if path.is_file()
                }
                validate_file_records(
                    catalog.get("datasets"),
                    dataset_paths,
                    check_rows=True,
                )

                catalog_outputs = {
                    str(catalog_descriptor.get("jsonPath") or ""),
                    str(catalog_descriptor.get("markdownPath") or ""),
                }
                doc_paths: set[str] = set()
                for doc_root in (root / "docs/evaluation", root / "docs/project"):
                    doc_paths.update(
                        path.relative_to(root).as_posix()
                        for path in doc_root.rglob("*")
                        if path.is_file()
                    )
                for extra in (
                    root / "README.md",
                    root / "docs/README.md",
                    root / "AI_Shop-backend/AI_Shop-agent/evaluation/README.md",
                    root / "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/README.md",
                ):
                    if extra.is_file():
                        doc_paths.add(extra.relative_to(root).as_posix())
                doc_paths.difference_update(catalog_outputs)
                validate_file_records(
                    catalog.get("documentation"),
                    doc_paths,
                    check_rows=False,
                )

                evidence_root = root / "AI_Shop-backend/AI_Shop-agent/evaluation-evidence"
                evidence_manifests = sorted(evidence_root.rglob("evidence-manifest.json"))
                package_paths = {
                    path.parent.relative_to(root).as_posix() for path in evidence_manifests
                }
                package_records = catalog.get("evidencePackages")
                if not isinstance(package_records, list) or {
                    str(item.get("path") or "")
                    for item in package_records
                    if isinstance(item, dict)
                } != package_paths:
                    catalog_inventory_valid = False
                else:
                    for item in package_records:
                        try:
                            package_root = _resolve(root, str(item.get("path") or ""))
                        except ValueError:
                            catalog_inventory_valid = False
                            continue
                        files = [path for path in package_root.rglob("*") if path.is_file()]
                        package_manifest = package_root / "evidence-manifest.json"
                        sums = package_root / "SHA256SUMS"
                        badcases = [path for path in files if _is_badcase_jsonl(path)]
                        try:
                            badcase_rows = sum(len(_jsonl(path)) for path in badcases)
                        except (OSError, json.JSONDecodeError, TypeError, ValueError):
                            catalog_inventory_valid = False
                            badcase_rows = -1
                        if (
                            not package_manifest.is_file()
                            or _sha256(package_manifest) != item.get("manifestSha256")
                            or len(files) != item.get("fileCount")
                            or sum(path.stat().st_size for path in files)
                            != item.get("totalBytes")
                            or sums.is_file() != item.get("checksumBound")
                            or len(badcases) != item.get("badCaseFiles")
                            or badcase_rows != item.get("badCaseRows")
                        ):
                            catalog_inventory_valid = False
                        if sums.is_file() and _sha256(sums) != item.get("sha256SumsSha256"):
                            catalog_inventory_valid = False

                badcase_paths = {
                    path.relative_to(root).as_posix()
                    for path in evidence_root.rglob("*.jsonl")
                    if _is_badcase_jsonl(path)
                }
                validate_file_records(
                    catalog.get("badCaseAssets"),
                    badcase_paths,
                    check_rows=True,
                )
                expected_counts = {
                    "datasetFileCount": catalog_descriptor.get("datasetFileCount"),
                    "evidencePackageCount": catalog_descriptor.get("evidencePackageCount"),
                    "badCaseFileCount": catalog_descriptor.get("badCaseFileCount"),
                    "badCaseRowCount": catalog_descriptor.get("badCaseRowCount"),
                    "rootLooseJsonOrJsonlCount": catalog_descriptor.get(
                        "rootLooseJsonOrJsonlCount"
                    ),
                }
                root_structured = [
                    path
                    for path in root.iterdir()
                    if path.is_file() and path.suffix in {".json", ".jsonl"}
                ]
                if (
                    catalog_descriptor.get("status")
                    != "REFERENCE_SAFE_INDEXED_ARCHIVE"
                    or catalog_descriptor.get("releaseGateEligible") is not False
                    or catalog_descriptor.get("finalUnseenEligible") is not False
                    or catalog.get("schemaVersion")
                    != "aishop-evaluation-asset-catalog/v1"
                    or policy.get("mode") != "REFERENCE_SAFE_INDEXED_ARCHIVE"
                    or policy.get("immutableEvidenceMoved") is not False
                    or any(summary.get(key) != value for key, value in expected_counts.items())
                    or catalog.get("rootLooseStructuredFiles") != []
                    or root_structured
                    or catalog_markdown_path is None
                    or not catalog_inventory_valid
                ):
                    errors.append(
                        "evaluation.evaluationAssetCatalog status, counts, or root cleanup is invalid"
                    )

    recovered_descriptor = evaluation.get("customerServiceRecoveredReviewIntake")
    recovered_root = _checksum_bound_package(
        root,
        recovered_descriptor,
        errors,
        label="evaluation.customerServiceRecoveredReviewIntake",
    )
    if recovered_root is not None and isinstance(recovered_descriptor, dict):
        recovery_path = _resolve_hashed_file(
            root,
            recovered_descriptor,
            path_field="recoveryAuditPath",
            sha_field="recoveryAuditSha256",
            label="evaluation.customerServiceRecoveredReviewIntake audit",
            errors=errors,
        )
        if recovery_path is not None:
            try:
                recovery = _json(recovery_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(
                    "evaluation.customerServiceRecoveredReviewIntake audit is invalid: "
                    f"{exc}"
                )
            else:
                reviews = recovery.get("recoveredReviews") or []
                review_by_id = {
                    str(item.get("reviewerId") or ""): item
                    for item in reviews
                    if isinstance(item, dict)
                }
                expected_review_hashes = {
                    "reviewer-a": recovered_descriptor.get("reviewerASha256"),
                    "reviewer-b": recovered_descriptor.get("reviewerBSha256"),
                }
                review_bindings_valid = set(review_by_id) == set(expected_review_hashes)
                for reviewer_id, expected_hash in expected_review_hashes.items():
                    item = review_by_id.get(reviewer_id) or {}
                    archived_relative = str(item.get("archivedPath") or "")
                    archived_path = recovered_root / archived_relative
                    if (
                        not archived_path.is_file()
                        or _sha256(archived_path) != expected_hash
                        or item.get("sha256") != expected_hash
                        or item.get("caseCount")
                        != recovered_descriptor.get("recoveredCaseCountPerReviewer")
                        or item.get("completeValidationBeforeArchive") is not True
                        or item.get("matchesOriginalManifestSheetSha256") is not True
                        or item.get("matchesSealedManifestSourceOpenSheetSha256") is not True
                    ):
                        review_bindings_valid = False
                unresolved = set(recovery.get("unresolvedFindings") or [])
                if (
                    recovery.get("status") != recovered_descriptor.get("status")
                    or recovery.get("schemaVersion")
                    != "aishop-recovered-human-review-intake/v1"
                    or recovery.get("sourceDataset", {}).get("caseCount") != 60
                    or not review_bindings_valid
                    or unresolved
                    != {
                        "REVIEWA_EXPORT_HASH_SEMANTICS_INVALID",
                        "REVIEWB_EXPORT_HASH_SEMANTICS_INVALID",
                        "INDEPENDENCE_ATTESTATION_MISSING",
                    }
                    or recovery.get("reviewerIndependenceVerified") is not False
                    or recovery.get("releaseGateEligible") is not False
                    or recovery.get("finalUnseenEligible") is not False
                    or recovered_descriptor.get("sourceBytesRecovered") is not True
                    or recovered_descriptor.get("reviewerIndependenceVerified") is not False
                ):
                    errors.append(
                        "evaluation.customerServiceRecoveredReviewIntake recovery boundary is invalid"
                    )
        for obsolete_root_name in (
            "reviewer-a.open.jsonl",
            "reviewer-a.open.jsonl.manifest.json",
            "reviewer-b.open.jsonl",
            "reviewer-b.open.jsonl.manifest.json",
        ):
            if (root / obsolete_root_name).exists():
                errors.append(
                    "recovered review intake still has a loose root duplicate: "
                    f"{obsolete_root_name}"
                )

    handoff = evaluation.get("humanReviewHandoff")
    if not isinstance(handoff, dict):
        errors.append("evaluation.humanReviewHandoff must be an object")
        return
    try:
        handoff_root = _resolve(root, str(handoff.get("path") or ""))
    except ValueError as exc:
        errors.append(str(exc))
        return
    if not handoff_root.is_dir():
        errors.append("evaluation.humanReviewHandoff directory is missing")
        return
    package_manifest_path = _resolve_hashed_file(
        root,
        handoff,
        path_field="packageManifestPath",
        sha_field="packageManifestSha256",
        label="evaluation.humanReviewHandoff package manifest",
        errors=errors,
    )
    sums_path = _resolve_hashed_file(
        root,
        handoff,
        path_field="originalSha256SumsPath",
        sha_field="originalSha256SumsSha256",
        label="evaluation.humanReviewHandoff original checksums",
        errors=errors,
    )
    delivery_path = _resolve_hashed_file(
        root,
        handoff,
        path_field="deliveryManifestPath",
        sha_field="deliveryManifestSha256",
        label="evaluation.humanReviewHandoff delivery manifest",
        errors=errors,
    )
    if package_manifest_path is None or sums_path is None or delivery_path is None:
        return
    try:
        package_manifest = _json(package_manifest_path)
        delivery = _json(delivery_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"evaluation.humanReviewHandoff JSON is invalid: {exc}")
        return
    checksum_errors = False
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        expected, separator, relative = line.partition("  ")
        path = handoff_root / relative
        if (
            not separator
            or not HEX64.fullmatch(expected)
            or not path.is_file()
            or _sha256(path) != expected
        ):
            checksum_errors = True
            break
    tasks = package_manifest.get("tasks") or {}
    if (
        handoff.get("status") != "OPEN_HUMAN_WORK_PENDING"
        or handoff.get("releaseGateEligible") is not False
        or handoff.get("finalUnseenEligible") is not False
        or package_manifest.get("schemaVersion") != "aishop-human-review-handoff/v1"
        or package_manifest.get("lifecycle") != handoff.get("status")
        or package_manifest.get("humanWorkComplete") is not False
        or package_manifest.get("releaseGateEligible") is not False
        or package_manifest.get("finalUnseenEligible") is not False
        or (tasks.get("labelPolicyV2_1") or {}).get("caseCount")
        != handoff.get("labelPolicyCaseCount")
        or (tasks.get("answerQualityV43") or {}).get("caseCount")
        != handoff.get("answerQualityCaseCount")
        or (tasks.get("provenanceIndependentReaudit") or {}).get("caseCount")
        != handoff.get("provenanceReauditCaseCount")
        or (tasks.get("adjudication") or {}).get("status")
        != handoff.get("adjudicationStatus")
        or (tasks.get("externalUnseenReplacement") or {}).get("caseCount")
        != handoff.get("formalExternalUnseenCaseCount")
        or handoff.get("completedHumanReviewCount") != 0
        or checksum_errors
    ):
        errors.append("evaluation.humanReviewHandoff lifecycle or task binding is invalid")

    forbidden = {
        "expected",
        "predicted",
        "prediction",
        "modelOutput",
        "modelPrediction",
        "currentImmutableExpected",
        "issueCodes",
    }
    declared_sheet_hashes = handoff.get("openSheetSha256") or {}
    sheet_specs = (
        (
            "01-label-policy-v2.1/reviewer-a.open.jsonl",
            25,
            "label-policy-reviewer-a",
            "labelPolicyReviewerA",
        ),
        (
            "01-label-policy-v2.1/reviewer-b.open.jsonl",
            25,
            "label-policy-reviewer-b",
            "labelPolicyReviewerB",
        ),
        (
            "02-answer-quality-v43/reviewer-a.open.jsonl",
            120,
            "reviewer-a",
            "answerQualityReviewerA",
        ),
        (
            "02-answer-quality-v43/reviewer-b.open.jsonl",
            120,
            "reviewer-b",
            "answerQualityReviewerB",
        ),
        (
            "03-provenance-independent-reaudit/independent-reaudit.open.jsonl",
            12,
            None,
            "provenanceIndependentReviewer",
        ),
    )
    case_orders: dict[str, list[str]] = {}
    sheet_rows_by_path: dict[str, list[dict[str, Any]]] = {}
    sheet_manifests_by_path: dict[str, dict[str, Any]] = {}
    for relative, expected_count, reviewer_id, hash_key in sheet_specs:
        path = handoff_root / relative
        manifest_path = path.with_suffix(path.suffix + ".manifest.json")
        try:
            rows = _jsonl(path)
            sheet_manifest = _json(manifest_path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(f"evaluation.humanReviewHandoff sheet is invalid: {relative}: {exc}")
            continue
        sheet_rows_by_path[relative] = rows
        sheet_manifests_by_path[relative] = sheet_manifest
        case_key = "caseId" if "answer-quality" in relative else "id"
        case_orders[relative] = [str(row.get(case_key) or "") for row in rows]
        if (
            len(rows) != expected_count
            or len(set(case_orders[relative])) != expected_count
            or "" in case_orders[relative]
            or _contains_forbidden_key(rows, forbidden)
            or any(
                not isinstance(row.get("labels"), dict)
                or any(value is not None for value in row["labels"].values())
                for row in rows
            )
            or sheet_manifest.get("caseCount") != expected_count
            or (reviewer_id is not None and sheet_manifest.get("reviewerId") != reviewer_id)
            or _sha256(path) != declared_sheet_hashes.get(hash_key)
            or sheet_manifest.get("sheetSha256") != declared_sheet_hashes.get(hash_key)
        ):
            errors.append(f"evaluation.humanReviewHandoff blind sheet boundary is invalid: {relative}")
        declared_path = sheet_manifest.get("sheetPath")
        actual_relative = path.resolve().relative_to(root.resolve()).as_posix()
        if declared_path is not None and declared_path != actual_relative:
            errors.append(f"evaluation.humanReviewHandoff sheet path differs: {relative}")
    for prefix in ("01-label-policy-v2.1", "02-answer-quality-v43"):
        left = case_orders.get(f"{prefix}/reviewer-a.open.jsonl", [])
        right = case_orders.get(f"{prefix}/reviewer-b.open.jsonl", [])
        if not left or set(left) != set(right) or left == right:
            errors.append(f"evaluation.humanReviewHandoff A/B ordering is invalid: {prefix}")

    label_a_relative = "01-label-policy-v2.1/reviewer-a.open.jsonl"
    label_b_relative = "01-label-policy-v2.1/reviewer-b.open.jsonl"
    label_manifest = sheet_manifests_by_path.get(label_a_relative) or {}
    try:
        source_template = _resolve(root, str(label_manifest.get("sourceTemplatePath") or ""))
        taxonomy = _resolve(root, str(label_manifest.get("taxonomyContractPath") or ""))
        source_rows = _jsonl(source_template)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"evaluation.humanReviewHandoff label-policy source is invalid: {exc}")
    else:
        source_by_id = {str(row.get("id") or ""): row.get("input") for row in source_rows}
        label_source_valid = len(source_by_id) == 25 and "" not in source_by_id
        for relative in (label_a_relative, label_b_relative):
            sheet_manifest = sheet_manifests_by_path.get(relative) or {}
            rows = sheet_rows_by_path.get(relative) or []
            if (
                sheet_manifest.get("sourceTemplateSha256") != _sha256(source_template)
                or sheet_manifest.get("taxonomyContractSha256") != _sha256(taxonomy)
                or sheet_manifest.get("sourceDatasetSha256")
                != evaluation.get("customerServiceV2", {}).get("datasetSha256")
                or sheet_manifest.get("guidelinesVersion")
                != "customer-service-taxonomy-v2.1"
                or sheet_manifest.get("containsCurrentGoldOrModelPredictions") is not False
                or {
                    str(row.get("id") or ""): row.get("input") for row in rows
                }
                != source_by_id
            ):
                label_source_valid = False
        if not label_source_valid:
            errors.append(
                "evaluation.humanReviewHandoff label-policy source binding is invalid"
            )

    v43_descriptor = evaluation.get("customerServiceHttpV43") or {}
    for relative in (
        "02-answer-quality-v43/reviewer-a.open.jsonl",
        "02-answer-quality-v43/reviewer-b.open.jsonl",
    ):
        sheet_manifest = sheet_manifests_by_path.get(relative) or {}
        if (
            sheet_manifest.get("sourceReportPath") != v43_descriptor.get("reportPath")
            or sheet_manifest.get("sourceReportSha256")
            != v43_descriptor.get("reportSha256")
            or sheet_manifest.get("sourceRunId")
            != "customer-service-http-v43-human-v2-routing-execution-fix-20260826"
            or sheet_manifest.get("containsExpectedOrSelfJudgment") is not False
        ):
            errors.append(
                f"evaluation.humanReviewHandoff v43 source binding is invalid: {relative}"
            )
    if list((handoff_root / "04-adjudication-after-sealing").glob("*.jsonl")):
        errors.append("evaluation.humanReviewHandoff contains premature adjudication rows")
    if list((handoff_root / "05-external-unseen-not-ready").glob("*.jsonl")):
        errors.append("evaluation.humanReviewHandoff contains ineligible external final rows")

    delivery_packages = delivery.get("packages") or {}
    if set(delivery_packages) != {
        "answerQualityReviewerA",
        "answerQualityReviewerB",
        "coordinator",
        "labelPolicyReviewerA",
        "labelPolicyReviewerB",
        "provenanceIndependentReviewer",
    }:
        errors.append("evaluation.humanReviewHandoff delivery package set is invalid")
    for package_name, package in delivery_packages.items():
        if not isinstance(package, dict):
            errors.append(
                f"evaluation.humanReviewHandoff delivery descriptor is invalid: {package_name}"
            )
            continue
        try:
            zip_path = _resolve(root, str(package.get("path") or ""))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if (
            not zip_path.is_file()
            or zip_path.stat().st_size != package.get("bytes")
            or _sha256(zip_path) != package.get("sha256")
        ):
            errors.append(
                f"evaluation.humanReviewHandoff delivery ZIP binding is invalid: {package_name}"
            )


def _expected_answer_reviewers(
    descriptor: dict[str, Any],
    errors: list[str],
    *,
    label: str,
) -> list[str]:
    """Return the two independent reviewer identities bound by an evidence run."""

    configured = descriptor.get("reviewerIds")
    if configured is None:
        return ["reviewer-a", "reviewer-b"]
    if (
        not isinstance(configured, list)
        or len(configured) != 2
        or any(not isinstance(value, str) or not value.strip() for value in configured)
        or len(set(configured)) != 2
    ):
        errors.append(f"{label}.reviewerIds must contain two distinct reviewer IDs")
        return ["reviewer-a", "reviewer-b"]
    return list(configured)


def _validate_round1_returns_and_followup(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the 2026-08-27 intake, label review, provenance gate, and ZIPs."""

    intake = evaluation.get("humanReviewRound1Returns")
    if not isinstance(intake, dict):
        errors.append("evaluation.humanReviewRound1Returns must be an object")
    else:
        try:
            intake_root = _resolve(root, str(intake.get("intakePath") or ""))
        except ValueError as exc:
            errors.append(str(exc))
            intake_root = None
        if intake_root is None or not intake_root.is_dir():
            errors.append("evaluation.humanReviewRound1Returns intake directory is missing")
        else:
            sums_path = intake_root / "SHA256SUMS"
            sums = _parse_sums(intake_root, errors)
            expected_returns = {
                "returns/01-label-policy-v2.1/reviewer-a.open.jsonl",
                "returns/01-label-policy-v2.1/reviewer-a.open.jsonl.manifest.json",
                "returns/01-label-policy-v2.1/reviewer-b.open.jsonl",
                "returns/01-label-policy-v2.1/reviewer-b.open.jsonl.manifest.json",
                "returns/02-answer-quality-v43/reviewer-a.open.jsonl",
                "returns/02-answer-quality-v43/reviewer-a.open.jsonl.manifest.json",
                "returns/02-answer-quality-v43/reviewer-b.open.jsonl",
                "returns/02-answer-quality-v43/reviewer-b.open.jsonl.manifest.json",
                "returns/independent-reaudit.open.jsonl",
                "returns/independent-reaudit.open.jsonl.manifest.json",
                "returns/independent-reaudit-custody-attestation.template.json",
            }
            try:
                audit = _json(intake_root / "intake-audit.json")
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"evaluation.humanReviewRound1Returns intake is invalid: {exc}")
                audit = {}
            archived_returns = {
                name for name in sums if name.startswith("returns/")
            }
            if (
                not sums_path.is_file()
                or _sha256(sums_path) != intake.get("intakeSha256SumsSha256")
                or _sha256(intake_root / "intake-audit.json")
                != intake.get("intakeAuditSha256")
                or audit.get("schemaVersion")
                != "aishop-human-review-return-intake-audit/v1"
                or audit.get("status")
                != "EXACT_RETURNS_ARCHIVED_PROCESSING_REQUIRED"
                or audit.get("returnFileCount") != intake.get("returnFileCount")
                or archived_returns != expected_returns
                or intake.get("status")
                != "SEALED_PENDING_ADJUDICATION_AND_EXPANSION"
                or intake.get("releaseGateEligible") is not False
                or intake.get("finalUnseenEligible") is not False
            ):
                errors.append(
                    "evaluation.humanReviewRound1Returns archive or lifecycle is invalid"
                )

    label_review = evaluation.get("customerServiceV2LabelPolicyReview")
    label_root = _checksum_bound_package(
        root,
        label_review,
        errors,
        label="evaluation.customerServiceV2LabelPolicyReview",
    )
    if label_root is not None and isinstance(label_review, dict):
        try:
            package = _json(label_root / "evidence-manifest.json")
            lifecycle = _json(label_root / "lifecycle.json")
            agreement = _json(label_root / "agreement.json")
            adjudication = _jsonl(label_root / "adjudication.template.jsonl")
            review_a_manifest = _json(
                label_root / "reviews/reviewer-a.sealed.jsonl.manifest.json"
            )
            review_b_manifest = _json(
                label_root / "reviews/reviewer-b.sealed.jsonl.manifest.json"
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(
                f"evaluation.customerServiceV2LabelPolicyReview payload is invalid: {exc}"
            )
        else:
            disagreement_ids = {
                str(item.get("id") or "")
                for item in agreement.get("disagreements") or []
            }
            template_ids = {str(row.get("id") or "") for row in adjudication}
            final_labels_blank = all(
                isinstance(row.get("finalLabels"), dict)
                and set(row["finalLabels"])
                == {
                    "intent",
                    "riskLevel",
                    "shouldHandoff",
                    "handoffSeverity",
                    "slots",
                }
                and all(value is None for value in row["finalLabels"].values())
                and row.get("adjudicator") == ""
                and row.get("reason") == ""
                for row in adjudication
            )
            reviewer_ids = {
                str(review_a_manifest.get("reviewerId") or ""),
                str(review_b_manifest.get("reviewerId") or ""),
            }
            if (
                package.get("schemaVersion") != label_review.get("schemaVersion")
                or package.get("status") != label_review.get("status")
                or lifecycle.get("lifecycle") != "PENDING_ADJUDICATION"
                or agreement.get("status") != "PENDING_ADJUDICATION"
                or _sha256(label_root / "agreement.json")
                != label_review.get("agreementSha256")
                or agreement.get("sourceDatasetSha256")
                != label_review.get("sourceDatasetSha256")
                or agreement.get("sourceTemplateSha256")
                != label_review.get("sourceTemplateSha256")
                or agreement.get("taxonomyContractSha256")
                != label_review.get("taxonomyContractSha256")
                or any(
                    agreement.get(field) != label_review.get(field)
                    for field in (
                        "caseCount",
                        "exactAgreementCaseCount",
                        "disagreementCaseCount",
                        "caseAgreementRate",
                    )
                )
                or len(adjudication) != label_review.get("disagreementCaseCount")
                or template_ids != disagreement_ids
                or not final_labels_blank
                or len(reviewer_ids) != 2
                or "" in reviewer_ids
                or review_a_manifest.get("lifecycle") != "SEALED"
                or review_b_manifest.get("lifecycle") != "SEALED"
                or label_review.get("reviewerIdentityEvidenceStatus")
                != "ATTESTATIONS_PENDING"
                or label_review.get("releaseGateEligible") is not False
                or label_review.get("finalUnseenEligible") is not False
            ):
                errors.append(
                    "evaluation.customerServiceV2LabelPolicyReview lifecycle or binding is invalid"
                )

    reaudit = evaluation.get("customerServiceV2IndependentReaudit")
    reaudit_root = _checksum_bound_package(
        root,
        reaudit,
        errors,
        label="evaluation.customerServiceV2IndependentReaudit",
    )
    if reaudit_root is not None and isinstance(reaudit, dict):
        result_path = _resolve_hashed_file(
            root,
            reaudit,
            path_field="resultPath",
            sha_field="resultSha256",
            label="evaluation.customerServiceV2IndependentReaudit result",
            errors=errors,
        )
        if result_path is not None:
            try:
                result = _json(result_path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(
                    f"evaluation.customerServiceV2IndependentReaudit result is invalid: {exc}"
                )
            else:
                if (
                    result.get("schemaVersion")
                    != "aishop-customer-service-independent-reaudit-result/v1"
                    or result.get("status") != reaudit.get("status")
                    or result.get("caseCount") != reaudit.get("caseCount")
                    or result.get("returnedSheetSha256")
                    != reaudit.get("returnedSheetSha256")
                    or result.get("metrics") != reaudit.get("metrics")
                    or (result.get("gates") or {}).get("labelGatePassed")
                    != reaudit.get("labelGatePassed")
                    or (result.get("attestation") or {}).get("valid")
                    != reaudit.get("attestationValid")
                    or reaudit.get("remainingCaseCount")
                    != reaudit.get("fullTargetCaseCount") - reaudit.get("caseCount")
                    or reaudit.get("releaseGateEligible") is not False
                    or reaudit.get("finalUnseenEligible") is not False
                ):
                    errors.append(
                        "evaluation.customerServiceV2IndependentReaudit gate or binding is invalid"
                    )

    followup = evaluation.get("humanReviewFollowup")
    if not isinstance(followup, dict):
        errors.append("evaluation.humanReviewFollowup must be an object")
        return
    delivery_path = _resolve_hashed_file(
        root,
        followup,
        path_field="deliveryManifestPath",
        sha_field="deliveryManifestSha256",
        label="evaluation.humanReviewFollowup delivery manifest",
        errors=errors,
    )
    if delivery_path is None:
        return
    try:
        delivery = _json(delivery_path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"evaluation.humanReviewFollowup delivery manifest is invalid: {exc}")
        return
    packages = delivery.get("packages") or {}
    valid_packages = isinstance(packages, dict) and len(packages) == followup.get(
        "packageCount"
    )
    if isinstance(packages, dict):
        for descriptor in packages.values():
            if not isinstance(descriptor, dict):
                valid_packages = False
                continue
            try:
                package_path = _resolve(root, str(descriptor.get("path") or ""))
            except ValueError:
                valid_packages = False
                continue
            if (
                not package_path.is_file()
                or package_path.stat().st_size != descriptor.get("bytes")
                or _sha256(package_path) != descriptor.get("sha256")
            ):
                valid_packages = False
    intake_descriptor = evaluation.get("humanReviewRound1Returns") or {}
    if (
        delivery.get("schemaVersion")
        != "aishop-human-review-followup-delivery/v1"
        or delivery.get("status") != followup.get("status")
        or followup.get("status") != "OPEN_HUMAN_FOLLOWUP_REQUIRED"
        or followup.get("releaseGateEligible") is not False
        or followup.get("finalUnseenEligible") is not False
        or intake_descriptor.get("followupDeliveryManifestSha256")
        != followup.get("deliveryManifestSha256")
        or not valid_packages
    ):
        errors.append("evaluation.humanReviewFollowup package binding is invalid")


def _validate_human_approved_ai_assisted_finalization(
    root: Path,
    evaluation: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate adjudication intake, corrected labels, and disclosed AI assistance."""

    tier = "HUMAN_APPROVED_AI_ASSISTED"
    intake_descriptor = evaluation.get("humanReviewAdjudicationReturns")
    intake_root = _checksum_bound_package(
        root,
        intake_descriptor,
        errors,
        label="evaluation.humanReviewAdjudicationReturns",
    )
    approval_path: Path | None = None
    if isinstance(intake_descriptor, dict):
        approval_path = _resolve_hashed_file(
            root,
            intake_descriptor,
            path_field="approvalClarificationPath",
            sha_field="approvalClarificationSha256",
            label="evaluation.humanReviewAdjudicationReturns approval clarification",
            errors=errors,
        )
    if intake_root is not None and isinstance(intake_descriptor, dict):
        try:
            package = _json(intake_root / "evidence-manifest.json")
            audit = _json(intake_root / "intake-audit.json")
            archived_approval = _json(
                intake_root / "human-approval-clarification.json"
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(
                "evaluation.humanReviewAdjudicationReturns payload is invalid: "
                f"{exc}"
            )
        else:
            task_results = list((audit.get("tasks") or {}).values())
            if (
                package.get("schemaVersion") != intake_descriptor.get("schemaVersion")
                or package.get("status") != intake_descriptor.get("status")
                or package.get("evidenceTier") != tier
                or package.get("humanApprovedAiAssistedEvidenceAccepted") is not True
                or package.get("pureHumanUnaidedEvidenceAccepted") is not False
                or audit.get("schemaVersion")
                != "aishop-human-adjudication-return-intake-audit/v1"
                or audit.get("status") != intake_descriptor.get("status")
                or audit.get("evidenceTier") != tier
                or audit.get("humanApprovedAiAssistedEvidenceAccepted") is not True
                or audit.get("pureHumanUnaidedEvidenceAccepted") is not False
                or _sha256(intake_root / "intake-audit.json")
                != intake_descriptor.get("intakeAuditSha256")
                or str(intake_descriptor.get("intakeAuditPath") or "")
                != str(
                    (intake_root / "intake-audit.json")
                    .resolve()
                    .relative_to(root.resolve())
                )
                or len(task_results) != 2
                or any(
                    not isinstance(item, dict)
                    or item.get("structurallyValid") is not True
                    or item.get("humanApprovalClarificationHashBound") is not True
                    or item.get("humanApprovedAiAssistedEvidenceAccepted") is not True
                    for item in task_results
                )
                or approval_path is None
                or archived_approval != _json(approval_path)
                or archived_approval.get("evidenceTier") != tier
                or archived_approval.get("externalSignaturePresent") is not False
                or intake_descriptor.get("evidenceTier") != tier
                or intake_descriptor.get("humanApprovedAiAssistedEvidenceAccepted")
                is not True
                or intake_descriptor.get("pureHumanUnaidedEvidenceAccepted") is not False
            ):
                errors.append(
                    "evaluation.humanReviewAdjudicationReturns lifecycle or binding is invalid"
                )

    label_descriptor = evaluation.get("customerServiceV21LabelPolicyFinal")
    label_root = _checksum_bound_package(
        root,
        label_descriptor,
        errors,
        label="evaluation.customerServiceV21LabelPolicyFinal",
    )
    if label_root is not None and isinstance(label_descriptor, dict):
        dataset_path = _resolve_hashed_file(
            root,
            label_descriptor,
            path_field="successorDatasetPath",
            sha_field="successorDatasetSha256",
            label="evaluation.customerServiceV21LabelPolicyFinal successor dataset",
            errors=errors,
        )
        dataset_manifest_path = _resolve_hashed_file(
            root,
            label_descriptor,
            path_field="successorDatasetManifestPath",
            sha_field="successorDatasetManifestSha256",
            label="evaluation.customerServiceV21LabelPolicyFinal dataset manifest",
            errors=errors,
        )
        try:
            package = _json(label_root / "evidence-manifest.json")
            report = _json(label_root / "final-report.json")
            routing = _json(label_root / "routing-rescore.json")
            package_dataset_manifest = _json(
                label_root / "successor-dataset.manifest.json"
            )
            decisions = _jsonl(label_root / "final-decisions.jsonl")
            package_dataset = _jsonl(label_root / "successor-dataset.jsonl")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(
                "evaluation.customerServiceV21LabelPolicyFinal payload is invalid: "
                f"{exc}"
            )
        else:
            metric_values = {
                name: (routing.get("metrics") or {}).get(name, {}).get("value")
                for name in label_descriptor.get("routingMetrics") or {}
            }
            decision_ids = {str(row.get("id") or "") for row in decisions}
            changed = [row for row in decisions if row.get("changedFields")]
            if (
                package.get("schemaVersion") != label_descriptor.get("schemaVersion")
                or package.get("status") != label_descriptor.get("status")
                or package.get("evidenceTier") != tier
                or report.get("schemaVersion")
                != "aishop-customer-service-label-policy-final-report/v1"
                or report.get("status") != label_descriptor.get("status")
                or report.get("evidenceTier") != tier
                or _sha256(label_root / "final-report.json")
                != label_descriptor.get("finalReportSha256")
                or _sha256(label_root / "routing-rescore.json")
                != label_descriptor.get("routingRescoreSha256")
                or metric_values != label_descriptor.get("routingMetrics")
                or routing.get("evidenceTier") != tier
                or routing.get("releaseGateEligible") is not False
                or any(
                    report.get(source) != label_descriptor.get(target)
                    for source, target in (
                        ("caseCount", "caseCount"),
                        ("reviewedCaseCount", "reviewedCaseCount"),
                        ("exactAgreementCaseCount", "exactAgreementCaseCount"),
                        ("adjudicatedCaseCount", "adjudicatedCaseCount"),
                        ("caseAgreementRate", "caseAgreementRate"),
                        ("changedCaseCount", "changedCaseCount"),
                    )
                )
                or len(package_dataset) != label_descriptor.get("caseCount")
                or len(decisions) != label_descriptor.get("reviewedCaseCount")
                or len(decision_ids) != len(decisions)
                or "" in decision_ids
                or len(changed) != label_descriptor.get("changedCaseCount")
                or any(row.get("evidenceTier") != tier for row in decisions)
                or dataset_path is None
                or _sha256(label_root / "successor-dataset.jsonl")
                != label_descriptor.get("successorDatasetSha256")
                or _sha256(dataset_path)
                != label_descriptor.get("successorDatasetSha256")
                or dataset_manifest_path is None
                or _json(dataset_manifest_path) != package_dataset_manifest
                or package_dataset_manifest.get("evidenceTier") != tier
                or package_dataset_manifest.get("datasetSha256")
                != label_descriptor.get("successorDatasetSha256")
                or label_descriptor.get("evidenceTier") != tier
            ):
                errors.append(
                    "evaluation.customerServiceV21LabelPolicyFinal lifecycle or binding is invalid"
                )

    provenance_descriptor = evaluation.get("humanReviewHumanApprovalProvenance")
    provenance_root = _checksum_bound_package(
        root,
        provenance_descriptor,
        errors,
        label="evaluation.humanReviewHumanApprovalProvenance",
    )
    if provenance_root is not None and isinstance(provenance_descriptor, dict):
        try:
            package = _json(provenance_root / "evidence-manifest.json")
            bindings = _json(provenance_root / "bindings.json")
            normalization = _json(provenance_root / "normalization.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(
                "evaluation.humanReviewHumanApprovalProvenance payload is invalid: "
                f"{exc}"
            )
        else:
            answer_descriptor = evaluation.get("customerServiceAnswerReviewV43") or {}
            answer_final = answer_descriptor.get("finalEvidence") or {}
            if (
                package.get("schemaVersion") != provenance_descriptor.get("schemaVersion")
                or package.get("status") != provenance_descriptor.get("status")
                or package.get("evidenceTier") != tier
                or bindings.get("schemaVersion")
                != "aishop-human-approval-evidence-bindings/v1"
                or bindings.get("status") != provenance_descriptor.get("status")
                or bindings.get("evidenceTier") != tier
                or bindings.get("pureHumanUnaidedClaim") is not False
                or (bindings.get("answerQualityEvidence") or {}).get(
                    "sha256SumsSha256"
                )
                != answer_final.get("sha256SumsSha256")
                or (bindings.get("labelPolicyEvidence") or {}).get(
                    "sha256SumsSha256"
                )
                != (label_descriptor or {}).get("sha256SumsSha256")
                or (bindings.get("exactReturnArchive") or {}).get(
                    "sha256SumsSha256"
                )
                != (intake_descriptor or {}).get("sha256SumsSha256")
                or (bindings.get("approvalClarification") or {}).get("sha256")
                != (intake_descriptor or {}).get("approvalClarificationSha256")
                or normalization.get("evidenceTier") != tier
                or any(
                    not (normalization.get(key) or {}).get("finalLabelsUnchanged")
                    or not (normalization.get(key) or {}).get("reasonsUnchanged")
                    or (normalization.get(key) or {}).get("modifiedFields")
                    != ["adjudicator"]
                    for key in ("answerQuality", "labelPolicy")
                )
                or provenance_descriptor.get("evidenceTier") != tier
            ):
                errors.append(
                    "evaluation.humanReviewHumanApprovalProvenance lifecycle or binding is invalid"
                )


def _answer_review_message(source_case: dict[str, Any], message_projection: str) -> str:
    """Reproduce the declared source/runtime question projection for sealed sheets."""

    message = str(source_case.get("message") or "")
    if message_projection != "RUNTIME_FIXTURE_AWARE_V1":
        return message
    http = source_case.get("http") if isinstance(source_case.get("http"), dict) else {}
    rendered_fields = {
        str(field)
        for field in (http.get("renderedFixtureTemplateFields") or [])
        if isinstance(field, str)
    }
    if "orderId" not in rendered_fields:
        return message
    fixture = http.get("fixtureEvidence") if isinstance(http.get("fixtureEvidence"), dict) else {}
    source_order_id = str(fixture.get("sourceOrderId") or "").strip()
    runtime_order_id = str(fixture.get("orderId") or "").strip()
    if source_order_id and runtime_order_id and source_order_id in message:
        return message.replace(source_order_id, runtime_order_id)
    return message


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

    message_projection = str(package.get("messageProjection") or "SOURCE_DATASET_MESSAGE_V1")
    source_by_id: dict[str, dict[str, Any]] = {}
    for source_case in report.get("cases") or []:
        if not isinstance(source_case, dict):
            continue
        case_id = str(source_case.get("caseId") or "")
        http = source_case.get("http") if isinstance(source_case.get("http"), dict) else {}
        source_by_id[case_id] = {
            "message": _answer_review_message(source_case, message_projection),
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
    expected_reviewer_ids = _expected_answer_reviewers(descriptor, errors, label=label)
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
    if set(reviewer_ids) != set(expected_reviewer_ids):
        errors.append(f"{label} reviewer IDs are invalid")
    if lifecycle.get("reviewers") != expected_reviewer_ids:
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
    expected_reviewer_ids = _expected_answer_reviewers(descriptor, errors, label=label)
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
    if reviewer_ids != set(expected_reviewer_ids):
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
            in set(expected_reviewer_ids)
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
    if not isinstance(lock_descriptors, list) or {
        str(row.get("split") or "") for row in lock_descriptors if isinstance(row, dict)
    } != {"development", "regression"}:
        errors.append("evaluation.datasetLocks must contain development and regression")
    else:
        for descriptor in lock_descriptors:
            all_rows.extend(_validate_lock(root, descriptor, suite, errors))
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
    _validate_current_customer_service_v2(root, evaluation, errors)
    _validate_customer_service_v54_remediation(root, evaluation, errors)
    _validate_customer_service_v56_regression(root, evaluation, errors)
    _validate_evaluation_archive_and_handoff(root, evaluation, errors)
    _validate_round1_returns_and_followup(root, evaluation, errors)
    _validate_human_approved_ai_assisted_finalization(root, evaluation, errors)
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
    v20_answer_review = evaluation.get("customerServiceAnswerReviewV20")
    if v20_answer_review is None:
        errors.append("evaluation.customerServiceAnswerReviewV20 is required")
    else:
        _validate_customer_service_answer_review(
            root,
            v20_answer_review,
            errors,
            label="evaluation.customerServiceAnswerReviewV20",
        )
    v25_targeted_answer_review = evaluation.get(
        "customerServiceAnswerReviewV25Targeted"
    )
    if v25_targeted_answer_review is None:
        errors.append(
            "evaluation.customerServiceAnswerReviewV25Targeted is required"
        )
    else:
        _validate_targeted_customer_service_answer_review(
            root,
            v25_targeted_answer_review,
            errors,
        )
    v27_answer_review = evaluation.get("customerServiceAnswerReviewV27")
    if v27_answer_review is None:
        errors.append("evaluation.customerServiceAnswerReviewV27 is required")
    else:
        _validate_customer_service_answer_review(
            root,
            v27_answer_review,
            errors,
            label="evaluation.customerServiceAnswerReviewV27",
        )
    v43_answer_review = evaluation.get("customerServiceAnswerReviewV43")
    if v43_answer_review is None:
        errors.append("evaluation.customerServiceAnswerReviewV43 is required")
    else:
        _validate_customer_service_answer_review(
            root,
            v43_answer_review,
            errors,
            label="evaluation.customerServiceAnswerReviewV43",
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
