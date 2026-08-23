"""Fail-closed two-person review workflow for the customer-service gold set.

The checked-in customer-service set is intentionally a draft.  This module
creates blinded JSONL sheets for two independent reviewers and merges their
labels only after every disagreement has an explicit adjudication.  It never
modifies the draft dataset in place and rejects sheets that contain model
predictions or a different source-data hash.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_json,
    atomic_write_jsonl,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_file,
    utc_now,
)
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset

REVIEW_SCHEMA = "aishop-customer-service-review/v1"
ADJUDICATION_SCHEMA = "aishop-customer-service-adjudication/v1"
REVIEW_EVIDENCE_SCHEMA = "aishop-customer-service-review-evidence/v1"
AGREEMENT_EVIDENCE_SCHEMA = "aishop-customer-service-review-agreement/v1"
GUIDELINES_VERSION = "customer-service-gold-v1"

_LABEL_FIELDS = ("intent", "riskLevel", "shouldHandoff", "handoffSeverity", "slots")
_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
_HANDOFF_SEVERITIES = frozenset({"NORMAL", "CRITICAL"})
_FORBIDDEN_REVIEW_KEYS = frozenset(
    {"expected", "predicted", "prediction", "modelOutput", "modelPrediction"}
)


class CustomerServiceReviewError(ValueError):
    """Raised when a review artifact is incomplete, leaked, or inconsistent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.casefold())


