"""Fail-closed assembly of the reviewed customer-service v2 input gold.

The v2 additions are reviewed and merged as their own immutable 60-case
artifact first.  This module only combines that artifact with the already
immutable v1 package after checking both provenance chains and rejecting any
draft, hash mismatch, or duplicate case ID.  It never invents labels and it
does not calculate model-quality metrics.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    atomic_write_json,
    atomic_write_jsonl,
    load_json,
    relative_to_repo,
    sha256_file,
    utc_now,
)
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset

COMBINED_SCHEMA = "aishop-customer-service-human-package/v2"
MERGE_EVIDENCE_SCHEMA = "aishop-customer-service-review-evidence/v1"
BASE_MANIFEST_SCHEMA = "aishop-customer-service-human-dataset/v1"


class CustomerServiceV2Error(ValueError):
    """Raised when the two reviewed input-gold chains cannot be combined."""


def _path_label(path: Path) -> str:
    try:
        return relative_to_repo(path)
    except ValueError:
        return str(path.resolve())


def _ensure_new(paths: Sequence[Path]) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing:
        raise CustomerServiceV2Error(
            "refusing to overwrite immutable artifact(s): " + ", ".join(existing)
        )


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.casefold())


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = load_json(path)
    except (OSError, ValueError) as exc:
        raise CustomerServiceV2Error(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CustomerServiceV2Error(f"{label} must be a JSON object")
    return dict(value)


def _contains_forbidden_key(value: Any) -> bool:
    forbidden = {"expected", "predicted", "prediction", "modelOutput", "modelPrediction"}
    if isinstance(value, Mapping):
        if any(str(key) in forbidden for key in value):
            return True
        return any(_contains_forbidden_key(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _load_human_dataset(
    path: Path,
    *,
    label: str,
    expected_count: int,
) -> tuple[list[dict[str, Any]], str]:
    try:
        rows = load_gold_dataset(path)
    except (OSError, ValueError) as exc:
        raise CustomerServiceV2Error(f"{label} failed schema validation: {exc}") from exc
    if len(rows) != expected_count:
        raise CustomerServiceV2Error(
            f"{label} must contain exactly {expected_count} cases; got {len(rows)}"
        )
    if any((row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in rows):
        raise CustomerServiceV2Error(f"{label} is not uniformly {HUMAN_STATUS}")
    return rows, sha256_file(path)


def _verify_base_manifest(
    manifest_path: Path,
    *,
    dataset_path: Path,
    dataset_sha: str,
    case_count: int,
) -> dict[str, Any]:
    manifest = _load_object(manifest_path, label="base manifest")
    if manifest.get("schemaVersion") != BASE_MANIFEST_SCHEMA:
        raise CustomerServiceV2Error("base manifest schema is invalid")
    if manifest.get("lifecycle") != "HUMAN_VERIFIED_IMMUTABLE":
        raise CustomerServiceV2Error("base manifest is not immutable HUMAN_VERIFIED")
    if manifest.get("datasetSha256") != dataset_sha:
        raise CustomerServiceV2Error("base manifest dataset hash differs")
    if manifest.get("caseCount") != case_count:
        raise CustomerServiceV2Error("base manifest case count differs")
    # A package may be copied to an archive (and the v1 evidence package uses
    # a different display filename from its reusable projection), so bind the
    # content hash rather than requiring a stale path or filename.
    return manifest


def _verify_additions_merge_evidence(
    evidence_path: Path,
    *,
    additions_path: Path,
    additions_sha: str,
    case_count: int,
) -> dict[str, Any]:
    evidence = _load_object(evidence_path, label="additions merge evidence")
    if evidence.get("schemaVersion") != MERGE_EVIDENCE_SCHEMA:
        raise CustomerServiceV2Error("additions merge evidence schema is invalid")
    if evidence.get("status") != HUMAN_STATUS or evidence.get("releaseGateEligible") is not False:
        raise CustomerServiceV2Error("additions merge evidence is not non-gating HUMAN_VERIFIED")
    if evidence.get("caseCount") != case_count:
        raise CustomerServiceV2Error("additions merge evidence case count differs")
    if evidence.get("outputDatasetSha256") != additions_sha:
        raise CustomerServiceV2Error("additions merge evidence output hash differs")
    if _contains_forbidden_key(evidence):
        raise CustomerServiceV2Error("additions merge evidence contains model prediction fields")
    for reviewer_key in ("reviewA", "reviewB"):
        reviewer = evidence.get(reviewer_key)
        if not isinstance(reviewer, Mapping) or not str(reviewer.get("reviewerId") or "").strip():
            raise CustomerServiceV2Error(f"additions merge evidence {reviewer_key} is invalid")
        if not _is_sha256(reviewer.get("sha256")):
            raise CustomerServiceV2Error(f"additions merge evidence {reviewer_key} hash is invalid")
    if evidence["reviewA"]["reviewerId"] == evidence["reviewB"]["reviewerId"]:
        raise CustomerServiceV2Error("additions merge evidence reviewers must be distinct")
    # The merge output may have been moved into an evidence archive.  The
    # exact output bytes are already bound by ``outputDatasetSha256``.
    return evidence


def combine_human_verified_v2(
    base_dataset_path: Path,
    base_manifest_path: Path,
    additions_dataset_path: Path,
    additions_evidence_path: Path,
    *,
    output_dataset_path: Path,
    output_manifest_path: Path,
    evidence_path: Path,
    expected_base_count: int = 60,
    expected_additions_count: int = 60,
) -> dict[str, Any]:
    """Combine the immutable v1 and reviewed v2 additions into a new package.

    The default counts intentionally encode the requested 60 + 60 = 120
    target.  Tests or future dataset versions may pass different counts, but
    an empty or draft input can never pass this function.
    """

    if expected_base_count <= 0 or expected_additions_count <= 0:
        raise CustomerServiceV2Error("expected case counts must be positive")
    output_targets = {
        output_dataset_path.resolve(),
        output_manifest_path.resolve(),
        evidence_path.resolve(),
    }
    if len(output_targets) != 3:
        raise CustomerServiceV2Error("output dataset, manifest, and evidence paths must be distinct")
    base_rows, base_sha = _load_human_dataset(
        base_dataset_path,
        label="base dataset",
        expected_count=expected_base_count,
    )
    additions_rows, additions_sha = _load_human_dataset(
        additions_dataset_path,
        label="additions dataset",
        expected_count=expected_additions_count,
    )
    base_manifest = _verify_base_manifest(
        base_manifest_path,
        dataset_path=base_dataset_path,
        dataset_sha=base_sha,
        case_count=len(base_rows),
    )
    additions_evidence = _verify_additions_merge_evidence(
        additions_evidence_path,
        additions_path=additions_dataset_path,
        additions_sha=additions_sha,
        case_count=len(additions_rows),
    )

    base_ids = [str(row["id"]) for row in base_rows]
    additions_ids = [str(row["id"]) for row in additions_rows]
    overlap = sorted(set(base_ids) & set(additions_ids))
    if overlap:
        raise CustomerServiceV2Error(
            "base and additions contain duplicate case IDs: " + ", ".join(overlap)
        )
    if len(base_ids) != len(set(base_ids)) or len(additions_ids) != len(set(additions_ids)):
        raise CustomerServiceV2Error("source dataset IDs must be unique")

    combined_rows = [*base_rows, *additions_rows]
    expected_total = expected_base_count + expected_additions_count
    if len(combined_rows) != expected_total:
        raise CustomerServiceV2Error("combined case count differs from the requested target")
    _ensure_new((output_dataset_path, output_manifest_path, evidence_path))

    # Write the data before the manifests, then validate the exact bytes that
    # are bound by both manifests.  No existing source or evidence file is
    # modified in place.
    atomic_write_jsonl(output_dataset_path, combined_rows, overwrite=False)
    try:
        validated_rows = load_gold_dataset(output_dataset_path)
    except (OSError, ValueError) as exc:
        raise CustomerServiceV2Error(f"combined dataset failed schema validation: {exc}") from exc
    if len(validated_rows) != expected_total or any(
        (row.get("annotation") or {}).get("status") != HUMAN_STATUS for row in validated_rows
    ):
        raise CustomerServiceV2Error("combined dataset is not uniformly HUMAN_VERIFIED")

    output_sha = sha256_file(output_dataset_path)
    manifest = {
        "schemaVersion": COMBINED_SCHEMA,
        "artifact": "COMBINED_REUSABLE_INPUT_GOLD",
        "lifecycle": "HUMAN_VERIFIED_IMMUTABLE",
        "status": HUMAN_STATUS,
        "releaseGateEligible": False,
        "caseCount": expected_total,
        "datasetPath": _path_label(output_dataset_path),
        "datasetSha256": output_sha,
        "base": {
            "datasetPath": _path_label(base_dataset_path),
            "datasetSha256": base_sha,
            "manifestPath": _path_label(base_manifest_path),
            "manifestSha256": sha256_file(base_manifest_path),
            "caseCount": len(base_rows),
            "lifecycle": base_manifest.get("lifecycle"),
        },
        "additions": {
            "datasetPath": _path_label(additions_dataset_path),
            "datasetSha256": additions_sha,
            "mergeEvidencePath": _path_label(additions_evidence_path),
            "mergeEvidenceSha256": sha256_file(additions_evidence_path),
            "caseCount": len(additions_rows),
            "lifecycle": additions_evidence.get("status"),
        },
        "idOverlapCount": 0,
        "order": ["base-v1", "additions-v2"],
        "qualityMetricsStatus": "NOT_COMPUTED",
        "note": (
            "This package contains independently reviewed input labels only. "
            "Run a new production-path evaluation and recompute every metric "
            "with this dataset; do not copy v1 point estimates."
        ),
        "createdAt": utc_now(),
    }
    atomic_write_json(output_manifest_path, manifest, overwrite=False)
    evidence = {
        "schemaVersion": COMBINED_SCHEMA,
        "artifact": "COMBINED_V2_EVIDENCE",
        "lifecycle": "HUMAN_VERIFIED_IMMUTABLE",
        "status": HUMAN_STATUS,
        "releaseGateEligible": False,
        "caseCount": expected_total,
        "base": {
            "datasetPath": _path_label(base_dataset_path),
            "datasetSha256": base_sha,
            "manifestPath": _path_label(base_manifest_path),
            "manifestSha256": sha256_file(base_manifest_path),
            "caseCount": len(base_rows),
        },
        "additions": {
            "datasetPath": _path_label(additions_dataset_path),
            "datasetSha256": additions_sha,
            "mergeEvidencePath": _path_label(additions_evidence_path),
            "mergeEvidenceSha256": sha256_file(additions_evidence_path),
            "caseCount": len(additions_rows),
        },
        "output": {
            "datasetPath": _path_label(output_dataset_path),
            "datasetSha256": output_sha,
            "manifestPath": _path_label(output_manifest_path),
            "manifestSha256": sha256_file(output_manifest_path),
        },
        "idOverlapCount": 0,
        "qualityMetricsStatus": "NOT_COMPUTED",
        "createdAt": manifest["createdAt"],
        "note": (
            "Input-gold assembly evidence only; no model-quality metric is "
            "derived from this operation."
        ),
    }
    atomic_write_json(evidence_path, evidence, overwrite=False)
    return evidence
