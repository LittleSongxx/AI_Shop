import hashlib
import json
from copy import deepcopy
from pathlib import Path

from check_evidence_manifest import (
    LOCK_SCHEMA,
    _canonical_sha256,
    _parse_sums,
    _validate_auxiliary_evidence,
    _validate_benchmarks,
    _validate_current_customer_service_v2,
    _validate_customer_service_answer_review,
    _validate_customer_service_v54_remediation,
    _validate_customer_service_v56_regression,
    _validate_diagnostic_evidence,
    _validate_evaluation_archive_and_handoff,
    _validate_failed_final_attempts,
    _validate_lock,
    _validate_pricing_estimate,
    _validate_suite,
    _validate_targeted_customer_service_answer_review,
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


def test_non_object_json_is_reported_without_crashing(tmp_path: Path) -> None:
    suite_path = tmp_path / "evaluation/suite.json"
    suite_path.parent.mkdir(parents=True)
    suite_path.write_text("[]\n", encoding="utf-8")
    descriptor = {
        "path": suite_path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
    }
    errors: list[str] = []

    result = _validate_suite(tmp_path, descriptor, errors)

    assert result == {}
    assert any("invalid evaluation suite" in error for error in errors)


def test_repository_accepts_published_final_when_required() -> None:
    assert not any("published final evidence package is required" in error for error in validate_repository())
    assert not any(
        "published final evidence package is required" in error
        for error in validate_repository(require_current=True)
    )


def test_pricing_estimate_requires_provenance_and_non_gating_status(tmp_path: Path) -> None:
    quote = {
        "schemaVersion": "aishop-list-price-estimate/v1",
        "status": "ESTIMATED_LIST_PRICE",
        "sourceUrl": "https://example.test/pricing",
        "retrievedAt": "2026-08-24T00:00:00+08:00",
        "sourceContentSha256": "a" * 64,
        "provider": "example",
        "region": "cn-beijing",
        "modelId": "model-a",
        "modelFingerprint": "model-a-1",
        "inputPriceCnyPerMillion": 2.0,
        "outputPriceCnyPerMillion": 8.0,
        "inputTokenUpperBound": 256000,
        "priceBasis": "ORIGINAL_PUBLIC_CATALOGUE_PRICE",
        "usableForBudgetGate": False,
        "billingContractVerified": False,
        "notes": [],
    }
    quote["quoteSha256"] = _canonical_sha256(quote)
    path = tmp_path / "docs/evaluation/pricing.json"
    _write_json(path, quote)
    descriptor = {
        "path": path.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    errors: list[str] = []
    _validate_pricing_estimate(tmp_path, descriptor, errors)
    assert errors == []

    quote["usableForBudgetGate"] = True
    _write_json(path, quote)
    descriptor["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    errors = []
    _validate_pricing_estimate(tmp_path, descriptor, errors)
    assert any("status or budget boundary" in error for error in errors)


def test_current_customer_service_report_is_human_verified_but_release_fail_closed() -> None:
    manifest = json.loads((Path(__file__).parents[1] / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = manifest["evaluation"]["customerServiceGold"]
    report_path = Path(__file__).parents[1] / descriptor["reportPath"]
    report = json.loads(report_path.read_text("utf-8"))

    assert descriptor["status"] == "HUMAN_VERIFIED"
    assert descriptor["releaseGateEligible"] is False
    assert report["status"] == "HUMAN_VERIFIED"
    assert report["humanReviewPlan"]["status"] == "COMPLETE"
    assert report["humanReviewPlan"]["requiredAnnotators"] == 2
    assert report["humanReviewPlan"]["blindedFirstPass"] is True
    assert report["humanReviewPlan"]["adjudicationComplete"] is True
    assert "customerServiceGoldDraft" not in manifest["evaluation"]
    assert "historicalDraft" not in descriptor


def test_current_v2_and_v43_evidence_remain_fail_closed_and_hash_bound() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    evaluation = deepcopy(manifest["evaluation"])
    errors: list[str] = []

    _validate_current_customer_service_v2(root, evaluation, errors)

    assert errors == []
    assert evaluation["customerServiceV2"]["releaseGateEligible"] is False
    assert evaluation["customerServiceV2LabelAudit"]["affectedCaseCount"] == 25
    assert evaluation["customerServiceHttpV43"]["humanAnswerReviewCount"] == 0

    evaluation["customerServiceHttpV43"]["humanAnswerReviewCount"] = 120
    errors = []
    _validate_current_customer_service_v2(root, evaluation, errors)

    assert any("execution, contracts, validity, or answer gate" in error for error in errors)


def test_v54_human_adjudication_and_final_metrics_are_hash_bound() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    evaluation = deepcopy(manifest["evaluation"])
    errors: list[str] = []

    _validate_customer_service_v54_remediation(root, evaluation, errors)

    assert errors == []
    final = evaluation["customerServiceBadcaseRemediationV54"]["finalAnswerQuality"]
    assert final["jointQualityPassedCount"] == 113
    assert final["badcaseCount"] == 7
    assert final["releaseGateEligible"] is False
    assert final["finalUnseenEligible"] is False

    final["jointQualityPassedCount"] = 114
    errors = []
    _validate_customer_service_v54_remediation(root, evaluation, errors)

    assert any("finalAnswerQuality metrics or provenance" in error for error in errors)


def test_v56_execution_and_final_human_review_are_hash_bound() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    evaluation = deepcopy(manifest["evaluation"])
    errors: list[str] = []

    _validate_customer_service_v56_regression(root, evaluation, errors)

    assert errors == []
    descriptor = evaluation["customerServiceV3KnowledgeRegressionV56"]
    assert descriptor["fullExecution"]["executionPassedCount"] == 120
    assert descriptor["fullExecution"]["behaviorContractPassedCount"] == 29
    assert descriptor["fullExecution"]["humanAnswerReviewCount"] == 0
    assert descriptor["humanReviewHandoff"]["exactAgreementCaseCount"] == 118
    assert descriptor["humanReviewHandoff"]["disagreementCaseCount"] == 2
    assert descriptor["humanReviewHandoff"]["adjudicationSendNow"] is False
    assert descriptor["finalAnswerQuality"]["jointQualityPassedCount"] == 120
    assert descriptor["finalAnswerQuality"]["badcaseCount"] == 0

    descriptor["finalAnswerQuality"]["jointQualityPassedCount"] = 119
    errors = []
    _validate_customer_service_v56_regression(root, evaluation, errors)

    assert any("final metrics or human provenance" in error for error in errors)


def test_archive_catalog_recovered_intake_and_open_handoff_are_hash_bound() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    evaluation = deepcopy(manifest["evaluation"])
    errors: list[str] = []

    _validate_evaluation_archive_and_handoff(root, evaluation, errors)

    assert errors == []
    assert evaluation["evaluationAssetCatalog"]["rootLooseJsonOrJsonlCount"] == 0
    assert evaluation["customerServiceRecoveredReviewIntake"][
        "reviewerIndependenceVerified"
    ] is False
    assert evaluation["humanReviewHandoff"]["completedHumanReviewCount"] == 0

    evaluation["humanReviewHandoff"]["completedHumanReviewCount"] = 1
    errors = []
    _validate_evaluation_archive_and_handoff(root, evaluation, errors)

    assert any("lifecycle or task binding" in error for error in errors)


def test_adjudicated_http_answer_review_requires_its_frozen_parent_and_metrics() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReview"])

    errors: list[str] = []
    _validate_customer_service_answer_review(root, descriptor, errors)
    assert errors == []

    descriptor["pendingEvidence"]["disagreementCaseCount"] = 7
    errors = []
    _validate_customer_service_answer_review(root, descriptor, errors)

    assert any("agreement summary differs" in error for error in errors)

    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReview"])
    descriptor["finalEvidence"]["adjudicationCaseCount"] = 7
    errors = []
    _validate_customer_service_answer_review(root, descriptor, errors)

    assert any("adjudication coverage/hash is invalid" in error for error in errors)

    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReview"])
    descriptor["finalEvidence"]["metrics"]["citationGroundingSupport"][
        "numerator"
    ] = 7
    errors = []
    _validate_customer_service_answer_review(root, descriptor, errors)

    assert any("metric is invalid: citationGroundingSupport" in error for error in errors)


def test_pending_v13_answer_review_binds_raw_observation_and_open_sheets() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReviewV13"])

    errors: list[str] = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV13",
        require_pre_evaluator_observation=True,
    )
    assert errors == []

    descriptor["preEvaluatorFixEvidence"]["sourceObservationReportSha256"] = "0" * 64
    errors = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV13",
        require_pre_evaluator_observation=True,
    )

    assert any("source observation is invalid" in error for error in errors)


def test_v20_answer_review_binds_fixture_projection_metrics_and_reviewers() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReviewV20"])

    errors: list[str] = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV20",
    )

    assert errors == []
    assert not any("immutable source field differs" in error for error in errors)

    descriptor["reviewerIds"] = ["reviewer-a", "reviewer-b"]
    errors = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV20",
    )
    assert any("reviewer IDs are invalid" in error for error in errors)

    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReviewV20"])
    descriptor["finalEvidence"]["metrics"]["answerCorrectness"]["numerator"] = 58
    errors = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV20",
    )
    assert any("metric is invalid: answerCorrectness" in error for error in errors)


