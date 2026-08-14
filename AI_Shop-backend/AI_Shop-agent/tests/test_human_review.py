import csv
import json
from pathlib import Path

from benchmarks.human_review.rag_v4_review import (
    DIMENSIONS,
    merge_reviews,
    prepare_review_package,
)


def _make_result_dir(tmp_path: Path) -> Path:
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    rows = [
        {
            "caseId": f"case-{index:03d}",
            "query": f"问题 {index}",
            "answer": f"答案 {index}",
            "retrievedRefs": [],
            "automaticMetrics": {},
        }
        for index in range(60)
    ]
    (result_dir / "review-template.json").write_text(
        json.dumps({"runId": "run-1", "cases": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    (result_dir / "summary.json").write_text(
        json.dumps({"cases": [{"caseId": row["caseId"], "taskSuccess": True} for row in rows]}),
        encoding="utf-8",
    )
    return result_dir


def _complete_csv(path: Path, reviewer: str, value: str = "TRUE") -> None:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["reviewerId", "blindCaseId", *DIMENSIONS, "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"reviewerId": reviewer, "blindCaseId": row["blindCaseId"], **{dimension: value for dimension in DIMENSIONS}, "notes": "ok"})


def test_prepare_and_merge_blind_review(tmp_path):
    result_dir = _make_result_dir(tmp_path)
    package_dir = tmp_path / "package"
    prepared = prepare_review_package(result_dir, package_dir)
    assert prepared["status"] == "HUMAN_REVIEW_PENDING"
    assert (package_dir / "blind-cases.jsonl").is_file()
    assert "automatic" not in (package_dir / "blind-cases.jsonl").read_text(encoding="utf-8").lower()

    _complete_csv(package_dir / "reviewer-a.csv", "alice")
    _complete_csv(package_dir / "reviewer-b.csv", "bob")
    merged = merge_reviews(
        package_dir,
        package_dir / "reviewer-a.csv",
        package_dir / "reviewer-b.csv",
        result_dir / "summary.json",
        tmp_path / "merged.json",
    )
    assert merged["status"] == "HUMAN_REVIEWED"
    assert merged["caseCount"] == 60
    assert all(value == 1.0 for value in merged["cohensKappa"].values())


def test_merge_stays_pending_until_both_reviewers_complete(tmp_path):
    result_dir = _make_result_dir(tmp_path)
    package_dir = tmp_path / "package"
    prepare_review_package(result_dir, package_dir)
    pending = merge_reviews(
        package_dir,
        package_dir / "reviewer-a.csv",
        package_dir / "reviewer-b.csv",
        result_dir / "summary.json",
        tmp_path / "merged.json",
    )
    assert pending["status"] == "HUMAN_REVIEW_PENDING"
