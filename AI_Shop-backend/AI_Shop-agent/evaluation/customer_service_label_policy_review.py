"""Fail-closed review lifecycle for the customer-service v2.1 label policy.

The label-consistency audit exports a 25-case blind sheet, but that export is
not evidence by itself.  This module validates returned copies, seals each
reviewer's exact bytes, measures agreement, and publishes an immutable pending
package containing only the true disagreement set for a third adjudicator.

It deliberately does not update the 120-case gold dataset.  A successor can be
built only after every disagreement has an independent final decision.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    canonical_json_bytes,
    load_json,
    load_jsonl,
    relative_to_repo,
    sha256_file,
    utc_now,
)

LABEL_POLICY_REVIEW_SCHEMA = "aishop-customer-service-label-policy-reaudit/v1"
LABEL_POLICY_AGREEMENT_SCHEMA = (
    "aishop-customer-service-label-policy-review-agreement/v1"
)
LABEL_POLICY_ADJUDICATION_SCHEMA = (
    "aishop-customer-service-label-policy-adjudication/v1"
)
LABEL_POLICY_PENDING_EVIDENCE_SCHEMA = (
    "aishop-customer-service-label-policy-review-pending-evidence/v1"
)
LABEL_POLICY_PENDING_LIFECYCLE_SCHEMA = (
    "aishop-customer-service-label-policy-review-pending-lifecycle/v1"
)
LABEL_POLICY_GUIDELINES_VERSION = "customer-service-taxonomy-v2.1"

_LABEL_FIELDS = (
    "intent",
    "riskLevel",
    "shouldHandoff",
    "handoffSeverity",
    "slots",
)
_ROW_FIELDS = frozenset(
    {
        "schemaVersion",
        "id",
        "input",
        "reviewerId",
        "guidelinesVersion",
        "labels",
        "comment",
    }
)
_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
_HANDOFF_SEVERITIES = frozenset({"NORMAL", "CRITICAL"})
_FORBIDDEN_KEYS = frozenset(
    {"expected", "predicted", "prediction", "modelOutput", "modelPrediction"}
)


class CustomerServiceLabelPolicyReviewError(ValueError):
    """Raised when a returned review or its lifecycle evidence is invalid."""


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


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
            "refusing to overwrite label-policy review artifact(s): "
            + ", ".join(existing)
        )


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in _FORBIDDEN_KEYS:
                found.add(key_text)
            found.update(_forbidden_keys(child))
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _blank_labels() -> dict[str, Any]:
    return {
        "intent": None,
        "riskLevel": None,
        "shouldHandoff": None,
        "handoffSeverity": None,
        "slots": None,
    }


def _validate_labels(
    value: Any,
    *,
    label: str,
    allowed_intents: set[str],
    message: str,
    require_complete: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_LABEL_FIELDS):
        raise CustomerServiceLabelPolicyReviewError(
            f"{label}: labels must contain exactly {list(_LABEL_FIELDS)}"
        )
    labels = {field: value.get(field) for field in _LABEL_FIELDS}
    if all(labels[field] is None for field in _LABEL_FIELDS):
        if require_complete:
            raise CustomerServiceLabelPolicyReviewError(f"{label}: labels are incomplete")
        return labels
    for field in ("intent", "riskLevel", "shouldHandoff", "slots"):
        if labels[field] is None:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: partially filled labels are not allowed"
            )

    intent = str(labels["intent"]).strip()
    if intent not in allowed_intents:
        raise CustomerServiceLabelPolicyReviewError(
            f"{label}: intent {intent!r} is outside the frozen taxonomy"
        )
    risk = str(labels["riskLevel"]).strip().upper()
    if risk not in _RISK_LEVELS:
        raise CustomerServiceLabelPolicyReviewError(
            f"{label}: invalid riskLevel {risk!r}"
        )
    should_handoff = labels["shouldHandoff"]
    if not isinstance(should_handoff, bool):
        raise CustomerServiceLabelPolicyReviewError(
            f"{label}: shouldHandoff must be boolean"
        )
    severity = labels["handoffSeverity"]
    if severity is not None:
        severity = str(severity).strip().upper()
        if severity not in _HANDOFF_SEVERITIES:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: invalid handoffSeverity {severity!r}"
            )
    if should_handoff != (severity is not None):
        raise CustomerServiceLabelPolicyReviewError(
            f"{label}: handoffSeverity must be present exactly when shouldHandoff=true"
        )

    raw_slots = labels["slots"]
    if not isinstance(raw_slots, Mapping):
        raise CustomerServiceLabelPolicyReviewError(f"{label}: slots must be an object")
    slots: dict[str, str] = {}
    for raw_key, raw_value in raw_slots.items():
        key = str(raw_key).strip()
        slot_value = str(raw_value).strip() if raw_value is not None else ""
        if not key or not slot_value:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: slot keys and values must be non-empty strings"
            )
        visible_parts = [
            part.strip()
            for part in slot_value.replace("；", ";").split(";")
            if part.strip()
        ]
        if not visible_parts or any(part not in message for part in visible_parts):
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: slot {key!r} is not an exact visible source span"
            )
        slots[key] = slot_value
    return {
        "intent": intent,
        "riskLevel": risk,
        "shouldHandoff": should_handoff,
        "handoffSeverity": severity,
        "slots": slots,
    }


def _source_context(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    str,
    str,
    str,
]:
    source_rows = load_jsonl(source_dataset_path)
    source_by_id = {str(row.get("id") or ""): row for row in source_rows}
    if not source_by_id or len(source_by_id) != len(source_rows) or "" in source_by_id:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy source dataset IDs are missing or duplicated"
        )
    template_rows = load_jsonl(source_template_path)
    template_by_id = {str(row.get("id") or ""): row for row in template_rows}
    if (
        not template_by_id
        or len(template_by_id) != len(template_rows)
        or "" in template_by_id
    ):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy source template IDs are missing or duplicated"
        )
    if not set(template_by_id).issubset(source_by_id):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy source template contains unknown cases"
        )
    for case_id, template in template_by_id.items():
        if _canonical(template.get("input")) != _canonical(
            source_by_id[case_id].get("input")
        ):
            raise CustomerServiceLabelPolicyReviewError(
                f"label-policy template source differs for {case_id}"
            )
    taxonomy = load_json(taxonomy_contract_path)
    if taxonomy.get("contractVersion") != LABEL_POLICY_GUIDELINES_VERSION:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy taxonomy contract version is invalid"
        )
    return (
        source_by_id,
        template_by_id,
        sha256_file(source_dataset_path),
        sha256_file(source_template_path),
        sha256_file(taxonomy_contract_path),
    )


def _load_review_sheet(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool,
    check_sheet_hash: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    (
        source_by_id,
        template_by_id,
        source_sha,
        template_sha,
        taxonomy_sha,
    ) = _source_context(
        source_dataset_path, source_template_path, taxonomy_contract_path
    )
    manifest = load_json(_sidecar_path(sheet_path))
    if not isinstance(manifest, Mapping):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review manifest must be an object"
        )
    if manifest.get("schemaVersion") != LABEL_POLICY_REVIEW_SCHEMA:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review manifest schema is invalid"
        )
    lifecycle = str(manifest.get("lifecycle") or "")
    if lifecycle not in {"OPEN", "SEALED"}:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review lifecycle must be OPEN or SEALED"
        )
    if (
        manifest.get("sourceDatasetSha256") != source_sha
        or manifest.get("sourceTemplateSha256") != template_sha
        or manifest.get("taxonomyContractSha256") != taxonomy_sha
    ):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review source hash binding is invalid"
        )
    if manifest.get("caseCount") != len(template_by_id):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review case count differs from source template"
        )
    manifest_path = str(manifest.get("sheetPath") or "")
    if manifest_path != _path_label(sheet_path) and (
        not manifest_path or Path(manifest_path).name != sheet_path.name
    ):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy manifest sheetPath differs from artifact"
        )
    if check_sheet_hash and manifest.get("sheetSha256") != sha256_file(sheet_path):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy sealed sheet hash differs from manifest"
        )
    label_schema = manifest.get("labelSchema")
    if not isinstance(label_schema, Mapping):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review label schema is missing"
        )
    allowed_intents_value = label_schema.get("intentValues")
    if not isinstance(allowed_intents_value, list) or not allowed_intents_value:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy intent vocabulary is missing"
        )
    allowed_intents = {str(value) for value in allowed_intents_value}
    reviewer_id = str(manifest.get("reviewerId") or "").strip()
    if not reviewer_id:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy reviewerId is missing"
        )

    rows = load_jsonl(sheet_path)
    reviewed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        label = f"{sheet_path}:{index}"
        if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: unknown or missing review fields"
            )
        leaked = _forbidden_keys(row)
        if leaked:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: blinded review leaks fields {sorted(leaked)}"
            )
        case_id = str(row.get("id") or "")
        if case_id not in template_by_id or case_id in reviewed:
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: case ID is unknown or duplicated"
            )
        template = template_by_id[case_id]
        if (
            row.get("schemaVersion") != LABEL_POLICY_REVIEW_SCHEMA
            or row.get("guidelinesVersion") != LABEL_POLICY_GUIDELINES_VERSION
            or row.get("reviewerId") != reviewer_id
            or _canonical(row.get("input")) != _canonical(template.get("input"))
        ):
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: immutable review fields differ from the blind template"
            )
        message = str((row.get("input") or {}).get("message") or "")
        labels = _validate_labels(
            row.get("labels"),
            label=f"{label}.labels",
            allowed_intents=allowed_intents,
            message=message,
            require_complete=require_complete,
        )
        comment = row.get("comment")
        if not isinstance(comment, str):
            raise CustomerServiceLabelPolicyReviewError(
                f"{label}: comment must be text"
            )
        reviewed[case_id] = {
            "row": dict(row),
            "labels": labels,
            "comment": comment.strip(),
            "source": source_by_id[case_id],
        }
    if set(reviewed) != set(template_by_id):
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy review does not cover the complete selected case set"
        )
    return dict(manifest), reviewed


def validate_label_policy_review_sheet(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
    sheet_path: Path,
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Validate one returned or sealed label-policy review sheet."""

    manifest = load_json(_sidecar_path(sheet_path))
    validated, _ = _load_review_sheet(
        source_dataset_path,
        source_template_path,
        taxonomy_contract_path,
        sheet_path,
        require_complete=require_complete,
        check_sheet_hash=manifest.get("lifecycle") != "OPEN",
    )
    return validated


