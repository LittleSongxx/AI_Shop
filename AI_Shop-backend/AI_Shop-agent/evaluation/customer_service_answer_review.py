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
from evaluation.core.redaction import (
    REDACTION_PROFILE,
    contains_unredacted_sensitive,
    redact,
)
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
ANSWER_REVIEW_PENDING_EVIDENCE_SCHEMA = (
    "aishop-customer-service-answer-review-pending-evidence/v1"
)
ANSWER_REVIEW_PENDING_LIFECYCLE_SCHEMA = (
    "aishop-customer-service-answer-review-pending-lifecycle/v1"
)
ANSWER_REVIEW_GUIDELINES_VERSION = "customer-service-answer-quality-v1"
ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE = "SOURCE_DATASET_MESSAGE_V1"
ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE = "RUNTIME_FIXTURE_AWARE_V1"
_MESSAGE_PROJECTIONS = frozenset(
    {
        ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
    }
)
_PRESENTATION_REDACTION = {
    "profile": REDACTION_PROFILE,
    "projection": "REDACTED_REVIEW_SAFE_FIELDS",
    "fields": ["message", "answer", "sourceRefs", "observedHandoff"],
    "sourceHashBinding": "COMPLETE_SOURCE_REPORT_FILE_BYTES",
    "rawInputPersistedByExporter": False,
}

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


def _presentation_redaction_enabled(manifest: Mapping[str, Any]) -> bool:
    """Return the source projection mode declared by a review artifact.

    Earlier v2 sheets are immutable historical evidence and predate this
    marker.  They remain readable in legacy mode.  Every newly exported sheet
    carries the exact descriptor below and is rejected if it is weakened.
    """

    declaration = manifest.get("presentationRedaction")
    if declaration is None:
        return False
    if not isinstance(declaration, Mapping) or _canonical(declaration) != _canonical(
        _PRESENTATION_REDACTION
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review presentation redaction declaration is invalid"
        )
    return True