def test_v25_targeted_answer_review_binds_agreement_without_adjudication() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(
        manifest["evaluation"]["customerServiceAnswerReviewV25Targeted"]
    )

    errors: list[str] = []
    _validate_targeted_customer_service_answer_review(root, descriptor, errors)

    assert errors == []
    assert descriptor["agreementStatus"] == "AGREED_NO_ADJUDICATION"
    assert descriptor["finalEvidence"]["adjudicationCaseCount"] == 0
    assert descriptor["normalQualityDenominatorExcluded"] is True

    descriptor["finalEvidence"]["disagreementCaseCount"] = 1
    errors = []
    _validate_targeted_customer_service_answer_review(root, descriptor, errors)
    assert any("no-adjudication agreement summary is invalid" in error for error in errors)

    descriptor = deepcopy(
        manifest["evaluation"]["customerServiceAnswerReviewV25Targeted"]
    )
    descriptor["finalEvidence"]["metrics"]["answerCorrectness"]["numerator"] = 9
    errors = []
    _validate_targeted_customer_service_answer_review(root, descriptor, errors)
    assert any("final metrics or badcases are invalid" in error for error in errors)


def test_v27_answer_review_binds_adjudication_and_current_metrics() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(manifest["evaluation"]["customerServiceAnswerReviewV27"])

    errors: list[str] = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV27",
    )

    assert errors == []
    assert descriptor["releaseGateEligible"] is False
    assert descriptor["pendingEvidence"]["exactAgreementCaseCount"] == 58
    assert descriptor["finalEvidence"]["metrics"]["answerCorrectness"]["numerator"] == 59
    assert descriptor["finalEvidence"]["metrics"]["citationGroundingSupport"]["denominator"] == 36

    descriptor["finalEvidence"]["metrics"]["answerCorrectness"]["numerator"] = 58
    errors = []
    _validate_customer_service_answer_review(
        root,
        descriptor,
        errors,
        label="evaluation.customerServiceAnswerReviewV27",
    )
    assert any("metric is invalid: answerCorrectness" in error for error in errors)