def seal_label_policy_review_sheet(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
    input_sheet_path: Path,
    output_sheet_path: Path,
) -> dict[str, Any]:
    """Seal the exact completed return without changing the intake artifact."""

    manifest, _ = _load_review_sheet(
        source_dataset_path,
        source_template_path,
        taxonomy_contract_path,
        input_sheet_path,
        require_complete=True,
        check_sheet_hash=False,
    )
    if manifest.get("lifecycle") != "OPEN":
        raise CustomerServiceLabelPolicyReviewError(
            "seal accepts only OPEN label-policy reviews"
        )
    output_manifest_path = _sidecar_path(output_sheet_path)
    _ensure_new((output_sheet_path, output_manifest_path))
    atomic_write_bytes(output_sheet_path, input_sheet_path.read_bytes(), overwrite=False)
    sealed_manifest = {
        **manifest,
        "artifact": "SEALED_LABEL_POLICY_REVIEW_SHEET",
        "lifecycle": "SEALED",
        "sheetPath": _path_label(output_sheet_path),
        "sheetSha256": sha256_file(output_sheet_path),
        "sourceOpenSheetPath": _path_label(input_sheet_path),
        "sourceOpenSheetSha256": sha256_file(input_sheet_path),
        "openSheetSha256AtExport": manifest["sheetSha256"],
        "sealedAt": utc_now(),
    }
    atomic_write_json(output_manifest_path, sealed_manifest, overwrite=False)
    return sealed_manifest


