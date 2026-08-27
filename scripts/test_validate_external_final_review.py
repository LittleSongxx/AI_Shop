import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest
from validate_external_final_review import ExternalReviewError, compare, seal, validate


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate_rows() -> list[dict]:
    return [
        {
            "schemaVersion": "aishop-evaluation-case/v3",
            "id": "search-ext-test-001",
            "split": "final",
            "domain": "search",
            "input": {"query": "耳机"},
            "expected": {"qrels": {"p1": 3}},
        },
        {
            "schemaVersion": "aishop-evaluation-case/v3",
            "id": "rag-ext-test-001",
            "split": "final",
            "domain": "rag",
            "input": {"question": "退货规则是什么"},
            "expected": {"answerable": True},
        },
        {
            "schemaVersion": "aishop-evaluation-case/v3",
            "id": "agent-ext-test-001",
            "split": "final",
            "domain": "agent",
            "input": {"turns": [{"message": "帮我找耳机"}]},
            "expected": {"terminalStatuses": ["SUCCEEDED"]},
        },
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_open_sheet(tmp_path: Path, reviewer: str, *, altered: bool = False) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    dataset = tmp_path / "candidate.jsonl"
    _write_jsonl(dataset, _candidate_rows())
    review = tmp_path / f"{reviewer}.open.jsonl"
    labels = {
        "search": {
            "relevantProductIds": ["p1"],
            "noResult": False,
            "judgmentMode": "EXHAUSTIVE_CATALOG",
            "notes": "完整 catalog 中 p1 满足约束",
        },
        "rag": {
            "answerable": False,
            "relevantFactIds": [],
            "requiredClaims": [],
            "noAnswerScope": "当前快照未提供该动态信息",
            "notes": "快照不足，不能扩大回答范围",
        },
        "agent": {
            "terminalStatuses": ["SUCCEEDED"],
            "requiredTools": [],
            "safetyExpectation": "SAFE",
            "notes": "只需要读取商品目录",
        },
    }
    rows = []
    for source in _candidate_rows():
        row = {
            "schemaVersion": "aishop-external-final-review/v1",
            "id": source["id"],
            "domain": source["domain"],
            "input": source["input"],
            "reviewerId": reviewer,
            "labels": labels[source["domain"]],
        }
        if altered and source["domain"] == "search":
            row["labels"] = {**row["labels"], "relevantProductIds": ["p2"]}
        rows.append(row)
    _write_jsonl(review, rows)
    manifest = {
        "schemaVersion": "aishop-external-final-review/v1",
        "artifact": "EXTERNAL_FINAL_LABEL_TEMPLATE",
        "lifecycle": "OPEN",
        "datasetSha256": _sha256(dataset),
        "sheetSha256": _sha256(review),
        "caseCount": len(rows),
        "reviewerId": reviewer,
    }
    manifest_path = review.with_name(review.name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return dataset, review, manifest_path


def test_open_template_validates_but_incomplete_sheet_fails(tmp_path: Path) -> None:
    dataset, review, _ = _write_open_sheet(tmp_path, "reviewer-a")
    result = validate(Namespace(dataset=dataset, review=review, complete=True))
    assert result["valid"] is True

    rows = _json_rows(review)
    for row in rows:
        row["labels"] = {key: None for key in row["labels"]}
    _write_jsonl(review, rows)
    with pytest.raises(ExternalReviewError, match="labels are incomplete"):
        validate(Namespace(dataset=dataset, review=review, complete=True))


def _json_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_seal_and_compare_are_bound_to_candidate_and_reviewer(tmp_path: Path) -> None:
    dataset, review_a, _ = _write_open_sheet(tmp_path, "reviewer-a")
    _, review_b, _ = _write_open_sheet(tmp_path / "b", "reviewer-b", altered=True)
    sealed_a = tmp_path / "reviewer-a.sealed.jsonl"
    sealed_b = tmp_path / "reviewer-b.sealed.jsonl"
    seal(Namespace(dataset=dataset, review=review_a, output=sealed_a))
    seal(Namespace(dataset=dataset, review=review_b, output=sealed_b))
    sealed_manifest = json.loads(
        sealed_a.with_name(sealed_a.name + ".manifest.json").read_text(encoding="utf-8")
    )
    assert sealed_manifest["openSheetSha256AtExport"] == _sha256(review_a)

    output = tmp_path / "agreement.json"
    result = compare(
        Namespace(dataset=dataset, review_a=sealed_a, review_b=sealed_b, output=output)
    )
    assert result["status"] == "PENDING_ADJUDICATION"
    assert result["caseCount"] == 3
    assert result["disagreementCaseCount"] == 1
    agreement = json.loads(output.read_text(encoding="utf-8"))
    assert agreement["releaseGateEligible"] is False
    assert agreement["disagreements"][0]["id"] == "search-ext-test-001"


def test_forbidden_prediction_field_is_rejected(tmp_path: Path) -> None:
    dataset, review, _ = _write_open_sheet(tmp_path, "reviewer-a")
    rows = _json_rows(review)
    rows[0]["expected"] = {"qrels": {"p1": 3}}
    _write_jsonl(review, rows)
    with pytest.raises(ExternalReviewError, match="forbidden fields"):
        validate(Namespace(dataset=dataset, review=review, complete=False))
