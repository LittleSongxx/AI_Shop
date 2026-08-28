from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from evaluation.text2sql import DATASET_SCHEMA_VERSION
from evaluation.text2sql.contracts import Outcome, Text2SqlCase
from evaluation.text2sql.io import (
    canonical_json_bytes,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    utc_now,
    verify_sha256s,
    write_json,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "evaluation" / "datasets" / "text2sql"
DEFAULT_DATASET = DATASET_DIR / "v0-candidates.jsonl"
DEFAULT_LOCK = DATASET_DIR / "v0-candidates.lock.json"
DEFAULT_CATALOG = DATASET_DIR / "catalog-v0.provisional.json"

EXPECTED_OUTCOME_COUNTS = {
    Outcome.ANSWER.value: 48,
    Outcome.CLARIFY.value: 10,
    Outcome.ABSTAIN.value: 10,
    Outcome.DENY.value: 12,
}
EXPECTED_ANSWER_VIEW_COUNTS = {
    "analytics_sales_daily": 7,
    "analytics_product_sales_daily": 6,
    "analytics_fulfillment_after_sales_daily": 5,
    "analytics_inventory_risk": 5,
    "analytics_inventory_forecast": 5,
    "analytics_recommendation_funnel_daily": 4,
    "analytics_recommendation_quality_daily": 4,
    "analytics_offer_quality_daily": 4,
    "analytics_agent_quality_daily": 4,
    "analytics_tool_quality_daily": 4,
}


def parse_cases(rows: Iterable[dict[str, Any]]) -> list[Text2SqlCase]:
    cases = [Text2SqlCase.model_validate(row) for row in rows]
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Text2SQL case IDs must be unique")
    return cases


def load_cases(path: Path = DEFAULT_DATASET) -> list[Text2SqlCase]:
    return parse_cases(read_jsonl(path))


def dataset_sha256(cases: Iterable[Text2SqlCase]) -> str:
    rows = [case.public() for case in sorted(cases, key=lambda item: item.case_id)]
    return sha256_bytes(b"\n".join(canonical_json_bytes(row) for row in rows) + b"\n")


def validate_v0(cases: list[Text2SqlCase]) -> dict[str, Any]:
    if len(cases) != 80:
        raise ValueError(f"V0 requires exactly 80 cases, found {len(cases)}")
    outcomes = Counter(case.expected.outcome.value for case in cases)
    if dict(outcomes) != EXPECTED_OUTCOME_COUNTS:
        raise ValueError(f"V0 outcome distribution mismatch: {dict(outcomes)}")
    views = Counter(
        case.expected.branches[0].semantic_view
        for case in cases
        if case.expected.outcome is Outcome.ANSWER
    )
    if dict(views) != EXPECTED_ANSWER_VIEW_COUNTS:
        raise ValueError(f"V0 answer view distribution mismatch: {dict(views)}")
    pagination = sum(case.flow.traverse_all_pages for case in cases)
    exports = sum(case.flow.export_frozen_result for case in cases)
    clarifications = sum(case.flow.follow_clarification for case in cases)
    multibranch = sum(len(case.expected.branches) > 1 for case in cases)
    if pagination < 8 or exports < 6 or clarifications < 8 or multibranch < 4:
        raise ValueError(
            "V0 flow coverage requires pagination>=8, export>=6, "
            "clarification>=8, multiBranch>=4"
        )
    if not {case.fixture_state for case in cases}.issuperset({"base", "boundary", "empty"}):
        raise ValueError("V0 must use base, boundary, and empty fixture states")
    return {
        "caseCount": len(cases),
        "outcomes": dict(sorted(outcomes.items())),
        "answerViews": dict(sorted(views.items())),
        "flows": {
            "pagination": pagination,
            "export": exports,
            "clarification": clarifications,
            "multiBranch": multibranch,
        },
        "datasetSha256": dataset_sha256(cases),
    }


def write_cases(path: Path, cases: list[Text2SqlCase], *, overwrite: bool = False) -> None:
    write_jsonl(path, [case.public() for case in cases], overwrite=overwrite)


def write_lock(
    cases: list[Text2SqlCase],
    *,
    dataset_path: Path = DEFAULT_DATASET,
    catalog_path: Path = DEFAULT_CATALOG,
    lock_path: Path = DEFAULT_LOCK,
    overwrite: bool = False,
) -> dict[str, Any]:
    summary = validate_v0(cases)
    catalog = read_json(catalog_path)
    lock = {
        "schemaVersion": DATASET_SCHEMA_VERSION,
        "lifecycle": "AI_DRAFT_PENDING_HUMAN_REVIEW",
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
        "createdAt": utc_now(),
        "datasetFile": dataset_path.name,
        "datasetFileSha256": sha256_file(dataset_path),
        "datasetCanonicalSha256": summary["datasetSha256"],
        "catalogFile": catalog_path.name,
        "catalogFileSha256": sha256_file(catalog_path),
        "catalogVersion": catalog.get("catalogVersion"),
        "summary": summary,
    }
    write_json(lock_path, lock, overwrite=overwrite)
    return lock


def verify_lock(
    *,
    dataset_path: Path = DEFAULT_DATASET,
    catalog_path: Path = DEFAULT_CATALOG,
    lock_path: Path = DEFAULT_LOCK,
) -> dict[str, Any]:
    lock = read_json(lock_path)
    cases = load_cases(dataset_path)
    summary = validate_v0(cases)
    checks = {
        "schemaVersion": lock.get("schemaVersion") == DATASET_SCHEMA_VERSION,
        "datasetFileSha256": lock.get("datasetFileSha256") == sha256_file(dataset_path),
        "datasetCanonicalSha256": (
            lock.get("datasetCanonicalSha256") == summary["datasetSha256"]
        ),
        "catalogFileSha256": lock.get("catalogFileSha256") == sha256_file(catalog_path),
        "lifecycle": lock.get("lifecycle") == "AI_DRAFT_PENDING_HUMAN_REVIEW",
        "releaseBoundary": all(
            (
                lock.get("development") is True,
                lock.get("provisional") is True,
                lock.get("unseen") is False,
                lock.get("releaseGateEligible") is False,
            )
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"Text2SQL dataset lock verification failed: {checks}")
    return {"verified": True, "checks": checks, "summary": summary}


def verify_human_gold(path: Path) -> dict[str, Any]:
    cases = load_cases(path)
    summary = validate_v0(cases)
    if any(not case.lifecycle.startswith("HUMAN_") for case in cases):
        raise ValueError("official baseline requires HUMAN_VERIFIED gold")
    package = path.parent
    verified_files = verify_sha256s(package)
    evidence_path = package / "evidence.json"
    catalog_path = package / DEFAULT_CATALOG.name
    if not evidence_path.is_file() or not catalog_path.is_file():
        raise ValueError("human gold package is missing evidence or catalog")
    evidence = read_json(evidence_path)
    reviewers = [str(item).strip() for item in evidence.get("reviewers") or []]
    checks = {
        "status": evidence.get("status")
        in {"HUMAN_VERIFIED", "HUMAN_REVIEWED_ADJUDICATED"},
        "humanDecisionAuthority": evidence.get("humanDecisionAuthority") is True,
        "reviewers": len(reviewers) == 2 and len(set(reviewers)) == 2,
        "datasetSha256": evidence.get("goldDatasetSha256") == sha256_file(path),
        "catalogSha256": evidence.get("catalogSha256") == sha256_file(catalog_path),
        "caseAnnotations": all(
            case.annotation.human_decision_authority
            and case.annotation.reviewers == reviewers
            for case in cases
        ),
        "releaseBoundary": all(
            (
                evidence.get("development") is True,
                evidence.get("provisional") is True,
                evidence.get("unseen") is False,
                evidence.get("releaseGateEligible") is False,
            )
        ),
    }
    if not all(checks.values()):
        raise ValueError(f"human gold package verification failed: {checks}")
    return {
        "verified": True,
        "checks": checks,
        "verifiedFiles": verified_files,
        "summary": summary,
        "evidence": evidence,
    }
