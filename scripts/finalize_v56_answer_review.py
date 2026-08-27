#!/usr/bin/env python3
"""Finalize the v56 human-approved, AI-assisted answer review."""

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
EDITABLE_FIELDS = {"finalLabels", "adjudicator", "reason"}
TASK_ID = "customer-service-http-v56-answer-quality-adjudication-20260827"


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
                raise ValueError(f"extracted return differs: {item.filename}")


def _validate_return(
    *,
    adjudication: Path,
    attestation_path: Path,
    template_path: Path,
    agreement: Mapping[str, Any],
) -> dict[str, Any]:
    template_rows = _load_jsonl(template_path)
    returned_rows = _load_jsonl(adjudication)
    template = {str(row.get("caseId") or ""): row for row in template_rows}
    returned = {str(row.get("caseId") or ""): row for row in returned_rows}
    disagreement_ids = {
        str(item.get("caseId") or "") for item in agreement.get("disagreements") or []
    }
    if (
        len(template) != len(template_rows)
        or len(returned) != len(returned_rows)
        or set(template) != disagreement_ids
        or set(returned) != disagreement_ids
    ):
        raise ValueError("adjudication coverage differs from frozen disagreements")

    frozen_changes: list[dict[str, str]] = []
    for case_id in sorted(disagreement_ids):
        if set(template[case_id]) != set(returned[case_id]):
            raise ValueError(f"adjudication fields differ for {case_id}")
        for field in sorted(set(template[case_id]) - EDITABLE_FIELDS):
            if _canonical(template[case_id][field]) != _canonical(
                returned[case_id][field]
            ):
                frozen_changes.append({"caseId": case_id, "field": field})
    if frozen_changes:
        raise ValueError(f"adjudication changed frozen fields: {frozen_changes}")

    label_fields = {
        "answerCorrect",
        "citationSupport",
        "handoffAppropriate",
        "unsafeAnswer",
    }
    citation_values = {
        "SUPPORTED",
        "UNSUPPORTED",
        "NOT_APPLICABLE",
        "UNDECIDABLE",
    }
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

    attestation = _load_json(attestation_path)
    adjudicator_id = next(iter(adjudicators))
    reviewer_ids = {
        str((agreement.get("reviewA") or {}).get("reviewerId") or ""),
        str((agreement.get("reviewB") or {}).get("reviewerId") or ""),
    }
    if (
        attestation.get("schemaVersion") != "aishop-human-adjudicator-attestation/v2"
        or attestation.get("taskId") != TASK_ID
        or set(attestation.get("sourceReviewerIds") or []) != reviewer_ids
        or attestation.get("adjudicatorId") != adjudicator_id
        or adjudicator_id in reviewer_ids
        or attestation.get("adjudicationTemplateSha256AtExport")
        != _sha256(template_path)
        or attestation.get("completedAdjudicationSha256") != _sha256(adjudication)
    ):
        raise ValueError("returned adjudicator attestation bindings are invalid")
    placeholder_fields = [
        field
        for field, expected in (
            ("status", "COMPLETE"),
            ("adjudicatorIsHuman", True),
            ("independentOfSourceReviewers", True),
            ("humanRetainedFinalDecisionAuthority", True),
        )
        if attestation.get(field) != expected
    ]
    if not str(attestation.get("attestedAt") or "").strip():
        placeholder_fields.append("attestedAt")
    return {
        "caseCount": len(returned_rows),
        "caseIds": sorted(returned),
        "adjudicatorId": adjudicator_id,
        "sourceReviewerIds": sorted(reviewer_ids),
        "frozenFieldChanges": frozen_changes,
        "finalLabelsComplete": True,
        "reasonsComplete": True,
        "rawAttestationStatus": attestation.get("status"),
        "rawAttestationPlaceholdersNotUpdated": placeholder_fields,
    }