def _shared_presentation_redaction(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    left_enabled = _presentation_redaction_enabled(left)
    right_enabled = _presentation_redaction_enabled(right)
    if left_enabled != right_enabled:
        raise CustomerServiceAnswerReviewError(
            "answer-review sheets use different presentation redaction modes"
        )
    return left_enabled


def _message_projection(manifest: Mapping[str, Any]) -> str:
    """Read a source-message projection while preserving legacy v2 sheets."""

    projection = str(
        manifest.get("messageProjection") or ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE
    )
    if projection not in _MESSAGE_PROJECTIONS:
        raise CustomerServiceAnswerReviewError(
            "answer-review message projection is invalid"
        )
    return projection


def _shared_message_projection(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    left_projection = _message_projection(left)
    right_projection = _message_projection(right)
    if left_projection != right_projection:
        raise CustomerServiceAnswerReviewError(
            "answer-review sheets use different message projections"
        )
    return left_projection


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


def _review_message(
    value: Mapping[str, Any],
    http: Mapping[str, Any],
    *,
    message_projection: str,
) -> str:
    message = value.get("message")
    if not isinstance(message, str) or not message.strip():
        raise CustomerServiceAnswerReviewError("answer-review source message is invalid")
    if message_projection == ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE:
        return message

    rendered_fields = {
        str(field)
        for field in (http.get("renderedFixtureTemplateFields") or [])
        if isinstance(field, str)
    }
    if "orderId" not in rendered_fields:
        return message
    fixture = http.get("fixtureEvidence")
    if not isinstance(fixture, Mapping) or not fixture:
        raise CustomerServiceAnswerReviewError(
            "fixture-aware answer review requires fixture evidence for a rendered order ID"
        )
    source_order_id = str(fixture.get("sourceOrderId") or "").strip()
    runtime_order_id = str(fixture.get("orderId") or "").strip()
    if not source_order_id or not runtime_order_id:
        raise CustomerServiceAnswerReviewError(
            "fixture-aware answer review requires a source and rendered order ID"
        )
    if source_order_id not in message:
        raise CustomerServiceAnswerReviewError(
            "fixture-aware answer review cannot locate the source order reference"
        )
    return message.replace(source_order_id, runtime_order_id)


def _report_context(
    report_path: Path,
    *,
    presentation_redaction: bool = False,
    message_projection: str = ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    if message_projection not in _MESSAGE_PROJECTIONS:
        raise CustomerServiceAnswerReviewError("answer-review message projection is invalid")
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
        presentation: dict[str, Any] = {
            "caseId": case_id,
            "sourceRunId": report.get("runId"),
            "sourceReportSha256": report_sha,
            "message": _review_message(
                value,
                http,
                message_projection=message_projection,
            ),
            "answer": http.get("answer") or "",
            "sourceRefs": copy.deepcopy(http.get("sourceRefs") or []),
            "observedHandoff": bool(http.get("handoffObserved")),
        }
        if presentation_redaction:
            redacted = redact(presentation)
            if not isinstance(redacted, Mapping) or contains_unredacted_sensitive(redacted):
                raise CustomerServiceAnswerReviewError(
                    "answer-review source projection contains sensitive data"
                )
            presentation = dict(redacted)
        cases[case_id] = presentation
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
    message_projection: str = ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
) -> dict[str, Any]:
    """Export one reviewer-specific, gold-blind answer sheet."""

    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise CustomerServiceAnswerReviewError("reviewer_id is required")
    report, report_sha, cases = _report_context(
        report_path,
        presentation_redaction=True,
        message_projection=message_projection,
    )
    if seed is None:
        seed_material = f"{reviewer}\0{report_sha}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
    output_manifest = _sidecar_path(output_path)
    _ensure_new((output_path, output_manifest))
    ordered = list(cases.values())
    random.Random(seed).shuffle(ordered)
    rows = [_sheet_source_row(source, reviewer=reviewer) for source in ordered]
    if contains_unredacted_sensitive(rows):
        raise CustomerServiceAnswerReviewError(
            "answer-review export contains unredacted sensitive data"
        )
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
        "presentationRedaction": dict(_PRESENTATION_REDACTION),
        "messageProjection": message_projection,
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
    manifest_path = _sidecar_path(sheet_path)
    if not manifest_path.is_file():
        raise CustomerServiceAnswerReviewError(
            f"answer-review manifest is missing: {manifest_path}"
        )
    manifest = load_json(manifest_path)
    rows = load_jsonl(sheet_path)
    presentation_redaction = _presentation_redaction_enabled(manifest)
    message_projection = _message_projection(manifest)
    report, report_sha, sources = _report_context(
        report_path,
        presentation_redaction=presentation_redaction,
        message_projection=message_projection,
    )
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
        # Review sheets are intentionally handed to people outside the runtime
        # workspace and normally return in an intake directory.  The path in an
        # OPEN manifest records where the blank export was created; it is not a
        # security boundary.  Permit a same-named returned copy while retaining
        # the stronger controls below: source-report hash, reviewer identity,
        # complete case set, immutable presentation fields, and (once SEALED)
        # the exact sheet hash all remain mandatory.
        relocatable = (
            lifecycle in {"OPEN", "SEALED"}
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
        if presentation_redaction and contains_unredacted_sensitive(row):
            raise CustomerServiceAnswerReviewError(
                f"{label}: contains unredacted sensitive data"
            )
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

    manifest, reviewed = _load_answer_review_sheet(
        report_path,
        review_path,
        require_complete=True,
        check_sheet_hash=load_json(_sidecar_path(review_path)).get("lifecycle")
        != "OPEN",
    )
    presentation_redaction = _presentation_redaction_enabled(manifest)
    message_projection = _message_projection(manifest)
    report, report_sha, sources = _report_context(
        report_path,
        presentation_redaction=presentation_redaction,
        message_projection=message_projection,
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
        "messageProjection": message_projection,
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
    presentation_redaction = _shared_presentation_redaction(manifest_a, manifest_b)
    message_projection = _shared_message_projection(manifest_a, manifest_b)
    report, report_sha, sources = _report_context(
        report_path,
        presentation_redaction=presentation_redaction,
        message_projection=message_projection,
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
        "presentationRedaction": (
            dict(_PRESENTATION_REDACTION) if presentation_redaction else None
        ),
        "messageProjection": message_projection,
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
    if contains_unredacted_sensitive(agreement):
        raise CustomerServiceAnswerReviewError(
            "answer-review agreement contains unredacted sensitive data"
        )
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
    if contains_unredacted_sensitive(rows):
        raise CustomerServiceAnswerReviewError(
            "answer-review adjudication template contains unredacted sensitive data"
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
    _message_projection(agreement)
    presentation_redaction = agreement.get("presentationRedaction") is not None
    if presentation_redaction and _canonical(
        agreement.get("presentationRedaction")
    ) != _canonical(_PRESENTATION_REDACTION):
        raise CustomerServiceAnswerReviewError(
            "answer-review agreement presentation redaction is invalid"
        )
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
        if presentation_redaction and contains_unredacted_sensitive(row):
            raise CustomerServiceAnswerReviewError(
                f"{label}: contains unredacted sensitive data"
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

    agreement = compare_answer_reviews(report_path, review_a_path, review_b_path)
    manifest_a, review_a = _load_answer_review_sheet(
        report_path, review_a_path, require_complete=True, check_sheet_hash=True
    )
    manifest_b, review_b = _load_answer_review_sheet(
        report_path, review_b_path, require_complete=True, check_sheet_hash=True
    )
    presentation_redaction = _shared_presentation_redaction(manifest_a, manifest_b)
    message_projection = _shared_message_projection(manifest_a, manifest_b)
    report, report_sha, sources = _report_context(
        report_path,
        presentation_redaction=presentation_redaction,
        message_projection=message_projection,
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
    source_evaluation = {
        "status": str(report.get("status") or "UNDECLARED"),
        "releaseGateEligible": report.get("releaseGateEligible") is True,
        "normalQualityDenominatorExcluded": (
            report.get("normalQualityDenominatorExcluded") is True
        ),
    }
    final_report = {
        "schemaVersion": ANSWER_REVIEW_REPORT_SCHEMA,
        "status": "HUMAN_REVIEWED_ADJUDICATED",
        "releaseGateEligible": False,
        "normalQualityDenominatorExcluded": source_evaluation[
            "normalQualityDenominatorExcluded"
        ],
        "selfJudged": False,
        "sourceRunId": report.get("runId"),
        "sourceReportPath": _path_label(report_path),
        "sourceReportSha256": report_sha,
        "sourceEvaluation": source_evaluation,
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
            "presentationRedaction": (
                dict(_PRESENTATION_REDACTION)
                if presentation_redaction
                else None
            ),
            "messageProjection": message_projection,
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
            f"These labels score the frozen {len(sources)}-case HTTP replay only; "
            "they are not CSAT, FCR, or online success rate.",
            *(
                [
                    "The source HTTP observation is explicitly excluded from the "
                    "normal quality denominator."
                ]
                if source_evaluation["normalQualityDenominatorExcluded"]
                else []
            ),
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
    return "\n".join(lines).rstrip("\n") + "\n"


def render_answer_adjudication_needed_markdown(
    agreement: Mapping[str, Any],
    *,
    adjudication_path: str = "adjudication.template.jsonl",
) -> str:
    """Render a concise guide for the independent third reviewer.

    Full frozen answers and sources live in the JSONL template.  Keeping the
    Markdown concise makes the actual points of disagreement easy to scan
    without creating a second editable source of truth.
    """

    if agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA:
        raise CustomerServiceAnswerReviewError("answer-review agreement schema is invalid")
    adjudication_label = str(adjudication_path or "").strip()
    if not adjudication_label:
        raise CustomerServiceAnswerReviewError("adjudication path is required")
    lines = [
        "# 客服 HTTP 答案仲裁清单",
        "",
        f"- 总案件：`{agreement.get('caseCount')}`",
        f"- 完全一致：`{agreement.get('exactAgreementCaseCount')}`",
        f"- 待仲裁：`{agreement.get('disagreementCaseCount')}`",
        f"- 案件级一致率：`{agreement.get('caseAgreementRate')}`",
        "",
        "这份文件只描述双人分歧，不是模型准确率。仲裁者须独立于 "
        "`reviewer-a` 和 `reviewer-b`，只基于冻结的用户问题、最终答案、"
        "`sourceRefs` 与标注规则判断。",
        "",
        f"请编辑单独导出的 `{adjudication_label}`，每行只填写：",
        "- `finalLabels`：四项最终标签，字段和枚举必须完整；",
        "- `adjudicator`：稳定的第三人标识，不能是两位原标注者；",
        "- `reason`：一句到数句可复核理由，说明答案/证据/风险边界。",
        "",
        "不要改写 `caseId`、问题、答案、引用、两位标注结果、源报告哈希或其他字段。"
        "完成后交回该 JSONL；维护者会 fail-closed 校验并生成最终只读证据包。",
        "",
    ]
    for item in agreement.get("disagreements") or []:
        message = str(item.get("message") or "").replace("\n", " ")
        fields = ", ".join(str(field) for field in item.get("fields") or [])
        reviewer_a = item.get("reviewerA") or {}
        reviewer_b = item.get("reviewerB") or {}
        lines.extend(
            [
                f"## {item.get('caseId')}",
                "",
                f"用户问题：{message}",
                "",
                f"分歧字段：`{fields}`",
                "",
                "Reviewer A：",
                "```json",
                _canonical(reviewer_a.get("labels") or {}).decode("utf-8"),
                "```",
                f"备注：{reviewer_a.get('comment') or '无'}",
                "",
                "Reviewer B：",
                "```json",
                _canonical(reviewer_b.get("labels") or {}).decode("utf-8"),
                "```",
                f"备注：{reviewer_b.get('comment') or '无'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip("\n") + "\n"


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


def write_pending_answer_review_evidence(
    report_path: Path,
    agreement: Mapping[str, Any],
    *,
    review_a_path: Path,
    review_b_path: Path,
    output_dir: Path,
    adjudication_output: Path | None = None,
) -> dict[str, Any]:
    """Freeze completed dual review before the third-person adjudication.

    The package is immutable.  When ``adjudication_output`` is supplied, a
    separate editable copy of the blank template is exported after the package
    is sealed, so the third reviewer cannot mutate the evidence package.
    """

    _assert_evidence_boundary(output_dir)
    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite pending answer-review evidence: {output_dir}"
        )
    if adjudication_output is not None:
        _ensure_new((adjudication_output,))
    if (
        agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA
        or agreement.get("status") != "PENDING_ADJUDICATION"
        or int(agreement.get("disagreementCaseCount") or 0) <= 0
    ):
        raise CustomerServiceAnswerReviewError(
            "pending evidence requires a non-empty PENDING_ADJUDICATION agreement"
        )

    manifest_a, _ = _load_answer_review_sheet(
        report_path, review_a_path, require_complete=True, check_sheet_hash=True
    )
    manifest_b, _ = _load_answer_review_sheet(
        report_path, review_b_path, require_complete=True, check_sheet_hash=True
    )
    if manifest_a.get("lifecycle") != "SEALED" or manifest_b.get("lifecycle") != "SEALED":
        raise CustomerServiceAnswerReviewError(
            "pending evidence accepts only SEALED answer-review sheets"
        )
    presentation_redaction = _shared_presentation_redaction(manifest_a, manifest_b)
    message_projection = _shared_message_projection(manifest_a, manifest_b)
    expected_presentation = (
        dict(_PRESENTATION_REDACTION) if presentation_redaction else None
    )
    if _canonical(agreement.get("presentationRedaction")) != _canonical(
        expected_presentation
    ):
        raise CustomerServiceAnswerReviewError(
            "pending agreement presentation redaction differs from sealed sheets"
        )
    if _message_projection(agreement) != message_projection:
        raise CustomerServiceAnswerReviewError(
            "pending agreement message projection differs from sealed sheets"
        )
    report, report_sha, _ = _report_context(
        report_path,
        presentation_redaction=presentation_redaction,
        message_projection=message_projection,
    )
    if (
        agreement.get("sourceRunId") != report.get("runId")
        or agreement.get("sourceReportSha256") != report_sha
    ):
        raise CustomerServiceAnswerReviewError(
            "pending agreement does not bind the supplied HTTP report"
        )
    pending_inputs = [
        agreement,
        load_jsonl(review_a_path),
        load_jsonl(review_b_path),
        load_json(_sidecar_path(review_a_path)),
        load_json(_sidecar_path(review_b_path)),
    ]
    if contains_unredacted_sensitive(pending_inputs):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence contains unredacted sensitive data"
        )
    if (
        manifest_a.get("sheetSha256") != (agreement.get("reviewA") or {}).get("sha256")
        or manifest_b.get("sheetSha256") != (agreement.get("reviewB") or {}).get("sha256")
    ):
        raise CustomerServiceAnswerReviewError(
            "pending agreement reviewer hashes differ from sealed sheets"
        )
    if manifest_a.get("reviewerId") == manifest_b.get("reviewerId"):
        raise CustomerServiceAnswerReviewError(
            "pending evidence requires two independent reviewer IDs"
        )
    frozen_agreement = copy.deepcopy(dict(agreement))
    for key, path in (
        ("reviewA", "reviews/reviewer-a.sealed.jsonl"),
        ("reviewB", "reviews/reviewer-b.sealed.jsonl"),
    ):
        review = frozen_agreement.get(key)
        if not isinstance(review, dict):
            raise CustomerServiceAnswerReviewError(
                f"pending agreement {key} descriptor is invalid"
            )
        # The package contains the sealed sheet, so retain a self-contained
        # provenance reference rather than a caller-owned staging path.
        review["path"] = path

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        atomic_write_json(
            staging / "agreement.json", frozen_agreement, overwrite=False
        )
        atomic_write_text(
            staging / "agreement.md",
            render_answer_agreement_markdown(frozen_agreement),
            overwrite=False,
        )
        template = export_answer_adjudication_template(
            frozen_agreement, staging / "adjudication.template.jsonl"
        )
        adjudication_label = (
            _path_label(adjudication_output)
            if adjudication_output is not None
            else "adjudication.template.jsonl"
        )
        atomic_write_text(
            staging / "adjudication-needed.md",
            render_answer_adjudication_needed_markdown(
                frozen_agreement,
                adjudication_path=adjudication_label,
            ),
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
        lifecycle = {
            "schemaVersion": ANSWER_REVIEW_PENDING_LIFECYCLE_SCHEMA,
            "artifact": "CUSTOMER_SERVICE_ANSWER_REVIEW",
            "lifecycle": "PENDING_ADJUDICATION",
            "releaseGateEligible": False,
            "selfJudged": False,
            "sourceRunId": report.get("runId"),
            "sourceReportPath": _path_label(report_path),
            "sourceReportSha256": report_sha,
            "caseCount": frozen_agreement.get("caseCount"),
            "reviewers": [manifest_a["reviewerId"], manifest_b["reviewerId"]],
            "reviewerASha256": manifest_a["sheetSha256"],
            "reviewerBSha256": manifest_b["sheetSha256"],
            "exactAgreementCaseCount": frozen_agreement.get("exactAgreementCaseCount"),
            "disagreementCaseCount": frozen_agreement.get("disagreementCaseCount"),
            "adjudicationTemplatePath": "adjudication.template.jsonl",
            "adjudicationTemplateSha256AtExport": template["sha256AtExport"],
            "presentationRedaction": expected_presentation,
            "messageProjection": message_projection,
            "createdAt": utc_now(),
            "note": (
                "This is inter-rater reliability evidence only. Final answer-quality "
                "metrics remain unavailable until an independent third reviewer "
                "adjudicates every disagreement."
            ),
        }
        atomic_write_json(staging / "lifecycle.json", lifecycle, overwrite=False)
        manifest = {
            "schemaVersion": ANSWER_REVIEW_PENDING_EVIDENCE_SCHEMA,
            "kind": "customer-service-answer-human-review",
            "status": "PENDING_ADJUDICATION",
            "releaseGateEligible": False,
            "selfJudged": False,
            "sourceRunId": report.get("runId"),
            "sourceReportPath": _path_label(report_path),
            "sourceReportSha256": report_sha,
            "caseCount": frozen_agreement.get("caseCount"),
            "reviewers": {
                "reviewerA": {
                    "reviewerId": manifest_a["reviewerId"],
                    "sealedPath": "reviews/reviewer-a.sealed.jsonl",
                    "sha256": manifest_a["sheetSha256"],
                },
                "reviewerB": {
                    "reviewerId": manifest_b["reviewerId"],
                    "sealedPath": "reviews/reviewer-b.sealed.jsonl",
                    "sha256": manifest_b["sheetSha256"],
                },
            },
            "agreement": {
                "path": "agreement.json",
                "exactAgreementCaseCount": frozen_agreement.get("exactAgreementCaseCount"),
                "disagreementCaseCount": frozen_agreement.get("disagreementCaseCount"),
                "caseAgreementRate": frozen_agreement.get("caseAgreementRate"),
            },
            "adjudicationTemplate": {
                "path": "adjudication.template.jsonl",
                "sha256AtExport": template["sha256AtExport"],
                "caseCount": template["caseCount"],
            },
            "presentationRedaction": expected_presentation,
            "messageProjection": message_projection,
            "createdAt": utc_now(),
            "files": _inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verify_pending_answer_review_evidence(staging)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    editable = None
    if adjudication_output is not None:
        atomic_write_text(
            adjudication_output,
            (output_dir / "adjudication.template.jsonl").read_text(encoding="utf-8"),
            overwrite=False,
        )
        editable = {
            "path": _path_label(adjudication_output),
            "sha256AtExport": sha256_file(adjudication_output),
        }
    return {
        **verify_pending_answer_review_evidence(output_dir),
        "editableAdjudication": editable,
    }


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
    manifest_a = load_json(_sidecar_path(review_a_path))
    manifest_b = load_json(_sidecar_path(review_b_path))
    presentation_redaction = _shared_presentation_redaction(manifest_a, manifest_b)
    message_projection = _shared_message_projection(manifest_a, manifest_b)
    expected_presentation = (
        dict(_PRESENTATION_REDACTION) if presentation_redaction else None
    )
    if (
        _canonical(agreement.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _canonical(
            (final_report.get("reviewEvidence") or {}).get("presentationRedaction")
        )
        != _canonical(expected_presentation)
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review presentation redaction differs from sealed evidence"
        )
    if (
        _message_projection(agreement) != message_projection
        or _message_projection(final_report.get("reviewEvidence") or {})
        != message_projection
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review message projection differs from sealed evidence"
        )
    final_inputs: list[Any] = [
        final_report,
        agreement,
        load_jsonl(review_a_path),
        load_jsonl(review_b_path),
        manifest_a,
        manifest_b,
    ]
    if adjudication_path is not None:
        final_inputs.append(load_jsonl(adjudication_path))
    if contains_unredacted_sensitive(final_inputs):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence contains unredacted sensitive data"
        )
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
            "normalQualityDenominatorExcluded": final_report.get(
                "normalQualityDenominatorExcluded"
            ),
            "sourceEvaluationStatus": (
                final_report.get("sourceEvaluation") or {}
            ).get("status"),
            "reviewASha256": sha256_file(review_a_path),
            "reviewBSha256": sha256_file(review_b_path),
            "adjudicationSha256": (
                sha256_file(adjudication_path) if adjudication_path else None
            ),
            "presentationRedaction": expected_presentation,
            "messageProjection": message_projection,
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


def verify_pending_answer_review_evidence(root: Path) -> dict[str, Any]:
    """Verify the immutable pre-adjudication package and its source bindings."""

    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    sums_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or not sums_path.is_file():
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence is incomplete"
        )
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise CustomerServiceAnswerReviewError(
                f"invalid pending answer-review SHA256SUMS line: {line!r}"
            )
        target = (root / name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CustomerServiceAnswerReviewError(
                "pending answer-review inventory escapes package"
            ) from exc
        expected[name] = digest
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
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if actual != required or set(expected) != required:
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence file set differs from required inventory"
        )
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review evidence hash mismatch: {name}"
            )
    manifest = load_json(manifest_path)
    lifecycle = load_json(root / "lifecycle.json")
    agreement = load_json(root / "agreement.json")
    if (
        manifest.get("schemaVersion") != ANSWER_REVIEW_PENDING_EVIDENCE_SCHEMA
        or manifest.get("status") != "PENDING_ADJUDICATION"
        or manifest.get("releaseGateEligible") is not False
        or manifest.get("selfJudged") is not False
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence manifest is invalid"
        )
    if (
        lifecycle.get("schemaVersion") != ANSWER_REVIEW_PENDING_LIFECYCLE_SCHEMA
        or lifecycle.get("lifecycle") != "PENDING_ADJUDICATION"
        or lifecycle.get("releaseGateEligible") is not False
        or lifecycle.get("selfJudged") is not False
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review lifecycle is invalid"
        )
    if (
        agreement.get("schemaVersion") != ANSWER_REVIEW_AGREEMENT_SCHEMA
        or agreement.get("status") != "PENDING_ADJUDICATION"
        or agreement.get("releaseGateEligible") is not False
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review agreement is invalid"
        )
    for field in ("sourceRunId", "sourceReportPath", "sourceReportSha256", "caseCount"):
        if (
            manifest.get(field) != agreement.get(field)
            or lifecycle.get(field) != agreement.get(field)
        ):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review source field differs: {field}"
            )
    if manifest.get("files") != _inventory(root):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence manifest inventory is stale"
        )

    reviewers = manifest.get("reviewers")
    if not isinstance(reviewers, Mapping) or set(reviewers) != {
        "reviewerA",
        "reviewerB",
    }:
        raise CustomerServiceAnswerReviewError(
            "pending answer-review reviewer inventory is invalid"
        )
    reviewer_ids: list[str] = []
    sheet_manifests: list[dict[str, Any]] = []
    sheet_rows: list[list[dict[str, Any]]] = []
    for label, filename, agreement_key in (
        ("reviewerA", "reviewer-a.sealed.jsonl", "reviewA"),
        ("reviewerB", "reviewer-b.sealed.jsonl", "reviewB"),
    ):
        descriptor = reviewers[label]
        if not isinstance(descriptor, Mapping):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review {label} descriptor is invalid"
            )
        sheet_path = root / "reviews" / filename
        sheet_manifest = load_json(_sidecar_path(sheet_path))
        rows = load_jsonl(sheet_path)
        if not isinstance(sheet_manifest, Mapping):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review {label} manifest is invalid"
            )
        sheet_manifests.append(dict(sheet_manifest))
        sheet_rows.append([dict(row) for row in rows if isinstance(row, Mapping)])
        reviewer_id = str(descriptor.get("reviewerId") or "")
        reviewer_ids.append(reviewer_id)
        if (
            descriptor.get("sealedPath") != f"reviews/{filename}"
            or descriptor.get("sha256") != sha256_file(sheet_path)
            or sheet_manifest.get("schemaVersion") != ANSWER_REVIEW_SCHEMA
            or sheet_manifest.get("artifact") != "SEALED_ANSWER_REVIEW_SHEET"
            or sheet_manifest.get("lifecycle") != "SEALED"
            or sheet_manifest.get("reviewerId") != reviewer_id
            or sheet_manifest.get("sheetSha256") != sha256_file(sheet_path)
            or sheet_manifest.get("sourceRunId") != agreement.get("sourceRunId")
            or sheet_manifest.get("sourceReportSha256")
            != agreement.get("sourceReportSha256")
            or (agreement.get(agreement_key) or {}).get("reviewerId") != reviewer_id
            or (agreement.get(agreement_key) or {}).get("sha256")
            != sha256_file(sheet_path)
            or (agreement.get(agreement_key) or {}).get("path")
            != f"reviews/{filename}"
        ):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review {label} sealed binding is invalid"
            )
        if len(rows) != agreement.get("caseCount"):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review {label} case count is invalid"
            )
        ids: set[str] = set()
        for index, row in enumerate(rows, 1):
            if (
                not isinstance(row, Mapping)
                or set(row) != _ROW_FIELDS
                or row.get("reviewerId") != reviewer_id
                or row.get("schemaVersion") != ANSWER_REVIEW_SCHEMA
                or row.get("guidelinesVersion") != ANSWER_REVIEW_GUIDELINES_VERSION
            ):
                raise CustomerServiceAnswerReviewError(
                    f"pending answer-review {label} row {index} is invalid"
                )
            case_id = str(row.get("caseId") or "")
            if not case_id or case_id in ids:
                raise CustomerServiceAnswerReviewError(
                    f"pending answer-review {label} case IDs are invalid"
                )
            ids.add(case_id)
            _validate_labels(
                row.get("labels"),
                label=f"pending answer-review {label} row {index}.labels",
                require_complete=True,
            )
    if len(set(reviewer_ids)) != 2 or lifecycle.get("reviewers") != reviewer_ids:
        raise CustomerServiceAnswerReviewError(
            "pending answer-review reviewers are not independent"
        )
    presentation_redaction = _shared_presentation_redaction(
        sheet_manifests[0], sheet_manifests[1]
    )
    message_projection = _shared_message_projection(
        sheet_manifests[0], sheet_manifests[1]
    )
    expected_presentation = (
        dict(_PRESENTATION_REDACTION) if presentation_redaction else None
    )
    if (
        _canonical(agreement.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _canonical(manifest.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _canonical(lifecycle.get("presentationRedaction"))
        != _canonical(expected_presentation)
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review presentation redaction is invalid"
        )
    if (
        _message_projection(agreement) != message_projection
        or _message_projection(manifest) != message_projection
        or _message_projection(lifecycle) != message_projection
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review message projection is invalid"
        )

    template_path = root / "adjudication.template.jsonl"
    template_rows = load_jsonl(template_path)
    disagreement_ids = [
        str(item.get("caseId") or "") for item in agreement.get("disagreements") or []
    ]
    if (
        len(template_rows) != agreement.get("disagreementCaseCount")
        or {str(row.get("caseId") or "") for row in template_rows}
        != set(disagreement_ids)
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review adjudication template coverage is invalid"
        )
    for index, row in enumerate(template_rows, 1):
        if (
            not isinstance(row, Mapping)
            or set(row) != _ADJUDICATION_FIELDS
            or row.get("schemaVersion") != ANSWER_REVIEW_ADJUDICATION_SCHEMA
            or row.get("finalLabels") != _blank_labels()
            or row.get("adjudicator") != ""
            or row.get("reason") != ""
        ):
            raise CustomerServiceAnswerReviewError(
                f"pending answer-review adjudication template row {index} is invalid"
            )
    if presentation_redaction and contains_unredacted_sensitive(
        [agreement, manifest, lifecycle, sheet_rows, template_rows]
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review evidence contains unredacted sensitive data"
        )
    template_sha = sha256_file(template_path)
    template_descriptor = manifest.get("adjudicationTemplate") or {}
    if (
        template_descriptor.get("path") != "adjudication.template.jsonl"
        or template_descriptor.get("sha256AtExport") != template_sha
        or template_descriptor.get("caseCount") != len(template_rows)
        or lifecycle.get("adjudicationTemplatePath")
        != "adjudication.template.jsonl"
        or lifecycle.get("adjudicationTemplateSha256AtExport") != template_sha
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review adjudication template binding is invalid"
        )
    if (
        manifest.get("agreement", {}).get("exactAgreementCaseCount")
        != agreement.get("exactAgreementCaseCount")
        or manifest.get("agreement", {}).get("disagreementCaseCount")
        != agreement.get("disagreementCaseCount")
        or manifest.get("agreement", {}).get("caseAgreementRate")
        != agreement.get("caseAgreementRate")
        or lifecycle.get("exactAgreementCaseCount")
        != agreement.get("exactAgreementCaseCount")
        or lifecycle.get("disagreementCaseCount")
        != agreement.get("disagreementCaseCount")
    ):
        raise CustomerServiceAnswerReviewError(
            "pending answer-review agreement summary is invalid"
        )
    writable = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise CustomerServiceAnswerReviewError(
            f"pending answer-review evidence is writable: {writable}"
        )
    return {
        "verified": True,
        "root": str(root),
        "sourceRunId": manifest.get("sourceRunId"),
        "caseCount": manifest.get("caseCount"),
        "disagreementCaseCount": agreement.get("disagreementCaseCount"),
        "sha256SumsSha256": sha256_file(sums_path),
    }


def verify_answer_review_evidence(root: Path) -> dict[str, Any]:
    """Verify an adjudicated package from its sealed inputs, not just its hashes.

    ``SHA256SUMS`` protects a published package from accidental edits.  The
    checks below additionally make a coherently re-hashed package fail closed:
    the final labels must be derived from the two sealed sheets plus any
    independent adjudication, and the reported metrics/badcases must be a
    deterministic score of those final labels.
    """

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
    base_required = {
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
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if not base_required.issubset(actual):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence is missing required files"
        )
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
        manifest.get("kind") != "customer-service-answer-human-review"
        or manifest.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or manifest.get("selfJudged") is not False
        or manifest.get("releaseGateEligible") is not False
        or final_report.get("status") != "HUMAN_REVIEWED_ADJUDICATED"
        or final_report.get("selfJudged") is not False
        or final_report.get("releaseGateEligible") is not False
        or agreement.get("releaseGateEligible") is not False
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence lifecycle is invalid"
        )
    disagreement_count = int(agreement.get("disagreementCaseCount") or 0)
    expected_agreement_status = (
        "PENDING_ADJUDICATION" if disagreement_count else "AGREED_NO_ADJUDICATION"
    )
    if agreement.get("status") != expected_agreement_status:
        raise CustomerServiceAnswerReviewError(
            "answer-review agreement lifecycle is invalid"
        )
    required = set(base_required)
    if disagreement_count:
        required.add("reviews/adjudication.final.jsonl")
    if actual != required:
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence file set does not match adjudication lifecycle"
        )
    if (
        manifest.get("sourceRunId") != final_report.get("sourceRunId")
        or manifest.get("sourceReportPath") != final_report.get("sourceReportPath")
        or manifest.get("sourceReportSha256")
        != final_report.get("sourceReportSha256")
        or manifest.get("caseCount") != final_report.get("caseCount")
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence manifest differs from final report"
        )
    source_evaluation = final_report.get("sourceEvaluation")
    if source_evaluation is not None:
        if (
            not isinstance(source_evaluation, Mapping)
            or set(source_evaluation)
            != {
                "status",
                "releaseGateEligible",
                "normalQualityDenominatorExcluded",
            }
            or not str(source_evaluation.get("status") or "")
            or not isinstance(source_evaluation.get("releaseGateEligible"), bool)
            or not isinstance(
                source_evaluation.get("normalQualityDenominatorExcluded"), bool
            )
            or final_report.get("normalQualityDenominatorExcluded")
            is not source_evaluation.get("normalQualityDenominatorExcluded")
            or manifest.get("normalQualityDenominatorExcluded")
            is not source_evaluation.get("normalQualityDenominatorExcluded")
            or manifest.get("sourceEvaluationStatus")
            != source_evaluation.get("status")
        ):
            raise CustomerServiceAnswerReviewError(
                "answer-review source evaluation boundary is invalid"
            )
    elif (
        "normalQualityDenominatorExcluded" in manifest
        or "sourceEvaluationStatus" in manifest
    ):
        raise CustomerServiceAnswerReviewError(
            "legacy answer-review evidence has unexpected source boundary fields"
        )
    if manifest.get("files") != _inventory(root):
        raise CustomerServiceAnswerReviewError(
            "answer-review evidence manifest inventory is stale"
        )

    source_run_id = str(final_report.get("sourceRunId") or "")
    source_report_sha = str(final_report.get("sourceReportSha256") or "")
    case_count = int(final_report.get("caseCount") or -1)
    if not source_run_id or not source_report_sha or case_count <= 0:
        raise CustomerServiceAnswerReviewError(
            "answer-review final report source binding is invalid"
        )
    for field in ("sourceRunId", "sourceReportPath", "sourceReportSha256", "caseCount"):
        if agreement.get(field) != final_report.get(field):
            raise CustomerServiceAnswerReviewError(
                f"answer-review agreement differs from final report: {field}"
            )

    review_paths = {
        "reviewA": root / "reviews" / "reviewer-a.sealed.jsonl",
        "reviewB": root / "reviews" / "reviewer-b.sealed.jsonl",
    }
    review_rows: dict[str, dict[str, dict[str, Any]]] = {}
    reviewer_ids: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    sheet_manifests: dict[str, dict[str, Any]] = {}
    sheet_rows_for_redaction: dict[str, list[dict[str, Any]]] = {}
    for key, path in review_paths.items():
        sidecar = _sidecar_path(path)
        sheet_manifest = load_json(sidecar)
        rows = load_jsonl(path)
        if not isinstance(sheet_manifest, Mapping):
            raise CustomerServiceAnswerReviewError(
                f"answer-review {key} sealed manifest is invalid"
            )
        sheet_manifests[key] = dict(sheet_manifest)
        sheet_rows_for_redaction[key] = [
            dict(row) for row in rows if isinstance(row, Mapping)
        ]
        sheet_hash = sha256_file(path)
        reviewer_id = str(sheet_manifest.get("reviewerId") or "")
        if (
            sheet_manifest.get("schemaVersion") != ANSWER_REVIEW_SCHEMA
            or sheet_manifest.get("artifact") != "SEALED_ANSWER_REVIEW_SHEET"
            or sheet_manifest.get("lifecycle") != "SEALED"
            or not reviewer_id
            or sheet_manifest.get("sheetSha256") != sheet_hash
            or sheet_manifest.get("sourceRunId") != source_run_id
            or sheet_manifest.get("sourceReportSha256") != source_report_sha
        ):
            raise CustomerServiceAnswerReviewError(
                f"answer-review {key} sealed sheet binding is invalid"
            )
        agreement_review = agreement.get(key) or {}
        if (
            not isinstance(agreement_review, Mapping)
            or agreement_review.get("reviewerId") != reviewer_id
            or agreement_review.get("sha256") != sheet_hash
        ):
            raise CustomerServiceAnswerReviewError(
                f"answer-review {key} agreement binding is invalid"
            )
        expected_manifest_hash = manifest.get(
            "reviewASha256" if key == "reviewA" else "reviewBSha256"
        )
        if expected_manifest_hash != sheet_hash:
            raise CustomerServiceAnswerReviewError(
                f"answer-review {key} evidence manifest hash is invalid"
            )
        by_id: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(rows, 1):
            label = f"answer-review {key} row {index}"
            if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
                raise CustomerServiceAnswerReviewError(f"{label} schema is invalid")
            case_id = str(row.get("caseId") or "")
            if not case_id or case_id in by_id:
                raise CustomerServiceAnswerReviewError(f"{label} caseId is invalid")
            if (
                row.get("schemaVersion") != ANSWER_REVIEW_SCHEMA
                or row.get("reviewerId") != reviewer_id
                or row.get("guidelinesVersion") != ANSWER_REVIEW_GUIDELINES_VERSION
                or row.get("sourceRunId") != source_run_id
                or row.get("sourceReportSha256") != source_report_sha
                or not isinstance(row.get("comment"), str)
            ):
                raise CustomerServiceAnswerReviewError(f"{label} source binding is invalid")
            labels = _validate_labels(
                row.get("labels"), label=f"{label}.labels", require_complete=True
            )
            normalized = dict(row)
            normalized["labels"] = labels
            by_id[case_id] = normalized
            if key == "reviewA":
                sources[case_id] = {
                    "caseId": case_id,
                    "sourceRunId": source_run_id,
                    "sourceReportSha256": source_report_sha,
                    "message": row.get("message"),
                    "answer": row.get("answer") or "",
                    "sourceRefs": copy.deepcopy(row.get("sourceRefs") or []),
                    "observedHandoff": bool(row.get("observedHandoff")),
                }
        if len(by_id) != case_count:
            raise CustomerServiceAnswerReviewError(
                f"answer-review {key} case count is invalid"
            )
        review_rows[key] = by_id
        reviewer_ids[key] = reviewer_id
    if reviewer_ids.get("reviewA") == reviewer_ids.get("reviewB"):
        raise CustomerServiceAnswerReviewError(
            "answer-review reviewers are not independent"
        )
    presentation_redaction = _shared_presentation_redaction(
        sheet_manifests["reviewA"], sheet_manifests["reviewB"]
    )
    message_projection = _shared_message_projection(
        sheet_manifests["reviewA"], sheet_manifests["reviewB"]
    )
    expected_presentation = (
        dict(_PRESENTATION_REDACTION) if presentation_redaction else None
    )
    if (
        _canonical(agreement.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _canonical(manifest.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _canonical(
            (final_report.get("reviewEvidence") or {}).get("presentationRedaction")
        )
        != _canonical(expected_presentation)
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review presentation redaction is invalid"
        )
    if (
        _message_projection(agreement) != message_projection
        or _message_projection(manifest) != message_projection
        or _message_projection(final_report.get("reviewEvidence") or {})
        != message_projection
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review message projection is invalid"
        )
    if set(review_rows["reviewA"]) != set(review_rows["reviewB"]):
        raise CustomerServiceAnswerReviewError(
            "answer-review sealed sheets cover different cases"
        )
    for case_id, source in sources.items():
        row = review_rows["reviewB"][case_id]
        for field in ("message", "answer", "sourceRefs", "observedHandoff"):
            if _canonical(row.get(field)) != _canonical(source.get(field)):
                raise CustomerServiceAnswerReviewError(
                    f"answer-review sealed source differs for {case_id}: {field}"
                )

    expected_disagreements: dict[str, dict[str, Any]] = {}
    field_values: dict[str, tuple[list[Any], list[Any]]] = {
        field: ([], []) for field in _LABEL_FIELDS
    }
    for case_id, source in sources.items():
        left = review_rows["reviewA"][case_id]
        right = review_rows["reviewB"][case_id]
        fields: list[str] = []
        for field in _LABEL_FIELDS:
            field_values[field][0].append(left["labels"][field])
            field_values[field][1].append(right["labels"][field])
            if _canonical(left["labels"][field]) != _canonical(right["labels"][field]):
                fields.append(field)
        if fields:
            expected_disagreements[case_id] = {
                **copy.deepcopy(source),
                "fields": fields,
                "reviewerA": {
                    "reviewerId": reviewer_ids["reviewA"],
                    "labels": copy.deepcopy(left["labels"]),
                    "comment": left["comment"],
                },
                "reviewerB": {
                    "reviewerId": reviewer_ids["reviewB"],
                    "labels": copy.deepcopy(right["labels"]),
                    "comment": right["comment"],
                },
            }
    agreement_disagreements = {
        str(item.get("caseId") or ""): item
        for item in agreement.get("disagreements") or []
        if isinstance(item, Mapping)
    }
    if (
        len(agreement_disagreements) != disagreement_count
        or set(agreement_disagreements) != set(expected_disagreements)
        or any(
            _canonical(agreement_disagreements[case_id])
            != _canonical(expected_disagreements[case_id])
            for case_id in expected_disagreements
        )
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review agreement disagreements are invalid"
        )
    expected_field_stats = {
        field: _categorical_agreement(*field_values[field]) for field in _LABEL_FIELDS
    }
    exact_count = case_count - len(expected_disagreements)
    if (
        agreement.get("caseCount") != case_count
        or agreement.get("exactAgreementCaseCount") != exact_count
        or agreement.get("disagreementCaseCount") != len(expected_disagreements)
        or agreement.get("caseAgreementRate") != round(exact_count / case_count, 6)
        or _canonical(agreement.get("fieldStats")) != _canonical(expected_field_stats)
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review agreement metrics are invalid"
        )

    adjudications: dict[str, dict[str, Any]] = {}
    adjudication_path = root / "reviews" / "adjudication.final.jsonl"
    if expected_disagreements:
        adjudications = _load_adjudications(adjudication_path, agreement=agreement)
        if manifest.get("adjudicationSha256") != sha256_file(adjudication_path):
            raise CustomerServiceAnswerReviewError(
                "answer-review adjudication hash is invalid"
            )
    elif manifest.get("adjudicationSha256") is not None:
        raise CustomerServiceAnswerReviewError(
            "answer-review has an unexpected adjudication hash"
        )
    if presentation_redaction:
        redaction_inputs: list[Any] = [
            manifest,
            final_report,
            agreement,
            sheet_rows_for_redaction,
        ]
        if expected_disagreements:
            redaction_inputs.append(load_jsonl(adjudication_path))
        if contains_unredacted_sensitive(redaction_inputs):
            raise CustomerServiceAnswerReviewError(
                "answer-review evidence contains unredacted sensitive data"
            )

    final_cases = final_report.get("cases")
    if not isinstance(final_cases, list) or len(final_cases) != case_count:
        raise CustomerServiceAnswerReviewError("answer-review final cases are invalid")
    final_by_id: dict[str, Mapping[str, Any]] = {}
    labels_by_id: dict[str, dict[str, Any]] = {}
    comments_by_id: dict[str, str] = {}
    expected_case_fields = {
        "caseId",
        "labels",
        "labelSource",
        "adjudicator",
        "comment",
        "answerSha256",
    }
    for index, final_case in enumerate(final_cases, 1):
        if not isinstance(final_case, Mapping) or set(final_case) != expected_case_fields:
            raise CustomerServiceAnswerReviewError(
                f"answer-review final case {index} schema is invalid"
            )
        case_id = str(final_case.get("caseId") or "")
        if case_id not in sources or case_id in final_by_id:
            raise CustomerServiceAnswerReviewError(
                f"answer-review final case {index} caseId is invalid"
            )
        labels = _validate_labels(
            final_case.get("labels"),
            label=f"answer-review final case {index}.labels",
            require_complete=True,
        )
        source = sources[case_id]
        if final_case.get("answerSha256") != sha256_bytes(
            str(source.get("answer") or "").encode("utf-8")
        ):
            raise CustomerServiceAnswerReviewError(
                f"answer-review final case {case_id} answer hash is invalid"
            )
        if case_id in adjudications:
            expected_labels = adjudications[case_id]["labels"]
            expected_source = "ADJUDICATED"
            expected_adjudicator = adjudications[case_id]["adjudicator"]
            expected_comment = adjudications[case_id]["reason"]
        else:
            left = review_rows["reviewA"][case_id]
            right = review_rows["reviewB"][case_id]
            if _canonical(left["labels"]) != _canonical(right["labels"]):
                raise CustomerServiceAnswerReviewError(
                    f"answer-review final case {case_id} lacks adjudication"
                )
            expected_labels = left["labels"]
            expected_source = "REVIEWER_AGREEMENT"
            expected_adjudicator = None
            expected_comment = " | ".join(
                value for value in (left["comment"], right["comment"]) if value
            )
        if (
            _canonical(labels) != _canonical(expected_labels)
            or final_case.get("labelSource") != expected_source
            or final_case.get("adjudicator") != expected_adjudicator
            or final_case.get("comment") != expected_comment
        ):
            raise CustomerServiceAnswerReviewError(
                f"answer-review final case {case_id} decision is invalid"
            )
        final_by_id[case_id] = final_case
        labels_by_id[case_id] = labels
        comments_by_id[case_id] = expected_comment
    if set(final_by_id) != set(sources):
        raise CustomerServiceAnswerReviewError(
            "answer-review final case coverage is invalid"
        )
    # The two blind sheets have independent randomized orders.  The final
    # report retains the source-report order, which is also the stable order
    # for badcase IDs, so score against that declared order rather than a
    # reviewer-specific ordering.
    ordered_sources = {
        str(final_case["caseId"]): sources[str(final_case["caseId"])]
        for final_case in final_cases
    }
    expected_metrics, expected_badcases = _score_final_labels(
        ordered_sources, labels_by_id, comments_by_id
    )
    expected_final_agreement = {
        "exactAgreementCaseCount": exact_count,
        "disagreementCaseCount": len(expected_disagreements),
        "caseAgreementRate": round(exact_count / case_count, 6),
        "fieldStats": expected_field_stats,
    }
    if (
        _canonical(final_report.get("agreement")) != _canonical(expected_final_agreement)
        or _canonical(final_report.get("metrics")) != _canonical(expected_metrics)
        or _canonical(final_report.get("badcases")) != _canonical(expected_badcases)
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review final metrics or badcases are inconsistent with labels"
        )
    review_evidence = final_report.get("reviewEvidence")
    if (
        not isinstance(review_evidence, Mapping)
        or _canonical(review_evidence.get("reviewA"))
        != _canonical(agreement.get("reviewA"))
        or _canonical(review_evidence.get("reviewB"))
        != _canonical(agreement.get("reviewB"))
        or review_evidence.get("adjudicationSha256") != manifest.get("adjudicationSha256")
        or review_evidence.get("guidelinesVersion") != ANSWER_REVIEW_GUIDELINES_VERSION
        or _canonical(review_evidence.get("presentationRedaction"))
        != _canonical(expected_presentation)
        or _message_projection(review_evidence) != message_projection
    ):
        raise CustomerServiceAnswerReviewError(
            "answer-review final review evidence is invalid"
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
