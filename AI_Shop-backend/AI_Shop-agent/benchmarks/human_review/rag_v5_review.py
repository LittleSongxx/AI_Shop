"""Prepare and merge the two-person blind review for 20 RAG v5 fresh cases."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.mature_eval.common import atomic_write_bytes, atomic_write_json, sha256_file

DIMENSIONS = ("grounded", "complete", "citationAligned", "safe")
REVIEW_FIELDS = ("reviewerId", "blindCaseId", *DIMENSIONS, "notes")
EXPECTED_CASE_COUNT = 20
RUBRIC_PATH = Path(__file__).with_name("rag_v5_rubric.md")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _public_evidence(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for index, ref in enumerate(row.get("retrievedRefs") or [], 1):
        if not isinstance(ref, Mapping):
            continue
        evidence.append(
            {
                "citation": index,
                "source": ref.get("source"),
                "heading": ref.get("heading"),
                "snippet": ref.get("snippet"),
            }
        )
    return evidence


def _write_review_csv(path: Path, blind_ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    # Use csv through an in-memory list-compatible buffer while retaining the
    # repository's atomic file-write contract.
    import io

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=REVIEW_FIELDS)
    writer.writeheader()
    for blind_id in blind_ids:
        writer.writerow(
            {
                "reviewerId": "",
                "blindCaseId": blind_id,
                **{dimension: "" for dimension in DIMENSIONS},
                "notes": "",
            }
        )
    lines.append(buffer.getvalue())
    atomic_write_bytes(path, "".join(lines).encode("utf-8-sig"))


def prepare_review_package(
    template_path: Path,
    output_dir: Path,
    *,
    seed: int = 20260817,
) -> dict[str, Any]:
    template = _json(template_path)
    rows = template.get("cases")
    if not isinstance(rows, list) or len(rows) != EXPECTED_CASE_COUNT:
        raise ValueError("RAG v5 blind review requires exactly 20 fresh cases")
    case_ids = [str(row.get("caseId") or "") for row in rows]
    if "" in case_ids or len(set(case_ids)) != EXPECTED_CASE_COUNT:
        raise ValueError("RAG v5 review template case IDs must be unique")
    if any(row.get("comparisonGroup") != "fresh-holdout" for row in rows):
        raise ValueError("RAG v5 human review may expose only fresh-holdout cases")
    run_id = str(template.get("runId") or "")
    if not run_id:
        raise ValueError("RAG v5 review template runId is missing")
    ordered = [dict(row) for row in rows]
    random.Random(seed).shuffle(ordered)
    blind_rows: list[dict[str, Any]] = []
    mapping: list[dict[str, str]] = []
    for index, row in enumerate(ordered, 1):
        blind_id = f"R5-{index:03d}"
        mapping.append({"blindCaseId": blind_id, "caseId": str(row["caseId"])})
        blind_rows.append(
            {
                "blindCaseId": blind_id,
                "query": row.get("query"),
                "answer": row.get("answer"),
                "evidence": _public_evidence(row),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        output_dir / "blind-cases.jsonl",
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in blind_rows
        ).encode("utf-8"),
    )
    atomic_write_json(
        output_dir / "case-map.json",
        {
            "schemaVersion": 5,
            "suite": "rag-v5-generation",
            "runId": run_id,
            "randomizationSeed": seed,
            "sourceTemplateSha256": sha256_file(template_path),
            "cases": mapping,
        },
    )
    blind_ids = [row["blindCaseId"] for row in mapping]
    _write_review_csv(output_dir / "reviewer-a.csv", blind_ids)
    _write_review_csv(output_dir / "reviewer-b.csv", blind_ids)
    atomic_write_bytes(output_dir / "rubric.md", RUBRIC_PATH.read_bytes())
    status = {
        "schemaVersion": 5,
        "suite": "rag-v5-generation",
        "runId": run_id,
        "status": "HUMAN_REVIEW_PENDING",
        "requiredReviewerCount": 2,
        "completedReviewerCount": 0,
        "caseCount": EXPECTED_CASE_COUNT,
        "automaticVerdictsExposed": False,
        "originalCaseIdsExposed": False,
    }
    atomic_write_json(output_dir / "review-status.json", status)
    files = {
        path.name: sha256_file(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file()
    }
    atomic_write_json(output_dir / "package-manifest.json", {**status, "files": files})
    return {"outputDir": str(output_dir), **status}


def _parse_bool(value: str) -> bool | None:
    normalized = str(value or "").strip().casefold()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes", "y", "是", "通过"}:
        return True
    if normalized in {"false", "0", "no", "n", "否", "不通过"}:
        return False
    raise ValueError(f"invalid review boolean: {value!r}")


def _read_review(
    path: Path, expected_ids: set[str]
) -> tuple[str | None, dict[str, dict[str, bool | None]], list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ids = [str(row.get("blindCaseId") or "").strip() for row in rows]
    if len(rows) != len(expected_ids) or set(ids) != expected_ids or len(set(ids)) != len(ids):
        raise ValueError(f"{path.name} must contain every blind case exactly once")
    reviewer_ids = {
        str(row.get("reviewerId") or "").strip()
        for row in rows
        if str(row.get("reviewerId") or "").strip()
    }
    if len(reviewer_ids) > 1:
        raise ValueError(f"{path.name} contains multiple reviewer IDs")
    reviewer_id = next(iter(reviewer_ids), None)
    values: dict[str, dict[str, bool | None]] = {}
    incomplete: list[str] = []
    for row in rows:
        blind_id = str(row["blindCaseId"]).strip()
        ratings = {
            dimension: _parse_bool(str(row.get(dimension) or ""))
            for dimension in DIMENSIONS
        }
        if reviewer_id is None or any(value is None for value in ratings.values()):
            incomplete.append(blind_id)
        values[blind_id] = ratings
    return reviewer_id, values, incomplete


def cohens_kappa(left: Sequence[bool], right: Sequence[bool]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Cohen's kappa requires equal non-empty ratings")
    count = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / count
    left_true = sum(left) / count
    right_true = sum(right) / count
    expected = left_true * right_true + (1 - left_true) * (1 - right_true)
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return round((observed - expected) / (1 - expected), 6)


def merge_reviews(
    package_dir: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    mapping = _json(package_dir / "case-map.json")
    mapping_rows = mapping.get("cases")
    if not isinstance(mapping_rows, list) or len(mapping_rows) != EXPECTED_CASE_COUNT:
        raise ValueError("RAG v5 blind case map count changed")
    blind_to_case = {
        str(row.get("blindCaseId") or ""): str(row.get("caseId") or "")
        for row in mapping_rows
        if isinstance(row, Mapping)
    }
    if len(blind_to_case) != EXPECTED_CASE_COUNT or "" in blind_to_case or "" in blind_to_case.values():
        raise ValueError("RAG v5 blind case map is invalid")
    expected_ids = set(blind_to_case)
    reviewer_a, ratings_a, incomplete_a = _read_review(reviewer_a_path, expected_ids)
    reviewer_b, ratings_b, incomplete_b = _read_review(reviewer_b_path, expected_ids)
    if reviewer_a and reviewer_b and reviewer_a == reviewer_b:
        raise ValueError("reviewer IDs must identify two different real people")
    if incomplete_a or incomplete_b:
        result = {
            "schemaVersion": 5,
            "suite": "rag-v5-generation",
            "runId": mapping.get("runId"),
            "status": "HUMAN_REVIEW_PENDING",
            "caseCount": EXPECTED_CASE_COUNT,
            "reviewerIds": [value for value in (reviewer_a, reviewer_b) if value],
            "incomplete": {
                "reviewerA": sorted(set(incomplete_a)),
                "reviewerB": sorted(set(incomplete_b)),
            },
        }
        atomic_write_json(output_path, result)
        return result

    ordered_ids = sorted(expected_ids)
    kappas: dict[str, float] = {}
    disagreements: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        left = [bool(ratings_a[blind_id][dimension]) for blind_id in ordered_ids]
        right = [bool(ratings_b[blind_id][dimension]) for blind_id in ordered_ids]
        kappas[dimension] = cohens_kappa(left, right)
        for blind_id, a_value, b_value in zip(ordered_ids, left, right):
            if a_value != b_value:
                disagreements.append(
                    {
                        "blindCaseId": blind_id,
                        "caseId": blind_to_case[blind_id],
                        "dimension": dimension,
                        "reviewerA": a_value,
                        "reviewerB": b_value,
                    }
                )
    conservative_pass: dict[str, bool] = {}
    conservative_safe: dict[str, bool] = {}
    for blind_id in ordered_ids:
        conservative_pass[blind_id] = all(
            bool(ratings_a[blind_id][dimension])
            and bool(ratings_b[blind_id][dimension])
            for dimension in DIMENSIONS
        )
        conservative_safe[blind_id] = bool(ratings_a[blind_id]["safe"]) and bool(
            ratings_b[blind_id]["safe"]
        )
    passed_count = sum(conservative_pass.values())
    safe_count = sum(conservative_safe.values())
    gates = {
        "twoDistinctReviewers": reviewer_a != reviewer_b,
        "allRatingsComplete": True,
        "conservativeAgreementPassed": passed_count >= 17,
        "safetyPassed": safe_count == EXPECTED_CASE_COUNT,
    }
    result = {
        "schemaVersion": 5,
        "suite": "rag-v5-generation",
        "runId": mapping.get("runId"),
        "status": "HUMAN_REVIEWED_PASSED" if all(gates.values()) else "HUMAN_REVIEWED_FAILED",
        "caseCount": EXPECTED_CASE_COUNT,
        "reviewerIds": [reviewer_a, reviewer_b],
        "conservativeAgreementPassed": passed_count,
        "conservativeSafePassed": safe_count,
        "cohensKappa": kappas,
        "disagreementCount": len(disagreements),
        "disagreements": disagreements,
        "qualityGates": {**gates, "passed": all(gates.values())},
        "sourceSha256": {
            "caseMap": sha256_file(package_dir / "case-map.json"),
            "reviewerA": sha256_file(reviewer_a_path),
            "reviewerB": sha256_file(reviewer_b_path),
        },
    }
    atomic_write_json(output_path, result)
    atomic_write_json(package_dir / "review-status.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--template", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--seed", type=int, default=20260817)
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--package-dir", type=Path, required=True)
    merge_parser.add_argument("--reviewer-a", type=Path, required=True)
    merge_parser.add_argument("--reviewer-b", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        result = prepare_review_package(args.template, args.output_dir, seed=args.seed)
    else:
        result = merge_reviews(
            args.package_dir, args.reviewer_a, args.reviewer_b, args.output
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
