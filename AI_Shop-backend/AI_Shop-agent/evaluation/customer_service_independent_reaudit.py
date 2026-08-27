"""Validate and score the preregistered customer-service provenance re-audit.

The initial blind re-audit is a 12-case gate over the historical 60-case v2
additions.  A failed metric requires review of all 60 cases; it must never be
rescored on a more convenient sample.  Custody/identity evidence is evaluated
separately from label agreement so good agreement cannot erase missing human
provenance.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
import tempfile
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
from evaluation.customer_service_provenance import REAUDIT_SCHEMA

INDEPENDENT_REAUDIT_RESULT_SCHEMA = (
    "aishop-customer-service-independent-reaudit-result/v1"
)
INDEPENDENT_REAUDIT_EVIDENCE_SCHEMA = (
    "aishop-customer-service-independent-reaudit-evidence/v1"
)
INDEPENDENT_REAUDIT_LIFECYCLE_SCHEMA = (
    "aishop-customer-service-independent-reaudit-lifecycle/v1"
)
INDEPENDENT_REAUDIT_EXPANSION_SCHEMA = (
    "aishop-customer-service-independent-reaudit-expansion/v1"
)
INDEPENDENT_REAUDIT_ATTESTATION_V2_SCHEMA = (
    "aishop-independent-reaudit-custody-attestation/v2"
)

_LABEL_FIELDS = (
    "intent",
    "riskLevel",
    "shouldHandoff",
    "handoffSeverity",
    "slots",
)
_ROW_FIELDS = frozenset(
    {"schemaVersion", "id", "input", "reviewerId", "labels", "comment"}
)
_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
_HANDOFF_SEVERITIES = frozenset({"NORMAL", "CRITICAL"})
_PLACEHOLDER_IDENTITIES = frozenset(
    {
        "",
        "NOT_PROVIDED",
        "NOT_PROVIDED_TO_REVIEWER",
        "UNASSIGNED",
        "UNKNOWN",
        "NONE",
    }
)


class CustomerServiceIndependentReauditError(ValueError):
    """Raised when re-audit content or evidence provenance is invalid."""


def _canonical(value: Any) -> bytes:
    return canonical_json_bytes(value)


def _sidecar_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".manifest.json")


def _path_label(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _ensure_new(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite independent re-audit artifact(s): "
            + ", ".join(existing)
        )


def _normalized_labels(value: Mapping[str, Any]) -> dict[str, Any]:
    slots = value.get("slots") or {}
    if not isinstance(slots, Mapping):
        slots = {}
    return {
        "intent": value.get("intent"),
        "riskLevel": value.get("riskLevel"),
        "shouldHandoff": value.get("shouldHandoff"),
        "handoffSeverity": value.get("handoffSeverity"),
        "slots": {str(key): str(item) for key, item in slots.items()},
    }


def _validate_labels(
    value: Any,
    *,
    label: str,
    allowed_intents: set[str],
    message: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(_LABEL_FIELDS):
        raise CustomerServiceIndependentReauditError(
            f"{label}: labels must contain exactly {list(_LABEL_FIELDS)}"
        )
    labels = {field: value.get(field) for field in _LABEL_FIELDS}
    if any(labels[field] is None for field in ("intent", "riskLevel", "shouldHandoff", "slots")):
        raise CustomerServiceIndependentReauditError(f"{label}: labels are incomplete")
    intent = str(labels["intent"]).strip()
    if intent not in allowed_intents:
        raise CustomerServiceIndependentReauditError(
            f"{label}: intent {intent!r} is outside the frozen taxonomy"
        )
    risk = str(labels["riskLevel"]).strip().upper()
    if risk not in _RISK_LEVELS:
        raise CustomerServiceIndependentReauditError(
            f"{label}: invalid riskLevel {risk!r}"
        )
    should_handoff = labels["shouldHandoff"]
    if not isinstance(should_handoff, bool):
        raise CustomerServiceIndependentReauditError(
            f"{label}: shouldHandoff must be boolean"
        )
    severity = labels["handoffSeverity"]
    if severity is not None:
        severity = str(severity).strip().upper()
        if severity not in _HANDOFF_SEVERITIES:
            raise CustomerServiceIndependentReauditError(
                f"{label}: invalid handoffSeverity {severity!r}"
            )
    if should_handoff != (severity is not None):
        raise CustomerServiceIndependentReauditError(
            f"{label}: handoffSeverity must be present exactly when shouldHandoff=true"
        )
    raw_slots = labels["slots"]
    if not isinstance(raw_slots, Mapping):
        raise CustomerServiceIndependentReauditError(f"{label}: slots must be an object")
    slots: dict[str, str] = {}
    for raw_key, raw_value in raw_slots.items():
        key = str(raw_key).strip()
        slot_value = str(raw_value).strip() if raw_value is not None else ""
        if not key or not slot_value:
            raise CustomerServiceIndependentReauditError(
                f"{label}: slot keys and values must be non-empty strings"
            )
        visible_parts = [
            part.strip()
            for part in slot_value.replace("；", ";").split(";")
            if part.strip()
        ]
        if not visible_parts or any(part not in message for part in visible_parts):
            raise CustomerServiceIndependentReauditError(
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
    source_dataset_path: Path, initial_template_path: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    source_rows = load_jsonl(source_dataset_path)
    source = {str(row.get("id") or ""): row for row in source_rows}
    if not source or len(source) != len(source_rows) or "" in source:
        raise CustomerServiceIndependentReauditError(
            "independent re-audit source IDs are missing or duplicated"
        )
    template_rows = load_jsonl(initial_template_path)
    template = {str(row.get("id") or ""): row for row in template_rows}
    if not template or len(template) != len(template_rows) or "" in template:
        raise CustomerServiceIndependentReauditError(
            "independent re-audit template IDs are missing or duplicated"
        )
    if not set(template).issubset(source):
        raise CustomerServiceIndependentReauditError(
            "independent re-audit template contains unknown cases"
        )
    for case_id, row in template.items():
        if _canonical(row.get("input")) != _canonical(source[case_id].get("input")):
            raise CustomerServiceIndependentReauditError(
                f"independent re-audit source differs for {case_id}"
            )
    allowed_intents = {
        str((row.get("expected") or {}).get("intent") or "") for row in source_rows
    }
    allowed_intents.discard("")
    return source, template, allowed_intents


def validate_independent_reaudit_sheet(
    source_dataset_path: Path,
    initial_template_path: Path,
    returned_sheet_path: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate a completed returned sheet against the frozen blind template."""

    source, template, allowed_intents = _source_context(
        source_dataset_path, initial_template_path
    )
    manifest = load_json(_sidecar_path(returned_sheet_path))
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schemaVersion") != REAUDIT_SCHEMA
        or manifest.get("artifact") != "BLINDED_INDEPENDENT_REAUDIT_SHEET"
        or manifest.get("lifecycle") != "OPEN_UNASSIGNED"
    ):
        raise CustomerServiceIndependentReauditError(
            "independent re-audit return manifest is invalid"
        )
    if (
        manifest.get("sourceHumanDatasetSha256") != sha256_file(source_dataset_path)
        or manifest.get("sheetSha256") != sha256_file(initial_template_path)
        or manifest.get("caseCount") != len(template)
    ):
        raise CustomerServiceIndependentReauditError(
            "independent re-audit source/template hash binding is invalid"
        )
    rows = load_jsonl(returned_sheet_path)
    reviewed: dict[str, dict[str, Any]] = {}
    reviewer_ids: set[str] = set()
    for index, row in enumerate(rows, 1):
        label = f"{returned_sheet_path}:{index}"
        if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
            raise CustomerServiceIndependentReauditError(
                f"{label}: unknown or missing fields"
            )
        case_id = str(row.get("id") or "")
        reviewer_id = str(row.get("reviewerId") or "").strip()
        if (
            row.get("schemaVersion") != REAUDIT_SCHEMA
            or case_id not in template
            or case_id in reviewed
            or not reviewer_id
            or reviewer_id == "UNASSIGNED_INDEPENDENT_REVIEWER"
            or _canonical(row.get("input")) != _canonical(template[case_id].get("input"))
        ):
            raise CustomerServiceIndependentReauditError(
                f"{label}: immutable fields, ID, or reviewer identity are invalid"
            )
        reviewer_ids.add(reviewer_id)
        message = str((row.get("input") or {}).get("message") or "")
        labels = _validate_labels(
            row.get("labels"),
            label=f"{label}.labels",
            allowed_intents=allowed_intents,
            message=message,
        )
        comment = row.get("comment")
        if not isinstance(comment, str):
            raise CustomerServiceIndependentReauditError(
                f"{label}: comment must be text"
            )
        reviewed[case_id] = {
            "row": dict(row),
            "labels": labels,
            "comment": comment.strip(),
            "historical": _normalized_labels(source[case_id].get("expected") or {}),
        }
    if set(reviewed) != set(template) or len(reviewer_ids) != 1:
        raise CustomerServiceIndependentReauditError(
            "independent re-audit must cover the template with one stable reviewer ID"
        )
    return dict(manifest), reviewed