def test_live_v14_http_diagnostic_preserves_its_execution_boundary() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptor = deepcopy(
        next(
            item
            for item in manifest["evaluation"]["diagnosticEvidence"]
            if item.get("packageId") == "customer-service-http-v14-20260825"
        )
    )

    errors: list[str] = []
    _validate_diagnostic_evidence(root, [descriptor], errors)

    assert errors == []

    descriptor["behaviorContractViolationCount"] = 0
    errors = []
    _validate_diagnostic_evidence(root, [descriptor], errors)
    assert any("live behavior-contract evidence is invalid" in error for error in errors)


def test_failed_final_attempt_is_hashed_and_failed(tmp_path: Path) -> None:
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


def test_auxiliary_evidence_requires_typed_hashed_package(tmp_path: Path) -> None:
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


def _diagnostic_fixture(
    root: Path,
    *,
    kind: str,
    slot_status: str = "UNAVAILABLE",
    qrels_modified: bool = False,
) -> dict:
    variant = ""
    if slot_status != "UNAVAILABLE":
        variant = f"-{slot_status.lower()}"
    if qrels_modified:
        variant = "-qrels-modified"
    package_id = f"{kind}-contract{variant}"
    package = root / f"evidence/benchmarks/{kind}/{package_id}"
    package.mkdir(parents=True)
    run_id = f"{kind}-run"
    if kind == "customer-service-http":
        source_observation_sha = "a" * 64
        report = {
            "schemaVersion": "aishop-customer-service-http-evaluation/v1",
            "runId": run_id,
            "releaseGateEligible": False,
            "normalQualityDenominatorExcluded": True,
            "httpRoute": {
                "metrics": {
                    "slotEntitySpanF1": {"status": slot_status},
                    "slotExactMatch": {"status": slot_status},
                }
            },
            "observationProvenance": {
                "mode": "OFFLINE_REBUILD_FROM_PRESERVED_OBSERVATIONS",
                "providerCallsReexecuted": False,
                "sourceReportSha256": source_observation_sha,
            },
            "answerQuality": {
                "status": "PENDING_HUMAN_REVIEW",
                "selfJudged": False,
                "answerCorrectness": None,
                "citationGroundingSupport": None,
                "unsafeAnswerRate": None,
            },
        }
        package_manifest = {
            "schemaVersion": "aishop-customer-service-http-evidence/v1",
            "kind": kind,
            "packageId": package_id,
            "runId": run_id,
            "releaseGateEligible": False,
            "providerCallsReexecuted": False,
            "sourceObservationReportSha256": source_observation_sha,
        }
        payloads = {
            "badcases.jsonl": b"",
            "report.json": json.dumps(report).encode() + b"\n",
            "report.md": b"# HTTP diagnostic\n",
        }
    elif kind == "search-paired-replay":
        baseline_run_id = "final-baseline"
        baseline_evidence_sha = "b" * 64
        qrels_sha = "c" * 64
        report = {
            "schemaVersion": "aishop-search-paired-replay/v1",
            "runId": run_id,
            "normalQualityDenominatorExcluded": True,
            "baselineFinalModified": False,
            "qrelsModified": qrels_modified,
            "provenance": {
                "baselineRunId": baseline_run_id,
                "baselineEvidenceSha256SumsSha256": baseline_evidence_sha,
                "selectedQrelsSha256": qrels_sha,
            },
        }
        package_manifest = {
            "schemaVersion": "aishop-search-paired-replay-evidence/v1",
            "kind": kind,
            "packageId": package_id,
            "runId": run_id,
            "baselineRunId": baseline_run_id,
            "baselineEvidenceSha256SumsSha256": baseline_evidence_sha,
            "selectedQrelsSha256": qrels_sha,
            "normalQualityDenominatorExcluded": True,
            "baselineFinalModified": False,
            "qrelsModified": qrels_modified,
        }
        payloads = {
            "badcases.jsonl": b"",
            "cases.jsonl": b"{}\n",
            "report.json": json.dumps(report).encode() + b"\n",
            "report.md": b"# Search paired replay\n",
        }
    elif kind == "customer-service-slot-replay":
        dataset_sha = "d" * 64
        baseline_sha = "e" * 64
        report = {
            "schemaVersion": "aishop-customer-service-slot-replay/v1",
            "runId": run_id,
            "dataset": {
                "annotationStatus": "HUMAN_VERIFIED",
                "caseCount": 2,
                "sha256": dataset_sha,
            },
            "metrics": {},
            "pairedCaseCounts": {"FIXED": 1, "UNCHANGED_PASS": 1},
            "normalQualityDenominatorExcluded": True,
        }
        package_manifest = {
            "schemaVersion": "aishop-customer-service-slot-replay-evidence/v1",
            "packageId": package_id,
            "runId": run_id,
            "datasetSha256": dataset_sha,
            "baselineReportSha256": baseline_sha,
            "normalQualityDenominatorExcluded": True,
        }
        payloads = {
            "paired-cases.jsonl": b"{}\n{}\n",
            "report.json": json.dumps(report).encode() + b"\n",
            "report.md": b"# Slot replay\n",
        }
    elif kind == "capacity-benchmark":
        dataset_sha = "f" * 64
        observation = {
            "caseId": "case-1",
            "answer": {"sha256": "a" * 64, "chars": 2, "rawStored": False},
        }
        report = {
            "schemaVersion": "aishop-capacity-benchmark/v1",
            "runId": run_id,
            "dataset": {
                "annotationStatus": "HUMAN_VERIFIED",
                "caseCount": 1,
                "sha256": dataset_sha,
            },
            "configuration": {"concurrencies": [1], "requestsPerLevel": 1},
            "levels": {
                "1": {
                    "concurrency": 1,
                    "requestedCount": 1,
                    "completedCount": 1,
                    "usage": {"costStatus": "NOT_APPLICABLE"},
                }
            },
            "notProductionSlo": True,
            "normalQualityDenominatorExcluded": True,
        }
        package_manifest = {
            "schemaVersion": "aishop-capacity-benchmark-evidence/v1",
            "benchmarkId": package_id,
            "runId": run_id,
            "datasetSha256": dataset_sha,
            "notProductionSlo": True,
            "normalQualityDenominatorExcluded": True,
            "preflightPassed": True,
        }
        payloads = {
            "observations.jsonl": json.dumps(observation).encode() + b"\n",
            "report.json": json.dumps(report).encode() + b"\n",
            "report.md": b"# Capacity benchmark\n",
        }
    else:
        raise AssertionError(f"unsupported diagnostic kind: {kind}")
    for name, content in payloads.items():
        (package / name).write_bytes(content)
    package_manifest["files"] = {
        name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        for name, content in payloads.items()
    }
    _write_json(package / "evidence-manifest.json", package_manifest)
    sums = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.iterdir()
        if path.is_file()
    }
    (package / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())),
        encoding="utf-8",
    )
    descriptor = {
        "kind": kind,
        "packageId": package_id,
        "path": package.relative_to(root).as_posix(),
        "runId": run_id,
        "sha256SumsSha256": hashlib.sha256(
            (package / "SHA256SUMS").read_bytes()
        ).hexdigest(),
    }
    if kind == "search-paired-replay":
        descriptor["baselineRunId"] = "final-baseline"
    if kind == "customer-service-slot-replay":
        descriptor["baselineReportSha256"] = "e" * 64
    if kind == "capacity-benchmark":
        descriptor["resultRole"] = "CURRENT"
    return descriptor


