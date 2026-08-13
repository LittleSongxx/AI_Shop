import json

from benchmarks import build_rag_v3_datasets as builder
from benchmarks.build_rag_v3_datasets import (
    LABEL_CHANGES,
    build_known,
    fresh_cases,
    public_cases,
)


def test_known_regression_converts_all_64_old_cases_without_id_changes():
    cases = build_known()

    assert len(cases) == 64
    assert len({case["id"] for case in cases}) == 64
    assert all(case["split"] == "known_regression" for case in cases)
    assert {case["id"] for case in cases if case.get("labelChangeReason")} == set(
        LABEL_CHANGES
    )


def test_public_v3_has_48_cases_and_is_not_mixed_with_holdout():
    cases = public_cases()

    assert len(cases) == 48
    assert len({case["id"] for case in cases}) == 48
    assert sum(case["expectedBehavior"] == "ANSWER" for case in cases) == 36
    assert sum(case["expectedBehavior"] == "REFUSE" for case in cases) == 8
    assert sum(bool(case["injection"]) for case in cases) == 6
    assert all(case["split"] == "public" for case in cases)


def test_fresh_definition_is_20_answerable_6_no_answer_and_6_injection():
    cases = fresh_cases()

    assert len(cases) == 32
    assert len({case["id"] for case in cases}) == 32
    assert sum(not case["noAnswer"] and not case["injection"] for case in cases) == 20
    assert sum(case["noAnswer"] and not case["injection"] for case in cases) == 6
    assert sum(bool(case["injection"]) for case in cases) == 6
    assert all(case["split"] == "fresh_holdout" for case in cases)


def test_holdout_lock_records_absolute_frozen_config(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    project_root = repo_root / "AI_Shop-backend" / "AI_Shop-agent"
    datasets_root = project_root / "benchmarks" / "datasets"
    frozen = project_root / "benchmarks" / "results" / "run" / "frozen-config.json"
    datasets_root.mkdir(parents=True)
    frozen.parent.mkdir(parents=True)
    frozen.write_text("{}\n", encoding="utf-8")
    catalog = repo_root / "AI_Shop-backend" / "data" / "demo_knowledge" / "catalog.v1.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(builder.CATALOG_PATH.read_bytes())
    known = builder.build_known()
    monkeypatch.setattr(builder, "REPO_ROOT", repo_root)
    monkeypatch.setattr(builder, "DATASETS_ROOT", datasets_root)
    monkeypatch.setattr(builder, "FRESH_PATH", datasets_root / "fresh.jsonl")
    monkeypatch.setattr(builder, "GENERATION_PATH", datasets_root / "generation.json")
    monkeypatch.setattr(builder, "KNOWN_PATH", datasets_root / "known.jsonl")
    monkeypatch.setattr(builder, "CATALOG_PATH", catalog)
    builder.write_jsonl(builder.KNOWN_PATH, "# known", known)

    builder.finalize_holdout(frozen)

    lock = json.loads(
        builder.FRESH_PATH.with_suffix(".lock.json").read_text(encoding="utf-8")
    )
    assert lock["frozenConfigPath"] == str(frozen.resolve())
