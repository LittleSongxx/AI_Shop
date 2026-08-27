from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.core import lifecycle
from evaluation.core.contracts import Domain, EvaluationCase, LifecycleError, Split
from evaluation.core.final_exposure import (
    audit_final_input_exposure,
    final_exposure_audit_report,
)
from evaluation.core.io import atomic_write_jsonl


def _case(case_id: str, query: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        split=Split.FINAL,
        domain=Domain.RAG,
        input={"query": query},
        expected={"relevantFactIds": ["fact-1"], "requiredClaims": [{}]},
        required_providers=("llm",),
    )


def test_source_exposure_audit_reports_location_and_hash_without_final_text(tmp_path):
    source = tmp_path / "builder.py"
    exposed = "这是一条只应存在于仓库外密封终局集中的完整问题"
    source.write_text(f"FINAL_QUESTION = {exposed!r}\n", encoding="utf-8")

    findings = audit_final_input_exposure(
        [_case("rag-fin-source-audit", exposed)],
        repository_root=tmp_path,
        source_paths=[source],
    )

    assert len(findings) == 1
    assert findings[0]["sourcePath"] == "builder.py"
    assert findings[0]["line"] == 1
    assert len(str(findings[0]["matchSha256"])) == 64
    assert exposed not in repr(findings)


def test_repository_scan_ignores_runtime_observation_artifacts(tmp_path):
    exposed = "这是一条只能保留在历史运行产物中的终局问题"
    runtime = tmp_path / "run" / "report.json"
    runtime.parent.mkdir()
    runtime.write_text(
        json.dumps({"query": exposed}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "visible.py"
    source.write_text(f"VALUE = {exposed!r}\n", encoding="utf-8")

    findings = audit_final_input_exposure(
        [_case("rag-fin-runtime-boundary", exposed)],
        repository_root=tmp_path,
    )

    assert {item["sourcePath"] for item in findings} == {"visible.py"}


def test_source_exposure_audit_includes_comments_and_plain_text_formats(tmp_path):
    exposed = "这条终局问题即使只写在注释或文档中也已经对开发者可见"
    python_source = tmp_path / "comment.py"
    python_source.write_text(f"# {exposed}\n", encoding="utf-8")
    yaml_source = tmp_path / "notes.yml"
    yaml_source.write_text(f"final_note: {exposed}\n", encoding="utf-8")

    findings = audit_final_input_exposure(
        [_case("rag-fin-visible-text", exposed)],
        repository_root=tmp_path,
    )

    assert {item["sourcePath"] for item in findings} == {
        "comment.py",
        "notes.yml",
    }


def test_short_exact_final_input_is_not_exempt_from_exposure_audit(tmp_path):
    exposed = "短问题也泄漏"
    source = tmp_path / "visible.txt"
    source.write_text(exposed + "\n", encoding="utf-8")

    findings = audit_final_input_exposure(
        [_case("rag-fin-short-exact", exposed)],
        repository_root=tmp_path,
    )

    assert len(findings) == 1
    assert findings[0]["sourcePath"] == "visible.txt"


def test_exposure_report_contains_only_safe_location_metadata():
    exposed = "这是一条不可出现在审计报告中的终局输入原文"
    case = _case("rag-fin-safe-report", exposed)
    report = final_exposure_audit_report(
        [case],
        [
            {
                "caseId": case.case_id,
                "inputKind": "query",
                "sourcePath": "visible.py",
                "line": 7,
                "matchSha256": "a" * 64,
                "matchChars": len(exposed),
            }
        ],
    )

    assert report["counts"] == {
        "auditedCases": 1,
        "exposedCases": 1,
        "findings": 1,
        "sourceFiles": 1,
    }
    assert set(report["findings"][0]) == {
        "caseId",
        "sourcePath",
        "line",
        "matchSha256",
    }
    assert exposed not in repr(report)


def test_current_v9_builder_is_detected_as_source_exposed(tmp_path, monkeypatch):
    from evaluation import build_final_holdout_v4 as builder
    from evaluation.core.datasets import parse_case

    monkeypatch.setattr(builder, "HOLDOUT_VERSION", "v9")
    monkeypatch.setattr(builder, "OUT", tmp_path / "v9.jsonl")
    monkeypatch.setattr(builder, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(builder, "validate_final_against_known", lambda _cases: None)
    rows = builder.build()
    cases = [parse_case(row, expected_split=Split.FINAL) for row in rows]

    findings = audit_final_input_exposure(
        cases,
        source_paths=[Path(builder.__file__)],
    )

    assert findings
    assert {item["sourcePath"] for item in findings} == {
        "AI_Shop-backend/AI_Shop-agent/evaluation/build_final_holdout_v4.py"
    }
    assert all("matchSha256" in item and "line" in item for item in findings)


def test_claim_final_fails_closed_without_echoing_exposed_input(tmp_path, monkeypatch):
    source = tmp_path / "visible.py"
    exposed = "这条终局输入已经出现在开发者可见源码中因此不能领取"
    source.write_text(f"VALUE = {exposed!r}\n", encoding="utf-8")
    dataset = tmp_path / "external-final.jsonl"
    row = {
        "schemaVersion": "aishop-evaluation-case/v2",
        "id": "rag-fin-exposure-contract",
        "split": "final",
        "domain": "rag",
        "input": {"query": exposed},
        "expected": {
            "relevantFactIds": ["fact-1"],
            "requiredClaims": [{"patterns": ["事实"], "factIds": ["fact-1"]}],
        },
        "requiredProviders": ["llm"],
        "tags": ["contract"],
    }
    atomic_write_jsonl(dataset, [row])

    monkeypatch.setattr(lifecycle, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(lifecycle, "CONSUMED_FINAL_PATH", tmp_path / "consumed.json")
    monkeypatch.setattr(lifecycle, "source_fingerprint", lambda: {"capturedAt": "now"})
    monkeypatch.setattr(lifecycle, "validate_repository_datasets", lambda: {})
    monkeypatch.setattr(lifecycle, "validate_final_against_known", lambda _cases: None)
    monkeypatch.setattr(
        lifecycle,
        "audit_final_input_exposure",
        lambda cases, **_kwargs: audit_final_input_exposure(
            cases,
            repository_root=tmp_path,
            source_paths=[source],
        ),
    )
    lifecycle.freeze_final("release-exposure-contract")

    with pytest.raises(LifecycleError) as raised:
        lifecycle.claim_final("release-exposure-contract", dataset)

    assert "visible.py:1" in str(raised.value)
    assert exposed not in str(raised.value)
    assert not (tmp_path / "state/releases/release-exposure-contract/final.jsonl").exists()