def _string_set(value: Any, *, label: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CustomerServiceReviewError(f"{label} must be a list of non-empty strings")
    return set(value)


def _blank_labels() -> dict[str, Any]:
    return {
        "intent": None,
        "riskLevel": None,
        "shouldHandoff": None,
        "handoffSeverity": None,
        "slots": None,
    }


def _sidecar_path(sheet_path: Path) -> Path:
    return sheet_path.with_suffix(sheet_path.suffix + ".manifest.json")


def _path_label(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _ensure_new(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite review artifact(s): " + ", ".join(existing)
        )


def _dataset_context(dataset_path: Path) -> tuple[list[dict[str, Any]], str, set[str]]:
    rows = load_gold_dataset(dataset_path)
    if any(
        (row.get("annotation") or {}).get("status") != "DRAFT_NEEDS_HUMAN_REVIEW"
        for row in rows
    ):
        raise CustomerServiceReviewError("review source must be the untouched draft dataset")
    dataset_sha = sha256_file(dataset_path)
    intents = {str((row.get("expected") or {}).get("intent") or "") for row in rows}
    return rows, dataset_sha, intents


def _forbidden_keys(value: Any) -> set[str]:
    """Find leaked model/gold keys at any nesting level of a review artifact."""

    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_REVIEW_KEYS:
                found.add(key_text)
            found.update(_forbidden_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _validate_slots(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise CustomerServiceReviewError(f"{label}: slots must be an object")
    result: dict[str, str] = {}
    for key, raw in value.items():
        key_text = str(key).strip()
        value_text = str(raw).strip() if raw is not None else ""
        if not key_text or not value_text:
            raise CustomerServiceReviewError(f"{label}: slot keys and values must be non-empty")
        result[key_text] = value_text
    return result


def _validate_labels(
    value: Any,
    *,
    label: str,
    allowed_intents: set[str],
    require_complete: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CustomerServiceReviewError(f"{label}: labels must be an object")
    unknown = set(value) - set(_LABEL_FIELDS)
    if unknown:
        raise CustomerServiceReviewError(f"{label}: unknown label fields: {sorted(unknown)}")
    labels = {field: value.get(field) for field in _LABEL_FIELDS}
    blank = all(labels[field] is None for field in _LABEL_FIELDS)
    if blank:
        if require_complete:
            raise CustomerServiceReviewError(f"{label}: labels are incomplete")
        return labels
    required_fields = ("intent", "riskLevel", "shouldHandoff", "slots")
    if any(labels[field] is None for field in required_fields):
        raise CustomerServiceReviewError(f"{label}: partially filled labels are not allowed")
    intent = str(labels["intent"]).strip()
    if intent not in allowed_intents:
        raise CustomerServiceReviewError(
            f"{label}: intent {intent!r} is outside the frozen taxonomy"
        )
    risk = str(labels["riskLevel"]).strip().upper()
    if risk not in _RISK_LEVELS:
        raise CustomerServiceReviewError(f"{label}: riskLevel is invalid: {risk!r}")
    if not isinstance(labels["shouldHandoff"], bool):
        raise CustomerServiceReviewError(f"{label}: shouldHandoff must be boolean")
    severity = labels["handoffSeverity"]
    if severity is not None:
        severity = str(severity).strip().upper()
        if severity not in _HANDOFF_SEVERITIES:
            raise CustomerServiceReviewError(f"{label}: handoffSeverity is invalid: {severity!r}")
    if labels["shouldHandoff"] and severity is None:
        raise CustomerServiceReviewError(f"{label}: handoffSeverity is required when handoff=true")
    if not labels["shouldHandoff"] and severity is not None:
        raise CustomerServiceReviewError(
            f"{label}: handoffSeverity must be empty when handoff=false"
        )
    slots = _validate_slots(labels["slots"], label=f"{label}.slots")
    return {
        "intent": intent,
        "riskLevel": risk,
        "shouldHandoff": labels["shouldHandoff"],
        "handoffSeverity": severity,
        "slots": slots,
    }


def _expected_to_labels(expected: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "intent": str(expected.get("intent") or ""),
        "riskLevel": str(expected.get("riskLevel") or ""),
        "shouldHandoff": bool(expected.get("shouldHandoff")),
        "handoffSeverity": expected.get("handoffSeverity"),
        "slots": {
            str(key): str(value)
            for key, value in (expected.get("slots") or {}).items()
        },
    }


def _labels_to_expected(labels: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "intent": labels["intent"],
        "riskLevel": labels["riskLevel"],
        "shouldHandoff": labels["shouldHandoff"],
        "slots": dict(labels["slots"]),
    }
    if labels.get("handoffSeverity") is not None:
        expected["handoffSeverity"] = labels["handoffSeverity"]
    return expected


def _validate_common_row(row: Mapping[str, Any], *, label: str) -> None:
    leaked = _forbidden_keys(row)
    if leaked:
        raise CustomerServiceReviewError(
            f"{label}: blinded sheet leaks model/gold fields: {sorted(leaked)}"
        )
    allowed_keys = {
        "schemaVersion",
        "id",
        "input",
        "reviewerId",
        "guidelinesVersion",
        "labels",
        "comment",
    }
    unknown = set(row) - allowed_keys
    if unknown:
        raise CustomerServiceReviewError(f"{label}: unknown review fields: {sorted(unknown)}")
    if row.get("schemaVersion") != REVIEW_SCHEMA:
        raise CustomerServiceReviewError(f"{label}: schemaVersion must be {REVIEW_SCHEMA}")
    case_id = str(row.get("id") or "")
    if not case_id:
        raise CustomerServiceReviewError(f"{label}: id is required")
    input_value = row.get("input")
    if not isinstance(input_value, Mapping):
        raise CustomerServiceReviewError(f"{label}: input must be an object")
    message = input_value.get("message")
    if not isinstance(message, str) or not message.strip():
        raise CustomerServiceReviewError(f"{label}: input.message is required")
    if not isinstance(row.get("reviewerId"), str) or not row["reviewerId"].strip():
        raise CustomerServiceReviewError(f"{label}: reviewerId is required")
    if row.get("guidelinesVersion") != GUIDELINES_VERSION:
        raise CustomerServiceReviewError(f"{label}: guidelinesVersion is invalid")
    if not isinstance(row.get("labels"), Mapping):
        raise CustomerServiceReviewError(f"{label}: labels is required")


def export_review_sheet(
    dataset_path: Path,
    output_path: Path,
    *,
    reviewer_id: str,
    seed: int | None = None,
) -> dict[str, Any]:
    """Export a shuffled sheet without expected labels or model predictions."""

    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise CustomerServiceReviewError("reviewer_id is required")
    rows, dataset_sha, intents = _dataset_context(dataset_path)
    if seed is None:
        # Stable but reviewer-specific ordering prevents both reviewers from
        # unconsciously copying the same row-by-row sequence.
        seed = int.from_bytes(hashlib.sha256(reviewer.encode("utf-8")).digest()[:8], "big")
    manifest_path = _sidecar_path(output_path)
    _ensure_new((output_path, manifest_path))
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    sheet_rows = [
        {
            "schemaVersion": REVIEW_SCHEMA,
            "id": str(row["id"]),
            "input": {"message": (row.get("input") or {}).get("message")},
            "reviewerId": reviewer,
            "guidelinesVersion": GUIDELINES_VERSION,
            "labels": _blank_labels(),
            "comment": "",
        }
        for row in shuffled
    ]
    atomic_write_jsonl(output_path, sheet_rows, overwrite=False)
    manifest = {
        "schemaVersion": REVIEW_SCHEMA,
        "artifact": "BLINDED_REVIEW_SHEET",
        "lifecycle": "OPEN",
        "datasetPath": _path_label(dataset_path),
        "datasetSha256": dataset_sha,
        "sheetPath": _path_label(output_path),
        "sheetSha256": sha256_file(output_path),
        "caseCount": len(sheet_rows),
        "reviewerId": reviewer,
        "guidelinesVersion": GUIDELINES_VERSION,
        "labelSchema": {
            "intentValues": sorted(intents),
            "riskLevelValues": sorted(_RISK_LEVELS),
            "handoffSeverityValues": sorted(_HANDOFF_SEVERITIES),
            "requiredFields": ["intent", "riskLevel", "shouldHandoff", "slots"],
            "conditionalFields": {
                "handoffSeverity": "required when shouldHandoff=true; empty otherwise"
            },
        },
        "orderSeed": seed,
        "createdAt": utc_now(),
        "containsExpectedOrPredicted": False,
    }
    atomic_write_json(manifest_path, manifest, overwrite=False)
    return manifest


def _load_review_sheet(
    dataset_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool,
    check_sheet_hash: bool = True,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    dataset_rows, dataset_sha, allowed_intents = _dataset_context(dataset_path)
    manifest_path = _sidecar_path(sheet_path)
    if not manifest_path.is_file():
        raise CustomerServiceReviewError(f"review manifest is missing: {manifest_path}")
    try:
        manifest = load_json(manifest_path)
        rows = load_jsonl(sheet_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CustomerServiceReviewError(f"invalid review artifact: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise CustomerServiceReviewError("review manifest must be an object")
    if manifest.get("schemaVersion") != REVIEW_SCHEMA:
        raise CustomerServiceReviewError("review manifest schema is invalid")
    lifecycle = manifest.get("lifecycle")
    artifact = manifest.get("artifact")
    expected_artifact = {
        "OPEN": "BLINDED_REVIEW_SHEET",
        "SEALED": "SEALED_REVIEW_SHEET",
    }.get(lifecycle)
    if expected_artifact is None or artifact != expected_artifact:
        raise CustomerServiceReviewError("review manifest lifecycle/artifact is invalid")
    if manifest.get("guidelinesVersion") != GUIDELINES_VERSION:
        raise CustomerServiceReviewError("review manifest guidelinesVersion is invalid")
    if manifest.get("datasetPath") != _path_label(dataset_path):
        raise CustomerServiceReviewError("review manifest datasetPath differs from source")
    if manifest.get("containsExpectedOrPredicted") is not False:
        raise CustomerServiceReviewError("review manifest must declare no expected/predicted fields")
    label_schema = manifest.get("labelSchema")
    if not isinstance(label_schema, Mapping):
        raise CustomerServiceReviewError("review manifest labelSchema is missing")
    intent_values = _string_set(label_schema.get("intentValues"), label="labelSchema.intentValues")
    risk_values = _string_set(label_schema.get("riskLevelValues"), label="labelSchema.riskLevelValues")
    severity_values = _string_set(
        label_schema.get("handoffSeverityValues"),
        label="labelSchema.handoffSeverityValues",
    )
    if (
        intent_values != allowed_intents
        or risk_values != set(_RISK_LEVELS)
        or severity_values != set(_HANDOFF_SEVERITIES)
    ):
        raise CustomerServiceReviewError("review manifest labelSchema differs from frozen taxonomy")
    if manifest.get("datasetSha256") != dataset_sha:
        raise CustomerServiceReviewError("review sheet source dataset hash differs")
    if check_sheet_hash and manifest.get("sheetSha256") != sha256_file(sheet_path):
        raise CustomerServiceReviewError("review sheet hash differs from its manifest")
    manifest_sheet_path = str(manifest.get("sheetPath") or "")
    actual_sheet_path = _path_label(sheet_path)
    if manifest_sheet_path != actual_sheet_path:
        # Sealed review artifacts are immutable and are routinely copied from
        # a temporary submission directory into the repository evidence
        # archive.  The content SHA-256 below is the integrity binding; keep
        # OPEN sheets path-bound, but allow a relocated SEALED file when its
        # basename remains the same.  A swapped file still fails the hash
        # check, and a renamed artifact fails this guard.
        can_relocate = (
            lifecycle == "SEALED"
            and bool(manifest_sheet_path)
            and Path(manifest_sheet_path).name == sheet_path.name
        )
        if not can_relocate:
            raise CustomerServiceReviewError("review manifest sheetPath differs from artifact")
    if not _is_sha256(manifest.get("sheetSha256")):
        raise CustomerServiceReviewError("review manifest sheetSha256 is invalid")
    if manifest.get("caseCount") != len(rows) or len(rows) != len(dataset_rows):
        raise CustomerServiceReviewError("review sheet case count differs from source dataset")
    reviewer = str(manifest.get("reviewerId") or "").strip()
    if not reviewer:
        raise CustomerServiceReviewError("review manifest reviewerId is missing")
    source_by_id = {str(row["id"]): row for row in dataset_rows}
    reviewed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        label = f"{sheet_path}:{index}"
        _validate_common_row(row, label=label)
        case_id = str(row["id"])
        if case_id in reviewed or case_id not in source_by_id:
            raise CustomerServiceReviewError(f"{label}: ID is duplicated or absent from source dataset")
        if row.get("reviewerId") != reviewer:
            raise CustomerServiceReviewError(f"{label}: reviewerId differs from manifest")
        if row["input"].get("message") != (source_by_id[case_id].get("input") or {}).get("message"):
            raise CustomerServiceReviewError(f"{label}: input message differs from source dataset")
        labels = _validate_labels(
            row["labels"],
            label=f"{label}.labels",
            allowed_intents=allowed_intents,
            require_complete=require_complete,
        )
        reviewed[case_id] = {"row": row, "labels": labels}
    if set(reviewed) != set(source_by_id):
        missing = sorted(set(source_by_id) - set(reviewed))
        raise CustomerServiceReviewError(f"review sheet is missing case IDs: {missing}")
    return manifest, reviewed


def validate_review_sheet(
    dataset_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Validate a sheet and return its immutable manifest."""

    # An OPEN sheet is expected to change while a reviewer fills labels.  The
    # source hash, row identity, taxonomy and blinded shape remain enforced;
    # the content hash is refreshed only when seal creates the immutable
    # artifact. SEALED sheets keep strict hash verification.
    lifecycle = None
    manifest_path = _sidecar_path(sheet_path)
    if manifest_path.is_file():
        try:
            lifecycle = str(load_json(manifest_path).get("lifecycle") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            lifecycle = None
    manifest, _ = _load_review_sheet(
        dataset_path,
        sheet_path,
        require_complete=require_complete,
        check_sheet_hash=lifecycle != "OPEN",
    )
    return manifest


def seal_review_sheet(
    dataset_path: Path,
    input_sheet_path: Path,
    output_sheet_path: Path,
) -> dict[str, Any]:
    """Validate an edited open sheet and create a new immutable sealed sheet."""

    source_manifest, reviewed = _load_review_sheet(
        dataset_path,
        input_sheet_path,
        require_complete=True,
        check_sheet_hash=False,
    )
    if source_manifest.get("lifecycle") != "OPEN":
        raise CustomerServiceReviewError("seal accepts only OPEN review sheets")
    output_manifest_path = _sidecar_path(output_sheet_path)
    _ensure_new((output_sheet_path, output_manifest_path))
    rows = [item["row"] for item in reviewed.values()]
    # Preserve the reviewer's order, which is useful when comparing a sealed
    # artifact with the sheet that was actually reviewed.
    input_rows = load_jsonl(input_sheet_path)
    rows_by_id = {str(row["id"]): row for row in rows}
    ordered_rows = [rows_by_id[str(row["id"])] for row in input_rows]
    atomic_write_jsonl(output_sheet_path, ordered_rows, overwrite=False)
    manifest = {
        **source_manifest,
        "artifact": "SEALED_REVIEW_SHEET",
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


def _load_adjudications(
    path: Path,
    *,
    allowed_intents: set[str],
    known_ids: set[str],
) -> dict[str, dict[str, Any]]:
    try:
        rows = load_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CustomerServiceReviewError(f"invalid adjudication file: {exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        label = f"{path}:{index}"
        if row.get("schemaVersion") != ADJUDICATION_SCHEMA:
            raise CustomerServiceReviewError(f"{label}: schemaVersion is invalid")
        leaked = _forbidden_keys(row)
        if leaked:
            raise CustomerServiceReviewError(
                f"{label}: adjudication leaks model/gold fields: {sorted(leaked)}"
            )
        case_id = str(row.get("id") or "")
        if not case_id or case_id not in known_ids or case_id in result:
            raise CustomerServiceReviewError(f"{label}: ID is invalid or duplicated")
        adjudicator = str(row.get("adjudicator") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not adjudicator or not reason:
            raise CustomerServiceReviewError(f"{label}: adjudicator and reason are required")
        labels = _validate_labels(
            row.get("finalLabels"),
            label=f"{label}.finalLabels",
            allowed_intents=allowed_intents,
            require_complete=True,
        )
        result[case_id] = {
            "labels": labels,
            "adjudicator": adjudicator,
            "reason": reason,
        }
    return result


def _field_disagreements(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[str]:
    return [field for field in _LABEL_FIELDS if _canonical(left.get(field)) != _canonical(right.get(field))]


def _categorical_agreement(
    left_values: Sequence[Any],
    right_values: Sequence[Any],
) -> dict[str, Any]:
    """Return observed agreement and Cohen's kappa for one label field.

    ``None`` is a real category here (for example, no handoff severity), so
    it is represented explicitly instead of being dropped from the denominator.
    Kappa is reported as ``None`` when the expected agreement is one and the
    statistic is mathematically undefined.
    """

    if len(left_values) != len(right_values) or not left_values:
        raise CustomerServiceReviewError("categorical agreement requires equal non-empty inputs")
    left = [_canonical(value) for value in left_values]
    right = [_canonical(value) for value in right_values]
    count = len(left)
    observed_count = sum(a == b for a, b in zip(left, right))
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        left_counts[label] * right_counts[label] for label in set(left_counts) | set(right_counts)
    ) / (count * count)
    observed = observed_count / count
    kappa = None if expected >= 1.0 else (observed - expected) / (1.0 - expected)
    return {
        "agreementCount": observed_count,
        "caseCount": count,
        "agreementRate": observed,
        "expectedAgreement": expected,
        "cohenKappa": kappa,
    }


def compare_human_reviews(
    dataset_path: Path,
    review_a_path: Path,
    review_b_path: Path,
) -> dict[str, Any]:
    """Compare two complete sealed sheets without creating a gold dataset.

    This is intentionally a separate, pre-adjudication artifact.  It keeps
    the original labels and source messages for conflict review, but never
    exposes the draft ``expected`` labels and never treats either reviewer as
    truth.
    """

    dataset_rows, dataset_sha, _ = _dataset_context(dataset_path)
    manifest_a, sheet_a = _load_review_sheet(dataset_path, review_a_path, require_complete=True)
    manifest_b, sheet_b = _load_review_sheet(dataset_path, review_b_path, require_complete=True)
    if manifest_a.get("lifecycle") != "SEALED" or manifest_b.get("lifecycle") != "SEALED":
        raise CustomerServiceReviewError("agreement comparison accepts only SEALED review sheets")
    reviewer_a = str(manifest_a["reviewerId"])
    reviewer_b = str(manifest_b["reviewerId"])
    if reviewer_a == reviewer_b:
        raise CustomerServiceReviewError("reviewers must be independent and have different IDs")

    field_agreement = Counter()
    slot_key_agreement = 0
    common_slot_count = 0
    common_slot_value_agreement = 0
    disagreement_cases: list[dict[str, Any]] = []
    field_values_a: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    field_values_b: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    for source in dataset_rows:
        case_id = str(source["id"])
        left = sheet_a[case_id]["labels"]
        right = sheet_b[case_id]["labels"]
        for field in _LABEL_FIELDS:
            field_values_a[field].append(left[field])
            field_values_b[field].append(right[field])
            if _canonical(left[field]) == _canonical(right[field]):
                field_agreement[field] += 1
        left_slots = left["slots"]
        right_slots = right["slots"]
        if set(left_slots) == set(right_slots):
            slot_key_agreement += 1
        common_keys = set(left_slots) & set(right_slots)
        common_slot_count += len(common_keys)
        common_slot_value_agreement += sum(
            _canonical(left_slots[key]) == _canonical(right_slots[key]) for key in common_keys
        )
        fields = _field_disagreements(left, right)
        if fields:
            disagreement_cases.append(
                {
                    "caseId": case_id,
                    "message": (source.get("input") or {}).get("message"),
                    "fields": fields,
                    "reviewerA": copy.deepcopy(left),
                    "reviewerB": copy.deepcopy(right),
                    "comments": {
                        "reviewerA": sheet_a[case_id]["row"].get("comment") or "",
                        "reviewerB": sheet_b[case_id]["row"].get("comment") or "",
                    },
                }
            )

    field_stats = {
        field: _categorical_agreement(field_values_a[field], field_values_b[field])
        for field in _LABEL_FIELDS
    }
    case_count = len(dataset_rows)
    exact_case_count = case_count - len(disagreement_cases)
    return {
        "schemaVersion": AGREEMENT_EVIDENCE_SCHEMA,
        "status": "PENDING_ADJUDICATION",
        "releaseGateEligible": False,
        "sourceDatasetPath": _path_label(dataset_path),
        "sourceDatasetSha256": dataset_sha,
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
        "caseCount": case_count,
        "exactAgreementCaseCount": exact_case_count,
        "disagreementCaseCount": len(disagreement_cases),
        "caseAgreementRate": exact_case_count / case_count if case_count else None,
        "fieldAgreement": dict(field_agreement),
        "fieldStats": field_stats,
        "slotStats": {
            "keySetAgreementCount": slot_key_agreement,
            "keySetAgreementRate": slot_key_agreement / case_count if case_count else None,
            "commonSlotCount": common_slot_count,
            "commonSlotValueAgreementCount": common_slot_value_agreement,
            "commonSlotValueAgreementRate": (
                common_slot_value_agreement / common_slot_count if common_slot_count else None
            ),
            "note": (
                "Raw slot-key agreement is reported separately because the current draft "
                "guideline does not yet freeze an operational slot taxonomy."
            ),
        },
        "disagreements": disagreement_cases,
        "createdAt": utc_now(),
        "note": (
            "Pre-adjudication inter-annotator evidence only. It is not a model score, "
            "not an accuracy estimate, and must not replace independent adjudication."
        ),
    }


def render_agreement_markdown(evidence: Mapping[str, Any]) -> str:
    """Render a concise, blind-safe human-review agreement report."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        "# 客服双人工一致性证据",
        "",
        f"> 状态：`{cell(evidence.get('status'))}`；此报告只描述标注可靠性，不是模型准确率，也不进入 release gate。",
        "",
        f"数据集 SHA-256：`{cell(evidence.get('sourceDatasetSha256'))}`；案件数：`{evidence.get('caseCount', 0)}`；",
        f"案件级完全一致：`{evidence.get('exactAgreementCaseCount', 0)}/{evidence.get('caseCount', 0)}`；",
        f"案件级一致率：`{evidence.get('caseAgreementRate')}`。",
        "",
        "## 字段一致性",
        "",
        "| 字段 | 一致数 | 一致率 | Cohen κ |",
        "|---|---:|---:|---:|",
    ]
    for field in _LABEL_FIELDS:
        stats = (evidence.get("fieldStats") or {}).get(field) or {}
        lines.append(
            f"| `{field}` | {stats.get('agreementCount', 0)}/{stats.get('caseCount', 0)} | "
            f"{stats.get('agreementRate')} | {stats.get('cohenKappa')} |"
        )
    slot_stats = evidence.get("slotStats") or {}
    lines.extend(
        [
            "",
            "## 槽位诊断",
            "",
            f"- key-set 一致：`{slot_stats.get('keySetAgreementCount', 0)}/{evidence.get('caseCount', 0)}`（{slot_stats.get('keySetAgreementRate')}）。",
            f"- 两位标注者共同填写的槽位值一致：`{slot_stats.get('commonSlotValueAgreementCount', 0)}/{slot_stats.get('commonSlotCount', 0)}`（{slot_stats.get('commonSlotValueAgreementRate')}）。",
            "- 当前 raw slot 一致率受 `budget/amount`、`brand/productName` 等未冻结的槽位 taxonomy 影响；仲裁前不能直接作为模型 Slot F1。",
            "",
            "## 冲突 Badcase",
            "",
            "| Case | 冲突字段 | 用户原话 |",
            "|---|---|---|",
        ]
    )
    for item in evidence.get("disagreements") or []:
        lines.append(
            f"| `{cell(item.get('caseId'))}` | `{cell(', '.join(item.get('fields') or []))}` | {cell(item.get('message'))} |"
        )
    lines.extend(
        [
            "",
            "## 下一步",
            "",
            "1. 先冻结 core slot taxonomy（建议先覆盖生产已支持的 `orderId`、`amount`、`productName`、`orderItemId`、`productId`）。",
            "2. 由 lead reviewer 只仲裁冲突 case，并在 adjudication JSONL 写明理由；未完成前禁止生成 `HUMAN_VERIFIED` 数据集。",
        ]
    )
    return "\n".join(lines) + "\n"


def merge_human_reviews(
    dataset_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    *,
    output_dataset_path: Path,
    evidence_path: Path,
    adjudication_path: Path | None = None,
    default_adjudicator: str = "consensus",
) -> dict[str, Any]:
    """Merge two complete sheets into a new immutable HUMAN_VERIFIED dataset."""

    dataset_rows, dataset_sha, allowed_intents = _dataset_context(dataset_path)
    if not str(default_adjudicator or "").strip():
        raise CustomerServiceReviewError("default_adjudicator is required")
    manifest_a, sheet_a = _load_review_sheet(dataset_path, review_a_path, require_complete=True)
    manifest_b, sheet_b = _load_review_sheet(dataset_path, review_b_path, require_complete=True)
    if manifest_a.get("lifecycle") != "SEALED" or manifest_b.get("lifecycle") != "SEALED":
        raise CustomerServiceReviewError("merge accepts only SEALED review sheets")
    reviewer_a = str(manifest_a["reviewerId"])
    reviewer_b = str(manifest_b["reviewerId"])
    if reviewer_a == reviewer_b:
        raise CustomerServiceReviewError("reviewers must be independent and have different IDs")
    if adjudication_path is not None:
        adjudications = _load_adjudications(
            adjudication_path,
            allowed_intents=allowed_intents,
            known_ids={str(row["id"]) for row in dataset_rows},
        )
    else:
        adjudications = {}

    disagreements: list[dict[str, Any]] = []
    field_agreement = Counter()
    merged_rows: list[dict[str, Any]] = []
    for source in dataset_rows:
        case_id = str(source["id"])
        left = sheet_a[case_id]["labels"]
        right = sheet_b[case_id]["labels"]
        fields = _field_disagreements(left, right)
        for field in _LABEL_FIELDS:
            field_agreement[field] += field not in fields
        if fields:
            disagreement = {
                "caseId": case_id,
                "fields": fields,
                "reviewerA": left,
                "reviewerB": right,
            }
            if case_id not in adjudications:
                disagreements.append(disagreement)
                continue
            disagreement["adjudication"] = adjudications[case_id]
            disagreements.append(disagreement)
            final_labels = adjudications[case_id]["labels"]
            adjudicator = adjudications[case_id]["adjudicator"]
        else:
            if case_id in adjudications:
                final_labels = adjudications[case_id]["labels"]
                adjudicator = adjudications[case_id]["adjudicator"]
            else:
                final_labels = left
                adjudicator = default_adjudicator
        merged = copy.deepcopy(source)
        merged["expected"] = _labels_to_expected(final_labels)
        merged["annotation"] = {
            "status": HUMAN_STATUS,
            "annotator": "human-adjudicated",
            "guidelinesVersion": GUIDELINES_VERSION,
            "reviewers": [reviewer_a, reviewer_b],
            "adjudicator": adjudicator,
            "reviewEvidence": {
                "sourceDatasetSha256": dataset_sha,
                "reviewASha256": manifest_a["sheetSha256"],
                "reviewBSha256": manifest_b["sheetSha256"],
                "adjudicationSha256": sha256_file(adjudication_path)
                if adjudication_path is not None
                else None,
                "agreement": "ADJUDICATED" if fields else "AGREED",
            },
        }
        merged_rows.append(merged)

    extra_adjudications = sorted(set(adjudications) - {str(row["id"]) for row in dataset_rows})
    if extra_adjudications:
        raise CustomerServiceReviewError(
            f"adjudication contains unknown case IDs: {extra_adjudications}"
        )
    agreed_adjudications = sorted(
        case_id
        for case_id in adjudications
        if not _field_disagreements(sheet_a[case_id]["labels"], sheet_b[case_id]["labels"])
    )
    if agreed_adjudications:
        raise CustomerServiceReviewError(
            "adjudication is only allowed for reviewer disagreements: "
            + ", ".join(agreed_adjudications)
        )
    unresolved = [item for item in disagreements if "adjudication" not in item]
    if unresolved:
        ids = [item["caseId"] for item in unresolved]
        raise CustomerServiceReviewError(
            f"unresolved reviewer disagreements require adjudication: {ids}"
        )

    _ensure_new((output_dataset_path, evidence_path))
    atomic_write_jsonl(output_dataset_path, merged_rows, overwrite=False)
    try:
        load_gold_dataset(output_dataset_path)
    except (OSError, ValueError) as exc:
        raise CustomerServiceReviewError(
            f"merged HUMAN_VERIFIED dataset failed schema validation: {exc}"
        ) from exc
    evidence = {
        "schemaVersion": REVIEW_EVIDENCE_SCHEMA,
        "status": HUMAN_STATUS,
        "releaseGateEligible": False,
        "sourceDatasetPath": _path_label(dataset_path),
        "sourceDatasetSha256": dataset_sha,
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
        "adjudication": {
            "path": _path_label(adjudication_path) if adjudication_path else None,
            "sha256": sha256_file(adjudication_path) if adjudication_path else None,
        },
        "outputDatasetPath": _path_label(output_dataset_path),
        "outputDatasetSha256": sha256_file(output_dataset_path),
        "caseCount": len(merged_rows),
        "exactAgreementCaseCount": sum(
            not _field_disagreements(sheet_a[str(row["id"])]["labels"], sheet_b[str(row["id"])]["labels"])
            for row in dataset_rows
        ),
        "disagreementCaseCount": sum(
            bool(_field_disagreements(sheet_a[str(row["id"])]["labels"], sheet_b[str(row["id"])]["labels"]))
            for row in dataset_rows
        ),
        "fieldAgreementCounts": dict(field_agreement),
        "disagreements": disagreements,
        "createdAt": utc_now(),
        "note": "Human-verified labels are separate from the draft dataset; release publication still requires an explicit project gate decision.",
    }
    atomic_write_json(evidence_path, evidence, overwrite=False)
    return evidence