def test_http_diagnostic_requires_unavailable_redacted_slots(tmp_path: Path) -> None:
    valid = _diagnostic_fixture(tmp_path, kind="customer-service-http")
    errors: list[str] = []

    _validate_diagnostic_evidence(tmp_path, [valid], errors)

    assert errors == []

    invalid = _diagnostic_fixture(
        tmp_path,
        kind="customer-service-http",
        slot_status="MEASURED",
    )
    errors = []
    _validate_diagnostic_evidence(tmp_path, [invalid], errors)
    assert any("HTTP slot metric must remain unavailable" in error for error in errors)


def test_runtime_version_diagnostic_pair_preserves_stale_baseline_and_recovery() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "docs/evidence-manifest.json").read_text("utf-8"))
    descriptors = [
        deepcopy(descriptor)
        for descriptor in manifest["evaluation"]["diagnosticEvidence"]
        if str(descriptor.get("resultRole") or "").startswith("RUNTIME_VERSION_")
    ]

    assert len(descriptors) == 2
    errors: list[str] = []
    _validate_diagnostic_evidence(root, descriptors, errors)
    assert errors == []

    stale = next(
        descriptor
        for descriptor in descriptors
        if descriptor["resultRole"] == "RUNTIME_VERSION_STALE_BASELINE"
    )
    stale["expectedBehaviorContractViolationCount"] = 0
    errors = []
    _validate_diagnostic_evidence(root, descriptors, errors)
    assert any("expected behavior-contract count is invalid" in error for error in errors)


