#!/usr/bin/env python3
"""Archive and finalize the v54 human-approved, AI-assisted answer review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

EVIDENCE_TIER = "HUMAN_APPROVED_AI_ASSISTED"
ANNOTATION_STATUS = "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED"
ZIP_MEMBERS = {
    "adjudication.open.jsonl",
    "adjudicator-attestation.template.json",
}
EDITABLE_ADJUDICATION_FIELDS = {"finalLabels", "adjudicator", "reason"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _inventory(
    root: Path, *, excluded: set[str] | None = None
) -> dict[str, dict[str, Any]]:
    excluded = excluded or set()
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def _sums(root: Path) -> str:
    return "".join(
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    )


def _verify_sums(root: Path) -> None:
    expected: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or not relative or relative in expected:
            raise ValueError(f"invalid checksum line: {line!r}")
        expected[relative] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise ValueError("checksum inventory differs")
    for relative, digest in expected.items():
        if _sha256(root / relative) != digest:
            raise ValueError(f"checksum mismatch: {relative}")


def _verify_read_only(root: Path) -> None:
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise ValueError(f"evidence files remain writable: {writable}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _validate_zip(return_zip: Path, extracted: Mapping[str, Path]) -> None:
    with zipfile.ZipFile(return_zip) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        names = {item.filename for item in members}
        if names != ZIP_MEMBERS or len(members) != len(ZIP_MEMBERS):
            raise ValueError(f"unexpected return ZIP inventory: {sorted(names)}")
        for item in members:
            member = PurePosixPath(item.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"unsafe ZIP member: {item.filename}")
            if archive.read(item) != extracted[item.filename].read_bytes():
                raise ValueError(f"extracted return differs from ZIP member: {item.filename}")


def _validate_return(
    *,
    raw_adjudication: Path,
    raw_attestation: Path,
    template_path: Path,
    agreement: Mapping[str, Any],
) -> dict[str, Any]:
    template_rows = _load_jsonl(template_path)
    returned_rows = _load_jsonl(raw_adjudication)
    template_by_id = {str(row.get("caseId") or ""): row for row in template_rows}
    returned_by_id = {str(row.get("caseId") or ""): row for row in returned_rows}
    disagreement_ids = {
        str(item.get("caseId") or "") for item in agreement.get("disagreements") or []
    }
    if (
        len(template_by_id) != len(template_rows)
        or len(returned_by_id) != len(returned_rows)
        or set(template_by_id) != disagreement_ids
        or set(returned_by_id) != disagreement_ids
    ):
        raise ValueError("adjudication case coverage differs from the frozen disagreements")

    forbidden_changes: list[dict[str, str]] = []
    for case_id in sorted(disagreement_ids):
        template = template_by_id[case_id]
        returned = returned_by_id[case_id]
        if set(template) != set(returned):
            raise ValueError(f"adjudication fields differ for {case_id}")
        for field in sorted(set(template) - EDITABLE_ADJUDICATION_FIELDS):
            if _canonical(template.get(field)) != _canonical(returned.get(field)):
                forbidden_changes.append({"caseId": case_id, "field": field})
    if forbidden_changes:
        raise ValueError(f"adjudication changed frozen fields: {forbidden_changes}")

    label_fields = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    citation_values = {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
    adjudicators: set[str] = set()
    for row in returned_rows:
        case_id = str(row["caseId"])
        labels = row.get("finalLabels")
        if not isinstance(labels, Mapping) or set(labels) != label_fields:
            raise ValueError(f"final labels are incomplete for {case_id}")
        if (
            not isinstance(labels["answerCorrect"], bool)
            or labels["citationSupport"] not in citation_values
            or not isinstance(labels["handoffAppropriate"], bool)
            or not isinstance(labels["unsafeAnswer"], bool)
        ):
            raise ValueError(f"final labels are invalid for {case_id}")
        adjudicator = str(row.get("adjudicator") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not adjudicator or not reason:
            raise ValueError(f"adjudicator and reason are required for {case_id}")
        adjudicators.add(adjudicator)
    if len(adjudicators) != 1:
        raise ValueError("one stable adjudicator ID is required")

    attestation = _load_json(raw_attestation)
    adjudicator_id = next(iter(adjudicators))
    reviewer_ids = {
        str((agreement.get("reviewA") or {}).get("reviewerId") or ""),
        str((agreement.get("reviewB") or {}).get("reviewerId") or ""),
    }
    if (
        attestation.get("schemaVersion") != "aishop-human-adjudicator-attestation/v2"
        or attestation.get("taskId")
        != "customer-service-http-v54-answer-quality-adjudication-20260827"
        or set(attestation.get("sourceReviewerIds") or []) != reviewer_ids
        or attestation.get("adjudicatorId") != adjudicator_id
        or adjudicator_id in reviewer_ids
        or attestation.get("adjudicationTemplateSha256AtExport") != _sha256(template_path)
        or attestation.get("completedAdjudicationSha256") != _sha256(raw_adjudication)
    ):
        raise ValueError("returned adjudicator attestation bindings are invalid")
    return {
        "caseCount": len(returned_rows),
        "caseIds": sorted(returned_by_id),
        "adjudicatorId": adjudicator_id,
        "sourceReviewerIds": sorted(reviewer_ids),
        "frozenFieldChanges": forbidden_changes,
        "finalLabelsComplete": True,
        "reasonsComplete": True,
        "rawAttestationStatus": attestation.get("status"),
        "rawAttestationPlaceholdersNotUpdated": [
            "status",
            "adjudicatorIdentity",
            "adjudicatorIsHuman",
            "independentOfSourceReviewers",
            "humanRetainedFinalDecisionAuthority",
            "attestedAt",
        ],
    }


def _publish_return_archive(
    *,
    repo_root: Path,
    return_zip: Path,
    raw_adjudication: Path,
    raw_attestation: Path,
    template_path: Path,
    pending_dir: Path,
    output_dir: Path,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    if output_dir.exists():
        _verify_sums(output_dir)
        _verify_read_only(output_dir)
        audit = _load_json(output_dir / "validation-audit.json")
        clarification_path = output_dir / "human-approval-clarification.json"
        if (
            audit.get("status")
            != "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED"
            or audit.get("evidenceTier") != EVIDENCE_TIER
            or (audit.get("returnZip") or {}).get("sha256") != _sha256(return_zip)
            or (audit.get("returnedAdjudication") or {}).get("sha256")
            != _sha256(raw_adjudication)
            or (audit.get("returnedAttestation") or {}).get("sha256")
            != _sha256(raw_attestation)
            or not clarification_path.is_file()
        ):
            raise ValueError("existing return archive does not match this return")
        return {
            "path": _relative(output_dir, repo_root),
            "sha256SumsSha256": _sha256(output_dir / "SHA256SUMS"),
            "clarificationSha256": _sha256(clarification_path),
        }
    created_at = _now()
    clarification = {
        "schemaVersion": "aishop-human-approval-ai-assistance-clarification/v3",
        "status": "PROJECT_OWNER_CONFIRMED_HUMAN_ADJUDICATION_AI_ASSISTED_EDITING",
        "task": "CUSTOMER_SERVICE_V54_ANSWER_QUALITY_ADJUDICATION",
        "confirmedAt": created_at,
        "recordedFrom": "current project-owner instruction in the shared Codex workspace",
        "source": "PROJECT_OWNER_CONFIRMATION_IN_CURRENT_WORKFLOW",
        "adjudicatorId": validation["adjudicatorId"],
        "sourceReviewerIds": validation["sourceReviewerIds"],
        "humanDecisionAuthority": True,
        "humanAnnotationClaimAllowed": True,
        "aiAssistanceUsed": True,
        "aiAssistanceScope": "TEXT_EDITING_AND_RECORDING_ONLY",
        "pureHumanUnaidedClaim": False,
        "evidenceTier": EVIDENCE_TIER,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "rawAttestationTemplateWasNotFullyUpdated": True,
        "rawAttestationInterpretation": (
            "The returned attestation remains an unconfirmed draft template. The exact raw "
            "bytes are preserved; the project-owner confirmation supplies human-decision "
            "provenance for the derived HUMAN_APPROVED_AI_ASSISTED evidence only."
        ),
        "externalSignaturePresent": False,
    }
    audit = {
        "schemaVersion": "aishop-v54-answer-adjudication-return-intake-audit/v1",
        "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED",
        "createdAt": created_at,
        "evidenceTier": EVIDENCE_TIER,
        "sourcePendingEvidence": {
            "path": _relative(pending_dir, repo_root),
            "sha256SumsSha256": _sha256(pending_dir / "SHA256SUMS"),
        },
        "returnZip": {
            "path": _relative(return_zip, repo_root),
            "sha256": _sha256(return_zip),
            "exactBytesPreserved": True,
        },
        "exportedTemplate": {
            "path": _relative(template_path, repo_root),
            "sha256": _sha256(template_path),
        },
        "returnedAdjudication": {
            "path": _relative(raw_adjudication, repo_root),
            "sha256": _sha256(raw_adjudication),
        },
        "returnedAttestation": {
            "path": _relative(raw_attestation, repo_root),
            "sha256": _sha256(raw_attestation),
            "rawDraftFieldsPreserved": True,
        },
        "validation": dict(validation),
        "humanDecisionAuthority": True,
        "aiAssistedTextOrRecording": True,
        "claimPureUnaidedHumanAnnotation": False,
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        raw_dir = staging / "raw-return"
        source_dir = staging / "source"
        raw_dir.mkdir()
        source_dir.mkdir()
        for name, source in (
            ("original-return.zip", return_zip),
            ("adjudication.open.jsonl", raw_adjudication),
            ("adjudicator-attestation.template.json", raw_attestation),
        ):
            shutil.copy2(source, raw_dir / name)
            if _sha256(source) != _sha256(raw_dir / name):
                raise ValueError(f"archive copy hash mismatch: {name}")
        shutil.copy2(template_path, source_dir / "adjudication.template.exported.jsonl")
        _write_json(staging / "human-approval-clarification.json", clarification)
        _write_json(staging / "validation-audit.json", audit)
        _write_text(
            staging / "README.md",
            "# v54 answer-quality adjudication return\n\n"
            "This package preserves the exact returned ZIP, extracted decision file, and "
            "unchanged draft attestation. The project owner confirms that a human made and "
            "approved the decisions while AI assisted text editing/recording. The supported "
            "claim is `HUMAN_APPROVED_AI_ASSISTED`, not unaided-human authorship or external "
            "unseen evaluation.\n",
        )
        manifest = {
            "schemaVersion": "aishop-v54-answer-adjudication-return-evidence/v1",
            "status": audit["status"],
            "evidenceTier": EVIDENCE_TIER,
            "createdAt": created_at,
            "readOnly": True,
            "files": _inventory(
                staging, excluded={"evidence-manifest.json", "SHA256SUMS"}
            ),
        }
        _write_json(staging / "evidence-manifest.json", manifest)
        _write_text(staging / "SHA256SUMS", _sums(staging))
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        _verify_sums(staging)
        _verify_read_only(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "path": _relative(output_dir, repo_root),
        "sha256SumsSha256": _sha256(output_dir / "SHA256SUMS"),
        "clarificationSha256": _sha256(output_dir / "human-approval-clarification.json"),
    }


def _publish_final_review(
    *,
    repo_root: Path,
    report_path: Path,
    pending_dir: Path,
    raw_adjudication: Path,
    raw_attestation: Path,
    archive_result: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    from evaluation.customer_service_answer_review import (
        merge_answer_reviews,
        verify_answer_review_evidence,
        write_answer_review_evidence,
    )

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite final review: {output_dir}")
    review_a = pending_dir / "reviews/reviewer-a.sealed.jsonl"
    review_b = pending_dir / "reviews/reviewer-b.sealed.jsonl"
    final_report, agreement = merge_answer_reviews(
        report_path,
        review_a,
        review_b,
        adjudication_path=raw_adjudication,
    )
    final_report["evidenceTier"] = EVIDENCE_TIER
    final_report["annotationStatus"] = ANNOTATION_STATUS
    final_report["humanDecisionAuthority"] = True
    final_report["aiAssistedTextOrRecording"] = True
    final_report["reviewEvidence"]["humanApproval"] = {
        "status": ANNOTATION_STATUS,
        "evidenceTier": EVIDENCE_TIER,
        "adjudicatorId": _load_json(raw_attestation)["adjudicatorId"],
        "humanDecisionAuthority": True,
        "aiAssistedTextOrRecording": True,
        "pureHumanUnaidedClaim": False,
        "clarificationPath": (
            f"{archive_result['path']}/human-approval-clarification.json"
        ),
        "clarificationSha256": archive_result["clarificationSha256"],
        "rawReturnZipSha256": _sha256(
            repo_root / "holdout/AI-Shop-v54-answer-quality-adjudicator-c-20260827-draft.zip"
        ),
        "rawAdjudicationSha256": _sha256(raw_adjudication),
        "rawAttestationSha256": _sha256(raw_attestation),
        "returnArchiveSha256SumsSha256": archive_result["sha256SumsSha256"],
    }
    final_report["limitations"].extend(
        [
            "Review and adjudication decisions are human-approved with disclosed AI text/editing assistance; this does not claim unaided human authorship.",
            "The returned attestation file retained draft placeholders; project-owner confirmation is preserved separately and the raw return is unchanged.",
        ]
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    temporary_output.rmdir()
    try:
        write_answer_review_evidence(
            final_report,
            agreement,
            review_a_path=review_a,
            review_b_path=review_b,
            adjudication_path=raw_adjudication,
            output_dir=temporary_output,
        )
        for path in temporary_output.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        manifest_path = temporary_output / "evidence-manifest.json"
        manifest = _load_json(manifest_path)
        manifest.update(
            {
                "annotationStatus": ANNOTATION_STATUS,
                "evidenceTier": EVIDENCE_TIER,
                "humanDecisionAuthority": True,
                "aiAssistedTextOrRecording": True,
                "humanApprovalClarificationSha256": archive_result[
                    "clarificationSha256"
                ],
                "rawReturnArchiveSha256SumsSha256": archive_result[
                    "sha256SumsSha256"
                ],
            }
        )
        manifest_path.unlink()
        _write_json(manifest_path, manifest)
        sums_path = temporary_output / "SHA256SUMS"
        sums_path.unlink()
        _write_text(sums_path, _sums(temporary_output))
        for path in temporary_output.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verification = verify_answer_review_evidence(temporary_output)
        _verify_sums(temporary_output)
        _verify_read_only(temporary_output)
        temporary_output.replace(output_dir)
    except BaseException:
        shutil.rmtree(temporary_output, ignore_errors=True)
        raise
    return {
        "path": _relative(output_dir, repo_root),
        "sha256SumsSha256": _sha256(output_dir / "SHA256SUMS"),
        "finalReportSha256": _sha256(output_dir / "final-report.json"),
        "metrics": final_report["metrics"],
        "badcaseCount": len(final_report["badcases"]),
        "badcaseIds": [item["caseId"] for item in final_report["badcases"]],
        "verification": verification,
    }


def finalize(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    agent = repo_root / "AI_Shop-backend/AI_Shop-agent"
    sys.path.insert(0, str(agent))
    from evaluation.customer_service_answer_review import (
        merge_answer_reviews,
        verify_pending_answer_review_evidence,
    )

    report_path = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-pending-human-review-20260827/"
        "report.json"
    )
    pending_dir = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v54-badcase-fixes-answer-review-pending-adjudication-20260827"
    )
    workspace = (
        agent
        / "run/review-workspaces/"
        "customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-20260827"
    )
    template_path = workspace / "adjudicator-c/adjudication.open.jsonl"
    return_zip = (
        repo_root
        / "holdout/AI-Shop-v54-answer-quality-adjudicator-c-20260827-draft.zip"
    )
    raw_adjudication = repo_root / "holdout/v54/adjudicator-c/adjudication.open.jsonl"
    raw_attestation = (
        repo_root / "holdout/v54/adjudicator-c/adjudicator-attestation.template.json"
    )
    archive_dir = (
        agent
        / "evaluation-evidence/intake-archive/"
        "customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827"
    )
    output_dir = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827"
    )

    for path in (
        report_path,
        pending_dir / "SHA256SUMS",
        template_path,
        return_zip,
        raw_adjudication,
        raw_attestation,
    ):
        if not path.exists():
            raise FileNotFoundError(path)
    pending_verification = verify_pending_answer_review_evidence(pending_dir)
    _validate_zip(
        return_zip,
        {
            "adjudication.open.jsonl": raw_adjudication,
            "adjudicator-attestation.template.json": raw_attestation,
        },
    )
    agreement = _load_json(pending_dir / "agreement.json")
    validation = _validate_return(
        raw_adjudication=raw_adjudication,
        raw_attestation=raw_attestation,
        template_path=template_path,
        agreement=agreement,
    )
    # This is the fail-closed source-binding validation used by the scorer.
    merge_answer_reviews(
        report_path,
        pending_dir / "reviews/reviewer-a.sealed.jsonl",
        pending_dir / "reviews/reviewer-b.sealed.jsonl",
        adjudication_path=raw_adjudication,
    )
    archive_result = _publish_return_archive(
        repo_root=repo_root,
        return_zip=return_zip,
        raw_adjudication=raw_adjudication,
        raw_attestation=raw_attestation,
        template_path=template_path,
        pending_dir=pending_dir,
        output_dir=archive_dir,
        validation=validation,
    )
    final_result = _publish_final_review(
        repo_root=repo_root,
        report_path=report_path,
        pending_dir=pending_dir,
        raw_adjudication=raw_adjudication,
        raw_attestation=raw_attestation,
        archive_result=archive_result,
        output_dir=output_dir,
    )
    return {
        "valid": True,
        "status": ANNOTATION_STATUS,
        "evidenceTier": EVIDENCE_TIER,
        "pendingVerification": pending_verification,
        "returnValidation": validation,
        "returnArchive": archive_result,
        "finalReview": final_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.repo_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
