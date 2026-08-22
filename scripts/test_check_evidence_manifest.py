import hashlib
import json
from pathlib import Path

from check_evidence_manifest import (
    LOCK_SCHEMA,
    _canonical_sha256,
    _parse_sums,
    _validate_auxiliary_evidence,
    _validate_benchmarks,
    _validate_failed_final_attempts,
    _validate_lock,
    _validate_visible_runs,
    validate_repository,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _lock_fixture(root: Path) -> tuple[dict, dict]:
    rows = [
        {
            "schemaVersion": "aishop-evaluation-case/v2",
            "id": "search-dev-contract-001",
            "split": "development",
            "domain": "search",
            "input": {"query": "耳机"},
            "expected": {},
            "requiredProviders": ["embedding"],
            "tags": [],
        }
    ]
    dataset = root / "evaluation/datasets/development/search.jsonl"
    dataset.parent.mkdir(parents=True)
    dataset.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    dataset_relative = dataset.relative_to(root).as_posix()
    lock = {
        "schemaVersion": LOCK_SCHEMA,
        "split": "development",
        "caseCount": 1,
        "domainCounts": {"agent": 0, "rag": 0, "search": 1},
        "canonicalDatasetSha256": _canonical_sha256(rows),
        "files": {
            dataset_relative: {
                "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "bytes": dataset.stat().st_size,
            }
        },
    }
    lock_path = root / "evaluation/datasets/locks/development.lock.json"
    _write_json(lock_path, lock)
    descriptor = {
        "split": "development",
        "path": lock_path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    }
    return descriptor, {"splitMinimums": {"development": {"search": 1}}}


def test_dataset_lock_cross_checks_files_counts_and_canonical_hash(tmp_path: Path) -> None:
    descriptor, suite = _lock_fixture(tmp_path)
    errors: list[str] = []

    rows = _validate_lock(tmp_path, descriptor, suite, errors)

    assert len(rows) == 1
    assert errors == []


def test_dataset_lock_rejects_tampered_file(tmp_path: Path) -> None:
    descriptor, suite = _lock_fixture(tmp_path)
    dataset = tmp_path / "evaluation/datasets/development/search.jsonl"
    dataset.write_text(dataset.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    errors: list[str] = []

    _validate_lock(tmp_path, descriptor, suite, errors)

    assert any("file hash mismatch" in error for error in errors)
    assert any("file size mismatch" in error for error in errors)


def test_sha256sums_rejects_path_escape(tmp_path: Path) -> None:
    (tmp_path / "SHA256SUMS").write_text(f"{'a' * 64}  ../secret\n", encoding="utf-8")
    errors: list[str] = []

    _parse_sums(tmp_path, errors)

    assert any("escapes repository" in error for error in errors)


def test_repository_accepts_published_final_when_required() -> None:
    assert not any("published final evidence package is required" in error for error in validate_repository())
    assert not any(
        "published final evidence package is required" in error
        for error in validate_repository(require_current=True)
    )


def test_current_customer_service_report_keeps_human_review_fail_closed() -> None:
    manifest = json.loads((Path(__file__).parents[1] / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = manifest["evaluation"]["customerServiceGold"]
    report_path = Path(__file__).parents[1] / descriptor["reportPath"]
    report = json.loads(report_path.read_text("utf-8"))

    assert descriptor["status"] == "PROVISIONAL_NOT_HUMAN_GOLD"
    assert report["humanReviewPlan"]["status"] == "PENDING_INDEPENDENT_REVIEW"
    assert report["humanReviewPlan"]["requiredAnnotators"] == 2
    assert report["humanReviewPlan"]["blindedFirstPass"] is True


def test_failed_final_attempt_is_hashed_failed_and_read_only(tmp_path: Path) -> None:
    package = tmp_path / "evaluation/.runs/final-failed"
    package.mkdir(parents=True)
    summary = {"runId": "final-failed", "split": "final"}
    gates = {"passed": False}
    source = {
        "source": {"sha256": "c" * 64},
        "providerConfigurationSha256": "d" * 64,
    }
    run = {
        "schemaVersion": "aishop-evaluation-run/v3",
        "runId": "final-failed",
        "split": "final",
        "datasetSha256": "a" * 64,
        "sourceFingerprint": source,
        "summary": summary,
        "gates": gates,
    }
    lifecycle = {
        "schemaVersion": "aishop-evaluation-final-lifecycle/v3",
        "releaseId": "release-failed",
        "status": "EXECUTED",
        "run": {
            "runId": "final-failed",
            "outcome": "FAILED",
            "evidenceSha256": None,
        },
    }
    payloads = {
        "bad-cases.jsonl": b"{}\n",
        "cases.jsonl": b"{}\n",
        "environment.json": b"{}\n",
        "evidence-manifest.json": json.dumps(
            {"schemaVersion": "aishop-evaluation-evidence/v3", "run": run}
        ).encode()
        + b"\n",
        "gates.json": json.dumps(gates).encode() + b"\n",
        "lifecycle.json": json.dumps(lifecycle).encode() + b"\n",
        "report.md": b"# failed final\n",
        "source-fingerprint.json": json.dumps(source).encode() + b"\n",
        "summary.json": json.dumps(summary).encode() + b"\n",
    }
    for name, content in payloads.items():
        (package / name).write_bytes(content)
    (package / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n"
            for name, content in sorted(payloads.items())
        ),
        encoding="utf-8",
    )
    for path in package.iterdir():
        if path.is_file():
            path.chmod(0o444)
    descriptor = {
        "path": package.relative_to(tmp_path).as_posix(),
        "releaseId": "release-failed",
        "runId": "final-failed",
        "datasetSha256": "a" * 64,
        "sourceSha256": "c" * 64,
        "providerConfigurationSha256": "d" * 64,
        "sha256SumsSha256": hashlib.sha256(
            (package / "SHA256SUMS").read_bytes()
        ).hexdigest(),
        "qualityGatePassed": False,
        "outcome": "FAILED",
    }
    errors: list[str] = []

    _validate_failed_final_attempts(tmp_path, [descriptor], errors)

    assert errors == []
    descriptor["qualityGatePassed"] = True
    errors = []
    _validate_failed_final_attempts(tmp_path, [descriptor], errors)
    assert any("qualityGatePassed=false" in error for error in errors)


def test_auxiliary_evidence_requires_typed_read_only_hashed_package(tmp_path: Path) -> None:
    package = tmp_path / "evidence/benchmarks/resilience/fault-contract"
    package.mkdir(parents=True)
    source_run = "fault-contract-source"
    files: dict[str, bytes] = {
        "bad-cases.jsonl": b"",
        "cases.jsonl": b'{"case_id":"fault-case","status":"FAILED"}\n',
        "environment.json": b"{}\n",
        "gates.json": b'{"passed":true}\n',
        "report.md": b"# fault contract\n",
        "source-fingerprint.json": b"{}\n",
        "summary.json": json.dumps({"runId": source_run}).encode() + b"\n",
    }
    for name, content in files.items():
        (package / name).write_bytes(content)
    inventory = {
        name: {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        for name, content in files.items()
    }
    manifest = {
        "schemaVersion": "aishop-auxiliary-evidence/v1",
        "kind": "resilience",
        "packageId": "fault-contract",
        "sourceRunId": source_run,
        "sourceRunSha256SumsSha256": "a" * 64,
        "normalQualityDenominatorExcluded": True,
        "shadowOnly": False,
        "files": inventory,
    }
    _write_json(package / "evidence-manifest.json", manifest)
    sums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.iterdir()
        if path.is_file()
    }
    (package / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    for path in package.iterdir():
        if path.is_file():
            path.chmod(0o444)
    errors: list[str] = []
    descriptor = {
        "kind": "resilience",
        "packageId": "fault-contract",
        "path": package.relative_to(tmp_path).as_posix(),
        "sourceRunId": source_run,
        "sourceRunSha256SumsSha256": "a" * 64,
        "sha256SumsSha256": hashlib.sha256((package / "SHA256SUMS").read_bytes()).hexdigest(),
    }

    _validate_auxiliary_evidence(tmp_path, [descriptor], errors)

    assert errors == []


def test_db_benchmark_v2_requires_counted_equivalent_rollback_evidence(
    tmp_path: Path,
) -> None:
    package = tmp_path / "evidence/benchmarks/db/db-v2"
    package.mkdir(parents=True)
    measurement = {
        "counterSource": "COUNTED_CURSOR_EXECUTE_AND_POOL_ACQUIRE_CALLS",
        "stableResult": True,
    }
    payload = {
        "schemaVersion": "aishop-db-benchmark/v2",
        "benchmarkId": "db-v2",
        "notProductionSlo": True,
        "sourceFingerprint": {
            "source": {"sha256": "a" * 64},
            "providerConfigurationSha256": "b" * 64,
        },
        "databaseFacts": {"dedicatedBenchmarkDatabase": False},
        "rollbackProbe": {
            "passed": True,
            "beforeCount": 0,
            "insideTransactionCount": 1,
            "afterRollbackCount": 0,
            "committedWrites": 0,
        },
        "rows": {
            "1": {
                "candidateCount": 1,
                "uniqueCandidateCount": 1,
                "resultEquivalence": {
                    "offerSnapshot": True,
                    "decisionFeature": True,
                },
                "batchOfferSnapshot": measurement,
                "nPlusOneOfferSnapshot": measurement,
                "batchDecisionFeature": measurement,
                "nPlusOneDecisionFeature": measurement,
            }
        },
    }
    _write_json(package / "benchmark.json", payload)
    (package / "report.md").write_text("# DB benchmark\n", encoding="utf-8")
    inventory = {
        path.name: {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in package.iterdir()
    }
    manifest = {
        "schemaVersion": "aishop-db-benchmark-evidence/v2",
        "benchmarkSchemaVersion": "aishop-db-benchmark/v2",
        "benchmarkId": "db-v2",
        "notProductionSlo": True,
        "dedicatedBenchmarkDatabase": False,
        "rollbackProbePassed": True,
        "sourceSha256": "a" * 64,
        "providerConfigurationSha256": "b" * 64,
        "files": inventory,
    }
    _write_json(package / "evidence-manifest.json", manifest)
    sums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.iterdir()
    }
    (package / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    for path in package.iterdir():
        if path.is_file():
            path.chmod(0o444)
    descriptor = {
        "benchmarkId": "db-v2",
        "path": package.relative_to(tmp_path).as_posix(),
        "sha256SumsSha256": hashlib.sha256(
            (package / "SHA256SUMS").read_bytes()
        ).hexdigest(),
    }
    errors: list[str] = []

    _validate_benchmarks(tmp_path, [descriptor], errors)

    assert errors == []


def test_visible_runs_cross_check_split_dataset_gate_source_and_hashes(tmp_path: Path) -> None:
    descriptors: dict[str, dict] = {}
    dataset_hashes = {"development": "a" * 64, "regression": "b" * 64}
    for split, dataset_hash in dataset_hashes.items():
        run_id = f"{split}-visible"
        run_root = tmp_path / f"evaluation/.runs/{run_id}"
        run_root.mkdir(parents=True)
        summary = {"runId": run_id, "split": split}
        gates = {"passed": True}
        source = {"source": {"sha256": "c" * 64}}
        run = {
            "schemaVersion": "aishop-evaluation-run/v3",
            "runId": run_id,
            "split": split,
            "datasetSha256": dataset_hash,
            "sourceFingerprint": source,
            "summary": summary,
            "gates": gates,
        }
        payloads = {
            "bad-cases.jsonl": b"",
            "cases.jsonl": b"{}\n",
            "environment.json": b"{}\n",
            "evidence-manifest.json": json.dumps(
                {"schemaVersion": "aishop-evaluation-evidence/v3", "run": run}
            ).encode()
            + b"\n",
            "gates.json": json.dumps(gates).encode() + b"\n",
            "report.md": b"# visible run\n",
            "source-fingerprint.json": json.dumps(source).encode() + b"\n",
            "summary.json": json.dumps(summary).encode() + b"\n",
        }
        for name, content in payloads.items():
            (run_root / name).write_bytes(content)
        (run_root / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256(content).hexdigest()}  {name}\n"
                for name, content in sorted(payloads.items())
            ),
            encoding="utf-8",
        )
        descriptors[split] = {
            "runId": run_id,
            "path": run_root.relative_to(tmp_path).as_posix(),
            "datasetSha256": dataset_hash,
            "sourceSha256": "c" * 64,
            "qualityGatePassed": True,
            "sha256SumsSha256": hashlib.sha256(
                (run_root / "SHA256SUMS").read_bytes()
            ).hexdigest(),
        }

    errors: list[str] = []
    _validate_visible_runs(tmp_path, descriptors, dataset_hashes, errors)
    assert errors == []

    descriptors["regression"]["datasetSha256"] = "d" * 64
    errors = []
    _validate_visible_runs(tmp_path, descriptors, dataset_hashes, errors)
    assert any("dataset hash differs from project manifest" in error for error in errors)
