"""Fail-closed human review for customer-service HTTP answers.

The production-path report contains the model answer and its retrieved sources,
but it is not a semantic quality label.  This module turns that immutable report
into two independently ordered review sheets, seals completed sheets, measures
pre-adjudication agreement, and scores only agreed or explicitly adjudicated
labels.  No model output is allowed to grade itself.
"""

from __future__ import annotations

import copy
import hashlib
import os
import random
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    EVIDENCE_ROOT,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from evaluation.core.metrics import wilson_interval
from evaluation.customer_service_http import HTTP_REPORT_SCHEMA

ANSWER_REVIEW_SCHEMA = "aishop-customer-service-answer-review/v2"
ANSWER_REVIEW_REPORT_SCHEMA = "aishop-customer-service-answer-review-report/v2"
ANSWER_REVIEW_AGREEMENT_SCHEMA = (
    "aishop-customer-service-answer-review-agreement/v1"
)
ANSWER_REVIEW_ADJUDICATION_SCHEMA = (
    "aishop-customer-service-answer-review-adjudication/v1"
)
ANSWER_REVIEW_EVIDENCE_SCHEMA = "aishop-customer-service-answer-review-evidence/v1"
ANSWER_REVIEW_GUIDELINES_VERSION = "customer-service-answer-quality-v1"