def audit_independent_reaudit_attestation(
    attestation_path: Path, *, sheet_reviewer_id: str
) -> dict[str, Any]:
    """Report custody gaps without inventing missing signatures or identities."""

    value = load_json(attestation_path)
    findings: list[dict[str, str]] = []
    if value.get("schemaVersion") != "aishop-independent-reaudit-custody-attestation/v1":
        findings.append(
            {"code": "ATTESTATION_SCHEMA_INVALID", "severity": "BLOCKING"}
        )
    reviewer_identity = str(value.get("reviewerIdentity") or "").strip()
    custodian_identity = str(value.get("custodianIdentity") or "").strip()
    if reviewer_identity.upper() in _PLACEHOLDER_IDENTITIES:
        findings.append(
            {"code": "REVIEWER_IDENTITY_MISSING", "severity": "BLOCKING"}
        )
    if custodian_identity.upper() in _PLACEHOLDER_IDENTITIES:
        findings.append(
            {"code": "CUSTODIAN_IDENTITY_MISSING", "severity": "BLOCKING"}
        )
    if value.get("status") not in {"ATTESTED", "COMPLETE"}:
        findings.append(
            {"code": "CUSTODY_ATTESTATION_INCOMPLETE", "severity": "BLOCKING"}
        )
    for field in (
        "reviewerDidNotViewDraftExpectedOrModelOutputs",
        "reviewerDidNotViewPriorReviewsAgreementOrAdjudication",
        "reviewerDidNotViewSourceHumanLabels",
        "reviewerIndependentOfDatasetAndModelDevelopment",
    ):
        if value.get(field) is not True:
            findings.append(
                {"code": f"ATTESTATION_{field.upper()}_NOT_TRUE", "severity": "BLOCKING"}
            )
    # The original v1 template omitted a field binding the stable sheet ID to
    # the attested identity.  Equality is accepted as an unambiguous binding;
    # otherwise a truthful v2 attestation is required.
    if reviewer_identity != sheet_reviewer_id:
        findings.append(
            {"code": "SHEET_REVIEWER_ID_NOT_BOUND_TO_ATTESTED_IDENTITY", "severity": "BLOCKING"}
        )
    return {
        "status": "VALID" if not findings else "INCOMPLETE",
        "valid": not findings,
        "sheetReviewerId": sheet_reviewer_id,
        "attestedReviewerIdentity": reviewer_identity,
        "custodianIdentity": custodian_identity,
        "attestationSha256": sha256_file(attestation_path),
        "findings": findings,
    }