def _publish_archive(
    *,
    repo_root: Path,
    return_zip: Path,
    adjudication: Path,
    attestation: Path,
    template: Path,
    pending: Path,
    output: Path,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite return archive: {output}")
    created_at = _now()
    clarification = {
        "schemaVersion": "aishop-human-approval-ai-assistance-clarification/v3",
        "status": "PROJECT_OWNER_CONFIRMED_HUMAN_ADJUDICATION_AI_ASSISTED_EDITING",
        "task": "CUSTOMER_SERVICE_V56_ANSWER_QUALITY_ADJUDICATION",
        "confirmedAt": created_at,
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
        "externalSignaturePresent": False,
    }
    audit = {
        "schemaVersion": "aishop-v56-answer-adjudication-return-intake-audit/v1",
        "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED",
        "createdAt": created_at,
        "evidenceTier": EVIDENCE_TIER,
        "sourcePendingEvidence": {
            "path": _relative(pending, repo_root),
            "sha256SumsSha256": _sha256(pending / "SHA256SUMS"),
        },
        "returnZip": {
            "path": _relative(return_zip, repo_root),
            "sha256": _sha256(return_zip),
            "exactBytesPreserved": True,
        },
        "exportedTemplate": {
            "path": _relative(template, repo_root),
            "sha256": _sha256(template),
        },
        "returnedAdjudication": {
            "path": _relative(adjudication, repo_root),
            "sha256": _sha256(adjudication),
        },
        "returnedAttestation": {
            "path": _relative(attestation, repo_root),
            "sha256": _sha256(attestation),
            "rawDraftFieldsPreserved": True,
        },
        "validation": dict(validation),
        "humanDecisionAuthority": True,
        "aiAssistedTextOrRecording": True,
        "claimPureUnaidedHumanAnnotation": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        raw_dir = staging / "raw-return"
        source_dir = staging / "source"
        raw_dir.mkdir()
        source_dir.mkdir()
        for name, source in (
            ("original-return.zip", return_zip),
            ("adjudication.open.jsonl", adjudication),
            ("adjudicator-attestation.template.json", attestation),
        ):
            shutil.copy2(source, raw_dir / name)
            if _sha256(source) != _sha256(raw_dir / name):
                raise ValueError(f"archive copy hash mismatch: {name}")
        shutil.copy2(template, source_dir / "adjudication.template.exported.jsonl")
        _write_json(staging / "human-approval-clarification.json", clarification)
        _write_json(staging / "validation-audit.json", audit)
        _write_text(
            staging / "README.md",
            "# v56 answer-quality adjudication return\n\n"
            "The exact returned ZIP and extracted files are preserved. The project "
            "owner confirms human decisions with AI-assisted editing. This supports "
            "`HUMAN_APPROVED_AI_ASSISTED`, not unaided-human or unseen claims.\n",
        )
        manifest = {
            "schemaVersion": "aishop-v56-answer-adjudication-return-evidence/v1",
            "status": audit["status"],
            "evidenceTier": EVIDENCE_TIER,
            "createdAt": created_at,
            "humanDecisionAuthority": True,
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
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
        staging.replace(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "path": _relative(output, repo_root),
        "sha256SumsSha256": _sha256(output / "SHA256SUMS"),
        "clarificationSha256": _sha256(output / "human-approval-clarification.json"),
    }


def _publish_final(
    *,
    repo_root: Path,
    report: Path,
    pending: Path,
    adjudication: Path,
    attestation: Path,
    return_zip: Path,
    archive: Mapping[str, Any],
    output: Path,
) -> dict[str, Any]:
    from evaluation.customer_service_answer_review import (
        merge_answer_reviews,
        verify_answer_review_evidence,
        write_answer_review_evidence,
    )

    if output.exists():
        raise FileExistsError(f"refusing to overwrite final review: {output}")
    review_a = pending / "reviews/reviewer-a.sealed.jsonl"
    review_b = pending / "reviews/reviewer-b.sealed.jsonl"
    final_report, agreement = merge_answer_reviews(
        report,
        review_a,
        review_b,
        adjudication_path=adjudication,
    )
    raw_attestation = _load_json(attestation)
    final_report.update(
        {
            "evidenceTier": EVIDENCE_TIER,
            "annotationStatus": ANNOTATION_STATUS,
            "humanDecisionAuthority": True,
            "aiAssistedTextOrRecording": True,
        }
    )
    final_report["reviewEvidence"]["humanApproval"] = {
        "status": ANNOTATION_STATUS,
        "evidenceTier": EVIDENCE_TIER,
        "adjudicatorId": raw_attestation["adjudicatorId"],
        "humanDecisionAuthority": True,
        "aiAssistedTextOrRecording": True,
        "pureHumanUnaidedClaim": False,
        "clarificationPath": (
            f"{archive['path']}/human-approval-clarification.json"
        ),
        "clarificationSha256": archive["clarificationSha256"],
        "rawReturnZipSha256": _sha256(return_zip),
        "rawAdjudicationSha256": _sha256(adjudication),
        "rawAttestationSha256": _sha256(attestation),
        "returnArchiveSha256SumsSha256": archive["sha256SumsSha256"],
    }
    final_report["limitations"].extend(
        [
            "Human-approved decisions used disclosed AI editing/recording assistance; this does not claim unaided human authorship.",
            "The returned attestation retained draft placeholders; project-owner confirmation is preserved separately.",
            "This developer-visible regression set is not an unseen release evaluation.",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    temporary.rmdir()
    try:
        write_answer_review_evidence(
            final_report,
            agreement,
            review_a_path=review_a,
            review_b_path=review_b,
            adjudication_path=adjudication,
            output_dir=temporary,
        )
        for path in temporary.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        manifest_path = temporary / "evidence-manifest.json"
        manifest = _load_json(manifest_path)
        manifest.update(
            {
                "annotationStatus": ANNOTATION_STATUS,
                "evidenceTier": EVIDENCE_TIER,
                "humanDecisionAuthority": True,
                "aiAssistedTextOrRecording": True,
                "humanApprovalClarificationSha256": archive[
                    "clarificationSha256"
                ],
                "rawReturnArchiveSha256SumsSha256": archive["sha256SumsSha256"],
            }
        )
        manifest_path.unlink()
        _write_json(manifest_path, manifest)
        sums_path = temporary / "SHA256SUMS"
        sums_path.unlink()
        _write_text(sums_path, _sums(temporary))
        for path in temporary.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verification = verify_answer_review_evidence(temporary)
        _verify_sums(temporary)
        _verify_read_only(temporary)
        temporary.replace(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "path": _relative(output, repo_root),
        "sha256SumsSha256": _sha256(output / "SHA256SUMS"),
        "finalReportSha256": _sha256(output / "final-report.json"),
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

    report = agent / (
        "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v56-full-v3-knowledge-regressions-"
        "pending-human-review-20260827/report.json"
    )
    pending = agent / (
        "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v56-v3-knowledge-answer-review-"
        "pending-adjudication-20260827"
    )
    raw = agent / (
        "run/review-workspaces/customer-service-http-v56-full-v3-knowledge-"
        "regressions-20260827/human-adjudication-return-raw"
    )
    adjudication = raw / "adjudication.open.jsonl"
    attestation = raw / "adjudicator-attestation.template.json"
    template = pending / "adjudication.template.jsonl"
    return_zip = (
        repo_root / "holdout/AI-Shop-v56-answer-quality-adjudicator-c-20260827-draft.zip"
    )
    archive_output = agent / (
        "evaluation-evidence/intake-archive/customer-service-v56-answer-review-"
        "adjudication-return-human-approved-ai-assisted-20260827"
    )
    final_output = agent / (
        "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v56-v3-knowledge-answer-review-"
        "human-approved-ai-assisted-20260827"
    )
    for path in (report, pending / "SHA256SUMS", adjudication, attestation, template, return_zip):
        if not path.exists():
            raise FileNotFoundError(path)
    pending_verification = verify_pending_answer_review_evidence(pending)
    _validate_zip(
        return_zip,
        {
            "adjudication.open.jsonl": adjudication,
            "adjudicator-attestation.template.json": attestation,
        },
    )
    agreement = _load_json(pending / "agreement.json")
    validation = _validate_return(
        adjudication=adjudication,
        attestation_path=attestation,
        template_path=template,
        agreement=agreement,
    )
    merge_answer_reviews(
        report,
        pending / "reviews/reviewer-a.sealed.jsonl",
        pending / "reviews/reviewer-b.sealed.jsonl",
        adjudication_path=adjudication,
    )
    archive = _publish_archive(
        repo_root=repo_root,
        return_zip=return_zip,
        adjudication=adjudication,
        attestation=attestation,
        template=template,
        pending=pending,
        output=archive_output,
        validation=validation,
    )
    final = _publish_final(
        repo_root=repo_root,
        report=report,
        pending=pending,
        adjudication=adjudication,
        attestation=attestation,
        return_zip=return_zip,
        archive=archive,
        output=final_output,
    )
    return {
        "valid": True,
        "status": ANNOTATION_STATUS,
        "evidenceTier": EVIDENCE_TIER,
        "pendingVerification": pending_verification,
        "returnValidation": validation,
        "returnArchive": archive,
        "finalReview": final,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.repo_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
