"""Canonical loaders for the two independently reviewed customer-service sets.

There are two intentionally different artifacts:

* ``gold-v1-human-adjudicated.jsonl`` is reusable input gold for intent/risk/
  slot evaluation.
* ``answer-review-v2-adjudicated.labels.jsonl`` is a frozen audit of one exact
  HTTP replay.  It may only be applied when the replay run and every answer
  SHA-256 still match.

The loaders fail closed so a changed model output cannot accidentally inherit
old human labels.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evaluation.core.io import load_json, load_jsonl, sha256_file
from evaluation.customer_service_gold import HUMAN_STATUS, load_gold_dataset

DATA_ROOT = Path(__file__).resolve().parent / "datasets" / "customer_service" / "adjudicated"
HUMAN_GOLD_PATH = DATA_ROOT / "gold-v1-human-adjudicated.jsonl"
HUMAN_GOLD_MANIFEST_PATH = DATA_ROOT / "gold-v1-human-adjudicated.manifest.json"
ANSWER_LABELS_PATH = DATA_ROOT / "answer-review-v2-adjudicated.labels.jsonl"
ANSWER_LABELS_MANIFEST_PATH = DATA_ROOT / "answer-review-v2-adjudicated.manifest.json"
ANSWER_REVIEW_REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-answer-review-v2-adjudicated-20260824"
    / "final-report.json"
)
ANSWER_SOURCE_REPORT_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation-evidence"
    / "benchmarks"
    / "customer-service"
    / "customer-service-http-v1-20260823"
    / "report.json"
)

ANSWER_LABEL_FIELDS = frozenset(
    {"answerCorrect", "citationSupport", "handoffAppropriate", "unsafeAnswer"}
)
ANSWER_CITATION_VALUES = frozenset(
    {"SUPPORTED", "UNSUPPORTED", "NOT_APPLICABLE", "UNDECIDABLE"}
)


class CustomerServiceHumanDataError(ValueError):
    """Raised when a reviewed artifact is missing, stale or malformed."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_sha(path: Path, expected: Any, label: str) -> str:
    if not path.is_file():
        raise CustomerServiceHumanDataError(f"{label} does not exist: {path}")
    actual = sha256_file(path)
    if str(expected or "").lower() != actual:
        raise CustomerServiceHumanDataError(
            f"{label} SHA-256 differs: expected={expected!s} actual={actual}"
        )
    return actual


def load_human_adjudicated_gold(
    path: Path = HUMAN_GOLD_PATH,
    *,
    manifest_path: Path = HUMAN_GOLD_MANIFEST_PATH,
) -> list[dict[str, Any]]:
    """Load the reusable 60-case intent/risk/slot gold set."""

    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != "aishop-customer-service-human-dataset/v1":
        raise CustomerServiceHumanDataError("human gold manifest schema is invalid")
    _require_sha(path, manifest.get("datasetSha256"), "human gold dataset")
    rows = load_gold_dataset(path)
    if len(rows) != int(manifest.get("caseCount") or 0):
        raise CustomerServiceHumanDataError("human gold caseCount differs from manifest")
    if any(row.get("annotation", {}).get("status") != HUMAN_STATUS for row in rows):
        raise CustomerServiceHumanDataError("human gold contains non-adjudicated rows")
    return rows


def _validate_answer_labels(rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        label = f"answer labels:{index}"
        if not isinstance(row, Mapping):
            raise CustomerServiceHumanDataError(f"{label} must be an object")
        case_id = str(row.get("caseId") or "")
        if not case_id or case_id in seen:
            raise CustomerServiceHumanDataError(f"{label} caseId is empty or duplicated")
        seen.add(case_id)
        labels = row.get("labels")
        if not isinstance(labels, Mapping) or set(labels) != ANSWER_LABEL_FIELDS:
            raise CustomerServiceHumanDataError(f"{label} labels are incomplete")
        for field in ("answerCorrect", "handoffAppropriate", "unsafeAnswer"):
            if not isinstance(labels.get(field), bool):
                raise CustomerServiceHumanDataError(f"{label} {field} must be boolean")
        citation = str(labels.get("citationSupport") or "").upper()
        if citation not in ANSWER_CITATION_VALUES:
            raise CustomerServiceHumanDataError(f"{label} citationSupport is invalid")
        if len(str(row.get("answerSha256") or "")) != 64:
            raise CustomerServiceHumanDataError(f"{label} answerSha256 is invalid")


def load_adjudicated_answer_labels(
    labels_path: Path = ANSWER_LABELS_PATH,
    *,
    manifest_path: Path = ANSWER_LABELS_MANIFEST_PATH,
    source_report_path: Path = ANSWER_SOURCE_REPORT_PATH,
) -> dict[str, dict[str, Any]]:
    """Load frozen HTTP labels only when the exact replay still matches."""

    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != "aishop-customer-service-answer-labels/v1":
        raise CustomerServiceHumanDataError("answer labels manifest schema is invalid")
    _require_sha(labels_path, manifest.get("labelsSha256"), "answer labels")
    expected_source_sha = str(manifest.get("sourceReportSha256") or "")
    actual_source_sha = _require_sha(
        source_report_path, expected_source_sha, "answer source report"
    )
    rows = load_jsonl(labels_path)
    _validate_answer_labels(rows)
    report = load_json(source_report_path)
    if report.get("runId") != manifest.get("sourceRunId"):
        raise CustomerServiceHumanDataError("answer source runId differs from manifest")
    report_cases = {
        str(case.get("caseId")): case
        for case in report.get("cases") or []
        if isinstance(case, Mapping)
    }
    if len(rows) != len(report_cases) or len(rows) != int(manifest.get("caseCount") or 0):
        raise CustomerServiceHumanDataError("answer label caseCount differs from source report")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row["caseId"])
        case = report_cases.get(case_id)
        if case is None:
            raise CustomerServiceHumanDataError(f"answer label case is absent from source: {case_id}")
        http = case.get("http") if isinstance(case.get("http"), Mapping) else {}
        actual_answer_sha = _sha256_text(str(http.get("answer") or ""))
        if actual_answer_sha != str(row.get("answerSha256")):
            raise CustomerServiceHumanDataError(
                f"answer label is stale for {case_id}: answer SHA-256 differs"
            )
        result[case_id] = dict(row)
    if actual_source_sha != expected_source_sha:
        raise CustomerServiceHumanDataError("answer source report hash check failed")
    return result
