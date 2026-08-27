"""Provenance audit and canonical packaging for customer-service v2 labels.

This module distinguishes label usability from release-grade provenance.  A
hash-valid, adjudicated dataset can support development diagnostics while an
independence attestation or blind re-audit is still missing; it must not be
silently promoted to final-unseen or release-gating evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    load_json,
    load_jsonl,
    sha256_file,
    utc_now,
)
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset
from evaluation.customer_service_review import compare_human_reviews, validate_review_sheet

PROVENANCE_SCHEMA = "aishop-customer-service-v2-provenance-audit/v1"
PACKAGE_SCHEMA = "aishop-customer-service-v2-canonical-package/v1"
REAUDIT_SCHEMA = "aishop-customer-service-independent-reaudit/v1"

_LABEL_FIELDS = ("intent", "riskLevel", "shouldHandoff", "handoffSeverity", "slots")


class CustomerServiceProvenanceError(ValueError):
    """Raised when reviewed labels or their provenance chain are inconsistent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _labels_from_expected(expected: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "intent": str(expected.get("intent") or ""),
        "riskLevel": str(expected.get("riskLevel") or ""),
        "shouldHandoff": bool(expected.get("shouldHandoff")),
        "handoffSeverity": expected.get("handoffSeverity"),
        "slots": {
            str(key): str(value) for key, value in (expected.get("slots") or {}).items()
        },
    }


def _labels_from_review(row: Mapping[str, Any]) -> dict[str, Any]:
    labels = row.get("labels") or {}
    return {
        "intent": str(labels.get("intent") or ""),
        "riskLevel": str(labels.get("riskLevel") or ""),
        "shouldHandoff": bool(labels.get("shouldHandoff")),
        "handoffSeverity": labels.get("handoffSeverity"),
        "slots": {
            str(key): str(value) for key, value in (labels.get("slots") or {}).items()
        },
    }


def _labels_from_adjudication(row: Mapping[str, Any]) -> dict[str, Any]:
    labels = row.get("finalLabels") or {}
    return {
        "intent": str(labels.get("intent") or ""),
        "riskLevel": str(labels.get("riskLevel") or ""),
        "shouldHandoff": bool(labels.get("shouldHandoff")),
        "handoffSeverity": labels.get("handoffSeverity"),
        "slots": {
            str(key): str(value) for key, value in (labels.get("slots") or {}).items()
        },
    }


def _rows_by_id(rows: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("id") or "")
        if not case_id or case_id in result:
            raise CustomerServiceProvenanceError(f"{label} contains a missing/duplicate case ID")
        result[case_id] = row
    return result


def _check(
    checks: list[dict[str, Any]],
    name: str,
    valid: bool,
    *,
    expected: Any = None,
    actual: Any = None,
    required: bool = True,
) -> None:
    item: dict[str, Any] = {"name": name, "valid": bool(valid), "required": required}
    if expected is not None:
        item["expected"] = expected
    if actual is not None:
        item["actual"] = actual
    checks.append(item)


def _open_sheet_audit(
    open_path: Path,
    sealed_manifest_path: Path,
) -> dict[str, Any]:
    open_manifest_path = open_path.with_suffix(open_path.suffix + ".manifest.json")
    open_manifest = load_json(open_manifest_path)
    sealed_manifest = load_json(sealed_manifest_path)
    current_sha = sha256_file(open_path)
    export_sha = str(open_manifest.get("sheetSha256") or "")
    filled_sha = str(sealed_manifest.get("sourceOpenSheetSha256") or "")
    sealed_export_sha = str(sealed_manifest.get("openSheetSha256AtExport") or "")
    return {
        "path": str(open_path.resolve()),
        "currentSha256": current_sha,
        "manifestExportSha256": export_sha,
        "sealedSourceOpenSha256": filled_sha,
        "sealedOpenSheetSha256AtExport": sealed_export_sha,
        "currentFileIsExportSnapshot": current_sha == export_sha,
        "filledSourceOpenFileAvailable": current_sha == filled_sha,
        "exportHashSemanticsValid": sealed_export_sha == export_sha,
    }