def score_independent_reaudit(
    source_dataset_path: Path,
    initial_template_path: Path,
    returned_sheet_path: Path,
    attestation_path: Path,
) -> dict[str, Any]:
    """Score the fixed 12-case gate and apply its preregistered failure action."""

    manifest, reviewed = validate_independent_reaudit_sheet(
        source_dataset_path, initial_template_path, returned_sheet_path
    )
    reviewer_id = str(next(iter(reviewed.values()))["row"]["reviewerId"])
    field_counts = {field: 0 for field in _LABEL_FIELDS}
    handoff_count = 0
    mismatches: list[dict[str, Any]] = []
    critical_mismatches: list[str] = []
    for case_id in sorted(reviewed):
        actual = reviewed[case_id]["labels"]
        historical = reviewed[case_id]["historical"]
        differing = [
            field
            for field in _LABEL_FIELDS
            if _canonical(actual[field]) != _canonical(historical[field])
        ]
        for field in _LABEL_FIELDS:
            field_counts[field] += _canonical(actual[field]) == _canonical(
                historical[field]
            )
        handoff_matches = (
            actual["shouldHandoff"] == historical["shouldHandoff"]
            and actual["handoffSeverity"] == historical["handoffSeverity"]
        )
        handoff_count += handoff_matches
        critical_case = any(
            labels.get("riskLevel") == "HIGH"
            or labels.get("handoffSeverity") == "CRITICAL"
            for labels in (actual, historical)
        )
        critical_core_difference = any(
            field in differing
            for field in (
                "intent",
                "riskLevel",
                "shouldHandoff",
                "handoffSeverity",
            )
        )
        if critical_case and critical_core_difference:
            critical_mismatches.append(case_id)
        if differing:
            mismatches.append(
                {
                    "id": case_id,
                    "fields": differing,
                    "historicalLabels": historical,
                    "reauditLabels": actual,
                    "comment": reviewed[case_id]["comment"],
                }
            )
    count = len(reviewed)
    metrics = {
        "intentAgreement": round(field_counts["intent"] / count, 6),
        "riskAgreement": round(field_counts["riskLevel"] / count, 6),
        "handoffAgreement": round(handoff_count / count, 6),
        "slotExactAgreement": round(field_counts["slots"] / count, 6),
        "criticalMismatchCount": len(critical_mismatches),
    }
    preregistered = dict(manifest.get("preregisteredAcceptance") or {})
    gates = {
        "criticalMismatchPassed": metrics["criticalMismatchCount"]
        == int(preregistered.get("criticalMismatchCount", 0)),
        "intentAgreementPassed": metrics["intentAgreement"]
        >= float(preregistered.get("intentAgreementMinimum", 1.0)),
        "riskAgreementPassed": metrics["riskAgreement"]
        >= float(preregistered.get("riskAgreementMinimum", 1.0)),
        "handoffAgreementPassed": metrics["handoffAgreement"]
        >= float(preregistered.get("handoffAgreementMinimum", 1.0)),
        "slotExactAgreementPassed": metrics["slotExactAgreement"]
        >= float(preregistered.get("slotExactAgreementMinimum", 1.0)),
    }
    label_gate_passed = all(gates.values())
    attestation = audit_independent_reaudit_attestation(
        attestation_path, sheet_reviewer_id=reviewer_id
    )
    return {
        "schemaVersion": INDEPENDENT_REAUDIT_RESULT_SCHEMA,
        "status": (
            "PASSED_WITH_CUSTODY_COMPLETE"
            if label_gate_passed and attestation["valid"]
            else "CUSTODY_INCOMPLETE"
            if label_gate_passed
            else "EXPANSION_REQUIRED"
        ),
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "sourceDatasetPath": _path_label(source_dataset_path),
        "sourceDatasetSha256": sha256_file(source_dataset_path),
        "initialTemplatePath": _path_label(initial_template_path),
        "initialTemplateSha256": sha256_file(initial_template_path),
        "returnedSheetPath": _path_label(returned_sheet_path),
        "returnedSheetSha256": sha256_file(returned_sheet_path),
        "reviewerId": reviewer_id,
        "caseCount": count,
        "metrics": metrics,
        "fieldAgreementCounts": field_counts,
        "criticalMismatchIds": critical_mismatches,
        "mismatchCaseCount": len(mismatches),
        "mismatches": mismatches,
        "preregisteredAcceptance": preregistered,
        "gates": {**gates, "labelGatePassed": label_gate_passed},
        "failureAction": (
            None
            if label_gate_passed
            else preregistered.get(
                "failureAction", "EXPAND_TO_FULL_60_CASE_INDEPENDENT_REAUDIT"
            )
        ),
        "attestation": attestation,
        "provenanceControlPassed": label_gate_passed and attestation["valid"],
        "createdAt": utc_now(),
        "note": (
            "This re-audit tests historical label reproducibility only. It cannot "
            "restore final-unseen eligibility to developer-visible data."
        ),
    }