def _field_stats(left: Sequence[Any], right: Sequence[Any]) -> dict[str, Any]:
    if len(left) != len(right) or not left:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy agreement needs equal non-empty sequences"
        )
    a = [_canonical(value) for value in left]
    b = [_canonical(value) for value in right]
    count = len(a)
    agreement_count = sum(x == y for x, y in zip(a, b, strict=True))
    counts_a = Counter(a)
    counts_b = Counter(b)
    expected = sum(
        counts_a[key] * counts_b[key] for key in set(counts_a) | set(counts_b)
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


def compare_label_policy_reviews(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
    review_a_path: Path,
    review_b_path: Path,
) -> dict[str, Any]:
    """Compare two sealed reviews without choosing either review as truth."""

    manifest_a, review_a = _load_review_sheet(
        source_dataset_path,
        source_template_path,
        taxonomy_contract_path,
        review_a_path,
        require_complete=True,
        check_sheet_hash=True,
    )
    manifest_b, review_b = _load_review_sheet(
        source_dataset_path,
        source_template_path,
        taxonomy_contract_path,
        review_b_path,
        require_complete=True,
        check_sheet_hash=True,
    )
    if manifest_a.get("lifecycle") != "SEALED" or manifest_b.get("lifecycle") != "SEALED":
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy comparison accepts only SEALED reviews"
        )
    reviewer_a = str(manifest_a["reviewerId"])
    reviewer_b = str(manifest_b["reviewerId"])
    if reviewer_a == reviewer_b:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy reviewers must have different stable IDs"
        )
    values_a: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    values_b: dict[str, list[Any]] = {field: [] for field in _LABEL_FIELDS}
    disagreements: list[dict[str, Any]] = []
    for case_id in sorted(review_a):
        left = review_a[case_id]["labels"]
        right = review_b[case_id]["labels"]
        fields = []
        for field in _LABEL_FIELDS:
            values_a[field].append(left[field])
            values_b[field].append(right[field])
            if _canonical(left[field]) != _canonical(right[field]):
                fields.append(field)
        if fields:
            disagreements.append(
                {
                    "id": case_id,
                    "input": copy.deepcopy(review_a[case_id]["row"]["input"]),
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
    case_count = len(review_a)
    exact_count = case_count - len(disagreements)
    return {
        "schemaVersion": LABEL_POLICY_AGREEMENT_SCHEMA,
        "status": "AGREED_NO_ADJUDICATION" if not disagreements else "PENDING_ADJUDICATION",
        "releaseGateEligible": False,
        "sourceDatasetPath": _path_label(source_dataset_path),
        "sourceDatasetSha256": sha256_file(source_dataset_path),
        "sourceTemplatePath": _path_label(source_template_path),
        "sourceTemplateSha256": sha256_file(source_template_path),
        "taxonomyContractPath": _path_label(taxonomy_contract_path),
        "taxonomyContractSha256": sha256_file(taxonomy_contract_path),
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
        "exactAgreementCaseCount": exact_count,
        "disagreementCaseCount": len(disagreements),
        "caseAgreementRate": round(exact_count / case_count, 6),
        "fieldStats": {
            field: _field_stats(values_a[field], values_b[field])
            for field in _LABEL_FIELDS
        },
        "disagreements": disagreements,
        "createdAt": utc_now(),
        "note": (
            "Agreement measures annotation reliability, not system quality. "
            "Every disagreement requires an independent third-person decision."
        ),
    }


def _load_adjudication_context(path: Path) -> dict[str, dict[str, Any]]:
    rows = load_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("id") or "")
        if (
            row.get("schemaVersion") != LABEL_POLICY_REVIEW_SCHEMA
            or not case_id
            or case_id in result
            or row.get("useOnlyAfterBothBlindSheetsAreSealed") is not True
        ):
            raise CustomerServiceLabelPolicyReviewError(
                "label-policy adjudication context is invalid"
            )
        result[case_id] = dict(row)
    return result


def export_label_policy_adjudication_template(
    agreement: Mapping[str, Any],
    adjudication_context_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Export only true A/B disagreements after both blind sheets are sealed."""

    if agreement.get("schemaVersion") != LABEL_POLICY_AGREEMENT_SCHEMA:
        raise CustomerServiceLabelPolicyReviewError(
            "label-policy agreement schema is invalid"
        )
    context = _load_adjudication_context(adjudication_context_path)
    _ensure_new((output_path,))
    rows = []
    for item in agreement.get("disagreements") or []:
        case_id = str(item.get("id") or "")
        if case_id not in context:
            raise CustomerServiceLabelPolicyReviewError(
                f"missing adjudication context for {case_id}"
            )
        source = context[case_id]
        rows.append(
            {
                "schemaVersion": LABEL_POLICY_ADJUDICATION_SCHEMA,
                "id": case_id,
                "input": copy.deepcopy(item.get("input")),
                "issueCodes": copy.deepcopy(source.get("issueCodes") or []),
                "currentImmutableExpected": copy.deepcopy(
                    source.get("currentImmutableExpected")
                ),
                "sourceDatasetSha256": agreement.get("sourceDatasetSha256"),
                "taxonomyContractSha256": agreement.get("taxonomyContractSha256"),
                "reviewerA": copy.deepcopy(item.get("reviewerA")),
                "reviewerB": copy.deepcopy(item.get("reviewerB")),
                "finalLabels": _blank_labels(),
                "adjudicator": "",
                "reason": "",
            }
        )
    atomic_write_jsonl(output_path, rows, overwrite=False)
    return {
        "schemaVersion": LABEL_POLICY_ADJUDICATION_SCHEMA,
        "status": "OPEN" if rows else "NOT_REQUIRED",
        "caseCount": len(rows),
        "path": _path_label(output_path),
        "sha256AtExport": sha256_file(output_path),
    }


def render_label_policy_agreement_markdown(agreement: Mapping[str, Any]) -> str:
    lines = [
        "# 客服 v2.1 标签政策双盲一致性",
        "",
        "> 此处衡量标注可靠性，不是系统准确率。",
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
    lines.extend(["", "## 分歧", "", "| Case | 字段 | 用户问题 |", "|---|---|---|"])
    for item in agreement.get("disagreements") or []:
        message = str((item.get("input") or {}).get("message") or "").replace(
            "|", "\\|"
        )
        lines.append(
            f"| `{item.get('id')}` | `{', '.join(item.get('fields') or [])}` | {message} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_label_policy_adjudication_instructions(
    agreement: Mapping[str, Any], *, editable_path: str
) -> str:
    return (
        "# 客服 v2.1 标签政策仲裁\n\n"
        f"只需处理 `{agreement.get('disagreementCaseCount')}` 条真实分歧。"
        "仲裁者必须不是 reviewer-a/reviewer-b，按随包 taxonomy v2.1 独立判断。\n\n"
        f"编辑 `{editable_path}`，每行仅填写 `finalLabels`、`adjudicator`、`reason`；"
        "不要修改题面、旧标签、A/B 结果或哈希字段。全部完成后原名返回。\n"
    )


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
    return "".join(
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    )


def write_pending_label_policy_review_evidence(
    source_dataset_path: Path,
    source_template_path: Path,
    taxonomy_contract_path: Path,
    adjudication_context_path: Path,
    review_a_path: Path,
    review_b_path: Path,
    *,
    output_dir: Path,
    adjudication_output: Path | None = None,
) -> dict[str, Any]:
    """Freeze the dual review and export a separate editable adjudication sheet."""

    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite pending label-policy evidence: {output_dir}"
        )
    if adjudication_output is not None:
        _ensure_new((adjudication_output,))
    agreement = compare_label_policy_reviews(
        source_dataset_path,
        source_template_path,
        taxonomy_contract_path,
        review_a_path,
        review_b_path,
    )
    if agreement.get("status") != "PENDING_ADJUDICATION":
        raise CustomerServiceLabelPolicyReviewError(
            "pending evidence requires at least one label-policy disagreement"
        )
    manifest_a = load_json(_sidecar_path(review_a_path))
    manifest_b = load_json(_sidecar_path(review_b_path))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        frozen_agreement = copy.deepcopy(agreement)
        frozen_agreement["reviewA"]["path"] = "reviews/reviewer-a.sealed.jsonl"
        frozen_agreement["reviewB"]["path"] = "reviews/reviewer-b.sealed.jsonl"
        atomic_write_json(staging / "agreement.json", frozen_agreement, overwrite=False)
        atomic_write_text(
            staging / "agreement.md",
            render_label_policy_agreement_markdown(frozen_agreement),
            overwrite=False,
        )
        template = export_label_policy_adjudication_template(
            frozen_agreement,
            adjudication_context_path,
            staging / "adjudication.template.jsonl",
        )
        editable_label = (
            _path_label(adjudication_output)
            if adjudication_output is not None
            else "adjudication.template.jsonl"
        )
        atomic_write_text(
            staging / "adjudication-needed.md",
            render_label_policy_adjudication_instructions(
                frozen_agreement, editable_path=editable_label
            ),
            overwrite=False,
        )
        shutil.copy2(taxonomy_contract_path, staging / "taxonomy-contract-v2.1.json")
        reviews = staging / "reviews"
        reviews.mkdir()
        for name, source in (
            ("reviewer-a.sealed.jsonl", review_a_path),
            ("reviewer-b.sealed.jsonl", review_b_path),
        ):
            shutil.copy2(source, reviews / name)
            shutil.copy2(_sidecar_path(source), reviews / f"{name}.manifest.json")
        lifecycle = {
            "schemaVersion": LABEL_POLICY_PENDING_LIFECYCLE_SCHEMA,
            "lifecycle": "PENDING_ADJUDICATION",
            "releaseGateEligible": False,
            "successorDatasetPublished": False,
            "sourceDatasetSha256": agreement["sourceDatasetSha256"],
            "taxonomyContractSha256": agreement["taxonomyContractSha256"],
            "caseCount": agreement["caseCount"],
            "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
            "disagreementCaseCount": agreement["disagreementCaseCount"],
            "reviewers": [manifest_a["reviewerId"], manifest_b["reviewerId"]],
            "reviewerIdentityEvidenceStatus": "ATTESTATIONS_NOT_INCLUDED",
            "createdAt": utc_now(),
        }
        atomic_write_json(staging / "lifecycle.json", lifecycle, overwrite=False)
        evidence_manifest = {
            "schemaVersion": LABEL_POLICY_PENDING_EVIDENCE_SCHEMA,
            "status": "PENDING_ADJUDICATION",
            "releaseGateEligible": False,
            "sourceDatasetSha256": agreement["sourceDatasetSha256"],
            "sourceTemplateSha256": agreement["sourceTemplateSha256"],
            "taxonomyContractSha256": agreement["taxonomyContractSha256"],
            "caseCount": agreement["caseCount"],
            "reviewers": {
                "reviewerA": {
                    "reviewerId": manifest_a["reviewerId"],
                    "path": "reviews/reviewer-a.sealed.jsonl",
                    "sha256": sha256_file(review_a_path),
                },
                "reviewerB": {
                    "reviewerId": manifest_b["reviewerId"],
                    "path": "reviews/reviewer-b.sealed.jsonl",
                    "sha256": sha256_file(review_b_path),
                },
            },
            "agreement": {
                "path": "agreement.json",
                "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
                "disagreementCaseCount": agreement["disagreementCaseCount"],
                "caseAgreementRate": agreement["caseAgreementRate"],
            },
            "adjudicationTemplate": {
                "path": "adjudication.template.jsonl",
                "caseCount": template["caseCount"],
                "sha256AtExport": template["sha256AtExport"],
            },
            "createdAt": lifecycle["createdAt"],
            "files": _inventory(staging),
        }
        atomic_write_json(
            staging / "evidence-manifest.json", evidence_manifest, overwrite=False
        )
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if adjudication_output is not None:
        atomic_write_bytes(
            adjudication_output,
            (output_dir / "adjudication.template.jsonl").read_bytes(),
            overwrite=False,
        )
    return verify_pending_label_policy_review_evidence(output_dir)


def verify_pending_label_policy_review_evidence(root: Path) -> dict[str, Any]:
    """Verify checksums and the core lifecycle bindings of a pending package."""

    errors: list[str] = []
    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise CustomerServiceLabelPolicyReviewError(
            "pending label-policy package is missing SHA256SUMS"
        )
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or name in expected:
            errors.append(f"checksum-line:{line}")
            continue
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        errors.append("inventory")
    for name, digest in expected.items():
        path = root / name
        if not path.is_file() or sha256_file(path) != digest:
            errors.append(f"checksum:{name}")
    manifest = load_json(root / "evidence-manifest.json")
    lifecycle = load_json(root / "lifecycle.json")
    agreement = load_json(root / "agreement.json")
    template_rows = load_jsonl(root / "adjudication.template.jsonl")
    if (
        manifest.get("schemaVersion") != LABEL_POLICY_PENDING_EVIDENCE_SCHEMA
        or lifecycle.get("schemaVersion") != LABEL_POLICY_PENDING_LIFECYCLE_SCHEMA
        or agreement.get("schemaVersion") != LABEL_POLICY_AGREEMENT_SCHEMA
    ):
        errors.append("schema")
    if any(
        value.get("status", value.get("lifecycle")) != "PENDING_ADJUDICATION"
        for value in (manifest, lifecycle, agreement)
    ):
        errors.append("lifecycle")
    disagreement_count = int(agreement.get("disagreementCaseCount") or 0)
    if disagreement_count <= 0 or len(template_rows) != disagreement_count:
        errors.append("adjudication-coverage")
    if manifest.get("files") != _inventory(root):
        errors.append("manifest-inventory")
    if errors:
        raise CustomerServiceLabelPolicyReviewError(
            "pending label-policy evidence is invalid: " + ", ".join(errors)
        )
    return {
        "valid": True,
        "root": str(root.resolve()),
        "caseCount": agreement["caseCount"],
        "exactAgreementCaseCount": agreement["exactAgreementCaseCount"],
        "disagreementCaseCount": disagreement_count,
        "sha256SumsSha256": sha256_file(sums_path),
    }
