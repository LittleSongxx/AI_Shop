#!/usr/bin/env python3
"""Validate, seal, and compare external Search/RAG/Agent review sheets.

This workflow is intentionally separate from the customer-service input-gold
reviewer. It checks source binding, row identity, domain-specific label shape,
and prediction-field leakage only. It never reads candidate expected labels
into a result and never computes model-quality metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REVIEW_SCHEMA = "aishop-external-final-review/v1"
AGREEMENT_SCHEMA = "aishop-external-final-review-agreement/v1"
CASE_SCHEMA = "aishop-evaluation-case/v3"
DOMAINS = {"search", "rag", "agent"}
JUDGMENT_MODES = {"EXHAUSTIVE_CATALOG", "UNDECIDABLE"}
SAFETY_EXPECTATIONS = {
    "SAFE",
    "HANDOFF",
    "REJECT",
    "PROPOSE_ONLY",
    "WRITE_AFTER_CONFIRMATION",
    "UNDECIDABLE",
}
FORBIDDEN_KEYS = {"expected", "predicted", "prediction", "modelOutput", "modelPrediction"}


class ExternalReviewError(ValueError):
    """Raised for a malformed or incorrectly bound external review artifact."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExternalReviewError(f"cannot read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExternalReviewError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ExternalReviewError(f"{path}:{line_number}: row must be an object")
        rows.append(value)
    return rows


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExternalReviewError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExternalReviewError(f"{path}: JSON root must be an object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ExternalReviewError(f"refusing to overwrite {path}")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ExternalReviewError(f"refusing to overwrite {path}")
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _strings(value: Any, *, label: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ExternalReviewError(f"{label} must be a list")
    if any(not isinstance(item, str) or (not item.strip() and not allow_empty) for item in value):
        raise ExternalReviewError(f"{label} must contain strings")
    return value


def _blank_labels(labels: Mapping[str, Any]) -> bool:
    return all(value is None or value == "" for value in labels.values())


def _validate_labels(domain: str, labels: Any, *, complete: bool, label: str) -> None:
    if not isinstance(labels, Mapping):
        raise ExternalReviewError(f"{label}: labels must be an object")
    expected_keys = {
        "search": {"relevantProductIds", "noResult", "judgmentMode", "notes"},
        "rag": {"answerable", "relevantFactIds", "requiredClaims", "noAnswerScope", "notes"},
        "agent": {"terminalStatuses", "requiredTools", "safetyExpectation", "notes"},
    }[domain]
    if set(labels) != expected_keys:
        raise ExternalReviewError(
            f"{label}: label fields differ from {domain}: {sorted(set(labels) ^ expected_keys)}"
        )
    if _blank_labels(labels):
        if complete:
            raise ExternalReviewError(f"{label}: labels are incomplete")
        return
    notes = labels.get("notes")
    if not isinstance(notes, str) or (complete and not notes.strip()):
        raise ExternalReviewError(f"{label}.notes must be non-empty text")
    if domain == "search":
        _strings(labels.get("relevantProductIds"), label=f"{label}.relevantProductIds")
        if not isinstance(labels.get("noResult"), bool):
            raise ExternalReviewError(f"{label}.noResult must be boolean")
        if labels.get("judgmentMode") not in JUDGMENT_MODES:
            raise ExternalReviewError(f"{label}.judgmentMode is invalid")
        return
    if domain == "rag":
        answerable = labels.get("answerable")
        if not isinstance(answerable, bool):
            raise ExternalReviewError(f"{label}.answerable must be boolean")
        facts = _strings(labels.get("relevantFactIds"), label=f"{label}.relevantFactIds")
        claims = labels.get("requiredClaims")
        if not isinstance(claims, list):
            raise ExternalReviewError(f"{label}.requiredClaims must be a list")
        for index, claim in enumerate(claims, 1):
            if not isinstance(claim, Mapping):
                raise ExternalReviewError(f"{label}.requiredClaims[{index}] must be an object")
            if not isinstance(claim.get("claimId"), str) or not claim["claimId"].strip():
                raise ExternalReviewError(f"{label}.requiredClaims[{index}].claimId is required")
            _strings(claim.get("factIds"), label=f"{label}.requiredClaims[{index}].factIds")
            if not isinstance(claim.get("required"), bool):
                raise ExternalReviewError(f"{label}.requiredClaims[{index}].required must be boolean")
        scope = labels.get("noAnswerScope")
        if scope is not None and (not isinstance(scope, str) or not scope.strip()):
            raise ExternalReviewError(f"{label}.noAnswerScope must be null or non-empty text")
        if answerable and not facts:
            raise ExternalReviewError(f"{label}.relevantFactIds is required when answerable=true")
        if answerable and not claims:
            raise ExternalReviewError(f"{label}.requiredClaims is required when answerable=true")
        if not answerable and facts:
            raise ExternalReviewError(f"{label}.relevantFactIds must be empty when answerable=false")
        if not answerable and not isinstance(scope, str):
            raise ExternalReviewError(f"{label}.noAnswerScope is required when answerable=false")
        return
    _strings(labels.get("terminalStatuses"), label=f"{label}.terminalStatuses")
    _strings(labels.get("requiredTools"), label=f"{label}.requiredTools", allow_empty=True)
    if labels.get("safetyExpectation") not in SAFETY_EXPECTATIONS:
        raise ExternalReviewError(f"{label}.safetyExpectation is invalid")


def _load_candidate(path: Path) -> tuple[list[dict[str, Any]], str, dict[str, dict[str, Any]]]:
    rows = _jsonl(path)
    if not rows:
        raise ExternalReviewError(f"candidate is empty: {path}")
    source_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        label = f"{path}:{index}"
        if row.get("schemaVersion") != CASE_SCHEMA:
            raise ExternalReviewError(f"{label}: candidate schemaVersion is invalid")
        if row.get("split") != "final" or row.get("domain") not in DOMAINS:
            raise ExternalReviewError(f"{label}: candidate split/domain is invalid")
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in source_by_id:
            raise ExternalReviewError(f"{label}: candidate ID is empty or duplicated")
        if not isinstance(row.get("input"), Mapping):
            raise ExternalReviewError(f"{label}: candidate input must be an object")
        source_by_id[case_id] = row
    return rows, _sha256(path), source_by_id


def _manifest_path(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def _load_review(
    candidate_path: Path,
    review_path: Path,
    *,
    complete: bool,
    require_sealed: bool | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidate_rows, candidate_sha, source_by_id = _load_candidate(candidate_path)
    manifest_path = _manifest_path(review_path)
    manifest = _json(manifest_path)
    lifecycle = manifest.get("lifecycle")
    if lifecycle not in {"OPEN", "SEALED"}:
        raise ExternalReviewError(f"{manifest_path}: lifecycle must be OPEN or SEALED")
    if require_sealed is True and lifecycle != "SEALED":
        raise ExternalReviewError(f"{review_path}: a SEALED sheet is required")
    if require_sealed is False and lifecycle != "OPEN":
        raise ExternalReviewError(f"{review_path}: an OPEN sheet is required")
    if manifest.get("schemaVersion") != REVIEW_SCHEMA:
        raise ExternalReviewError(f"{manifest_path}: schemaVersion is invalid")
    expected_artifact = "SEALED_REVIEW_SHEET" if lifecycle == "SEALED" else "EXTERNAL_FINAL_LABEL_TEMPLATE"
    if manifest.get("artifact") != expected_artifact:
        raise ExternalReviewError(f"{manifest_path}: artifact/lifecycle mismatch")
    if manifest.get("datasetSha256") != candidate_sha:
        raise ExternalReviewError(f"{manifest_path}: candidate hash differs")
    reviewer_id = manifest.get("reviewerId")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ExternalReviewError(f"{manifest_path}: reviewerId is required")
    rows = _jsonl(review_path)
    if len(rows) != len(candidate_rows):
        raise ExternalReviewError(f"{review_path}: row count differs from candidate")
    reviewed: dict[str, dict[str, Any]] = {}
    allowed_row_keys = {"domain", "id", "input", "labels", "reviewerId", "schemaVersion"}
    for index, row in enumerate(rows, 1):
        label = f"{review_path}:{index}"
        leaked = _forbidden_keys(row)
        if leaked:
            raise ExternalReviewError(f"{label}: forbidden fields found: {sorted(leaked)}")
        if set(row) != allowed_row_keys:
            raise ExternalReviewError(f"{label}: row fields are invalid")
        case_id = row.get("id")
        if not isinstance(case_id, str) or case_id not in source_by_id or case_id in reviewed:
            raise ExternalReviewError(f"{label}: ID is invalid or duplicated")
        source = source_by_id[case_id]
        if row.get("domain") != source.get("domain") or row.get("input") != source.get("input"):
            raise ExternalReviewError(f"{label}: domain/input differs from candidate projection")
        if row.get("schemaVersion") != REVIEW_SCHEMA or row.get("reviewerId") != reviewer_id:
            raise ExternalReviewError(f"{label}: reviewer binding is invalid")
        _validate_labels(str(source["domain"]), row.get("labels"), complete=complete, label=f"{label}.labels")
        reviewed[case_id] = row
    if set(reviewed) != set(source_by_id):
        raise ExternalReviewError(f"{review_path}: case ID set differs from candidate")
    if manifest.get("caseCount") != len(rows):
        raise ExternalReviewError(f"{manifest_path}: caseCount is stale")
    if lifecycle == "SEALED" and manifest.get("sheetSha256") != _sha256(review_path):
        raise ExternalReviewError(f"{manifest_path}: sealed sheet hash differs")
    return manifest, rows, reviewed


def validate(args: argparse.Namespace) -> dict[str, Any]:
    manifest, rows, _ = _load_review(args.dataset, args.review, complete=args.complete)
    return {
        "valid": True,
        "lifecycle": manifest["lifecycle"],
        "caseCount": len(rows),
        "reviewerId": manifest["reviewerId"],
        "candidateSha256": manifest["datasetSha256"],
        "reviewSha256": _sha256(args.review),
        "complete": args.complete,
    }


def seal(args: argparse.Namespace) -> dict[str, Any]:
    manifest, rows, _ = _load_review(args.dataset, args.review, complete=True, require_sealed=False)
    output_manifest_path = _manifest_path(args.output)
    if args.output.exists() or output_manifest_path.exists():
        raise ExternalReviewError(f"refusing to overwrite sealed artifact: {args.output}")
    _write_jsonl(args.output, rows)
    sealed_manifest = {
        **manifest,
        "artifact": "SEALED_REVIEW_SHEET",
        "lifecycle": "SEALED",
        "sheetPath": str(args.output.resolve()),
        "sheetSha256": _sha256(args.output),
        "sourceOpenSheetPath": str(args.review.resolve()),
        "sourceOpenSheetSha256": _sha256(args.review),
        "openSheetSha256AtExport": _sha256(args.review),
    }
    _write_json(output_manifest_path, sealed_manifest)
    os.chmod(args.output, 0o444)
    os.chmod(output_manifest_path, 0o444)
    return {"sealed": True, "output": str(args.output.resolve()), "manifest": str(output_manifest_path.resolve()), "sheetSha256": sealed_manifest["sheetSha256"], "caseCount": len(rows)}


def compare(args: argparse.Namespace) -> dict[str, Any]:
    manifest_a, _, reviewed_a = _load_review(args.dataset, args.review_a, complete=True, require_sealed=True)
    manifest_b, _, reviewed_b = _load_review(args.dataset, args.review_b, complete=True, require_sealed=True)
    if manifest_a["reviewerId"] == manifest_b["reviewerId"]:
        raise ExternalReviewError("reviewer IDs must be different")
    disagreements: list[dict[str, Any]] = []
    for case_id in reviewed_a:
        left, right = reviewed_a[case_id], reviewed_b[case_id]
        fields = sorted(key for key in left["labels"] if _canonical(left["labels"].get(key)) != _canonical(right["labels"].get(key)))
        if fields:
            disagreements.append({"id": case_id, "domain": left["domain"], "input": left["input"], "fields": fields, "reviewerA": left["labels"], "reviewerB": right["labels"]})
    case_count = len(reviewed_a)
    agreement = {
        "schemaVersion": AGREEMENT_SCHEMA,
        "status": "PENDING_ADJUDICATION" if disagreements else "AGREED_NO_ADJUDICATION",
        "releaseGateEligible": False,
        "sourceDatasetSha256": manifest_a["datasetSha256"],
        "reviewA": {"reviewerId": manifest_a["reviewerId"], "sha256": manifest_a["sheetSha256"]},
        "reviewB": {"reviewerId": manifest_b["reviewerId"], "sha256": manifest_b["sheetSha256"]},
        "caseCount": case_count,
        "exactAgreementCaseCount": case_count - len(disagreements),
        "disagreementCaseCount": len(disagreements),
        "caseAgreementRate": (case_count - len(disagreements)) / case_count if case_count else None,
        "disagreements": disagreements,
        "note": "Inter-annotator evidence only; this is not a model-quality metric.",
    }
    _write_json(args.output, agreement)
    return {"status": agreement["status"], "caseCount": case_count, "exactAgreementCaseCount": agreement["exactAgreementCaseCount"], "disagreementCaseCount": agreement["disagreementCaseCount"], "output": str(args.output.resolve())}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_command = commands.add_parser("validate")
    validate_command.add_argument("--dataset", type=Path, required=True)
    validate_command.add_argument("--review", type=Path, required=True)
    validate_command.add_argument("--complete", action="store_true")
    seal_command = commands.add_parser("seal")
    seal_command.add_argument("--dataset", type=Path, required=True)
    seal_command.add_argument("--review", type=Path, required=True)
    seal_command.add_argument("--output", type=Path, required=True)
    compare_command = commands.add_parser("compare")
    compare_command.add_argument("--dataset", type=Path, required=True)
    compare_command.add_argument("--review-a", type=Path, required=True)
    compare_command.add_argument("--review-b", type=Path, required=True)
    compare_command.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = validate(args)
        elif args.command == "seal":
            result = seal(args)
        elif args.command == "compare":
            result = compare(args)
        else:  # pragma: no cover
            raise ExternalReviewError(f"unknown command: {args.command}")
    except ExternalReviewError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
