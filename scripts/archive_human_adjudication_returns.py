#!/usr/bin/env python3
"""Validate and archive returned adjudications without promoting invalid evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ATTESTATION_SCHEMA = "aishop-human-adjudicator-attestation/v1"
EDITABLE_FIELDS = frozenset({"finalLabels", "adjudicator", "reason"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"{path}:{index}: JSONL row must be an object")
        rows.append(value)
    return rows


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite archive artifact: {path}")
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


def _inventory(root: Path, *, excluded: set[str] | None = None) -> dict[str, Any]:
    excluded = excluded or set()
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def _immutable_differences(
    template_path: Path,
    returned_path: Path,
    *,
    id_field: str,
) -> list[dict[str, str]]:
    template = {str(row.get(id_field) or ""): row for row in _load_jsonl(template_path)}
    returned = {str(row.get(id_field) or ""): row for row in _load_jsonl(returned_path)}
    findings: list[dict[str, str]] = []
    for case_id in sorted(set(template) | set(returned)):
        if case_id not in template or case_id not in returned:
            findings.append({"caseId": case_id, "field": "ROW_PRESENCE"})
            continue
        for field in sorted(set(template[case_id]) | set(returned[case_id])):
            if field in EDITABLE_FIELDS:
                continue
            if template[case_id].get(field) != returned[case_id].get(field):
                findings.append({"caseId": case_id, "field": field})
    return findings


def _attestation_audit(
    returned_path: Path,
    template_path: Path,
    attestation_path: Path,
    *,
    expected_task_id: str,
    expected_reviewer_ids: list[str],
) -> dict[str, Any]:
    attestation = _load_json(attestation_path)
    rows = _load_jsonl(returned_path)
    row_adjudicator_ids = sorted(
        {
            str(row.get("adjudicator") or "").strip()
            for row in rows
            if str(row.get("adjudicator") or "").strip()
        }
    )
    findings: list[dict[str, str]] = []

    def block(code: str, detail: str) -> None:
        findings.append({"code": code, "severity": "BLOCKING", "detail": detail})

    if not isinstance(attestation, Mapping):
        block("ATTESTATION_NOT_OBJECT", "The returned attestation is not a JSON object.")
        attestation = {}
    if attestation.get("schemaVersion") != ATTESTATION_SCHEMA:
        block("ATTESTATION_SCHEMA_INVALID", "The attestation schema is not v1.")
    if attestation.get("taskId") != expected_task_id:
        block("TASK_ID_MISMATCH", "The attestation taskId differs from the exported task.")
    if attestation.get("sourceReviewerIds") != expected_reviewer_ids:
        block(
            "SOURCE_REVIEWER_IDS_MISMATCH",
            "The source reviewer IDs differ from the exported attestation template.",
        )
    if attestation.get("adjudicationTemplateSha256AtExport") != _sha256(template_path):
        block(
            "EXPORT_TEMPLATE_HASH_MISMATCH",
            "The blank adjudication template hash is not correctly bound.",
        )
    if attestation.get("completedAdjudicationSha256") != _sha256(returned_path):
        block(
            "COMPLETED_ADJUDICATION_HASH_MISMATCH",
            "The completed adjudication hash does not match the returned bytes.",
        )
    if attestation.get("status") != "COMPLETE":
        block("ATTESTATION_INCOMPLETE", "status must be COMPLETE.")
    if attestation.get("adjudicatorIsHuman") is not True:
        block("ADJUDICATOR_NOT_HUMAN", "adjudicatorIsHuman must be true.")
    if attestation.get("independentOfSourceReviewers") is not True:
        block(
            "SOURCE_REVIEWER_INDEPENDENCE_NOT_ATTESTED",
            "independentOfSourceReviewers must be true.",
        )
    if attestation.get("independentOfDatasetAndModelDevelopment") is not True:
        block(
            "DEVELOPMENT_INDEPENDENCE_NOT_ATTESTED",
            "independentOfDatasetAndModelDevelopment must be true.",
        )
    if attestation.get("generativeAiProducedOrSuggestedFinalLabels") is not False:
        block(
            "GENERATIVE_AI_USED_OR_NOT_EXCLUDED",
            "Formal human adjudication requires this field to be false.",
        )
    adjudicator_id = str(attestation.get("adjudicatorId") or "").strip()
    identity = str(attestation.get("adjudicatorIdentity") or "").strip()
    if not adjudicator_id:
        block("ADJUDICATOR_ID_MISSING", "adjudicatorId is required.")
    if not identity or identity.upper() in {"NOT_PROVIDED", "UNASSIGNED", "UNKNOWN"}:
        block("ADJUDICATOR_IDENTITY_MISSING", "A non-placeholder identity is required.")
    if len(row_adjudicator_ids) != 1 or row_adjudicator_ids != [adjudicator_id]:
        block(
            "ROW_ATTESTATION_IDENTITY_BINDING_MISMATCH",
            "Every row adjudicator must exactly equal attestation.adjudicatorId.",
        )
    if adjudicator_id in set(expected_reviewer_ids):
        block(
            "ADJUDICATOR_EQUALS_SOURCE_REVIEWER",
            "The adjudicator ID must differ from both source reviewers.",
        )
    if not str(attestation.get("attestedAt") or "").strip():
        block("ATTESTED_AT_MISSING", "attestedAt is required.")
    if not isinstance(attestation.get("signaturesOrExternalReferences"), list):
        block(
            "SIGNATURE_REFERENCES_INVALID",
            "signaturesOrExternalReferences must be an array.",
        )
    return {
        "valid": not findings,
        "status": "VALID" if not findings else "REJECTED",
        "attestationSha256": _sha256(attestation_path),
        "returnedAdjudicationSha256": _sha256(returned_path),
        "exportTemplateSha256": _sha256(template_path),
        "rowAdjudicatorIds": row_adjudicator_ids,
        "attestedAdjudicatorId": adjudicator_id,
        "findings": findings,
    }


def archive_adjudication_returns(
    repo_root: Path, output: Path, approval_clarification_path: Path
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    output = output.resolve()
    approval_clarification_path = approval_clarification_path.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite return archive: {output}")
    if not approval_clarification_path.is_file():
        raise FileNotFoundError(approval_clarification_path)
    approval = _load_json(approval_clarification_path)
    declarations = approval.get("declarations") if isinstance(approval, Mapping) else None
    if (
        not isinstance(approval, Mapping)
        or approval.get("schemaVersion")
        != "aishop-project-owner-human-approval-clarification/v1"
        or approval.get("status")
        != "HUMAN_APPROVAL_CONFIRMED_AI_ASSISTANCE_DISCLOSED"
        or approval.get("evidenceTier") != "HUMAN_APPROVED_AI_ASSISTED"
        or not isinstance(declarations, Mapping)
        or declarations.get("allInScopeLabelsWereReviewedAndApprovedByHumans")
        is not True
        or declarations.get("twoReviewerLabelsReflectTheirHumanSubjectiveDecisions")
        is not True
        or declarations.get("adjudicationFinalLabelsWereHumanConfirmed") is not True
        or declarations.get("humanRetainedFinalDecisionAuthority") is not True
        or declarations.get("aiAssistanceUsed") is not True
        or declarations.get("claimPureUnaidedHumanAnnotation") is not False
    ):
        raise ValueError("human-approval clarification is invalid or incomplete")
    agent = repo_root / "AI_Shop-backend/AI_Shop-agent"
    sys.path.insert(0, str(agent))
    from evaluation.core.io import load_json, load_jsonl
    from evaluation.customer_service_answer_review import _load_adjudications
    from evaluation.customer_service_label_policy_review import _validate_labels

    tasks = {
        "answer-quality-v43": {
            "idField": "caseId",
            "approvalKey": "answerQualityAdjudication",
            "returned": repo_root
            / "holdout/AI-Shop-answer-quality-adjudicator-c-20260827-completed/adjudication.open.jsonl",
            "attestation": repo_root
            / "holdout/AI-Shop-answer-quality-adjudicator-c-20260827-completed/adjudicator-attestation.template.json",
            "template": repo_root
            / "deliverables/human-review/AI-Shop-answer-quality-adjudicator-c-20260827/adjudication.open.jsonl",
            "agreement": agent
            / "evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-pending-adjudication-20260827/agreement.json",
            "taskId": "customer-service-http-v43-answer-quality-adjudication-20260827",
            "reviewerIds": ["reviewer-a", "reviewer-b"],
        },
        "label-policy-v2.1": {
            "idField": "id",
            "approvalKey": "labelPolicyAdjudication",
            "returned": repo_root
            / "holdout/AI-Shop-label-policy-adjudicator-c-20260827/adjudication.open.jsonl",
            "attestation": repo_root
            / "holdout/AI-Shop-label-policy-adjudicator-c-20260827/adjudicator-attestation.template.json",
            "template": repo_root
            / "deliverables/human-review/AI-Shop-label-policy-adjudicator-c-20260827/adjudication.open.jsonl",
            "agreement": agent
            / "evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-label-policy-review-pending-adjudication-20260827/agreement.json",
            "reviewerIds": ["label-policy-reviewer-a", "label-policy-reviewer-b"],
            "taskId": "customer-service-label-policy-v2.1-adjudication-20260827",
        },
    }
    for task in tasks.values():
        for key in ("returned", "attestation", "template", "agreement"):
            if not task[key].is_file():
                raise FileNotFoundError(task[key])

    approval_files = approval.get("inScopeReturnedFiles") or {}
    audits: dict[str, Any] = {}
    answer = tasks["answer-quality-v43"]
    answer_errors: list[str] = []
    try:
        _load_adjudications(answer["returned"], agreement=load_json(answer["agreement"]))
    except Exception as exc:  # noqa: BLE001 - preserve rejected intake findings
        answer_errors.append(f"{type(exc).__name__}: {exc}")
    answer_immutable = _immutable_differences(
        answer["template"], answer["returned"], id_field=answer["idField"]
    )
    answer_attestation = _attestation_audit(
        answer["returned"],
        answer["template"],
        answer["attestation"],
        expected_task_id=answer["taskId"],
        expected_reviewer_ids=answer["reviewerIds"],
    )
    answer_approval_bound = (
        isinstance(approval_files, Mapping)
        and isinstance(approval_files.get(answer["approvalKey"]), Mapping)
        and approval_files[answer["approvalKey"]].get("sha256")
        == _sha256(answer["returned"])
    )
    audits["answer-quality-v43"] = {
        "structurallyValid": not answer_errors and not answer_immutable,
        "structuralErrors": answer_errors,
        "immutableDifferences": answer_immutable,
        "caseCount": len(load_jsonl(answer["returned"])),
        "rawAttestationAudit": answer_attestation,
        "pureHumanAttestationValid": answer_attestation["valid"],
        "humanApprovalClarificationHashBound": answer_approval_bound,
        "humanApprovedAiAssistedEvidenceAccepted": (
            not answer_errors and not answer_immutable and answer_approval_bound
        ),
    }

    label = tasks["label-policy-v2.1"]
    label_errors: list[str] = []
    label_rows = load_jsonl(label["returned"])
    pending_root = label["agreement"].parent
    reviewer_manifest = load_json(
        pending_root / "reviews/reviewer-a.sealed.jsonl.manifest.json"
    )
    allowed_intents = set(reviewer_manifest["labelSchema"]["intentValues"])
    expected_ids = {
        str(row["id"])
        for row in load_jsonl(pending_root / "adjudication.template.jsonl")
    }
    returned_ids = [str(row.get("id") or "") for row in label_rows]
    if set(returned_ids) != expected_ids or len(returned_ids) != len(set(returned_ids)):
        label_errors.append("adjudication coverage differs from the pending disagreement set")
    for index, row in enumerate(label_rows, 1):
        try:
            _validate_labels(
                row.get("finalLabels"),
                label=f"{label['returned']}:{index}.finalLabels",
                allowed_intents=allowed_intents,
                message=str((row.get("input") or {}).get("message") or ""),
                require_complete=True,
            )
            if not str(row.get("adjudicator") or "").strip() or not str(
                row.get("reason") or ""
            ).strip():
                raise ValueError("adjudicator and reason are required")
        except Exception as exc:  # noqa: BLE001 - preserve every row-level finding
            label_errors.append(f"{type(exc).__name__}: {exc}")
    label_immutable = _immutable_differences(
        label["template"], label["returned"], id_field=label["idField"]
    )
    label_attestation = _attestation_audit(
        label["returned"],
        label["template"],
        label["attestation"],
        expected_task_id=label["taskId"],
        expected_reviewer_ids=label["reviewerIds"],
    )
    label_approval_bound = (
        isinstance(approval_files, Mapping)
        and isinstance(approval_files.get(label["approvalKey"]), Mapping)
        and approval_files[label["approvalKey"]].get("sha256")
        == _sha256(label["returned"])
    )
    audits["label-policy-v2.1"] = {
        "structurallyValid": not label_errors and not label_immutable,
        "structuralErrors": label_errors,
        "immutableDifferences": label_immutable,
        "caseCount": len(label_rows),
        "rawAttestationAudit": label_attestation,
        "pureHumanAttestationValid": label_attestation["valid"],
        "humanApprovalClarificationHashBound": label_approval_bound,
        "humanApprovedAiAssistedEvidenceAccepted": (
            not label_errors and not label_immutable and label_approval_bound
        ),
    }

    created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    accepted = all(
        item["humanApprovedAiAssistedEvidenceAccepted"] for item in audits.values()
    )
    pure_human = all(item["pureHumanAttestationValid"] for item in audits.values())
    status = (
        "HUMAN_APPROVED_AI_ASSISTED_INTAKE_ACCEPTED"
        if accepted
        else "HUMAN_APPROVAL_EVIDENCE_INCOMPLETE"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, task in tasks.items():
            destination = staging / "returns" / name
            destination.mkdir(parents=True)
            for key in ("returned", "attestation"):
                source = task[key]
                target = destination / source.name
                shutil.copy2(source, target)
                if _sha256(source) != _sha256(target):
                    raise ValueError(f"return copy hash mismatch: {source}")
        approval_target = staging / "human-approval-clarification.json"
        shutil.copy2(approval_clarification_path, approval_target)
        if _sha256(approval_clarification_path) != _sha256(approval_target):
            raise ValueError("human-approval clarification copy hash mismatch")
        intake_audit = {
            "schemaVersion": "aishop-human-adjudication-return-intake-audit/v1",
            "artifactId": output.name,
            "createdAt": created_at,
            "status": status,
            "sourceDirectory": str((repo_root / "holdout").resolve()),
            "sourceBytesPreserved": True,
            "sourceDirectoryModified": False,
            "evidenceTier": "HUMAN_APPROVED_AI_ASSISTED",
            "humanApprovedAiAssistedEvidenceAccepted": accepted,
            "pureHumanUnaidedEvidenceAccepted": pure_human,
            "approvalClarificationSha256": _sha256(approval_clarification_path),
            "tasks": audits,
            "notes": [
                "Both returned JSONL files are structurally complete and preserve immutable fields.",
                "The project owner confirms that humans retained final decision authority while AI assisted text output and recording.",
                "Metrics derived from these labels must disclose HUMAN_APPROVED_AI_ASSISTED and must not claim unaided human authorship.",
                "Raw identity-field inconsistencies remain preserved; normalized processing must retain byte-level lineage to these returns.",
            ],
            "returns": _inventory(staging / "returns"),
        }
        _write_json(staging / "intake-audit.json", intake_audit)
        _write_text(
            staging / "README.md",
            "# Human-adjudication return intake\n\n"
            "`returns/` preserves the exact returned bytes. Both decision files pass structural "
            "checks. The project owner confirms human final approval with AI-assisted text output; "
            "downstream metrics therefore use the `HUMAN_APPROVED_AI_ASSISTED` tier and do not "
            "claim unaided human authorship. Raw identity mismatches remain visible here.\n",
        )
        manifest = {
            "schemaVersion": "aishop-human-adjudication-return-archive/v1",
            "artifactId": output.name,
            "createdAt": created_at,
            "status": status,
            "readOnly": True,
            "evidenceTier": "HUMAN_APPROVED_AI_ASSISTED",
            "humanApprovedAiAssistedEvidenceAccepted": accepted,
            "pureHumanUnaidedEvidenceAccepted": pure_human,
            "returnFileCount": 4,
            "files": _inventory(
                staging, excluded={"evidence-manifest.json", "SHA256SUMS"}
            ),
        }
        _write_json(staging / "evidence-manifest.json", manifest)
        sums = "".join(
            f"{_sha256(path)}  {path.relative_to(staging).as_posix()}\n"
            for path in sorted(staging.rglob("*"))
            if path.is_file() and path.name != "SHA256SUMS"
        )
        _write_text(staging / "SHA256SUMS", sums)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        staging.replace(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "valid": True,
        "status": status,
        "evidenceTier": "HUMAN_APPROVED_AI_ASSISTED",
        "humanApprovedAiAssistedEvidenceAccepted": accepted,
        "pureHumanUnaidedEvidenceAccepted": pure_human,
        "output": str(output),
        "sha256SumsSha256": _sha256(output / "SHA256SUMS"),
        "tasks": audits,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approval-clarification", type=Path, required=True)
    args = parser.parse_args()
    result = archive_adjudication_returns(
        args.repo_root, args.output, args.approval_clarification
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