def audit_v2_provenance(
    *,
    candidate_path: Path,
    review_a_open_path: Path,
    review_a_sealed_path: Path,
    review_b_open_path: Path,
    review_b_sealed_path: Path,
    agreement_path: Path,
    adjudication_path: Path,
    additions_dataset_path: Path,
    additions_evidence_path: Path,
    base_dataset_path: Path,
    combined_dataset_path: Path,
    combined_manifest_path: Path,
    combined_evidence_path: Path,
    independence_attestation_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute the v2 label chain and report provenance gaps separately."""

    checks: list[dict[str, Any]] = []
    candidate_rows = load_gold_dataset(candidate_path)
    additions_rows = load_gold_dataset(additions_dataset_path)
    base_rows = load_gold_dataset(base_dataset_path)
    combined_rows = load_gold_dataset(combined_dataset_path)
    if len(candidate_rows) != 60 or len(additions_rows) != 60 or len(combined_rows) != 120:
        raise CustomerServiceProvenanceError("v2 package must have 60 candidate + 60 additions = 120")
    if any((row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in additions_rows):
        raise CustomerServiceProvenanceError("v2 additions are not uniformly HUMAN_VERIFIED")
    if any((row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in combined_rows):
        raise CustomerServiceProvenanceError("combined v2 dataset is not uniformly HUMAN_VERIFIED")

    manifest_a = validate_review_sheet(candidate_path, review_a_sealed_path, require_complete=True)
    manifest_b = validate_review_sheet(candidate_path, review_b_sealed_path, require_complete=True)
    _check(checks, "review-a-sealed-hash", manifest_a.get("sheetSha256") == sha256_file(review_a_sealed_path))
    _check(checks, "review-b-sealed-hash", manifest_b.get("sheetSha256") == sha256_file(review_b_sealed_path))
    _check(
        checks,
        "distinct-reviewer-identifiers",
        manifest_a.get("reviewerId") != manifest_b.get("reviewerId"),
    )

    saved_agreement = load_json(agreement_path)
    recomputed_agreement = compare_human_reviews(
        candidate_path, review_a_sealed_path, review_b_sealed_path
    )
    for key in (
        "caseCount",
        "exactAgreementCaseCount",
        "disagreementCaseCount",
        "fieldStats",
        "slotStats",
    ):
        _check(
            checks,
            f"agreement-{key}",
            _canonical(saved_agreement.get(key)) == _canonical(recomputed_agreement.get(key)),
            expected=saved_agreement.get(key),
            actual=recomputed_agreement.get(key),
        )
    saved_disagreements = [
        str(row.get("caseId") or "") for row in saved_agreement.get("disagreements") or []
    ]
    recomputed_disagreements = [
        str(row.get("caseId") or "")
        for row in recomputed_agreement.get("disagreements") or []
    ]
    _check(
        checks,
        "agreement-disagreement-ids",
        saved_disagreements == recomputed_disagreements,
        expected=saved_disagreements,
        actual=recomputed_disagreements,
    )

    review_a_rows = _rows_by_id(load_jsonl(review_a_sealed_path), label="review A")
    review_b_rows = _rows_by_id(load_jsonl(review_b_sealed_path), label="review B")
    adjudication_rows = _rows_by_id(load_jsonl(adjudication_path), label="adjudication")
    additions_by_id = _rows_by_id(additions_rows, label="v2 additions")
    candidate_by_id = _rows_by_id(candidate_rows, label="v2 candidate")
    disagreement_set = set(recomputed_disagreements)
    _check(
        checks,
        "adjudication-covers-every-disagreement-only",
        set(adjudication_rows) == disagreement_set,
        expected=sorted(disagreement_set),
        actual=sorted(adjudication_rows),
    )

    merged_label_mismatches: list[str] = []
    for case_id, candidate in candidate_by_id.items():
        left = _labels_from_review(review_a_rows[case_id])
        right = _labels_from_review(review_b_rows[case_id])
        if case_id in disagreement_set:
            expected_labels = _labels_from_adjudication(adjudication_rows[case_id])
        else:
            if _canonical(left) != _canonical(right):
                merged_label_mismatches.append(case_id)
                continue
            expected_labels = left
        merged = _labels_from_expected((additions_by_id[case_id].get("expected") or {}))
        if _canonical(expected_labels) != _canonical(merged):
            merged_label_mismatches.append(case_id)
        if str(candidate.get("id")) != case_id:
            merged_label_mismatches.append(case_id)
    _check(
        checks,
        "merged-labels-equal-agreement-or-adjudication",
        not merged_label_mismatches,
        actual=sorted(set(merged_label_mismatches)),
    )

    additions_evidence = load_json(additions_evidence_path)
    _check(
        checks,
        "additions-output-hash",
        additions_evidence.get("outputDatasetSha256") == sha256_file(additions_dataset_path),
        expected=additions_evidence.get("outputDatasetSha256"),
        actual=sha256_file(additions_dataset_path),
    )
    _check(
        checks,
        "additions-review-a-hash",
        (additions_evidence.get("reviewA") or {}).get("sha256")
        == sha256_file(review_a_sealed_path),
    )
    _check(
        checks,
        "additions-review-b-hash",
        (additions_evidence.get("reviewB") or {}).get("sha256")
        == sha256_file(review_b_sealed_path),
    )
    _check(
        checks,
        "additions-adjudication-hash",
        (additions_evidence.get("adjudication") or {}).get("sha256")
        == sha256_file(adjudication_path),
    )

    _check(
        checks,
        "combined-row-projection",
        combined_rows[: len(base_rows)] == base_rows
        and combined_rows[len(base_rows) :] == additions_rows,
    )
    combined_manifest = load_json(combined_manifest_path)
    combined_evidence = load_json(combined_evidence_path)
    combined_sha = sha256_file(combined_dataset_path)
    _check(
        checks,
        "combined-manifest-dataset-hash",
        combined_manifest.get("datasetSha256") == combined_sha,
    )
    _check(
        checks,
        "combined-evidence-dataset-hash",
        (combined_evidence.get("output") or {}).get("datasetSha256") == combined_sha,
    )
    _check(
        checks,
        "combined-evidence-manifest-hash",
        (combined_evidence.get("output") or {}).get("manifestSha256")
        == sha256_file(combined_manifest_path),
    )
    _check(
        checks,
        "combined-evidence-additions-hash",
        (combined_evidence.get("additions") or {}).get("datasetSha256")
        == sha256_file(additions_dataset_path),
    )

    alignment: dict[str, Any] = {}
    for reviewer, rows in (("reviewA", review_a_rows), ("reviewB", review_b_rows)):
        exact_ids: list[str] = []
        per_field = {field: 0 for field in _LABEL_FIELDS}
        for case_id, candidate in candidate_by_id.items():
            draft = _labels_from_expected(candidate.get("expected") or {})
            reviewed = _labels_from_review(rows[case_id])
            for field in _LABEL_FIELDS:
                if _canonical(draft[field]) == _canonical(reviewed[field]):
                    per_field[field] += 1
            if _canonical(draft) == _canonical(reviewed):
                exact_ids.append(case_id)
        alignment[reviewer] = {
            "exactDraftMatchCount": len(exact_ids),
            "caseCount": len(candidate_rows),
            "exactDraftMatchRate": len(exact_ids) / len(candidate_rows),
            "fieldMatchCounts": per_field,
            "exactDraftMatchIds": exact_ids,
            "interpretation": "diagnostic leakage signal only; not proof of copying",
        }

    open_sheet_audit = {
        "reviewA": _open_sheet_audit(
            review_a_open_path,
            review_a_sealed_path.with_suffix(review_a_sealed_path.suffix + ".manifest.json"),
        ),
        "reviewB": _open_sheet_audit(
            review_b_open_path,
            review_b_sealed_path.with_suffix(review_b_sealed_path.suffix + ".manifest.json"),
        ),
    }
    attestation_present = bool(
        independence_attestation_path and independence_attestation_path.is_file()
    )
    core_valid = all(item["valid"] for item in checks if item["required"])
    provenance_findings: list[dict[str, Any]] = []
    if not open_sheet_audit["reviewA"]["filledSourceOpenFileAvailable"]:
        provenance_findings.append(
            {
                "severity": "HIGH",
                "code": "REVIEW_A_FILLED_OPEN_SOURCE_MISSING",
                "detail": "The sealed review declares a filled open-sheet hash, but those bytes are unavailable.",
            }
        )
    for reviewer in ("reviewA", "reviewB"):
        if not open_sheet_audit[reviewer]["exportHashSemanticsValid"]:
            provenance_findings.append(
                {
                    "severity": "HIGH",
                    "code": f"{reviewer.upper()}_EXPORT_HASH_SEMANTICS_INVALID",
                    "detail": (
                        "openSheetSha256AtExport equals the filled input hash rather than the "
                        "OPEN manifest's immutable export hash."
                    ),
                }
            )
    if not attestation_present:
        provenance_findings.append(
            {
                "severity": "HIGH",
                "code": "INDEPENDENCE_ATTESTATION_MISSING",
                "detail": "No signed reviewer-independence and label-blinding attestation is available.",
            }
        )
    if alignment["reviewB"]["exactDraftMatchCount"] == len(candidate_rows):
        provenance_findings.append(
            {
                "severity": "MEDIUM",
                "code": "REVIEW_B_EXACT_DRAFT_ALIGNMENT",
                "detail": (
                    "Reviewer B matches every draft label. This is not proof of leakage, but "
                    "requires independent attestation or a blind re-audit."
                ),
            }
        )

    return {
        "schemaVersion": PROVENANCE_SCHEMA,
        "createdAt": utc_now(),
        "status": (
            "HUMAN_VERIFIED_PROVENANCE_REVIEW_REQUIRED"
            if core_valid
            else "INVALID_HASH_OR_LABEL_CHAIN"
        ),
        "labelLifecycle": "HUMAN_VERIFIED_IMMUTABLE" if core_valid else "INVALID",
        "evidenceLifecycle": "CANONICALIZED_PROVENANCE_REVIEW_REQUIRED",
        "hashAndLabelChainValid": core_valid,
        "developmentDiagnosticEligible": core_valid,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "independentReviewClaimVerified": False,
        "caseCounts": {
            "candidate": len(candidate_rows),
            "base": len(base_rows),
            "additions": len(additions_rows),
            "combined": len(combined_rows),
            "disagreements": len(disagreement_set),
            "adjudications": len(adjudication_rows),
        },
        "agreement": {
            "exactAgreementCaseCount": recomputed_agreement["exactAgreementCaseCount"],
            "caseAgreementRate": recomputed_agreement["caseAgreementRate"],
            "fieldStats": recomputed_agreement["fieldStats"],
            "slotStats": recomputed_agreement["slotStats"],
        },
        "checks": checks,
        "openSheetAudit": open_sheet_audit,
        "draftAlignmentDiagnostic": alignment,
        "provenanceFindings": provenance_findings,
        "requiredActions": [
            "Obtain signed reviewer independence/blinding attestations, or replace the review with independently controlled review.",
            "Run the supplied 12-case blind independent re-audit; expand to all 60 additions if its preregistered gates fail.",
            "Keep this dataset out of final-unseen and release gates until both controls pass.",
        ],
        "sourceHashes": {
            "candidate": sha256_file(candidate_path),
            "reviewASealed": sha256_file(review_a_sealed_path),
            "reviewBSealed": sha256_file(review_b_sealed_path),
            "agreement": sha256_file(agreement_path),
            "adjudication": sha256_file(adjudication_path),
            "additions": sha256_file(additions_dataset_path),
            "additionsEvidence": sha256_file(additions_evidence_path),
            "combined": combined_sha,
            "combinedManifest": sha256_file(combined_manifest_path),
            "combinedEvidence": sha256_file(combined_evidence_path),
        },
    }


def _stable_order(rows: Sequence[Mapping[str, Any]], *, seed: str) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{seed}\0{row.get('id')}".encode("utf-8")
        ).hexdigest(),
    )


def build_independent_reaudit_sheet(
    human_additions_path: Path,
    output_path: Path,
    *,
    sample_size: int = 12,
    seed: str = "customer-service-v2-independent-reaudit-20260826",
) -> dict[str, Any]:
    """Export a deterministic, gold-blind, risk-stratified re-audit sheet."""

    rows = load_gold_dataset(human_additions_path)
    if sample_size < 1 or sample_size > len(rows):
        raise CustomerServiceProvenanceError("re-audit sample size is outside dataset bounds")
    high = [row for row in rows if (row.get("expected") or {}).get("riskLevel") == "HIGH"]
    slots = [
        row
        for row in rows
        if row not in high and bool((row.get("expected") or {}).get("slots"))
    ]
    no_slots = [
        row
        for row in rows
        if row not in high and not bool((row.get("expected") or {}).get("slots"))
    ]
    selected = [*_stable_order(high, seed=seed)]
    slots_target = min(4, max(0, sample_size - len(selected)))
    selected.extend(_stable_order(slots, seed=f"{seed}:slots")[:slots_target])
    remaining = sample_size - len(selected)
    selected.extend(_stable_order(no_slots, seed=f"{seed}:no-slots")[:remaining])
    if len(selected) < sample_size:
        selected_ids = {str(row["id"]) for row in selected}
        remainder = [row for row in rows if str(row["id"]) not in selected_ids]
        selected.extend(_stable_order(remainder, seed=f"{seed}:remainder")[: sample_size - len(selected)])

    sheet_rows = [
        {
            "schemaVersion": REAUDIT_SCHEMA,
            "id": str(row["id"]),
            "input": {"message": str((row.get("input") or {}).get("message") or "")},
            "reviewerId": "UNASSIGNED_INDEPENDENT_REVIEWER",
            "labels": {
                "intent": None,
                "riskLevel": None,
                "shouldHandoff": None,
                "handoffSeverity": None,
                "slots": None,
            },
            "comment": "",
        }
        for row in _stable_order(selected, seed=f"{seed}:presentation")
    ]
    atomic_write_jsonl(output_path, sheet_rows, overwrite=False)
    high_ids = {str(row["id"]) for row in high}
    selected_high = sum(str(row["id"]) in high_ids for row in selected)
    manifest = {
        "schemaVersion": REAUDIT_SCHEMA,
        "artifact": "BLINDED_INDEPENDENT_REAUDIT_SHEET",
        "lifecycle": "OPEN_UNASSIGNED",
        "createdAt": utc_now(),
        "sourceHumanDatasetSha256": sha256_file(human_additions_path),
        "sheetSha256": sha256_file(output_path),
        "caseCount": len(sheet_rows),
        "selectionSeed": seed,
        "selectionPolicy": {
            "includeAllHighRisk": True,
            "highRiskSelected": selected_high,
            "nonHighWithSlotsTarget": slots_target,
            "remainingFromNoSlotPool": True,
        },
        "goldLabelsPresent": False,
        "modelPredictionsPresent": False,
        "preregisteredAcceptance": {
            "criticalMismatchCount": 0,
            "intentAgreementMinimum": 0.8,
            "riskAgreementMinimum": 0.9,
            "handoffAgreementMinimum": 0.9,
            "slotExactAgreementMinimum": 0.7,
            "failureAction": "EXPAND_TO_FULL_60_CASE_INDEPENDENT_REAUDIT",
        },
        "instructions": [
            "Custodian must transfer only this sheet and the frozen guideline to the reviewer.",
            "Reviewer must not receive the source human dataset, draft labels, model output, or prior reviews.",
            "Replace reviewerId with a stable independent identity and complete every label.",
        ],
    }
    atomic_write_json(output_path.with_suffix(output_path.suffix + ".manifest.json"), manifest, overwrite=False)
    return manifest


def render_provenance_markdown(audit: Mapping[str, Any]) -> str:
    findings = audit.get("provenanceFindings") or []
    lines = [
        "# Customer-service v2 provenance audit",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Hash/label chain valid: `{str(bool(audit.get('hashAndLabelChainValid'))).lower()}`",
        "- Label lifecycle: `HUMAN_VERIFIED_IMMUTABLE`",
        "- Release gate eligible: `false`",
        "- Final-unseen eligible: `false`",
        "",
        "The 120 labels are structurally usable for development diagnostics. They are not yet "
        "release-grade evidence because reviewer independence/blinding provenance is incomplete.",
        "",
        "## Findings",
        "",
    ]
    lines.extend(
        f"- **{item.get('severity')} / {item.get('code')}**: {item.get('detail')}"
        for item in findings
    )
    lines.extend(["", "## Required controls", ""])
    lines.extend(f"- {item}" for item in audit.get("requiredActions") or [])
    lines.extend(["", "## Agreement", ""])
    agreement = audit.get("agreement") or {}
    lines.append(
        f"- Exact case agreement: `{agreement.get('exactAgreementCaseCount')}/60` "
        f"(`{agreement.get('caseAgreementRate')}`)"
    )
    for field, stats in sorted((agreement.get("fieldStats") or {}).items()):
        lines.append(
            f"- `{field}`: agreement `{stats.get('agreementRate')}`, "
            f"Cohen's kappa `{stats.get('cohenKappa')}`"
        )
    return "\n".join(lines) + "\n"


def _copy_exact(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise CustomerServiceProvenanceError(f"missing source artifact: {source}")
    atomic_write_bytes(destination, source.read_bytes(), overwrite=False)
    if sha256_file(source) != sha256_file(destination):
        raise CustomerServiceProvenanceError(f"copy hash mismatch: {source}")


def _package_files(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def canonicalize_v2_workspace(
    *,
    workspace_dir: Path,
    candidate_path: Path,
    candidate_manifest_path: Path,
    base_dataset_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Copy a completed external v2 workspace into a fail-closed evidence package."""

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite evidence package: {output_dir}")
    output_dir.mkdir(parents=True)
    source_files = {
        "source/candidate-v2-additions.jsonl": candidate_path,
        "source/candidate-v2-additions.manifest.json": candidate_manifest_path,
        "source-workspace/reviewer-a.open.jsonl": workspace_dir / "reviewer-a.open.jsonl",
        "source-workspace/reviewer-a.open.jsonl.manifest.json": workspace_dir / "reviewer-a.open.jsonl.manifest.json",
        "source-workspace/reviewer-b.open.jsonl": workspace_dir / "reviewer-b.open.jsonl",
        "source-workspace/reviewer-b.open.jsonl.manifest.json": workspace_dir / "reviewer-b.open.jsonl.manifest.json",
        "reviews/reviewer-a.sealed.jsonl": workspace_dir / "reviewer-a.sealed.jsonl",
        "reviews/reviewer-a.sealed.jsonl.manifest.json": workspace_dir / "reviewer-a.sealed.jsonl.manifest.json",
        "reviews/reviewer-b.sealed.jsonl": workspace_dir / "reviewer-b.sealed.jsonl",
        "reviews/reviewer-b.sealed.jsonl.manifest.json": workspace_dir / "reviewer-b.sealed.jsonl.manifest.json",
        "reviews/agreement.json": workspace_dir / "agreement.json",
        "reviews/agreement.md": workspace_dir / "agreement.md",
        "reviews/adjudication.final.jsonl": workspace_dir / "adjudication.final.jsonl",
        "labels/customer-service-v2-additions-human.jsonl": workspace_dir / "customer-service-v2-additions-human.jsonl",
        "labels/customer-service-v2-additions-merge.evidence.json": workspace_dir / "customer-service-v2-additions-merge.evidence.json",
        "labels/customer-service-human-v2.jsonl": workspace_dir / "customer-service-human-v2.jsonl",
        "labels/customer-service-human-v2.jsonl.manifest.json": workspace_dir / "customer-service-human-v2.jsonl.manifest.json",
        "labels/customer-service-human-v2.evidence.json": workspace_dir / "customer-service-human-v2.evidence.json",
        "diagnostics/v1-current-rule.report.json": workspace_dir / "customer-service-human-v1.current-rule.report.json",
        "diagnostics/v1-current-rule.report.md": workspace_dir / "customer-service-human-v1.current-rule.report.md",
        "diagnostics/v2-additions-current-rule.report.json": workspace_dir / "customer-service-v2-additions-human.report.json",
        "diagnostics/v2-additions-current-rule.report.md": workspace_dir / "customer-service-v2-additions-human.report.md",
        "diagnostics/v2-combined-current-rule.report.json": workspace_dir / "customer-service-human-v2.report.json",
        "diagnostics/v2-combined-current-rule.report.md": workspace_dir / "customer-service-human-v2.report.md",
    }
    for relative, source in source_files.items():
        _copy_exact(source, output_dir / relative)

    audit = audit_v2_provenance(
        candidate_path=candidate_path,
        review_a_open_path=workspace_dir / "reviewer-a.open.jsonl",
        review_a_sealed_path=workspace_dir / "reviewer-a.sealed.jsonl",
        review_b_open_path=workspace_dir / "reviewer-b.open.jsonl",
        review_b_sealed_path=workspace_dir / "reviewer-b.sealed.jsonl",
        agreement_path=workspace_dir / "agreement.json",
        adjudication_path=workspace_dir / "adjudication.final.jsonl",
        additions_dataset_path=workspace_dir / "customer-service-v2-additions-human.jsonl",
        additions_evidence_path=workspace_dir / "customer-service-v2-additions-merge.evidence.json",
        base_dataset_path=base_dataset_path,
        combined_dataset_path=workspace_dir / "customer-service-human-v2.jsonl",
        combined_manifest_path=workspace_dir / "customer-service-human-v2.jsonl.manifest.json",
        combined_evidence_path=workspace_dir / "customer-service-human-v2.evidence.json",
    )
    if not audit["hashAndLabelChainValid"]:
        raise CustomerServiceProvenanceError("v2 hash/label chain audit failed")
    atomic_write_json(output_dir / "provenance-audit.json", audit, overwrite=False)
    atomic_write_text(
        output_dir / "provenance-audit.md",
        render_provenance_markdown(audit),
        overwrite=False,
    )
    build_independent_reaudit_sheet(
        workspace_dir / "customer-service-v2-additions-human.jsonl",
        output_dir / "reaudit" / "independent-reaudit.open.jsonl",
    )
    atomic_write_json(
        output_dir / "reaudit" / "independence-attestation.template.json",
        {
            "schemaVersion": "aishop-reviewer-independence-attestation/v1",
            "artifactId": output_dir.name,
            "reviewerAIdentity": None,
            "reviewerBIdentity": None,
            "reviewersWorkedIndependently": None,
            "reviewersDidNotViewDraftOrModelLabels": None,
            "reviewersDidNotShareSheetsBeforeSealing": None,
            "custodianIdentity": None,
            "attestedAt": None,
            "signaturesOrExternalReferences": [],
            "status": "TEMPLATE_NOT_EVIDENCE",
        },
        overwrite=False,
    )
    lifecycle = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": utc_now(),
        "labelLifecycle": "HUMAN_VERIFIED_IMMUTABLE",
        "evidenceLifecycle": "CANONICALIZED_PROVENANCE_REVIEW_REQUIRED",
        "developmentDiagnosticEligible": True,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "qualityMetricsStatus": "PRE_FIX_RULE_DIAGNOSTICS_ONLY",
        "blockingControls": [
            "SIGNED_INDEPENDENCE_ATTESTATION_OR_REPLACEMENT_REVIEW",
            "BLIND_INDEPENDENT_REAUDIT",
            "NEW_PRODUCTION_HTTP_RUN_AND_BLIND_ANSWER_REVIEW",
        ],
    }
    atomic_write_json(output_dir / "lifecycle.json", lifecycle, overwrite=False)
    atomic_write_text(
        output_dir / "README.md",
        "# Customer-service human v2 — provenance pending\n\n"
        "This archive preserves the completed 60-case additions review and the combined "
        "120-case input gold. The label/adjudication hash chain is valid, but reviewer "
        "independence and blinding are not sufficiently evidenced. Use it for development "
        "diagnostics only; do not use it as final-unseen or release-gating evidence.\n\n"
        "The `reaudit/` directory contains a preregistered 12-case blind independent re-audit "
        "sheet and an attestation template. A real independent person/custodian must complete "
        "those controls; this package does not fabricate them.\n",
        overwrite=False,
    )
    manifest = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": lifecycle["createdAt"],
        "labelLifecycle": lifecycle["labelLifecycle"],
        "evidenceLifecycle": lifecycle["evidenceLifecycle"],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "hashAndLabelChainValid": True,
        "combinedDataset": {
            "path": "labels/customer-service-human-v2.jsonl",
            "sha256": audit["sourceHashes"]["combined"],
            "caseCount": 120,
        },
        "files": _package_files(output_dir),
    }
    atomic_write_json(output_dir / "evidence-manifest.json", manifest, overwrite=False)
    checksum_paths = sorted(path for path in output_dir.rglob("*") if path.is_file())
    atomic_write_text(
        output_dir / "SHA256SUMS",
        "\n".join(
            f"{sha256_file(path)}  {path.relative_to(output_dir).as_posix()}"
            for path in checksum_paths
        )
        + "\n",
        overwrite=False,
    )
    return {
        "status": audit["status"],
        "outputDir": str(output_dir),
        "caseCount": 120,
        "hashAndLabelChainValid": True,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "findingCount": len(audit["provenanceFindings"]),
        "sha256SumsSha256": sha256_file(output_dir / "SHA256SUMS"),
    }


def verify_v2_package(output_dir: Path) -> dict[str, Any]:
    """Verify package checksums and fail-closed lifecycle declarations."""

    errors: list[str] = []
    for line in (output_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        path = output_dir / relative
        if not separator or not path.is_file() or sha256_file(path) != digest:
            errors.append(f"checksum:{relative or line}")
    manifest = load_json(output_dir / "evidence-manifest.json")
    lifecycle = load_json(output_dir / "lifecycle.json")
    audit = load_json(output_dir / "provenance-audit.json")
    if manifest.get("releaseGateEligible") is not False:
        errors.append("manifest-release-gate")
    if lifecycle.get("finalUnseenEligible") is not False:
        errors.append("lifecycle-final-unseen")
    if audit.get("hashAndLabelChainValid") is not True:
        errors.append("audit-hash-label-chain")
    combined = output_dir / str((manifest.get("combinedDataset") or {}).get("path") or "")
    if not combined.is_file() or sha256_file(combined) != (manifest.get("combinedDataset") or {}).get("sha256"):
        errors.append("combined-dataset")
    return {
        "valid": not errors,
        "status": "VERIFIED" if not errors else "INVALID",
        "errors": errors,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
    }
