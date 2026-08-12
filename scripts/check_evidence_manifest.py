#!/usr/bin/env python3
"""Validate AI_Shop's interview evidence manifest and optional local results."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "evidence-manifest.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_LEVELS = {
    "E0_SOURCE",
    "E1_DETERMINISTIC",
    "E2_LOCAL_INTEGRATION",
    "E3_CONFIGURED_LIVE",
    "E4_REAL_USER",
}
ALLOWED_STATES = {"VERIFIED", "LOCAL_RESULT", "CI_ARTIFACT", "NOT_COLLECTED"}
ALLOWED_LOCATIONS = {"tracked", "local-ignored", "ci-artifact", "not-collected"}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(relative: str) -> Path:
    candidate = (REPO_ROOT / relative).resolve()
    try:
        candidate.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes repository: {relative}") from exc
    return candidate


def _check_dataset_lock(
    lock_spec: str | dict[str, Any], errors: list[str]
) -> None:
    if isinstance(lock_spec, str):
        lock_relative = lock_spec
        dataset_override = None
    elif isinstance(lock_spec, dict):
        lock_relative = str(lock_spec.get("path") or "")
        dataset_override = lock_spec.get("datasetPath")
    else:
        errors.append("datasetLocks must contain paths or path descriptors")
        return
    if not lock_relative:
        errors.append("dataset lock path is required")
        return
    lock_path = _resolve(lock_relative)
    if not lock_path.is_file():
        errors.append(f"dataset lock missing: {lock_relative}")
        return
    try:
        lock = _json(lock_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid dataset lock {lock_relative}: {exc}")
        return
    dataset = dataset_override or lock.get("dataset") or lock.get("datasetPath")
    expected = lock.get("datasetSha256")
    if not isinstance(dataset, str) or not isinstance(expected, str):
        errors.append(f"dataset lock lacks dataset/datasetSha256: {lock_relative}")
        return
    dataset_path = (
        _resolve(dataset)
        if dataset_override
        else (lock_path.parent / dataset).resolve()
    )
    if not dataset_path.is_file() and not dataset_path.suffix:
        jsonl_candidate = dataset_path.with_suffix(".jsonl")
        if jsonl_candidate.is_file():
            dataset_path = jsonl_candidate
    if not dataset_path.is_file():
        errors.append(f"locked dataset missing: {dataset_path.relative_to(REPO_ROOT)}")
        return
    actual = _sha256(dataset_path)
    if actual != expected:
        errors.append(
            f"dataset hash mismatch for {dataset_path.relative_to(REPO_ROOT)}: "
            f"expected {expected}, got {actual}"
        )


def _check_result(evidence: dict[str, Any], path: Path, errors: list[str]) -> None:
    try:
        result = _json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid result {path.relative_to(REPO_ROOT)}: {exc}")
        return
    expected_sha = evidence.get("resultSha256")
    if expected_sha and _sha256(path) != expected_sha:
        errors.append(f"result hash mismatch: {path.relative_to(REPO_ROOT)}")
    if evidence.get("kind") == "evaluation":
        metadata = result.get("metadata") or {}
        summary = result.get("summary") or {}
        if metadata.get("schemaVersion") != "aishop-eval/v1":
            errors.append(f"evaluation result has wrong schema: {path.relative_to(REPO_ROOT)}")
        if metadata.get("suite") != evidence.get("suite"):
            errors.append(f"evaluation suite mismatch: {path.relative_to(REPO_ROOT)}")
        if summary.get("caseCount") != evidence.get("caseCount"):
            errors.append(f"evaluation caseCount mismatch: {path.relative_to(REPO_ROOT)}")
        if summary.get("executedCount") != summary.get("caseCount"):
            errors.append(f"evaluation contains unexecuted cases: {path.relative_to(REPO_ROOT)}")
        if summary.get("criticalSafetyViolationCount") not in (None, 0):
            errors.append(f"evaluation contains critical safety violations: {path.relative_to(REPO_ROOT)}")
        if not isinstance(result.get("cases"), list) or not result["cases"]:
            errors.append(f"evaluation has no case results: {path.relative_to(REPO_ROOT)}")
    elif evidence.get("kind") == "ablation":
        if result.get("schemaVersion") != "aishop-eval/v1":
            errors.append(f"ablation result has wrong schema: {path.relative_to(REPO_ROOT)}")
        main = result.get("mainExperiment") or {}
        secondary = result.get("secondaryExperiment") or {}
        if not main.get("passed") or not secondary.get("passed"):
            errors.append(f"ablation gate failed: {path.relative_to(REPO_ROOT)}")
        if not result.get("processIsolated") or result.get("cacheSharedAcrossVariants"):
            errors.append(f"ablation isolation contract failed: {path.relative_to(REPO_ROOT)}")


def _check_markdown_links(relative: str, errors: list[str]) -> None:
    path = _resolve(relative)
    if not path.is_file():
        errors.append(f"claim document missing: {relative}")
        return
    text = path.read_text(encoding="utf-8")
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = unquote(target).split("#", 1)[0]
        if not target:
            continue
        candidate = (path.parent / target).resolve()
        try:
            candidate.relative_to(REPO_ROOT.resolve())
        except ValueError:
            errors.append(f"link escapes repository in {relative}: {raw_target}")
            continue
        if not candidate.exists():
            errors.append(f"broken link in {relative}: {raw_target}")


def validate_manifest(payload: dict[str, Any], *, check_local_results: bool) -> list[str]:
    errors: list[str] = []
    if payload.get("schemaVersion") != 1:
        errors.append("schemaVersion must be 1")
    baseline = payload.get("implementationBaseline") or {}
    if not HEX40.fullmatch(str(baseline.get("gitHead") or "")):
        errors.append("implementationBaseline.gitHead must be a full commit SHA")
    if not HEX64.fullmatch(str(baseline.get("workspaceDiffSha256") or "")):
        errors.append("implementationBaseline.workspaceDiffSha256 must be SHA-256")

    evidence_rows = payload.get("evidence")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        return [*errors, "evidence must be a non-empty array"]
    seen: set[str] = set()
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, dict):
            errors.append(f"evidence[{index}] must be an object")
            continue
        evidence_id = str(row.get("id") or "")
        if not evidence_id:
            errors.append(f"evidence[{index}].id is required")
        elif evidence_id in seen:
            errors.append(f"duplicate evidence id: {evidence_id}")
        seen.add(evidence_id)
        if row.get("level") not in ALLOWED_LEVELS:
            errors.append(f"{evidence_id}.level is invalid")
        if row.get("state") not in ALLOWED_STATES:
            errors.append(f"{evidence_id}.state is invalid")
        location = row.get("resultLocation")
        if location not in ALLOWED_LOCATIONS:
            errors.append(f"{evidence_id}.resultLocation is invalid")
        if not str(row.get("command") or "").strip():
            errors.append(f"{evidence_id}.command is required")
        if not str(row.get("claim") or "").strip():
            errors.append(f"{evidence_id}.claim is required")
        if not str(row.get("boundary") or "").strip():
            errors.append(f"{evidence_id}.boundary is required")
        result_path = row.get("resultPath")
        if location == "not-collected":
            if row.get("state") != "NOT_COLLECTED" or result_path is not None:
                errors.append(f"{evidence_id} not-collected evidence must not have a result path")
        elif not isinstance(result_path, str) or not result_path:
            errors.append(f"{evidence_id}.resultPath is required")
        elif location == "tracked" and not _resolve(result_path).exists():
            errors.append(f"tracked evidence path missing: {result_path}")
        elif location == "local-ignored" and check_local_results:
            path = _resolve(result_path)
            if not path.is_file():
                errors.append(f"local result missing: {result_path}")
            else:
                _check_result(row, path, errors)
        result_sha = row.get("resultSha256")
        if result_sha is not None and not HEX64.fullmatch(str(result_sha)):
            errors.append(f"{evidence_id}.resultSha256 must be SHA-256")
        for lock in row.get("datasetLocks") or []:
            _check_dataset_lock(lock, errors)

    boundaries = payload.get("honestBoundaries")
    if not isinstance(boundaries, list) or not boundaries:
        errors.append("honestBoundaries must be a non-empty array")
    documents = payload.get("claimDocuments")
    if not isinstance(documents, list) or not documents:
        errors.append("claimDocuments must be a non-empty array")
    else:
        for document in documents:
            if not isinstance(document, str):
                errors.append("claimDocuments must contain paths")
            else:
                _check_markdown_links(document, errors)
    for rule in payload.get("forbiddenCurrentClaims") or []:
        if not isinstance(rule, dict) or not rule.get("pattern"):
            errors.append("forbiddenCurrentClaims entries require pattern and paths")
            continue
        try:
            pattern = re.compile(str(rule["pattern"]))
        except re.error as exc:
            errors.append(f"invalid forbidden claim pattern {rule['pattern']}: {exc}")
            continue
        for relative in rule.get("paths") or []:
            path = _resolve(str(relative))
            if not path.is_file():
                errors.append(f"forbidden-claim document missing: {relative}")
            elif pattern.search(path.read_text(encoding="utf-8")):
                errors.append(f"forbidden current claim in {relative}: {rule['pattern']}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--check-local-results",
        action="store_true",
        help="also require and validate ignored local result files",
    )
    parser.add_argument(
        "--check-current-head",
        action="store_true",
        help="require the manifest implementation HEAD to match the current HEAD",
    )
    args = parser.parse_args()
    payload = _json(args.path)
    errors = validate_manifest(payload, check_local_results=args.check_local_results)
    if args.check_current_head:
        current = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if current != (payload.get("implementationBaseline") or {}).get("gitHead"):
            errors.append(f"manifest HEAD {payload['implementationBaseline']['gitHead']} != {current}")
    if errors:
        raise SystemExit("evidence manifest is invalid:\n- " + "\n- ".join(errors))
    print(f"evidence manifest valid: {len(payload['evidence'])} entries")


if __name__ == "__main__":
    main()