def test_search_paired_replay_binds_baseline_and_preserves_qrels(tmp_path: Path) -> None:
    valid = _diagnostic_fixture(tmp_path, kind="search-paired-replay")
    errors: list[str] = []

    _validate_diagnostic_evidence(tmp_path, [valid], errors)

    assert errors == []
    valid["baselineRunId"] = "different-final"
    errors = []
    _validate_diagnostic_evidence(tmp_path, [valid], errors)
    assert any("baseline run binding is invalid" in error for error in errors)
    assert any("paired replay provenance is invalid" in error for error in errors)


def test_search_paired_replay_rejects_qrels_mutation(tmp_path: Path) -> None:
    descriptor = _diagnostic_fixture(
        tmp_path,
        kind="search-paired-replay",
        qrels_modified=True,
    )
    errors: list[str] = []

    _validate_diagnostic_evidence(tmp_path, [descriptor], errors)

    assert any("paired replay boundary is invalid" in error for error in errors)


def test_slot_replay_binds_human_dataset_and_baseline(tmp_path: Path) -> None:
    descriptor = _diagnostic_fixture(
        tmp_path,
        kind="customer-service-slot-replay",
    )
    errors: list[str] = []

    _validate_diagnostic_evidence(tmp_path, [descriptor], errors)

    assert errors == []
    descriptor["baselineReportSha256"] = "0" * 64
    errors = []
    _validate_diagnostic_evidence(tmp_path, [descriptor], errors)
    assert any("slot replay baseline binding is invalid" in error for error in errors)


def test_capacity_diagnostic_requires_redacted_answers_and_explicit_role(
    tmp_path: Path,
) -> None:
    descriptor = _diagnostic_fixture(tmp_path, kind="capacity-benchmark")
    errors: list[str] = []

    _validate_diagnostic_evidence(tmp_path, [descriptor], errors)

    assert errors == []
    descriptor["resultRole"] = "PRODUCTION_SLO"
    errors = []
    _validate_diagnostic_evidence(tmp_path, [descriptor], errors)
    assert any("capacity result role is invalid" in error for error in errors)


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