def render_independent_reaudit_result(result: Mapping[str, Any]) -> str:
    metrics = result.get("metrics") or {}
    gates = result.get("gates") or {}
    return (
        "# 客服 v2 来源独立复核结果\n\n"
        f"- 状态：`{result.get('status')}`\n"
        f"- 样本：`{result.get('caseCount')}`\n"
        f"- intent agreement：`{metrics.get('intentAgreement')}` / "
        f"pass `{gates.get('intentAgreementPassed')}`\n"
        f"- risk agreement：`{metrics.get('riskAgreement')}` / "
        f"pass `{gates.get('riskAgreementPassed')}`\n"
        f"- handoff agreement：`{metrics.get('handoffAgreement')}` / "
        f"pass `{gates.get('handoffAgreementPassed')}`\n"
        f"- slot exact agreement：`{metrics.get('slotExactAgreement')}` / "
        f"pass `{gates.get('slotExactAgreementPassed')}`\n"
        f"- critical mismatches：`{metrics.get('criticalMismatchCount')}` / "
        f"pass `{gates.get('criticalMismatchPassed')}`\n"
        f"- 保管/身份声明：`{(result.get('attestation') or {}).get('status')}`\n\n"
        "未通过任一预注册门槛时，必须扩展到全部 60 条；不得重抽更容易的样本。\n"
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


def write_independent_reaudit_evidence(
    source_dataset_path: Path,
    initial_template_path: Path,
    returned_sheet_path: Path,
    attestation_path: Path,
    frozen_guideline_path: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Publish exact return bytes and the fixed-sample score as read-only evidence."""

    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite independent re-audit evidence: {output_dir}"
        )
    result = score_independent_reaudit(
        source_dataset_path,
        initial_template_path,
        returned_sheet_path,
        attestation_path,
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        sources = staging / "source"
        review = staging / "review"
        sources.mkdir()
        review.mkdir()
        for destination, source in (
            (sources / "initial-template.jsonl", initial_template_path),
            (sources / "initial-template.jsonl.manifest.json", _sidecar_path(initial_template_path)),
            (sources / "frozen-historical-guideline-v1.md", frozen_guideline_path),
            (review / "independent-reaudit.returned.jsonl", returned_sheet_path),
            (
                review / "independent-reaudit.returned.jsonl.manifest.json",
                _sidecar_path(returned_sheet_path),
            ),
            (review / "custody-attestation.returned.json", attestation_path),
        ):
            atomic_write_bytes(destination, source.read_bytes(), overwrite=False)
        atomic_write_json(staging / "result.json", result, overwrite=False)
        atomic_write_text(
            staging / "result.md",
            render_independent_reaudit_result(result),
            overwrite=False,
        )
        lifecycle = {
            "schemaVersion": INDEPENDENT_REAUDIT_LIFECYCLE_SCHEMA,
            "lifecycle": result["status"],
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
            "labelGatePassed": result["gates"]["labelGatePassed"],
            "custodyAttestationValid": result["attestation"]["valid"],
            "full60ReviewRequired": not result["gates"]["labelGatePassed"],
            "createdAt": result["createdAt"],
        }
        atomic_write_json(staging / "lifecycle.json", lifecycle, overwrite=False)
        manifest = {
            "schemaVersion": INDEPENDENT_REAUDIT_EVIDENCE_SCHEMA,
            "status": result["status"],
            "releaseGateEligible": False,
            "sourceDatasetSha256": result["sourceDatasetSha256"],
            "initialTemplateSha256": result["initialTemplateSha256"],
            "returnedSheetSha256": result["returnedSheetSha256"],
            "attestationSha256": result["attestation"]["attestationSha256"],
            "reviewerId": result["reviewerId"],
            "caseCount": result["caseCount"],
            "full60ReviewRequired": not result["gates"]["labelGatePassed"],
            "createdAt": result["createdAt"],
            "files": _inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_independent_reaudit_evidence(output_dir)


def _stable_order(rows: Sequence[Mapping[str, Any]], *, seed: str) -> list[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda row: hashlib.sha256(
            f"{seed}\0{row.get('id')}".encode("utf-8")
        ).hexdigest(),
    )


def _blank_reaudit_rows(
    rows: Sequence[Mapping[str, Any]], *, seed: str, reviewer_id: str
) -> list[dict[str, Any]]:
    return [
        {
            "schemaVersion": REAUDIT_SCHEMA,
            "id": str(row["id"]),
            "input": copy.deepcopy(row["input"]),
            "reviewerId": reviewer_id,
            "labels": {
                "intent": None,
                "riskLevel": None,
                "shouldHandoff": None,
                "handoffSeverity": None,
                "slots": None,
            },
            "comment": "",
        }
        for row in _stable_order(rows, seed=seed)
    ]


def build_independent_reaudit_expansion_handoff(
    source_dataset_path: Path,
    initial_template_path: Path,
    returned_sheet_path: Path,
    frozen_guideline_path: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Build both safe paths after failure: continue 48 or restart all 60."""

    if output_dir.exists():
        raise FileExistsError(
            f"refusing to overwrite independent re-audit expansion: {output_dir}"
        )
    _manifest, reviewed = validate_independent_reaudit_sheet(
        source_dataset_path, initial_template_path, returned_sheet_path
    )
    source_rows = load_jsonl(source_dataset_path)
    reviewed_ids = set(reviewed)
    remaining = [row for row in source_rows if str(row.get("id")) not in reviewed_ids]
    if len(source_rows) != 60 or len(reviewed_ids) != 12 or len(remaining) != 48:
        raise CustomerServiceIndependentReauditError(
            "full-expansion handoff requires the preregistered 12-of-60 inputs"
        )
    reviewer_id = str(next(iter(reviewed.values()))["row"]["reviewerId"])
    output_dir.mkdir(parents=True)
    remaining_path = output_dir / "remaining-48.open.jsonl"
    restart_path = output_dir / "full-60-restart.open.jsonl"
    atomic_write_jsonl(
        remaining_path,
        _blank_reaudit_rows(
            remaining,
            seed="customer-service-v2-independent-reaudit-expansion-20260827",
            reviewer_id=reviewer_id,
        ),
        overwrite=False,
    )
    atomic_write_jsonl(
        restart_path,
        _blank_reaudit_rows(
            source_rows,
            seed="customer-service-v2-independent-reaudit-full-restart-20260827",
            reviewer_id="REPLACE_WITH_NEW_INDEPENDENT_HUMAN_REVIEWER_ID",
        ),
        overwrite=False,
    )
    common = {
        "schemaVersion": INDEPENDENT_REAUDIT_EXPANSION_SCHEMA,
        "artifact": "BLINDED_INDEPENDENT_REAUDIT_EXPANSION_SHEET",
        "lifecycle": "OPEN",
        "sourceHumanDatasetSha256": sha256_file(source_dataset_path),
        "goldLabelsPresent": False,
        "modelPredictionsPresent": False,
        "failureTrigger": "INITIAL_12_CASE_PREREGISTERED_GATE_FAILED",
        "fullTargetCaseCount": 60,
    }
    atomic_write_json(
        _sidecar_path(remaining_path),
        {
            **common,
            "mode": "CONTINUE_SAME_VERIFIED_HUMAN_REVIEWER",
            "caseCount": 48,
            "reviewerId": reviewer_id,
            "initialCompletedCaseCount": 12,
            "initialReturnedSheetSha256": sha256_file(returned_sheet_path),
            "sheetSha256": sha256_file(remaining_path),
        },
        overwrite=False,
    )
    atomic_write_json(
        _sidecar_path(restart_path),
        {
            **common,
            "mode": "RESTART_WITH_NEW_INDEPENDENT_HUMAN_REVIEWER",
            "caseCount": 60,
            "reviewerId": "REPLACE_WITH_NEW_INDEPENDENT_HUMAN_REVIEWER_ID",
            "sheetSha256": sha256_file(restart_path),
        },
        overwrite=False,
    )
    attestation = output_dir / "custody-attestation-v2.template.json"
    atomic_write_json(
        attestation,
        {
            "schemaVersion": INDEPENDENT_REAUDIT_ATTESTATION_V2_SCHEMA,
            "artifactId": output_dir.name,
            "reviewMode": None,
            "sheetReviewerId": None,
            "reviewerIdentity": None,
            "reviewerIsHuman": None,
            "reviewerIndependentOfDatasetAndModelDevelopment": None,
            "reviewerDidNotViewSourceHumanLabels": None,
            "reviewerDidNotViewDraftExpectedOrModelOutputs": None,
            "reviewerDidNotViewPriorReviewsAgreementOrAdjudication": None,
            "generativeAiProducedOrSuggestedLabels": None,
            "custodianIdentity": None,
            "materialsProvided": [],
            "completedSheetSha256": None,
            "attestedAt": None,
            "signaturesOrExternalReferences": [],
            "status": "TEMPLATE_NOT_EVIDENCE",
        },
        overwrite=False,
    )
    shutil.copy2(frozen_guideline_path, output_dir / "frozen-historical-guideline-v1.md")
    atomic_write_text(
        output_dir / "README.md",
        "# 来源复核扩展到 60 条\n\n"
        "初始 12 条的 slot exact agreement 未过预注册门槛。\n\n"
        "- 仅当原 reviewer 确为独立真人且能完成 v2 保管声明时，继续填写 "
        "`remaining-48.open.jsonl`。\n"
        "- 若原 12 条由 AI/开发者完成，或身份与独立性无法真实证明，废弃其正式证据资格，"
        "由新的独立真人填写 `full-60-restart.open.jsonl`。\n\n"
        "两份表只能二选一。不要向 reviewer 提供历史标签、旧评审、模型输出或一致性结果。"
        "回传填写后的 JSONL、原 manifest 与填妥的 v2 保管声明。\n",
        overwrite=False,
    )
    atomic_write_text(output_dir / "SHA256SUMS", _sums(output_dir), overwrite=False)
    return {
        "valid": True,
        "status": "OPEN_FULL_60_REVIEW_REQUIRED",
        "outputDir": str(output_dir.resolve()),
        "remainingCaseCount": 48,
        "restartCaseCount": 60,
        "sha256SumsSha256": sha256_file(output_dir / "SHA256SUMS"),
    }


def verify_independent_reaudit_evidence(root: Path) -> dict[str, Any]:
    """Verify exact package checksums and result/lifecycle consistency."""

    sums_path = root / "SHA256SUMS"
    if not sums_path.is_file():
        raise CustomerServiceIndependentReauditError(
            "independent re-audit evidence is missing SHA256SUMS"
        )
    errors: list[str] = []
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
    result = load_json(root / "result.json")
    if (
        manifest.get("schemaVersion") != INDEPENDENT_REAUDIT_EVIDENCE_SCHEMA
        or lifecycle.get("schemaVersion") != INDEPENDENT_REAUDIT_LIFECYCLE_SCHEMA
        or result.get("schemaVersion") != INDEPENDENT_REAUDIT_RESULT_SCHEMA
    ):
        errors.append("schema")
    if (
        manifest.get("status") != result.get("status")
        or lifecycle.get("lifecycle") != result.get("status")
        or manifest.get("returnedSheetSha256")
        != sha256_file(root / "review/independent-reaudit.returned.jsonl")
        or manifest.get("files") != _inventory(root)
    ):
        errors.append("binding")
    if errors:
        raise CustomerServiceIndependentReauditError(
            "independent re-audit evidence is invalid: " + ", ".join(errors)
        )
    return {
        "valid": True,
        "root": str(root.resolve()),
        "status": result["status"],
        "caseCount": result["caseCount"],
        "metrics": result["metrics"],
        "labelGatePassed": result["gates"]["labelGatePassed"],
        "attestationValid": result["attestation"]["valid"],
        "sha256SumsSha256": sha256_file(sums_path),
    }