_LABEL_FIELDS = (
    "answerCorrect",
    "citationSupport",
    "handoffAppropriate",
    "unsafeAnswer",
)
_CITATION_LABELS = frozenset(
    {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
)
_ROW_FIELDS = frozenset(
    {
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
)
_ADJUDICATION_FIELDS = frozenset(
    {
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
)


class CustomerServiceAnswerReviewError(ValueError):
    """Raised when answer-review evidence is incomplete or inconsistent."""


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _path_label(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _sidecar_path(sheet_path: Path) -> Path:
    return sheet_path.with_suffix(sheet_path.suffix + ".manifest.json")


def _ensure_new(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite answer-review artifact(s): "
            + ", ".join(existing)
        )


def _blank_labels() -> dict[str, Any]:
    return {
        "answerCorrect": None,
        "citationSupport": None,
        "handoffAppropriate": None,
        "unsafeAnswer": None,
    }


def _validate_labels(
    value: Any,
    *,
    label: str,
    require_complete: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_LABEL_FIELDS):
        raise CustomerServiceAnswerReviewError(
            f"{label}: labels must contain exactly {list(_LABEL_FIELDS)}"
        )
    labels = {field: value.get(field) for field in _LABEL_FIELDS}
    if all(value is None for value in labels.values()):
        if require_complete:
            raise CustomerServiceAnswerReviewError(f"{label}: labels are incomplete")
        return labels
    if any(value is None for value in labels.values()):
        raise CustomerServiceAnswerReviewError(
            f"{label}: partially filled labels are not allowed"
        )
    for field in ("answerCorrect", "handoffAppropriate", "unsafeAnswer"):
        if not isinstance(labels[field], bool):
            raise CustomerServiceAnswerReviewError(
                f"{label}: {field} must be boolean"
            )
    citation = str(labels["citationSupport"] or "").strip().upper()
    if citation not in _CITATION_LABELS:
        raise CustomerServiceAnswerReviewError(
            f"{label}: citationSupport is invalid: {citation!r}"
        )
    labels["citationSupport"] = citation
    return labels


def _report_context(
    report_path: Path,
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    report = load_json(report_path)
    if report.get("schemaVersion") != HTTP_REPORT_SCHEMA:
        raise CustomerServiceAnswerReviewError(
            "answer-review source must be a customer-service HTTP report"
        )
    report_sha = sha256_file(report_path)
    cases: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(report.get("cases") or [], 1):
        if not isinstance(value, Mapping):
            raise CustomerServiceAnswerReviewError(
                f"source report case {index} must be an object"
            )
        case_id = str(value.get("caseId") or "")
        if not case_id or case_id in cases:
            raise CustomerServiceAnswerReviewError(
                f"source report case {index} has a missing or duplicate caseId"
            )
        http = value.get("http") if isinstance(value.get("http"), Mapping) else {}
        cases[case_id] = {
            "caseId": case_id,
            "sourceRunId": report.get("runId"),
            "sourceReportSha256": report_sha,
            "message": value.get("message"),
            "answer": http.get("answer") or "",
            "sourceRefs": copy.deepcopy(http.get("sourceRefs") or []),
            "observedHandoff": bool(http.get("handoffObserved")),
        }
    if not cases:
        raise CustomerServiceAnswerReviewError("source report contains no cases")
    return report, report_sha, cases


def _sheet_source_row(
    source: Mapping[str, Any], *, reviewer: str
) -> dict[str, Any]:
    return {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "caseId": source["caseId"],
        "reviewerId": reviewer,
        "guidelinesVersion": ANSWER_REVIEW_GUIDELINES_VERSION,
        "sourceRunId": source["sourceRunId"],
        "sourceReportSha256": source["sourceReportSha256"],
        "message": source["message"],
        "answer": source["answer"],
        "sourceRefs": copy.deepcopy(source["sourceRefs"]),
        "observedHandoff": source["observedHandoff"],
        "labels": _blank_labels(),
        "comment": "",
    }


def export_answer_review_sheet(
    report_path: Path,
    output_path: Path,
    *,
    reviewer_id: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """Export one reviewer-specific, gold-blind answer sheet."""

    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise CustomerServiceAnswerReviewError("reviewer_id is required")
    report, report_sha, cases = _report_context(report_path)
    if seed is None:
        seed_material = f"{reviewer}\0{report_sha}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    output_manifest = _sidecar_path(output_path)
    _ensure_new((output_path, output_manifest))
    ordered = list(cases.values())
    random.Random(seed).shuffle(ordered)
    rows = [_sheet_source_row(source, reviewer=reviewer) for source in ordered]
    atomic_write_jsonl(output_path, rows, overwrite=False)
    manifest = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "artifact": "BLINDED_ANSWER_REVIEW_SHEET",
        "lifecycle": "OPEN",
        "reviewerId": reviewer,
        "guidelinesVersion": ANSWER_REVIEW_GUIDELINES_VERSION,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _path_label(report_path),
        "sourceReportSha256": report_sha,
        "sheetPath": _path_label(output_path),
        "sheetSha256": sha256_file(output_path),
        "caseCount": len(rows),
        "orderSeed": seed,
        "labelSchema": {
            "fields": list(_LABEL_FIELDS),
            "citationSupportValues": sorted(_CITATION_LABELS),
        },
        "blinding": (
            "Expected labels, rule/HTTP intent predictions, quality scores, and "
            "other reviewer labels are omitted"
        ),
        "containsExpectedOrSelfJudgment": False,
        "createdAt": utc_now(),
    }
    atomic_write_json(output_manifest, manifest, overwrite=False)
    return manifest


def _load_answer_review_sheet(
    report_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool,
    check_sheet_hash: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    report, report_sha, sources = _report_context(report_path)
    manifest_path = _sidecar_path(sheet_path)
    if not manifest_path.is_file():
        raise CustomerServiceAnswerReviewError(
            f"answer-review manifest is missing: {manifest_path}"
        )
    manifest = load_json(manifest_path)
    rows = load_jsonl(sheet_path)
    if manifest.get("schemaVersion") != ANSWER_REVIEW_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review manifest schema is invalid")
    lifecycle = str(manifest.get("lifecycle") or "")
    expected_artifact = {
        "OPEN": "BLINDED_ANSWER_REVIEW_SHEET",
        "SEALED": "SEALED_ANSWER_REVIEW_SHEET",
    }.get(lifecycle)
    if not expected_artifact or manifest.get("artifact") != expected_artifact:
        raise CustomerServiceAnswerReviewError(
            "answer-review manifest lifecycle/artifact is invalid"
        )
    if manifest.get("guidelinesVersion") != ANSWER_REVIEW_GUIDELINES_VERSION:
        raise CustomerServiceAnswerReviewError(
            "answer-review guidelinesVersion is invalid"
        )
    if manifest.get("sourceRunId") != report.get("runId"):
        raise CustomerServiceAnswerReviewError("answer-review source run differs")
    if manifest.get("sourceReportSha256") != report_sha:
        raise CustomerServiceAnswerReviewError("answer-review source report hash differs")
    if manifest.get("containsExpectedOrSelfJudgment") is not False:
        raise CustomerServiceAnswerReviewError(
            "answer review must declare no expected labels or self-judgment"
        )
    if check_sheet_hash and manifest.get("sheetSha256") != sha256_file(sheet_path):
        raise CustomerServiceAnswerReviewError(
            "answer-review sheet hash differs from manifest"
        )
    manifest_path_label = str(manifest.get("sheetPath") or "")
    actual_path_label = _path_label(sheet_path)
    if manifest_path_label != actual_path_label:
        relocatable = (
            lifecycle == "SEALED"
            and bool(manifest_path_label)
            and Path(manifest_path_label).name == sheet_path.name
        )
        if not relocatable:
            raise CustomerServiceAnswerReviewError(
                "answer-review manifest sheetPath differs from artifact"
            )
    if manifest.get("caseCount") != len(rows) or len(rows) != len(sources):
        raise CustomerServiceAnswerReviewError(
            "answer-review case count differs from source report"
        )
    reviewer = str(manifest.get("reviewerId") or "").strip()
    if not reviewer:
        raise CustomerServiceAnswerReviewError("answer-review reviewerId is missing")
    reviewed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        label = f"{sheet_path}:{index}"
        if not isinstance(row, Mapping) or set(row) != set(_ROW_FIELDS):
            raise CustomerServiceAnswerReviewError(
                f"{label}: unknown or missing review fields"
            )
        if row.get("schemaVersion") != ANSWER_REVIEW_SCHEMA:
            raise CustomerServiceAnswerReviewError(f"{label}: schema is invalid")
        case_id = str(row.get("caseId") or "")
        if case_id not in sources or case_id in reviewed:
            raise CustomerServiceAnswerReviewError(
                f"{label}: caseId is unknown or duplicated"
            )
        if row.get("reviewerId") != reviewer:
            raise CustomerServiceAnswerReviewError(
                f"{label}: reviewerId differs from manifest"
            )
        if row.get("guidelinesVersion") != ANSWER_REVIEW_GUIDELINES_VERSION:
            raise CustomerServiceAnswerReviewError(
                f"{label}: guidelinesVersion is invalid"
            )
        expected_source = _sheet_source_row(sources[case_id], reviewer=reviewer)
        for field in (
            "sourceRunId",
            "sourceReportSha256",
            "message",
            "answer",
            "sourceRefs",
            "observedHandoff",
        ):
            if _canonical(row.get(field)) != _canonical(expected_source[field]):
                raise CustomerServiceAnswerReviewError(
                    f"{label}: source field {field} differs from immutable HTTP report"
                )
        labels = _validate_labels(
            row.get("labels"),
            label=f"{label}.labels",
            require_complete=require_complete,
        )
        comment = row.get("comment")
        if not isinstance(comment, str):
            raise CustomerServiceAnswerReviewError(f"{label}: comment must be text")
        reviewed[case_id] = {
            "row": dict(row),
            "labels": labels,
            "comment": comment.strip(),
        }
    if set(reviewed) != set(sources):
        raise CustomerServiceAnswerReviewError(
            "answer-review sheet does not cover every source case"
        )
    return dict(manifest), reviewed


def validate_answer_review_sheet(
    report_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Validate source binding and labels; OPEN sheet hashes may change while edited."""

    manifest = load_json(_sidecar_path(sheet_path))
    validated, _ = _load_answer_review_sheet(
        report_path,
        sheet_path,
        require_complete=require_complete,
        check_sheet_hash=manifest.get("lifecycle") != "OPEN",
    )
    return validated


def seal_answer_review_sheet(
    report_path: Path,
    input_sheet_path: Path,
    output_sheet_path: Path,
) -> dict[str, Any]:
    """Create a new immutable-input sheet after a reviewer completes every label."""

    source_manifest, reviewed = _load_answer_review_sheet(
        report_path,
        input_sheet_path,
        require_complete=True,
        check_sheet_hash=False,
    )
    if source_manifest.get("lifecycle") != "OPEN":
        raise CustomerServiceAnswerReviewError(
            "seal accepts only OPEN answer-review sheets"
        )
    output_manifest_path = _sidecar_path(output_sheet_path)
    _ensure_new((output_sheet_path, output_manifest_path))
    input_rows = load_jsonl(input_sheet_path)
    rows_by_id = {case_id: item["row"] for case_id, item in reviewed.items()}
    ordered_rows = [rows_by_id[str(row["caseId"])] for row in input_rows]
    atomic_write_jsonl(output_sheet_path, ordered_rows, overwrite=False)
    manifest = {
        **source_manifest,
        "artifact": "SEALED_ANSWER_REVIEW_SHEET",
        "lifecycle": "SEALED",
        "sheetPath": _path_label(output_sheet_path),
        "sheetSha256": sha256_file(output_sheet_path),
        "sourceOpenSheetPath": _path_label(input_sheet_path),
        "sourceOpenSheetSha256": sha256_file(input_sheet_path),
        "openSheetSha256AtExport": source_manifest["sheetSha256"],
        "sealedAt": utc_now(),
    }
    atomic_write_json(output_manifest_path, manifest, overwrite=False)
    return manifest


def _ratio_metric(
    numerator: int,
    denominator: int,
    *,
    badcase_ids: Sequence[str],
    lower_is_better: bool = False,
) -> dict[str, Any]:
    if denominator <= 0:
        return {
            "status": "UNAVAILABLE",
            "value": None,
            "numerator": numerator,
            "denominator": denominator,
            "confidenceInterval95": None,
            "badcaseCount": len(set(badcase_ids)),
            "badcaseIds": list(dict.fromkeys(badcase_ids)),
            "lowerIsBetter": lower_is_better,
        }
    lower, upper = wilson_interval(numerator, denominator)
    unique_badcases = list(dict.fromkeys(badcase_ids))
    return {
        "status": "MEASURED",
        "value": round(numerator / denominator, 6),
        "numerator": numerator,
        "denominator": denominator,
        "confidenceInterval95": {
            "lower": round(lower, 6),
            "upper": round(upper, 6),
            "method": "wilson",
            "confidenceLevel": 0.95,
        },
        "badcaseCount": len(unique_badcases),
        "badcaseIds": unique_badcases,
        "lowerIsBetter": lower_is_better,
    }


def _score_final_labels(
    sources: Mapping[str, Mapping[str, Any]],
    labels_by_id: Mapping[str, Mapping[str, Any]],
    comments_by_id: Mapping[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = Counter()
    metric_badcases: dict[str, list[str]] = {
        field: []
        for field in (
            "answerCorrectness",
            "citationGroundingSupport",
            "handoffAppropriateness",
            "unsafeAnswerRate",
            "jointQualityPassRate",
        )
    }
    badcases: list[dict[str, Any]] = []
    for case_id, source in sources.items():
        labels = labels_by_id[case_id]
        answer_ok = labels["answerCorrect"] is True
        citation_label = str(labels["citationSupport"])
        citation_eligible = citation_label != "NOT_APPLICABLE"
        citation_ok = citation_label in {"SUPPORTED", "NOT_APPLICABLE"}
        handoff_ok = labels["handoffAppropriate"] is True
        unsafe = labels["unsafeAnswer"] is True
        joint_ok = answer_ok and citation_ok and handoff_ok and not unsafe
        counts["total"] += 1
        counts["answerCorrect"] += int(answer_ok)
        counts["citationEligible"] += int(citation_eligible)
        counts["citationSupported"] += int(citation_label == "SUPPORTED")
        counts["citationUndecidable"] += int(citation_label == "UNDECIDABLE")
        counts["handoffAppropriate"] += int(handoff_ok)
        counts["unsafeAnswer"] += int(unsafe)
        counts["jointPass"] += int(joint_ok)
        failures: list[str] = []
        for metric, passed in (
            ("answerCorrectness", answer_ok),
            ("citationGroundingSupport", citation_ok),
            ("handoffAppropriateness", handoff_ok),
            ("unsafeAnswerRate", not unsafe),
            ("jointQualityPassRate", joint_ok),
        ):
            if not passed:
                metric_badcases[metric].append(case_id)
                failures.append(metric)
        if failures:
            badcases.append(
                {
                    "caseId": case_id,
                    "message": source.get("message"),
                    "answerSha256": sha256_bytes(
                        str(source.get("answer") or "").encode("utf-8")
                    ),
                    "failedMetrics": failures,
                    "labels": dict(labels),
                    "comment": comments_by_id.get(case_id, ""),
                }
            )
    total = counts["total"]
    metrics = {
        "answerCorrectness": _ratio_metric(
            counts["answerCorrect"],
            total,
            badcase_ids=metric_badcases["answerCorrectness"],
        ),
        "citationGroundingSupport": _ratio_metric(
            counts["citationSupported"],
            counts["citationEligible"],
            badcase_ids=metric_badcases["citationGroundingSupport"],
        ),
        "handoffAppropriateness": _ratio_metric(
            counts["handoffAppropriate"],
            total,
            badcase_ids=metric_badcases["handoffAppropriateness"],
        ),
        "unsafeAnswerRate": _ratio_metric(
            counts["unsafeAnswer"],
            total,
            badcase_ids=metric_badcases["unsafeAnswerRate"],
            lower_is_better=True,
        ),
        "jointQualityPassRate": _ratio_metric(
            counts["jointPass"],
            total,
            badcase_ids=metric_badcases["jointQualityPassRate"],
        ),
        "citationUndecidableCount": counts["citationUndecidable"],
    }
    return metrics, badcases


def score_answer_review(report_path: Path, review_path: Path) -> dict[str, Any]:
    """Score one complete rater pass for diagnostics, never as adjudicated truth."""

    report, report_sha, sources = _report_context(report_path)
    manifest, reviewed = _load_answer_review_sheet(
        report_path,
        review_path,
        require_complete=True,
        check_sheet_hash=load_json(_sidecar_path(review_path)).get("lifecycle")
        != "OPEN",
    )
    labels = {case_id: item["labels"] for case_id, item in reviewed.items()}
    comments = {case_id: item["comment"] for case_id, item in reviewed.items()}
    metrics, badcases = _score_final_labels(sources, labels, comments)
    return {
        "schemaVersion": ANSWER_REVIEW_REPORT_SCHEMA,
        "status": "HUMAN_REVIEWED_SINGLE_RATER",
        "releaseGateEligible": False,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _path_label(report_path),
        "sourceReportSha256": report_sha,
        "reviewPath": _path_label(review_path),
        "reviewSha256": sha256_file(review_path),
        "reviewerIds": [manifest["reviewerId"]],
        "caseCount": len(sources),
        "metrics": metrics,
        "badcases": badcases,
        "limitations": [
            "One rater pass is diagnostic only; it is not dual-review agreement or adjudicated truth.",
            "The Agent/LLM does not grade its own final answer.",
        ],
        "createdAt": utc_now(),
    }


def _categorical_agreement(
    left_values: Sequence[Any], right_values: Sequence[Any]
) -> dict[str, Any]:
    if len(left_values) != len(right_values) or not left_values:
        raise CustomerServiceAnswerReviewError(
            "agreement requires equal non-empty label sequences"
        )
    left = [_canonical(value) for value in left_values]
    right = [_canonical(value) for value in right_values]
    count = len(left)
    agreement_count = sum(a == b for a, b in zip(left, right, strict=True))
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        left_counts[label] * right_counts[label]
        for label in set(left_counts) | set(right_counts)
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


def compare_answer_reviews(
    report_path: Path, review_a_path: Path, review_b_path: Path
) -> dict[str, Any]:
    """Measure agreement between two complete, independently sealed sheets."""

    report, report_sha, sources = _report_context(report_path)
    manifest_a, review_a = _load_answer_review_sheet(
        report_path, review_a_path, require_complete=True, check_sheet_hash=True
    )
    manifest_b, review_b = _load_answer_review_sheet(
        report_path, review_b_path, require_complete=True, check_sheet_hash=True
    )
    if manifest_a.get("lifecycle") != "SEALED" or manifest_b.get("lifecycle") != "SEALED":
        raise CustomerServiceAnswerReviewError(
            "agreement comparison accepts only SEALED answer-review sheets"
        )
    reviewer_a = str(manifest_a["reviewerId"])
    reviewer_b = str(manifest_b["reviewerId"])
    if reviewer_a == reviewer_b:
        raise CustomerServiceAnswerReviewError(
            "answer reviewers must have different stable IDs"
        )
    field_values_a: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    field_values_b: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    disagreements: list[dict[str, Any]] = []
    for case_id, source in sources.items():
        left = review_a[case_id]["labels"]
        right = review_b[case_id]["labels"]
        fields = []
        for field in _LABEL_FIELDS:
            field_values_a[field].append(left[field])
            field_values_b[field].append(right[field])
            if _canonical(left[field]) != _canonical(right[field]):
                fields.append(field)
        if fields:
            disagreements.append(
                {
                    **copy.deepcopy(dict(source)),
                    "fields": fields,
                    "reviewerA": {
                        "reviewerId": reviewer_a,
                        "labels": copy.deepcopy(left),
                        "comment": review_a[case_id]["comment"],
                    },
                    "reviewerB": {
                        "reviewerId": reviewer_b,
                        "labels": copy.deepcopy(right),
                        "comment": review_b[case_id]["comment"],
                    },
                }
            )
    field_stats = {
        field: _categorical_agreement(field_values_a[field], field_values_b[field])
        for field in _LABEL_FIELDS
    }
    exact_count = len(sources) - len(disagreements)
    return {
        "schemaVersion": ANSWER_REVIEW_AGREEMENT_SCHEMA,
        "status": (
            "AGREED_NO_ADJUDICATION"
            if not disagreements
            else "PENDING_ADJUDICATION"
        ),
        "releaseGateEligible": False,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _path_label(report_path),
        "sourceReportSha256": report_sha,
        "reviewA": {
            "path": _path_label(review_a_path),
            "sha256": manifest_a["sheetSha256"],
            "reviewerId": reviewer_a,
        },
        "reviewB": {
            "path": _path_label(review_b_path),
            "sha256": manifest_b["sheetSha256"],
            "reviewerId": reviewer_b,
        },
        "caseCount": len(sources),
        "exactAgreementCaseCount": exact_count,
        "disagreementCaseCount": len(disagreements),
        "caseAgreementRate": round(exact_count / len(sources), 6),
        "fieldStats": field_stats,
        "disagreements": disagreements,
        "createdAt": utc_now(),
        "note": (
            "Inter-rater agreement measures annotation reliability, not model accuracy. "
            "Disagreements require a third-person decision."
        ),
    }


def export_answer_adjudication_template(
    agreement: Mapping[str, Any], output_path: Path
) -> dict[str, Any]:
    """Write only disagreement cases for a third person to resolve."""

    if agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review agreement schema is invalid")
    _ensure_new((output_path,))
    rows = []
    for item in agreement.get("disagreements") or []:
        rows.append(
            {
                "schemaVersion": ANSWER_REVIEW_ADJUDICATION_SCHEMA,
                "caseId": item.get("caseId"),
                "sourceRunId": agreement.get("sourceRunId"),
                "sourceReportSha256": agreement.get("sourceReportSha256"),
                "message": item.get("message"),
                "answer": item.get("answer"),
                "sourceRefs": item.get("sourceRefs") or [],
                "observedHandoff": bool(item.get("observedHandoff")),
                "reviewerA": item.get("reviewerA"),
                "reviewerB": item.get("reviewerB"),
                "finalLabels": _blank_labels(),
                "adjudicator": "",
                "reason": "",
            }
        )
    atomic_write_jsonl(output_path, rows, overwrite=False)
    return {
        "schemaVersion": ANSWER_REVIEW_ADJUDICATION_SCHEMA,
        "status": "OPEN" if rows else "NOT_REQUIRED",
        "sourceReportSha256": agreement.get("sourceReportSha256"),
        "caseCount": len(rows),
        "path": _path_label(output_path),
        "sha256AtExport": sha256_file(output_path),
    }


def _load_adjudications(
    path: Path,
    *,
    agreement: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    disagreement_by_id = {
        str(item["caseId"]): item for item in agreement.get("disagreements") or []
    }
    rows = load_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    reviewers = {
        str((agreement.get("reviewA") or {}).get("reviewerId") or ""),
        str((agreement.get("reviewB") or {}).get("reviewerId") or ""),
    }
    for index, row in enumerate(rows, 1):
        label = f"{path}:{index}"
        if not isinstance(row, Mapping) or set(row) != set(_ADJUDICATION_FIELDS):
            raise CustomerServiceAnswerReviewError(
                f"{label}: unknown or missing adjudication fields"
            )
        if row.get("schemaVersion") != ANSWER_REVIEW_ADJUDICATION_SCHEMA:
            raise CustomerServiceAnswerReviewError(f"{label}: schema is invalid")
        case_id = str(row.get("caseId") or "")
        if case_id not in disagreement_by_id or case_id in result:
            raise CustomerServiceAnswerReviewError(
                f"{label}: adjudication caseId is invalid or duplicated"
            )
        source = disagreement_by_id[case_id]
        for field in (
            "sourceRunId",
            "sourceReportSha256",
            "message",
            "answer",
            "sourceRefs",
            "observedHandoff",
            "reviewerA",
            "reviewerB",
        ):
            expected = (
                agreement.get(field)
                if field in {"sourceRunId", "sourceReportSha256"}
                else source.get(field)
            )
            if _canonical(row.get(field)) != _canonical(expected):
                raise CustomerServiceAnswerReviewError(
                    f"{label}: adjudication source field {field} was modified"
                )
        adjudicator = str(row.get("adjudicator") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not adjudicator or not reason:
            raise CustomerServiceAnswerReviewError(
                f"{label}: adjudicator and reason are required"
            )
        if adjudicator in reviewers:
            raise CustomerServiceAnswerReviewError(
                f"{label}: adjudicator must be independent from both reviewers"
            )
        result[case_id] = {
            "labels": _validate_labels(
                row.get("finalLabels"),
                label=f"{label}.finalLabels",
                require_complete=True,
            ),
            "adjudicator": adjudicator,
            "reason": reason,
            "row": dict(row),
        }
    if set(result) != set(disagreement_by_id):
        missing = sorted(set(disagreement_by_id) - set(result))
        raise CustomerServiceAnswerReviewError(
            f"unresolved answer-review disagreements: {missing}"
        )
    return result


def merge_answer_reviews(
    report_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    *,
    adjudication_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return adjudicated answer metrics plus the pre-adjudication agreement."""

    report, report_sha, sources = _report_context(report_path)
    agreement = compare_answer_reviews(report_path, review_a_path, review_b_path)
    _, review_a = _load_answer_review_sheet(
        report_path, review_a_path, require_complete=True, check_sheet_hash=True
    )
    _, review_b = _load_answer_review_sheet(
        report_path, review_b_path, require_complete=True, check_sheet_hash=True
    )
    disagreement_ids = {
        str(item["caseId"]) for item in agreement.get("disagreements") or []
    }
    if disagreement_ids:
        if adjudication_path is None:
            raise CustomerServiceAnswerReviewError(
                "reviewer disagreements require a third-person adjudication file"
            )
        adjudications = _load_adjudications(
            adjudication_path, agreement=agreement
        )
    else:
        if adjudication_path is not None and load_jsonl(adjudication_path):
            raise CustomerServiceAnswerReviewError(
                "adjudication is only allowed for reviewer disagreements"
            )
        adjudications = {}

    labels_by_id: dict[str, dict[str, Any]] = {}
    comments_by_id: dict[str, str] = {}
    cases = []
    for case_id, source in sources.items():
        if case_id in disagreement_ids:
            decision = adjudications[case_id]
            labels = decision["labels"]
            label_source = "ADJUDICATED"
            comment = decision["reason"]
            adjudicator = decision["adjudicator"]
        else:
            labels = review_a[case_id]["labels"]
            label_source = "REVIEWER_AGREEMENT"
            comments = [
                value
                for value in (
                    review_a[case_id]["comment"],
                    review_b[case_id]["comment"],
                )
                if value
            ]
            comment = " | ".join(comments)
            adjudicator = None
        labels_by_id[case_id] = copy.deepcopy(labels)
        comments_by_id[case_id] = comment
        cases.append(
            {
                "caseId": case_id,
                "labels": copy.deepcopy(labels),
                "labelSource": label_source,
                "adjudicator": adjudicator,
                "comment": comment,
                "answerSha256": sha256_bytes(
                    str(source.get("answer") or "").encode("utf-8")
                ),
            }
        )
    metrics, badcases = _score_final_labels(sources, labels_by_id, comments_by_id)
    final_report = {
        "schemaVersion": ANSWER_REVIEW_REPORT_SCHEMA,
        "status": "HUMAN_REVIEWED_ADJUDICATED",
        "releaseGateEligible": False,
        "selfJudged": False,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _path_label(report_path),
        "sourceReportSha256": report_sha,
        "reviewEvidence": {
            "reviewA": dict(agreement["reviewA"]),
            "reviewB": dict(agreement["reviewB"]),
            "adjudicationPath": (
                _path_label(adjudication_path) if adjudication_path else None
            ),
            "adjudicationSha256": (
                sha256_file(adjudication_path) if adjudication_path else None
            ),
            "guidelinesVersion": ANSWER_REVIEW_GUIDELINES_VERSION,
        },
        "caseCount": len(sources),
        "agreement": {
            "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
            "disagreementCaseCount": agreement["disagreementCaseCount"],
            "caseAgreementRate": agreement["caseAgreementRate"],
            "fieldStats": agreement["fieldStats"],
        },
        "metrics": metrics,
        "badcases": badcases,
        "cases": cases,
        "limitations": [
            "These labels score the frozen 60-case HTTP replay only; they are not CSAT, FCR, or online success rate.",
            "The review is bound to one immutable answer/source report and does not generalize to future Provider outputs.",
            "Release thresholds were not predeclared for this already-captured run, so the result remains quality evidence rather than a retroactive gate.",
        ],
        "createdAt": utc_now(),
    }
    return final_report, agreement


def render_answer_agreement_markdown(agreement: Mapping[str, Any]) -> str:
    lines = [
        "# 客服 HTTP 答案双人工一致性",
        "",
        "> 此处衡量标注可靠性，不是模型准确率。",
        "",
        f"案件级完全一致：`{agreement.get('exactAgreementCaseCount')}/{agreement.get('caseCount')}`；"
        f"一致率：`{agreement.get('caseAgreementRate')}`；待仲裁：`{agreement.get('disagreementCaseCount')}`。",
        "",
        "| 字段 | 一致数 | 一致率 | Cohen κ |",
        "|---|---:|---:|---:|",
    ]
    for field in _LABEL_FIELDS:
        stats = (agreement.get("fieldStats") or {}).get(field) or {}
        lines.append(
            f"| `{field}` | {stats.get('agreementCount')}/{stats.get('caseCount')} | "
            f"{stats.get('agreementRate')} | {stats.get('cohenKappa')} |"
        )
    lines.extend(["", "## 分歧 Badcase", "", "| Case | 字段 | 用户问题 |", "|---|---|---|"])
    for item in agreement.get("disagreements") or []:
        message = str(item.get("message") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{item.get('caseId')}` | `{', '.join(item.get('fields') or [])}` | {message} |"
        )
    return "\n".join(lines) + "\n"


def render_answer_review_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# 客服 HTTP 答案人工质量证据",
        "",
        f"> `{report.get('status')}`；样本 `{report.get('caseCount')}`；不是线上 CSAT/FCR。",
        "",
        "| 指标 | 数值 | 分子/分母 | 95% CI | badcase |",
        "|---|---:|---:|---|---|",
    ]
    labels = {
        "answerCorrectness": "答案正确率",
        "citationGroundingSupport": "引用语义支持率",
        "handoffAppropriateness": "转人工适当率",
        "unsafeAnswerRate": "Unsafe-answer rate（越低越好）",
        "jointQualityPassRate": "联合质量通过率",
    }
    for key, label in labels.items():
        metric = (report.get("metrics") or {}).get(key) or {}
        ci = metric.get("confidenceInterval95") or {}
        interval = (
            f"[{ci.get('lower')}, {ci.get('upper')}]" if ci else "UNAVAILABLE"
        )
        lines.append(
            f"| {label} | {metric.get('value')} | {metric.get('numerator')}/{metric.get('denominator')} | "
            f"{interval} | {', '.join(metric.get('badcaseIds') or []) or '-'} |"
        )
    agreement = report.get("agreement") or {}
    lines.extend(
        [
            "",
            f"双人案件级一致：`{agreement.get('exactAgreementCaseCount')}/{report.get('caseCount')}`；"
            f"仲裁：`{agreement.get('disagreementCaseCount')}`。",
            "",
            "逐项标签、评论和 badcase 位于同包 `final-report.json`/`badcases.jsonl`。",
        ]
    )
    return "\n".join(lines) + "\n"


def _inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _sums(root: Path) -> str:
    rows = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(rows.items()))


def _assert_evidence_boundary(path: Path) -> None:
    resolved = path.resolve()
    for protected in (
        EVIDENCE_ROOT.resolve(),
        (EVIDENCE_ROOT.parent / "archive").resolve(),
    ):
        try:
            resolved.relative_to(protected)
        except ValueError:
            continue
        raise CustomerServiceAnswerReviewError(
            f"answer-review benchmark cannot write inside protected {protected}"
        )


def write_answer_review_evidence(
    final_report: Mapping[str, Any],
    agreement: Mapping[str, Any],
    *,
    review_a_path: Path,
    review_b_path: Path,
    output_dir: Path,
    adjudication_path: Path | None = None,
) -> dict[str, Any]:
    """Atomically publish a self-contained, read-only review package."""

    _assert_evidence_boundary(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite answer-review evidence: {output_dir}")
    if final_report.get("schemaVersion") != ANSWER_REVIEW_REPORT_SCHEMA:
        raise CustomerServiceAnswerReviewError("final answer-review report schema is invalid")
    if agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review agreement schema is invalid")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        atomic_write_json(staging / "final-report.json", final_report, overwrite=False)
        atomic_write_text(
            staging / "final-report.md",
            render_answer_review_markdown(final_report),
            overwrite=False,
        )
        atomic_write_json(staging / "agreement.json", agreement, overwrite=False)
        atomic_write_text(
            staging / "agreement.md",
            render_answer_agreement_markdown(agreement),
            overwrite=False,
        )
        atomic_write_jsonl(
            staging / "badcases.jsonl",
            list(final_report.get("badcases") or []),
            overwrite=False,
        )
        review_dir = staging / "reviews"
        review_dir.mkdir()
        for name, source in (
            ("reviewer-a.sealed.jsonl", review_a_path),
            ("reviewer-b.sealed.jsonl", review_b_path),
        ):
            shutil.copy2(source, review_dir / name)
            shutil.copy2(
                _sidecar_path(source),
                review_dir / f"{name}.manifest.json",
            )
        if adjudication_path is not None:
            shutil.copy2(adjudication_path, review_dir / "adjudication.final.jsonl")
        manifest = {
            "schemaVersion": ANSWER_REVIEW_EVIDENCE_SCHEMA,
            "kind": "customer-service-answer-human-review",
            "status": final_report.get("status"),
            "sourceRunId": final_report.get("sourceRunId"),
            "sourceReportPath": final_report.get("sourceReportPath"),
            "sourceReportSha256": final_report.get("sourceReportSha256"),
            "caseCount": final_report.get("caseCount"),
            "selfJudged": False,
            "releaseGateEligible": False,
            "reviewASha256": sha256_file(review_a_path),
            "reviewBSha256": sha256_file(review_b_path),
            "adjudicationSha256": (
                sha256_file(adjudication_path) if adjudication_path else None
            ),
            "createdAt": utc_now(),
            "files": _inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verify_answer_review_evidence(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_answer_review_evidence(output_dir)


def verify_answer_review_evidence(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    sums_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or not sums_path.is_file():
        raise CustomerServiceAnswerReviewError("answer-review evidence is incomplete")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise CustomerServiceAnswerReviewError(
                f"invalid answer-review SHA256SUMS line: {line!r}"
            )
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CustomerServiceAnswerReviewError(
                "answer-review inventory escapes package"
            ) from exc
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence file set differs from SHA256SUMS"
        )
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise CustomerServiceAnswerReviewError(
                f"answer-review evidence hash mismatch: {name}"
            )
    manifest = load_json(manifest_path)
    final_report = load_json(root / "final-report.json")
    agreement = load_json(root / "agreement.json")
    if manifest.get("schemaVersion") != ANSWER_REVIEW_EVIDENCE_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review evidence schema is invalid")
    if final_report.get("schemaVersion") != ANSWER_REVIEW_REPORT_SCHEMA:
        raise CustomerServiceAnswerReviewError("final answer-review schema is invalid")
    if agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review agreement schema is invalid")
    if (
        manifest.get("sourceRunId") != final_report.get("sourceRunId")
        or manifest.get("sourceReportSha256")
        != final_report.get("sourceReportSha256")
        or manifest.get("caseCount") != final_report.get("caseCount")
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence manifest differs from final report"
        )
    if manifest.get("files") != _inventory(root):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence manifest inventory is stale"
        )
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise CustomerServiceAnswerReviewError(
            f"answer-review evidence is writable: {writable}"
        )
    return {
        "verified": True,
        "root": str(root),
        "sourceRunId": manifest.get("sourceRunId"),
        "caseCount": manifest.get("caseCount"),
        "sha256SumsSha256": sha256_file(sums_path),
    }
