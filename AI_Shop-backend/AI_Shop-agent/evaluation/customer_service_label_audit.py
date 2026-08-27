"""Deterministic consistency audit for customer-service human gold labels.

The audit deliberately does not rewrite HUMAN_VERIFIED labels.  It detects
cross-case policy collisions that make a metric hard to interpret, emits a
blind re-annotation sheet, and fails closed for release use until real humans
re-adjudicate a successor dataset.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    load_json,
    sha256_file,
    utc_now,
)
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset

AUDIT_SCHEMA = "aishop-customer-service-label-consistency-audit/v1"
REAUDIT_SCHEMA = "aishop-customer-service-label-policy-reaudit/v1"
PACKAGE_SCHEMA = "aishop-customer-service-label-audit-package/v1"

_RECOMMEND_REFINEMENT_MARKERS = (
    "刚才推荐",
    "上一批",
    "换一批",
    "再推荐",
    "重新给",
)
_FOLLOWUP_REVIEW_MARKERS = ("追评", "追加评价", "二次评价", "再评")
_CURRENCY_MARKERS = ("元", "人民币", "¥", "￥")
_COUNT_RE = re.compile(r"(?:[二两三四五六七八九十]|[2-9])\s*次")


class CustomerServiceLabelAuditError(ValueError):
    """Raised when an audit input or immutable package is invalid."""


def _expected(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("expected") or {}
    if not isinstance(value, Mapping):
        raise CustomerServiceLabelAuditError(f"case {row.get('id')} expected must be an object")
    return dict(value)


def _message(row: Mapping[str, Any]) -> str:
    value = row.get("input") or {}
    return str(value.get("message") or "") if isinstance(value, Mapping) else ""


def _finding(
    *,
    code: str,
    severity: str,
    title: str,
    case_ids: Sequence[str],
    evidence: Mapping[str, Any],
    metric_impact: Sequence[str],
    required_resolution: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "status": "REQUIRES_HUMAN_READJUDICATION",
        "title": title,
        "caseCount": len(set(case_ids)),
        "caseIds": sorted(set(case_ids)),
        "evidence": dict(evidence),
        "metricImpact": list(metric_impact),
        "requiredResolution": required_resolution,
    }


def _amount_style(message: str, amount: str) -> str:
    normalized_message = unicodedata.normalize("NFKC", message)
    normalized_amount = unicodedata.normalize("NFKC", amount)
    if not any(marker in normalized_message for marker in _CURRENCY_MARKERS):
        return "NO_VISIBLE_CURRENCY"
    if any(marker in normalized_amount for marker in _CURRENCY_MARKERS):
        return "VISIBLE_SURFACE_WITH_CURRENCY"
    return "NUMERIC_CORE_ONLY"


def audit_label_consistency(
    dataset_path: Path,
    *,
    taxonomy_contract_path: Path,
    provenance_audit_path: Path | None = None,
) -> dict[str, Any]:
    """Audit cross-case taxonomy and slot-policy consistency."""

    rows = load_gold_dataset(dataset_path)
    ids = [str(row.get("id") or "") for row in rows]
    if not ids or len(ids) != len(set(ids)):
        raise CustomerServiceLabelAuditError("dataset IDs must be present and unique")
    if any((row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in rows):
        raise CustomerServiceLabelAuditError("label audit requires uniformly HUMAN_VERIFIED rows")
    contract = load_json(taxonomy_contract_path)
    if contract.get("contractVersion") != "customer-service-taxonomy-v2.1":
        raise CustomerServiceLabelAuditError("unsupported taxonomy contract")

    findings: list[dict[str, Any]] = []

    taxonomy_rows = [
        row
        for row in rows
        if str(_expected(row).get("intent")) == "RECOMMENT"
        and any(marker in _message(row) for marker in _RECOMMEND_REFINEMENT_MARKERS)
        and not any(marker in _message(row) for marker in _FOLLOWUP_REVIEW_MARKERS)
    ]
    if taxonomy_rows:
        findings.append(
            _finding(
                code="TAXONOMY_RECOMMENT_ACTION_COLLISION",
                severity="BLOCKING",
                title="RECOMMENT gold labels route catalog refinement into an order-review write action",
                case_ids=[str(row["id"]) for row in taxonomy_rows],
                evidence={
                    "currentGoldIntent": "RECOMMENT",
                    "productionActionFamily": "ORDER_REVIEW_APPEND_PROPOSAL",
                    "messageActionFamily": "CATALOG_RETRIEVAL",
                    "contractIntent": "PRODUCT_SEARCH",
                },
                metric_impact=("intentMacroF1", "HTTP behavior", "write-safety"),
                required_resolution=(
                    "Blindly re-annotate the affected cases under taxonomy v2.1, then adjudicate; "
                    "do not mutate the existing immutable dataset."
                ),
            )
        )

    amount_rows: list[tuple[Mapping[str, Any], str]] = []
    for row in rows:
        slots = _expected(row).get("slots") or {}
        if isinstance(slots, Mapping) and str(slots.get("amount") or ""):
            style = _amount_style(_message(row), str(slots["amount"]))
            if style != "NO_VISIBLE_CURRENCY":
                amount_rows.append((row, style))
    amount_styles = sorted({style for _row, style in amount_rows})
    if len(amount_styles) > 1:
        examples = {
            style: [str(row["id"]) for row, item_style in amount_rows if item_style == style][:5]
            for style in amount_styles
        }
        findings.append(
            _finding(
                code="SLOT_AMOUNT_SPAN_POLICY_SPLIT",
                severity="BLOCKING",
                title="amount alternates between a numeric core and the original visible currency span",
                case_ids=[str(row["id"]) for row, _style in amount_rows],
                evidence={"observedStyles": amount_styles, "examplesByStyle": examples},
                metric_impact=("slotEntitySpanF1", "slotExactMatch"),
                required_resolution=(
                    "Choose one raw-span convention, document normalization separately, and "
                    "re-adjudicate every amount-bearing case under that convention."
                ),
            )
        )

    budget_rows: list[tuple[Mapping[str, Any], bool]] = []
    for row in rows:
        message = _message(row)
        slots = _expected(row).get("slots") or {}
        if (
            isinstance(slots, Mapping)
            and "amount" in slots
            and any(marker in message for marker in ("预算", "以内"))
        ):
            budget_rows.append((row, "budget" in slots))
    if {has_budget for _row, has_budget in budget_rows} == {False, True}:
        findings.append(
            _finding(
                code="SLOT_BUDGET_COMPLETENESS_SPLIT",
                severity="BLOCKING",
                title="equivalent budget language is not annotated with a consistent slot set",
                case_ids=[str(row["id"]) for row, _has_budget in budget_rows],
                evidence={
                    "withBudgetSlot": [str(row["id"]) for row, value in budget_rows if value],
                    "withoutBudgetSlot": [str(row["id"]) for row, value in budget_rows if not value],
                },
                metric_impact=("slotEntitySpanF1", "slotExactMatch"),
                required_resolution="Define whether budget is canonical or derived and re-adjudicate all budget cases.",
            )
        )

    count_rows: list[tuple[Mapping[str, Any], bool]] = []
    for row in rows:
        slots = _expected(row).get("slots") or {}
        if _COUNT_RE.search(_message(row)) and isinstance(slots, Mapping):
            count_rows.append((row, "quantity" in slots))
    if {has_quantity for _row, has_quantity in count_rows} == {False, True}:
        findings.append(
            _finding(
                code="SLOT_QUANTITY_SCOPE_SPLIT",
                severity="MAJOR",
                title="explicit occurrence counts are sometimes quantity slots and sometimes omitted",
                case_ids=[str(row["id"]) for row, _has_quantity in count_rows],
                evidence={
                    "withQuantity": [str(row["id"]) for row, value in count_rows if value],
                    "withoutQuantity": [str(row["id"]) for row, value in count_rows if not value],
                },
                metric_impact=("slotEntitySpanF1", "slotExactMatch"),
                required_resolution=(
                    "Separate item quantity from occurrence/frequency, or explicitly exclude frequency "
                    "from quantity, then re-adjudicate both groups."
                ),
            )
        )

    compound_rows = []
    for row in rows:
        slots = _expected(row).get("slots") or {}
        if (
            "降噪耳机" in _message(row)
            and isinstance(slots, Mapping)
            and str(slots.get("feature") or "") == "降噪"
            and str(slots.get("productName") or "")
        ):
            compound_rows.append((row, str(slots["productName"])))
    product_forms = sorted({value for _row, value in compound_rows})
    if len(product_forms) > 1:
        findings.append(
            _finding(
                code="SLOT_PRODUCT_FEATURE_COMPOSITION_SPLIT",
                severity="MAJOR",
                title="feature text is inconsistently duplicated inside productName",
                case_ids=[str(row["id"]) for row, _value in compound_rows],
                evidence={
                    "productNameForms": {
                        form: [str(row["id"]) for row, value in compound_rows if value == form]
                        for form in product_forms
                    }
                },
                metric_impact=("slotEntitySpanF1", "slotExactMatch"),
                required_resolution="Define non-overlapping canonical entity spans and re-adjudicate compound product names.",
            )
        )

    provenance: dict[str, Any] = {
        "status": "NOT_SUPPLIED",
        "releaseGateEligible": False,
    }
    if provenance_audit_path is not None:
        value = load_json(provenance_audit_path)
        provenance = {
            "path": str(provenance_audit_path),
            "sha256": sha256_file(provenance_audit_path),
            "status": value.get("status"),
            "hashAndLabelChainValid": value.get("hashAndLabelChainValid"),
            "releaseGateEligible": value.get("releaseGateEligible", False),
            "findingCount": len(value.get("provenanceFindings") or []),
        }

    affected = sorted({case_id for finding in findings for case_id in finding["caseIds"]})
    blocking = [finding for finding in findings if finding["severity"] == "BLOCKING"]
    return {
        "schemaVersion": AUDIT_SCHEMA,
        "createdAt": utc_now(),
        "status": "BLOCKED_HUMAN_READJUDICATION" if blocking else "REVIEW_REQUIRED",
        "dataset": {
            "path": str(dataset_path),
            "sha256": sha256_file(dataset_path),
            "caseCount": len(rows),
            "annotationStatus": HUMAN_STATUS,
        },
        "taxonomyContract": {
            "path": str(taxonomy_contract_path),
            "sha256": sha256_file(taxonomy_contract_path),
            "version": contract.get("contractVersion"),
        },
        "provenance": provenance,
        "summary": {
            "findingCount": len(findings),
            "blockingFindingCount": len(blocking),
            "affectedCaseCount": len(affected),
            "affectedCaseIds": affected,
        },
        "findings": findings,
        "metricValidity": {
            "intentMacroF1": "CONFOUNDED_BY_TAXONOMY_COLLISION",
            "slotEntitySpanF1": "CONFOUNDED_BY_LABEL_POLICY_SPLITS",
            "slotExactMatch": "NOT_VALID_AS_STRICT_SCHEMA_METRIC_UNTIL_READJUDICATED",
            "highRiskIntentRecall": "DEVELOPMENT_DIAGNOSTIC_ONLY_PROVENANCE_PENDING",
            "handoffRecall": "DEVELOPMENT_DIAGNOSTIC_ONLY_PROVENANCE_PENDING",
            "criticalHandoffMissRate": "DEVELOPMENT_DIAGNOSTIC_ONLY_SMALL_DENOMINATOR",
            "answerCorrectness": "NOT_MEASURED_BY_INPUT_GOLD",
        },
        "gates": {
            "labelConsistencyPassed": not findings,
            "taxonomyPassed": not any(
                item["code"] == "TAXONOMY_RECOMMENT_ACTION_COLLISION" for item in findings
            ),
            "slotPolicyPassed": not any(item["code"].startswith("SLOT_") for item in findings),
            "provenancePassed": provenance.get("releaseGateEligible") is True,
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
        },
        "requiredActions": [
            "Freeze this audit; do not edit the 120-case HUMAN_VERIFIED artifact in place.",
            "Give the blind re-annotation sheet and taxonomy v2.1 to independent reviewers.",
            "Seal two completed sheets, measure agreement, adjudicate every disagreement, and publish a successor dataset hash.",
            "Re-run rule and HTTP evaluation from the successor dataset; do not carry forward current point estimates.",
        ],
    }


def _render_markdown(audit: Mapping[str, Any]) -> str:
    summary = audit.get("summary") or {}
    lines = [
        "# Customer-service v2 label-consistency audit",
        "",
        f"- Status: `{audit.get('status')}`",
        f"- Dataset: `{(audit.get('dataset') or {}).get('caseCount')}` cases / `{(audit.get('dataset') or {}).get('sha256')}`",
        f"- Findings: `{summary.get('findingCount')}`; blocking: `{summary.get('blockingFindingCount')}`; affected cases: `{summary.get('affectedCaseCount')}`",
        "- Release gate eligible: `false`",
        "",
        "The audit does not change a human label. It shows where the same schema was applied with conflicting semantics, so current point estimates remain development diagnostics.",
        "",
        "## Findings",
        "",
    ]
    for finding in audit.get("findings") or []:
        lines.extend(
            [
                f"### {finding.get('severity')} — `{finding.get('code')}`",
                "",
                str(finding.get("title") or ""),
                "",
                f"Cases: `{', '.join(finding.get('caseIds') or [])}`",
                "",
                f"Required resolution: {finding.get('requiredResolution')}",
                "",
            ]
        )
    lines.extend(["## Metric validity", ""])
    lines.extend(
        f"- `{name}`: `{status}`"
        for name, status in (audit.get("metricValidity") or {}).items()
    )
    lines.extend(["", "## Required workflow", ""])
    lines.extend(f"- {item}" for item in audit.get("requiredActions") or [])
    return "\n".join(lines) + "\n"


def _copy_exact(source: Path, destination: Path) -> None:
    atomic_write_bytes(destination, source.read_bytes(), overwrite=False)
    if sha256_file(source) != sha256_file(destination):
        raise CustomerServiceLabelAuditError(f"copy hash mismatch: {source}")


def build_label_audit_package(
    dataset_path: Path,
    *,
    taxonomy_contract_path: Path,
    provenance_audit_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a checksum-bound, non-release audit and human handoff package."""

    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite label-audit package: {output_dir}")
    output_dir.mkdir(parents=True)
    audit = audit_label_consistency(
        dataset_path,
        taxonomy_contract_path=taxonomy_contract_path,
        provenance_audit_path=provenance_audit_path,
    )
    _copy_exact(dataset_path, output_dir / "source" / "customer-service-human-v2.jsonl")
    _copy_exact(taxonomy_contract_path, output_dir / "source" / taxonomy_contract_path.name)
    _copy_exact(provenance_audit_path, output_dir / "source" / "provenance-audit.json")
    atomic_write_json(output_dir / "label-consistency-audit.json", audit, overwrite=False)
    atomic_write_text(output_dir / "label-consistency-audit.md", _render_markdown(audit), overwrite=False)

    rows = {str(row["id"]): row for row in load_gold_dataset(dataset_path)}
    affected = list((audit.get("summary") or {}).get("affectedCaseIds") or [])
    blind_rows = [
        {
            "schemaVersion": REAUDIT_SCHEMA,
            "id": case_id,
            "input": rows[case_id]["input"],
            "reviewerId": "REPLACE_WITH_INDEPENDENT_REVIEWER_ID",
            "guidelinesVersion": "customer-service-taxonomy-v2.1",
            "labels": {
                "intent": None,
                "riskLevel": None,
                "shouldHandoff": None,
                "handoffSeverity": None,
                "slots": None,
            },
            "comment": "",
        }
        for case_id in affected
    ]
    blind_path = output_dir / "reaudit" / "label-policy-reaudit.open.jsonl"
    atomic_write_jsonl(blind_path, blind_rows, overwrite=False)
    atomic_write_json(
        blind_path.with_suffix(blind_path.suffix + ".manifest.json"),
        {
            "schemaVersion": REAUDIT_SCHEMA,
            "status": "OPEN_TEMPLATE_NOT_EVIDENCE",
            "sourceDatasetSha256": sha256_file(dataset_path),
            "taxonomyContractSha256": sha256_file(taxonomy_contract_path),
            "sheetSha256": sha256_file(blind_path),
            "caseCount": len(blind_rows),
            "goldLabelsPresent": False,
            "modelPredictionsPresent": False,
            "selectionPolicy": "UNION_OF_DETERMINISTIC_LABEL_CONSISTENCY_FINDINGS",
            "acceptance": {
                "requiredReviewers": 2,
                "criticalTaxonomyAgreement": 1.0,
                "adjudicateEveryDisagreement": True,
                "publishSuccessorDataset": True,
            },
        },
        overwrite=False,
    )
    context_rows = []
    for case_id in affected:
        issue_codes = [
            finding["code"] for finding in audit["findings"] if case_id in finding["caseIds"]
        ]
        context_rows.append(
            {
                "schemaVersion": REAUDIT_SCHEMA,
                "id": case_id,
                "input": rows[case_id]["input"],
                "currentImmutableExpected": rows[case_id]["expected"],
                "issueCodes": issue_codes,
                "useOnlyAfterBothBlindSheetsAreSealed": True,
            }
        )
    atomic_write_jsonl(
        output_dir / "reaudit" / "adjudication-context.after-sealing.jsonl",
        context_rows,
        overwrite=False,
    )
    lifecycle = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": utc_now(),
        "status": audit["status"],
        "developmentDiagnosticEligible": True,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "humanWorkComplete": False,
        "blockingControls": [
            "TWO_INDEPENDENT_BLIND_LABEL_POLICY_REVIEWS",
            "SEALED_REVIEW_HASHES_AND_INDEPENDENCE_ATTESTATION",
            "FULL_DISAGREEMENT_ADJUDICATION",
            "SUCCESSOR_DATASET_AND_FRESH_METRICS",
        ],
    }
    atomic_write_json(output_dir / "lifecycle.json", lifecycle, overwrite=False)
    atomic_write_text(
        output_dir / "README.md",
        "# Customer-service v2 label consistency — re-adjudication required\n\n"
        "This immutable diagnostic package proves that the current 120-case gold uses "
        "conflicting taxonomy and slot policies. It does not alter labels or fabricate "
        "human review. Send only the open sheet plus taxonomy contract to each independent "
        "reviewer; reveal adjudication context only after both sheets are sealed.\n",
        overwrite=False,
    )
    files = {
        path.relative_to(output_dir).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name not in {"evidence-manifest.json", "SHA256SUMS"}
    }
    manifest = {
        "schemaVersion": PACKAGE_SCHEMA,
        "artifactId": output_dir.name,
        "createdAt": lifecycle["createdAt"],
        "status": lifecycle["status"],
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "datasetSha256": sha256_file(dataset_path),
        "findingCount": (audit.get("summary") or {}).get("findingCount"),
        "files": files,
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
        "status": lifecycle["status"],
        "outputDir": str(output_dir),
        "findingCount": manifest["findingCount"],
        "affectedCaseCount": (audit.get("summary") or {}).get("affectedCaseCount"),
        "releaseGateEligible": False,
        "sha256SumsSha256": sha256_file(output_dir / "SHA256SUMS"),
    }


def verify_label_audit_package(output_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    checksum_path = output_dir / "SHA256SUMS"
    if not checksum_path.is_file():
        return {"valid": False, "errors": ["missing:SHA256SUMS"]}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        path = output_dir / relative
        if not separator or not path.is_file() or sha256_file(path) != digest:
            errors.append(f"checksum:{relative or line}")
    audit = load_json(output_dir / "label-consistency-audit.json")
    manifest = load_json(output_dir / "evidence-manifest.json")
    lifecycle = load_json(output_dir / "lifecycle.json")
    if audit.get("gates", {}).get("releaseGateEligible") is not False:
        errors.append("audit-release-gate")
    if manifest.get("releaseGateEligible") is not False:
        errors.append("manifest-release-gate")
    if lifecycle.get("humanWorkComplete") is not False:
        errors.append("lifecycle-human-work")
    source = output_dir / "source" / "customer-service-human-v2.jsonl"
    if not source.is_file() or sha256_file(source) != manifest.get("datasetSha256"):
        errors.append("source-dataset")
    return {
        "valid": not errors,
        "status": "VERIFIED" if not errors else "INVALID",
        "errors": errors,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
    }
