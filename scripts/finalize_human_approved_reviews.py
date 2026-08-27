#!/usr/bin/env python3
"""Finalize human-approved, AI-assisted label and answer review evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVIDENCE_TIER = "HUMAN_APPROVED_AI_ASSISTED"


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
        raise FileExistsError(f"refusing to overwrite finalization artifact: {path}")
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


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    _write_text(
        path,
        "".join(
            json.dumps(
                dict(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
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


def _sums(root: Path) -> str:
    return "".join(
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    )


def _verify_sums(root: Path) -> None:
    sums_path = root / "SHA256SUMS"
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
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
        raise ValueError(f"checksum inventory differs for {root}")
    for relative, digest in expected.items():
        if _sha256(root / relative) != digest:
            raise ValueError(f"checksum mismatch: {root / relative}")


def _verify_read_only(root: Path) -> None:
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise ValueError(f"evidence files remain writable: {writable}")


def _normalize_adjudication(
    rows: list[dict[str, Any]], *, human_approver_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized = copy.deepcopy(rows)
    original_ids = sorted(
        {str(row.get("adjudicator") or "").strip() for row in rows}
    )
    for row in normalized:
        row["adjudicator"] = human_approver_id
    for original, updated in zip(rows, normalized, strict=True):
        for field in set(original) | set(updated):
            if field == "adjudicator":
                continue
            if _canonical(original.get(field)) != _canonical(updated.get(field)):
                raise ValueError(f"normalization modified forbidden field: {field}")
    return normalized, {
        "originalAdjudicatorIds": original_ids,
        "normalizedHumanApproverId": human_approver_id,
        "modifiedFields": ["adjudicator"],
        "finalLabelsUnchanged": True,
        "reasonsUnchanged": True,
    }


def _normalized_expected(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "intent": value.get("intent"),
        "riskLevel": value.get("riskLevel"),
        "shouldHandoff": value.get("shouldHandoff"),
        "handoffSeverity": value.get("handoffSeverity"),
        "slots": copy.deepcopy(value.get("slots") or {}),
    }


def _render_label_summary(report: Mapping[str, Any]) -> str:
    fields = report.get("changedFieldCaseCounts") or {}
    return (
        "# 客服 v2.1 标签政策终审结果\n\n"
        f"- 证据口径：`{report.get('evidenceTier')}`\n"
        f"- 双人复核：`{report.get('reviewedCaseCount')}` 条\n"
        f"- A/B 完全一致：`{report.get('exactAgreementCaseCount')}` 条\n"
        f"- 第三方终审：`{report.get('adjudicatedCaseCount')}` 条\n"
        f"- 相对 v2 实际改标：`{report.get('changedCaseCount')}` 条\n"
        f"- 字段改动：`{json.dumps(fields, ensure_ascii=False, sort_keys=True)}`\n\n"
        "这些标签由人类保留最终决策权，AI 仅辅助文字输出和记录；结果不声称逐字无 AI。"
        "本数据仍是项目开发集，不是外部未见测试集。\n"
    )


def _publish_label_policy_final(
    *,
    repo_root: Path,
    agent: Path,
    pending: Path,
    raw_adjudication_path: Path,
    raw_attestation_path: Path,
    normalized_rows: list[dict[str, Any]],
    approval_path: Path,
    human_approver_id: str,
    output_dir: Path,
    dataset_output: Path,
) -> dict[str, Any]:
    from evaluation.customer_service_gold import (
        evaluate_predictions,
        load_gold_dataset,
    )
    from evaluation.customer_service_label_policy_review import (
        _validate_labels,
        verify_pending_label_policy_review_evidence,
    )

    verify_pending_label_policy_review_evidence(pending)
    if output_dir.exists() or dataset_output.exists() or dataset_output.with_suffix(
        dataset_output.suffix + ".manifest.json"
    ).exists():
        raise FileExistsError("label-policy final output already exists")
    agreement = _load_json(pending / "agreement.json")
    review_a_path = pending / "reviews/reviewer-a.sealed.jsonl"
    review_b_path = pending / "reviews/reviewer-b.sealed.jsonl"
    review_a = {str(row["id"]): row for row in _load_jsonl(review_a_path)}
    review_b = {str(row["id"]): row for row in _load_jsonl(review_b_path)}
    adjudication = {str(row["id"]): row for row in normalized_rows}
    manifest_a = _load_json(review_a_path.with_suffix(review_a_path.suffix + ".manifest.json"))
    allowed_intents = set(manifest_a["labelSchema"]["intentValues"])
    disagreement_ids = {
        str(item["id"]) for item in agreement.get("disagreements") or []
    }
    if set(adjudication) != disagreement_ids:
        raise ValueError("label adjudication coverage differs from agreement")

    source_dataset = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-human-v2-label-consistency-audit-20260826/"
        "source/customer-service-human-v2.jsonl"
    )
    source_rows = load_gold_dataset(source_dataset)
    source_by_id = {str(row["id"]): row for row in source_rows}
    decisions: list[dict[str, Any]] = []
    final_by_id: dict[str, dict[str, Any]] = {}
    for case_id in sorted(review_a):
        left = review_a[case_id]
        right = review_b.get(case_id)
        if right is None:
            raise ValueError(f"review B is missing {case_id}")
        if case_id in disagreement_ids:
            decision = adjudication[case_id]
            labels = _validate_labels(
                decision.get("finalLabels"),
                label=f"adjudication {case_id}",
                allowed_intents=allowed_intents,
                message=str((decision.get("input") or {}).get("message") or ""),
                require_complete=True,
            )
            label_source = "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION"
            reason = str(decision.get("reason") or "").strip()
            adjudicator = human_approver_id
        else:
            if _canonical(left.get("labels")) != _canonical(right.get("labels")):
                raise ValueError(f"unadjudicated reviewer disagreement: {case_id}")
            labels = _validate_labels(
                left.get("labels"),
                label=f"reviewer agreement {case_id}",
                allowed_intents=allowed_intents,
                message=str((left.get("input") or {}).get("message") or ""),
                require_complete=True,
            )
            label_source = "HUMAN_REVIEWER_AGREEMENT_AI_ASSISTED_RECORDING"
            reason = " | ".join(
                value
                for value in (
                    str(left.get("comment") or "").strip(),
                    str(right.get("comment") or "").strip(),
                )
                if value
            )
            adjudicator = None
        old_labels = _normalized_expected(source_by_id[case_id]["expected"])
        differing_fields = [
            field
            for field in ("intent", "riskLevel", "shouldHandoff", "handoffSeverity", "slots")
            if _canonical(old_labels[field]) != _canonical(labels[field])
        ]
        final_by_id[case_id] = copy.deepcopy(labels)
        decisions.append(
            {
                "schemaVersion": "aishop-customer-service-label-policy-final-decision/v1",
                "id": case_id,
                "input": copy.deepcopy(left.get("input")),
                "previousExpected": old_labels,
                "finalLabels": copy.deepcopy(labels),
                "changedFields": differing_fields,
                "labelSource": label_source,
                "reviewerA": {
                    "reviewerId": left.get("reviewerId"),
                    "labels": copy.deepcopy(left.get("labels")),
                    "comment": left.get("comment"),
                },
                "reviewerB": {
                    "reviewerId": right.get("reviewerId"),
                    "labels": copy.deepcopy(right.get("labels")),
                    "comment": right.get("comment"),
                },
                "adjudicator": adjudicator,
                "humanApproverId": human_approver_id,
                "reason": reason,
                "evidenceTier": EVIDENCE_TIER,
            }
        )
    if len(decisions) != int(agreement["caseCount"]):
        raise ValueError("label-policy final decision count is invalid")

    normalized_sha = hashlib.sha256(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for row in normalized_rows
        ).encode("utf-8")
    ).hexdigest()
    approval_sha = _sha256(approval_path)
    successor_rows = copy.deepcopy(source_rows)
    for row in successor_rows:
        case_id = str(row["id"])
        if case_id not in final_by_id:
            continue
        row["expected"] = copy.deepcopy(final_by_id[case_id])
        decision = next(item for item in decisions if item["id"] == case_id)
        row["annotation"] = {
            "status": "HUMAN_VERIFIED",
            "annotator": "human-approved-ai-assisted",
            "reviewers": [
                str(review_a[case_id]["reviewerId"]),
                str(review_b[case_id]["reviewerId"]),
            ],
            "adjudicator": human_approver_id,
            "guidelinesVersion": "customer-service-taxonomy-v2.1",
            "evidenceTier": EVIDENCE_TIER,
            "reviewEvidence": {
                "agreement": decision["labelSource"],
                "sourceDatasetSha256": _sha256(source_dataset),
                "reviewASha256": _sha256(review_a_path),
                "reviewBSha256": _sha256(review_b_path),
                "adjudicationSha256": normalized_sha,
                "rawAdjudicationSha256": _sha256(raw_adjudication_path),
                "humanApprovalClarificationSha256": approval_sha,
            },
        }
    changed = [item for item in decisions if item["changedFields"]]
    changed_fields = Counter(
        field for item in changed for field in item["changedFields"]
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    try:
        _write_jsonl(staging / "successor-dataset.jsonl", successor_rows)
        load_gold_dataset(staging / "successor-dataset.jsonl")
        dataset_sha = _sha256(staging / "successor-dataset.jsonl")
        dataset_manifest = {
            "schemaVersion": "aishop-customer-service-gold-successor/v1",
            "datasetId": "customer-service-human-v2.1-human-approved-ai-assisted",
            "status": "HUMAN_VERIFIED",
            "evidenceTier": EVIDENCE_TIER,
            "caseCount": len(successor_rows),
            "reviewedCaseCount": len(decisions),
            "changedCaseCount": len(changed),
            "sourceDatasetPath": str(source_dataset.relative_to(repo_root)),
            "sourceDatasetSha256": _sha256(source_dataset),
            "datasetSha256": dataset_sha,
            "agreementSha256": _sha256(pending / "agreement.json"),
            "reviewASha256": _sha256(review_a_path),
            "reviewBSha256": _sha256(review_b_path),
            "rawAdjudicationSha256": _sha256(raw_adjudication_path),
            "normalizedAdjudicationSha256": normalized_sha,
            "humanApprovalClarificationSha256": approval_sha,
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
        }
        _write_json(staging / "successor-dataset.manifest.json", dataset_manifest)
        _write_jsonl(staging / "final-decisions.jsonl", decisions)
        _write_jsonl(staging / "adjudication.normalized.jsonl", normalized_rows)
        shutil.copy2(raw_adjudication_path, staging / "adjudication.returned.jsonl")
        shutil.copy2(raw_attestation_path, staging / "adjudicator-attestation.returned.json")
        shutil.copy2(approval_path, staging / "human-approval-clarification.json")
        shutil.copy2(pending / "agreement.json", staging / "agreement.json")
        shutil.copy2(
            pending / "taxonomy-contract-v2.1.json",
            staging / "taxonomy-contract-v2.1.json",
        )
        reviews = staging / "reviews"
        reviews.mkdir()
        for source in sorted((pending / "reviews").iterdir()):
            if source.is_file():
                shutil.copy2(source, reviews / source.name)
        change_report = {
            "schemaVersion": "aishop-customer-service-label-policy-final-report/v1",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED",
            "evidenceTier": EVIDENCE_TIER,
            "caseCount": len(successor_rows),
            "reviewedCaseCount": len(decisions),
            "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
            "adjudicatedCaseCount": agreement["disagreementCaseCount"],
            "caseAgreementRate": agreement["caseAgreementRate"],
            "changedCaseCount": len(changed),
            "unchangedReviewedCaseCount": len(decisions) - len(changed),
            "changedFieldCaseCounts": dict(sorted(changed_fields.items())),
            "changedCaseIds": [item["id"] for item in changed],
            "successorDatasetSha256": dataset_sha,
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
            "limitations": [
                "Humans retained final decision authority, while AI assisted text output and recording.",
                "This is a corrected project development dataset, not an external unseen test set.",
                "Agreement measures reviewer reliability and does not itself measure system accuracy.",
            ],
        }
        _write_json(staging / "final-report.json", change_report)
        _write_text(staging / "final-report.md", _render_label_summary(change_report))

        http_report_path = (
            agent
            / "evaluation-evidence/benchmarks/customer-service/"
            "customer-service-http-v43-human-v2-routing-execution-fix-20260826/report.json"
        )
        http_report = _load_json(http_report_path)
        predictions = {
            str(case["caseId"]): copy.deepcopy((case.get("http") or {}).get("prediction") or {})
            for case in http_report.get("cases") or []
        }
        if set(predictions) != {str(row["id"]) for row in successor_rows}:
            raise ValueError("HTTP prediction coverage differs from successor dataset")
        routing_rescore = evaluate_predictions(
            successor_rows,
            predictions,
            provenance={
                "datasetPath": str(dataset_output.relative_to(repo_root)),
                "datasetSha256": dataset_sha,
                "predictionSourcePath": str(http_report_path.relative_to(repo_root)),
                "predictionSourceSha256": _sha256(http_report_path),
                "predictionProjection": "cases[].http.prediction",
                "postHocLabelRescore": True,
                "evidenceTier": EVIDENCE_TIER,
            },
        )
        routing_rescore["evidenceTier"] = EVIDENCE_TIER
        routing_rescore["humanReviewPlan"]["note"] = (
            "Humans retained final label authority; AI assisted text output and recording. "
            "The frozen v43 predictions are rescored without rerunning the system."
        )
        routing_rescore["limitations"] = [
            "The labels are human-approved with disclosed AI assistance, not unaided human-authored text.",
            "This post-hoc rescore uses frozen v43 predictions and the corrected development set; it is not external unseen performance.",
            *list(routing_rescore.get("limitations") or [])[1:],
        ]
        _write_json(staging / "routing-rescore.json", routing_rescore)
        _write_jsonl(staging / "routing-badcases.jsonl", routing_rescore["badcases"])

        evidence_manifest = {
            "schemaVersion": "aishop-customer-service-label-policy-final-evidence/v1",
            "kind": "customer-service-label-policy-human-approved-review",
            "status": change_report["status"],
            "evidenceTier": EVIDENCE_TIER,
            "caseCount": len(successor_rows),
            "reviewedCaseCount": len(decisions),
            "changedCaseCount": len(changed),
            "successorDatasetSha256": dataset_sha,
            "routingPredictionSourceSha256": _sha256(http_report_path),
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
            "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
            "files": _inventory(
                staging, excluded={"evidence-manifest.json", "SHA256SUMS"}
            ),
        }
        _write_json(staging / "evidence-manifest.json", evidence_manifest)
        _write_text(staging / "SHA256SUMS", _sums(staging))
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        _verify_sums(staging)
        if _load_json(staging / "evidence-manifest.json").get("files") != _inventory(
            staging, excluded={"evidence-manifest.json", "SHA256SUMS"}
        ):
            raise ValueError("label evidence manifest inventory differs")
        _verify_read_only(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    dataset_output.parent.mkdir(parents=True, exist_ok=True)
    dataset_output.write_bytes((output_dir / "successor-dataset.jsonl").read_bytes())
    dataset_sidecar = dataset_output.with_suffix(dataset_output.suffix + ".manifest.json")
    dataset_sidecar.write_bytes(
        (output_dir / "successor-dataset.manifest.json").read_bytes()
    )
    if _sha256(dataset_output) != _sha256(output_dir / "successor-dataset.jsonl"):
        raise ValueError("published successor dataset hash mismatch")
    return {
        "output": str(output_dir),
        "sha256SumsSha256": _sha256(output_dir / "SHA256SUMS"),
        "successorDataset": str(dataset_output),
        "successorDatasetSha256": _sha256(dataset_output),
        "reviewedCaseCount": len(decisions),
        "changedCaseCount": len(changed),
        "routingMetrics": _load_json(output_dir / "routing-rescore.json")["metrics"],
    }


def _publish_approval_provenance(
    *,
    repo_root: Path,
    approval_path: Path,
    intake_archive: Path,
    raw_answer: Path,
    raw_answer_attestation: Path,
    raw_label: Path,
    raw_label_attestation: Path,
    answer_output: Path,
    label_output: Path,
    answer_normalization: Mapping[str, Any],
    label_normalization: Mapping[str, Any],
    normalized_answer_sha: str,
    normalized_label_sha: str,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    try:
        shutil.copy2(approval_path, staging / "human-approval-clarification.json")
        raw = staging / "raw-return-bindings"
        raw.mkdir()
        for name, source in (
            ("answer-adjudication.returned.jsonl", raw_answer),
            ("answer-attestation.returned.json", raw_answer_attestation),
            ("label-adjudication.returned.jsonl", raw_label),
            ("label-attestation.returned.json", raw_label_attestation),
        ):
            shutil.copy2(source, raw / name)
        normalization = {
            "schemaVersion": "aishop-human-approved-ai-assisted-normalization/v1",
            "evidenceTier": EVIDENCE_TIER,
            "policy": (
                "Preserve returned bytes; processing copies replace only the machine identity "
                "in adjudicator with the project-owner-confirmed human final approver ID."
            ),
            "answerQuality": {
                **dict(answer_normalization),
                "rawSha256": _sha256(raw_answer),
                "normalizedSha256": normalized_answer_sha,
            },
            "labelPolicy": {
                **dict(label_normalization),
                "rawSha256": _sha256(raw_label),
                "normalizedSha256": normalized_label_sha,
            },
        }
        _write_json(staging / "normalization.json", normalization)
        bindings = {
            "schemaVersion": "aishop-human-approval-evidence-bindings/v1",
            "status": "HUMAN_APPROVED_AI_ASSISTED_FINALIZED",
            "evidenceTier": EVIDENCE_TIER,
            "approvalClarification": {
                "path": str(approval_path.relative_to(repo_root)),
                "sha256": _sha256(approval_path),
            },
            "exactReturnArchive": {
                "path": str(intake_archive.relative_to(repo_root)),
                "sha256SumsSha256": _sha256(intake_archive / "SHA256SUMS"),
            },
            "answerQualityEvidence": {
                "path": str(answer_output.relative_to(repo_root)),
                "sha256SumsSha256": _sha256(answer_output / "SHA256SUMS"),
                "finalReportSha256": _sha256(answer_output / "final-report.json"),
            },
            "labelPolicyEvidence": {
                "path": str(label_output.relative_to(repo_root)),
                "sha256SumsSha256": _sha256(label_output / "SHA256SUMS"),
                "finalReportSha256": _sha256(label_output / "final-report.json"),
                "successorDatasetSha256": _sha256(
                    label_output / "successor-dataset.jsonl"
                ),
            },
            "pureHumanUnaidedClaim": False,
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
        }
        _write_json(staging / "bindings.json", bindings)
        _write_text(
            staging / "README.md",
            "# Human-approval and AI-assistance provenance\n\n"
            "This package binds the exact returned bytes, the project owner's human-approval "
            "clarification, identity-only normalization, and both finalized evidence packages. "
            "It supports a human-approved/AI-assisted claim, not an unaided-human or external-unseen claim.\n",
        )
        manifest = {
            "schemaVersion": "aishop-human-approval-ai-assistance-provenance/v1",
            "status": bindings["status"],
            "evidenceTier": EVIDENCE_TIER,
            "readOnly": True,
            "createdAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            ),
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
        "output": str(output_dir),
        "sha256SumsSha256": _sha256(output_dir / "SHA256SUMS"),
    }


def finalize(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    agent = repo_root / "AI_Shop-backend/AI_Shop-agent"
    sys.path.insert(0, str(agent))
    from evaluation.core.io import atomic_write_jsonl
    from evaluation.customer_service_answer_review import (
        merge_answer_reviews,
        verify_answer_review_evidence,
        write_answer_review_evidence,
    )

    approval_path = (
        repo_root
        / "deliverables/human-review/HUMAN-APPROVAL-CLARIFICATION-20260827.json"
    )
    approval = _load_json(approval_path)
    if approval.get("evidenceTier") != EVIDENCE_TIER:
        raise ValueError("approval evidence tier is invalid")
    human_approver_id = str(approval.get("humanApproverId") or "").strip()
    if not human_approver_id:
        raise ValueError("human approver ID is missing")
    for descriptor in (approval.get("inScopeReturnedFiles") or {}).values():
        path = repo_root / str(descriptor.get("path") or "")
        if not path.is_file() or _sha256(path) != descriptor.get("sha256"):
            raise ValueError(f"approval binding is invalid: {path}")

    intake_archive = (
        agent
        / "evaluation-evidence/intake-archive/human-review-adjudication-returns-20260827"
    )
    intake = _load_json(intake_archive / "intake-audit.json")
    if (
        intake.get("status") != "HUMAN_APPROVED_AI_ASSISTED_INTAKE_ACCEPTED"
        or intake.get("humanApprovedAiAssistedEvidenceAccepted") is not True
    ):
        raise ValueError("adjudication intake has not passed human-approved validation")
    _verify_sums(intake_archive)
    _verify_read_only(intake_archive)

    answer_pending = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v43-answer-review-pending-adjudication-20260827"
    )
    label_pending = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-human-v2-label-policy-review-pending-adjudication-20260827"
    )
    raw_answer = (
        repo_root
        / "holdout/AI-Shop-answer-quality-adjudicator-c-20260827-completed/adjudication.open.jsonl"
    )
    raw_answer_attestation = (
        repo_root
        / "holdout/AI-Shop-answer-quality-adjudicator-c-20260827-completed/adjudicator-attestation.template.json"
    )
    raw_label = (
        repo_root
        / "holdout/AI-Shop-label-policy-adjudicator-c-20260827/adjudication.open.jsonl"
    )
    raw_label_attestation = (
        repo_root
        / "holdout/AI-Shop-label-policy-adjudicator-c-20260827/adjudicator-attestation.template.json"
    )
    normalized_answer, answer_normalization = _normalize_adjudication(
        _load_jsonl(raw_answer), human_approver_id=human_approver_id
    )
    normalized_label, label_normalization = _normalize_adjudication(
        _load_jsonl(raw_label), human_approver_id=human_approver_id
    )

    answer_output = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827"
    )
    label_output = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827"
    )
    provenance_output = (
        agent
        / "evaluation-evidence/intake-archive/"
        "human-review-human-approval-ai-assistance-provenance-20260827"
    )
    dataset_output = (
        agent
        / "evaluation/datasets/customer_service/adjudicated/"
        "customer-service-human-v2.1-human-approved-ai-assisted.jsonl"
    )
    for path in (answer_output, label_output, provenance_output, dataset_output):
        if path.exists():
            raise FileExistsError(path)

    with tempfile.TemporaryDirectory(prefix="aishop-human-approved-finalization-") as temp:
        temporary = Path(temp)
        normalized_answer_path = temporary / "answer-adjudication.normalized.jsonl"
        normalized_label_path = temporary / "label-adjudication.normalized.jsonl"
        atomic_write_jsonl(normalized_answer_path, normalized_answer)
        atomic_write_jsonl(normalized_label_path, normalized_label)
        answer_normalized_sha = _sha256(normalized_answer_path)
        label_normalized_sha = _sha256(normalized_label_path)

        label_result = _publish_label_policy_final(
            repo_root=repo_root,
            agent=agent,
            pending=label_pending,
            raw_adjudication_path=raw_label,
            raw_attestation_path=raw_label_attestation,
            normalized_rows=normalized_label,
            approval_path=approval_path,
            human_approver_id=human_approver_id,
            output_dir=label_output,
            dataset_output=dataset_output,
        )

        report_path = (
            agent
            / "evaluation-evidence/benchmarks/customer-service/"
            "customer-service-http-v43-human-v2-routing-execution-fix-20260826/report.json"
        )
        review_a = answer_pending / "reviews/reviewer-a.sealed.jsonl"
        review_b = answer_pending / "reviews/reviewer-b.sealed.jsonl"
        final_report, final_agreement = merge_answer_reviews(
            report_path,
            review_a,
            review_b,
            adjudication_path=normalized_answer_path,
        )
        final_report["evidenceTier"] = EVIDENCE_TIER
        final_report["humanApprovalClarificationSha256"] = _sha256(approval_path)
        final_report["rawAdjudicationSha256"] = _sha256(raw_answer)
        final_report["reviewEvidence"]["humanApproval"] = {
            "evidenceTier": EVIDENCE_TIER,
            "humanApproverId": human_approver_id,
            "clarificationSha256": _sha256(approval_path),
            "rawAdjudicationSha256": _sha256(raw_answer),
            "identityOnlyNormalization": True,
        }
        final_report["limitations"].append(
            "Review labels are human-approved with disclosed AI text/recording assistance; "
            "the evidence does not claim unaided human authorship."
        )
        answer_verification = write_answer_review_evidence(
            final_report,
            final_agreement,
            review_a_path=review_a,
            review_b_path=review_b,
            adjudication_path=normalized_answer_path,
            output_dir=answer_output,
        )
        verify_answer_review_evidence(answer_output)

        provenance_result = _publish_approval_provenance(
            repo_root=repo_root,
            approval_path=approval_path,
            intake_archive=intake_archive,
            raw_answer=raw_answer,
            raw_answer_attestation=raw_answer_attestation,
            raw_label=raw_label,
            raw_label_attestation=raw_label_attestation,
            answer_output=answer_output,
            label_output=label_output,
            answer_normalization=answer_normalization,
            label_normalization=label_normalization,
            normalized_answer_sha=answer_normalized_sha,
            normalized_label_sha=label_normalized_sha,
            output_dir=provenance_output,
        )

    answer_report = _load_json(answer_output / "final-report.json")
    return {
        "valid": True,
        "status": "HUMAN_APPROVED_AI_ASSISTED_FINALIZED",
        "evidenceTier": EVIDENCE_TIER,
        "answerQuality": {
            "output": str(answer_output),
            "verification": answer_verification,
            "metrics": answer_report["metrics"],
            "badcaseCount": len(answer_report["badcases"]),
        },
        "labelPolicy": label_result,
        "approvalProvenance": provenance_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.repo_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
