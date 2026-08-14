"""Prepare and merge a randomized two-reviewer RAG v4 blind review."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from benchmarks.mature_eval.common import atomic_write_json, sha256_file

DIMENSIONS = ("grounded", "complete", "citationAligned", "safe")
REVIEW_FIELDS = ("reviewerId", "blindCaseId", *DIMENSIONS, "notes")
RUBRIC_PATH = Path(__file__).with_name("rubric.md")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _case_rows(result_dir: Path) -> tuple[str, list[dict[str, Any]], Path]:
    template_path = result_dir / "review-template.json"
    if not template_path.is_file():
        raise ValueError("RAG v4 review-template.json is missing")
    template = _load_json(template_path)
    rows = template.get("cases")
    if not isinstance(rows, list):
        raise ValueError("review template cases must be a list")
    run_id = str(template.get("runId") or "")
    if not run_id:
        raise ValueError("review template runId is missing")
    return run_id, [dict(row) for row in rows], template_path


def _public_evidence(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, ref in enumerate(row.get("retrievedRefs") or [], 1):
        if not isinstance(ref, Mapping):
            continue
        result.append(
            {
                "citation": index,
                "source": ref.get("source"),
                "heading": ref.get("heading"),
                "snippet": ref.get("snippet"),
            }
        )
    return result


def _write_review_csv(path: Path, blind_ids: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
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


def prepare_review_package(
    result_dir: Path,
    output_dir: Path,
    *,
    seed: int = 20260814,
    expected_count: int = 60,
) -> dict[str, Any]:
    run_id, rows, template_path = _case_rows(result_dir)
    case_ids = [str(row.get("caseId") or "") for row in rows]
    if (
        len(rows) != expected_count
        or "" in case_ids
        or len(set(case_ids)) != expected_count
    ):
        raise ValueError(
            f"blind review requires {expected_count} unique generation cases"
        )
    ordered = list(rows)
    random.Random(seed).shuffle(ordered)
    blind_rows: list[dict[str, Any]] = []
    mapping: list[dict[str, str]] = []
    for index, row in enumerate(ordered, 1):
        blind_id = f"R4-{index:03d}"
        case_id = str(row["caseId"])
        mapping.append({"blindCaseId": blind_id, "caseId": case_id})
        blind_rows.append(
            {
                "blindCaseId": blind_id,
                "query": row.get("query"),
                "answer": row.get("answer"),
                "evidence": _public_evidence(row),
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "blind-cases.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in blind_rows
        ),
        encoding="utf-8",
    )
    atomic_write_json(
        output_dir / "case-map.json",
        {
            "schemaVersion": 1,
            "suite": "rag-generation-live-v4",
            "runId": run_id,
            "randomizationSeed": seed,
            "sourceTemplateSha256": sha256_file(template_path),
            "cases": mapping,
        },
    )
    _write_review_csv(output_dir / "reviewer-a.csv", [row["blindCaseId"] for row in mapping])
    _write_review_csv(output_dir / "reviewer-b.csv", [row["blindCaseId"] for row in mapping])
    (output_dir / "rubric.md").write_bytes(RUBRIC_PATH.read_bytes())
    status = {
        "schemaVersion": 1,
        "suite": "rag-generation-live-v4",
        "runId": run_id,
        "status": "HUMAN_REVIEW_PENDING",
        "reviewerCount": 0,
        "caseCount": expected_count,
        "automaticVerdictsExposed": False,
    }
    atomic_write_json(output_dir / "review-status.json", status)
    sums = {
        path.name: sha256_file(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file()
    }
    atomic_write_json(output_dir / "package-manifest.json", {**status, "files": sums})
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
        dimensions = {dimension: _parse_bool(str(row.get(dimension) or "")) for dimension in DIMENSIONS}
        if reviewer_id is None or any(value is None for value in dimensions.values()):
            incomplete.append(blind_id)
        values[blind_id] = dimensions
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


def _automatic_by_case(summary_path: Path) -> dict[str, bool]:
    payload = _load_json(summary_path)
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise ValueError("generation summary cases are missing")
    return {
        str(row.get("caseId") or ""): bool(row.get("taskSuccess"))
        for row in rows
    }


def merge_reviews(
    package_dir: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    summary_path: Path,
    output_path: Path,
    *,
    expected_count: int = 60,
) -> dict[str, Any]:
    mapping_payload = _load_json(package_dir / "case-map.json")
    mapping_rows = mapping_payload.get("cases")
    if not isinstance(mapping_rows, list) or len(mapping_rows) != expected_count:
        raise ValueError("blind case map count changed")
    blind_to_case = {
        str(row.get("blindCaseId") or ""): str(row.get("caseId") or "")
        for row in mapping_rows
        if isinstance(row, Mapping)
    }
    if len(blind_to_case) != expected_count or "" in blind_to_case or "" in blind_to_case.values():
        raise ValueError("blind case map is invalid")
    expected_ids = set(blind_to_case)
    reviewer_a, ratings_a, incomplete_a = _read_review(reviewer_a_path, expected_ids)
    reviewer_b, ratings_b, incomplete_b = _read_review(reviewer_b_path, expected_ids)
    if reviewer_a and reviewer_b and reviewer_a == reviewer_b:
        raise ValueError("reviewer IDs must identify two different people")
    if incomplete_a or incomplete_b:
        result = {
            "schemaVersion": 1,
            "suite": "rag-generation-live-v4",
            "runId": mapping_payload.get("runId"),
            "status": "HUMAN_REVIEW_PENDING",
            "caseCount": expected_count,
            "reviewerIds": [value for value in (reviewer_a, reviewer_b) if value],
            "incomplete": {
                "reviewerA": sorted(set(incomplete_a)),
                "reviewerB": sorted(set(incomplete_b)),
            },
        }
        atomic_write_json(output_path, result)
        return result

    automatic = _automatic_by_case(summary_path)
    if set(automatic) != set(blind_to_case.values()):
        raise ValueError("automatic result cases do not match the blind review")
    kappas: dict[str, float] = {}
    disagreements: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        left = [bool(ratings_a[blind_id][dimension]) for blind_id in sorted(expected_ids)]
        right = [bool(ratings_b[blind_id][dimension]) for blind_id in sorted(expected_ids)]
        kappas[dimension] = cohens_kappa(left, right)
        for blind_id, a_value, b_value in zip(sorted(expected_ids), left, right):
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
    reviewer_pass = {
        "reviewerA": {
            blind_id: all(bool(ratings_a[blind_id][dimension]) for dimension in DIMENSIONS)
            for blind_id in expected_ids
        },
        "reviewerB": {
            blind_id: all(bool(ratings_b[blind_id][dimension]) for dimension in DIMENSIONS)
            for blind_id in expected_ids
        },
    }
    agreement: dict[str, float] = {}
    for reviewer, values in reviewer_pass.items():
        agreement[reviewer] = round(
            sum(values[blind_id] == automatic[blind_to_case[blind_id]] for blind_id in expected_ids)
            / expected_count,
            6,
        )
    result = {
        "schemaVersion": 1,
        "suite": "rag-generation-live-v4",
        "runId": mapping_payload.get("runId"),
        "status": "HUMAN_REVIEWED",
        "caseCount": expected_count,
        "reviewerIds": [reviewer_a, reviewer_b],
        "cohensKappa": kappas,
        "automaticHumanAgreement": agreement,
        "disagreementCount": len(disagreements),
        "disagreements": disagreements,
        "sourceSha256": {
            "caseMap": sha256_file(package_dir / "case-map.json"),
            "reviewerA": sha256_file(reviewer_a_path),
            "reviewerB": sha256_file(reviewer_b_path),
            "automaticSummary": sha256_file(summary_path),
        },
    }
    atomic_write_json(output_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--result-dir", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--seed", type=int, default=20260814)
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--package-dir", type=Path, required=True)
    merge_parser.add_argument("--reviewer-a", type=Path, required=True)
    merge_parser.add_argument("--reviewer-b", type=Path, required=True)
    merge_parser.add_argument("--source-summary", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "prepare":
        result = prepare_review_package(
            args.result_dir, args.output_dir, seed=args.seed
        )
    else:
        result = merge_reviews(
            args.package_dir,
            args.reviewer_a,
            args.reviewer_b,
            args.source_summary,
            args.output,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
