from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.core.io import atomic_write_json, atomic_write_jsonl, load_json, load_jsonl
from evaluation.customer_service_answer_review import (
    ANSWER_REVIEW_ADJUDICATION_SCHEMA,
    ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
    ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
    ANSWER_REVIEW_REPORT_SCHEMA,
    CustomerServiceAnswerReviewError,
    compare_answer_reviews,
    export_answer_adjudication_template,
    export_answer_review_sheet,
    merge_answer_reviews,
    seal_answer_review_sheet,
    validate_answer_review_sheet,
    verify_answer_review_evidence,
    verify_pending_answer_review_evidence,
    write_answer_review_evidence,
    write_pending_answer_review_evidence,
)
from evaluation.customer_service_http import HTTP_REPORT_SCHEMA


def _http_report(path: Path) -> Path:
    cases = []
    for index in range(1, 4):
        cases.append(
            {
                "caseId": f"case-{index}",
                "message": f"问题 {index}",
                # Gold routing labels and predictions may exist in the source
                # report, but must not be copied into the blind review sheet.
                "expected": {"intent": "CHAT"},
                "rulePrediction": {"intent": "CHAT"},
                "http": {
                    "answer": f"回答 {index} [{index}]",
                    "sourceRefs": [
                        {"citation": index, "factIds": [f"fact-{index}"]}
                    ],
                    "handoffObserved": index == 3,
                    "prediction": {"intent": "CHAT"},
                },
            }
        )
    atomic_write_json(
        path,
        {
            "schemaVersion": HTTP_REPORT_SCHEMA,
            "runId": "customer-http-test",
            "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
            "releaseGateEligible": False,
            "normalQualityDenominatorExcluded": True,
            "cases": cases,
        },
        overwrite=False,
    )
    return path


def _fixture_http_report(path: Path) -> Path:
    report = _http_report(path)
    raw = load_json(report)
    raw["cases"][0]["message"] = "订单 SM202608050002 的物流到哪了"
    raw["cases"][0]["http"]["fixtureEvidence"] = {
        "sourceOrderId": "SM202608050002",
        "orderId": "20220205175455334F51D3ADFEBAC358",
        "scope": "LOCAL_EVALUATION_ONLY",
        "provisioningBoundary": "DIRECT_SQL_FIXTURE_ONLY",
    }
    raw["cases"][0]["http"]["renderedFixtureTemplateFields"] = ["orderId"]
    atomic_write_json(report, raw, overwrite=True)
    return report


def _labels(
    *,
    answer: bool = True,
    citation: str = "SUPPORTED",
    handoff: bool = True,
    unsafe: bool = False,
) -> dict:
    return {
        "answerCorrect": answer,
        "citationSupport": citation,
        "handoffAppropriate": handoff,
        "unsafeAnswer": unsafe,
    }


def _fill_sheet(path: Path, labels_by_id: dict[str, dict]) -> None:
    rows = load_jsonl(path)
    for row in rows:
        row["labels"] = labels_by_id[str(row["caseId"])]
        row["comment"] = f"reviewed {row['caseId']}"
    atomic_write_jsonl(path, rows)


def _rehash_answer_review_evidence(package: Path) -> None:
    """Model a coherent on-disk rewrite so semantic verification is exercised."""

    for path in package.rglob("*"):
        if path.is_file():
            path.chmod(0o644)
    manifest_path = package / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory = {}
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}:
            inventory[path.relative_to(package).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    manifest["files"] = inventory
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = []
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(f"{digest}  {path.relative_to(package).as_posix()}\n")
    (package / "SHA256SUMS").write_text("".join(rows), encoding="utf-8")
    for path in package.rglob("*"):
        if path.is_file():
            path.chmod(0o444)


def _write_adjudicated_evidence(tmp_path: Path) -> Path:
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-2"] = _labels(answer=False, citation="UNSUPPORTED")
    report, sealed_a, sealed_b = _sealed_pair(
        tmp_path, left=left, right=right
    )
    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    adjudication = tmp_path / "adjudication.jsonl"
    export_answer_adjudication_template(agreement, adjudication)
    rows = load_jsonl(adjudication)
    rows[0]["adjudicator"] = "reviewer-c"
    rows[0]["reason"] = "independent review"
    rows[0]["finalLabels"] = _labels(answer=False, citation="UNSUPPORTED")
    atomic_write_jsonl(adjudication, rows)
    final_report, final_agreement = merge_answer_reviews(
        report,
        sealed_a,
        sealed_b,
        adjudication_path=adjudication,
    )
    evidence = tmp_path / "answer-review-evidence"
    write_answer_review_evidence(
        final_report,
        final_agreement,
        review_a_path=sealed_a,
        review_b_path=sealed_b,
        adjudication_path=adjudication,
        output_dir=evidence,
    )
    return evidence


def _sealed_pair(
    tmp_path: Path,
    *,
    left: dict[str, dict] | None = None,
    right: dict[str, dict] | None = None,
    report_path: Path | None = None,
    message_projection: str = ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
) -> tuple[Path, Path, Path]:
    report = report_path or _http_report(tmp_path / "http-report.json")
    default = {f"case-{index}": _labels() for index in range(1, 4)}
    open_a = tmp_path / "reviewer-a.open.jsonl"
    open_b = tmp_path / "reviewer-b.open.jsonl"
    manifest_a = export_answer_review_sheet(
        report,
        open_a,
        reviewer_id="reviewer-a",
        message_projection=message_projection,
    )
    manifest_b = export_answer_review_sheet(
        report,
        open_b,
        reviewer_id="reviewer-b",
        message_projection=message_projection,
    )
    assert manifest_a["orderSeed"] != manifest_b["orderSeed"]
    assert "expected" not in load_jsonl(open_a)[0]
    assert "rulePrediction" not in load_jsonl(open_a)[0]
    _fill_sheet(open_a, left or default)
    _fill_sheet(open_b, right or default)
    sealed_a = tmp_path / "reviewer-a.sealed.jsonl"
    sealed_b = tmp_path / "reviewer-b.sealed.jsonl"
    seal_answer_review_sheet(report, open_a, sealed_a)
    seal_answer_review_sheet(report, open_b, sealed_b)
    return report, sealed_a, sealed_b


def test_answer_review_is_source_bound_and_requires_complete_labels(tmp_path: Path):
    report = _http_report(tmp_path / "http-report.json")
    open_sheet = tmp_path / "review.open.jsonl"
    export_answer_review_sheet(report, open_sheet, reviewer_id="reviewer-a")

    with pytest.raises(CustomerServiceAnswerReviewError, match="incomplete"):
        validate_answer_review_sheet(
            report, open_sheet, require_complete=True
        )

    rows = load_jsonl(open_sheet)
    rows[0]["answer"] = "reviewer changed the model output"
    atomic_write_jsonl(open_sheet, rows)
    with pytest.raises(CustomerServiceAnswerReviewError, match="source field answer"):
        validate_answer_review_sheet(report, open_sheet)


def test_completed_open_answer_review_can_return_in_an_intake_directory(
    tmp_path: Path,
):
    report = _http_report(tmp_path / "http-report.json")
    exported = tmp_path / "delivery" / "reviewer-a.open.jsonl"
    exported.parent.mkdir()
    export_answer_review_sheet(report, exported, reviewer_id="reviewer-a")
    _fill_sheet(
        exported,
        {f"case-{index}": _labels() for index in range(1, 4)},
    )

    returned = tmp_path / "intake" / exported.name
    returned.parent.mkdir()
    returned.write_bytes(exported.read_bytes())
    returned.with_suffix(returned.suffix + ".manifest.json").write_bytes(
        exported.with_suffix(exported.suffix + ".manifest.json").read_bytes()
    )

    manifest = validate_answer_review_sheet(report, returned, require_complete=True)
    assert manifest["lifecycle"] == "OPEN"
    sealed = tmp_path / "reviewer-a.sealed.jsonl"
    seal_answer_review_sheet(report, returned, sealed)
    assert validate_answer_review_sheet(report, sealed, require_complete=True)[
        "lifecycle"
    ] == "SEALED"


def test_answer_review_projects_sensitive_runtime_fields_and_rejects_reviewer_leaks(
    tmp_path: Path,
):
    report = _http_report(tmp_path / "http-report.json")
    action_token = "act_1234567890abcdef1234567890abcdef"
    raw = load_json(report)
    raw["cases"][0]["http"]["answer"] = (
        f'{{"type":"ACTION_CONFIRM","actionToken":"{action_token}"}}'
    )
    raw["cases"][0]["http"]["sourceRefs"][0]["userId"] = "real-user-42"
    atomic_write_json(report, raw, overwrite=True)

    open_sheet = tmp_path / "review.open.jsonl"
    manifest = export_answer_review_sheet(report, open_sheet, reviewer_id="reviewer-a")
    rows = load_jsonl(open_sheet)
    rendered = json.dumps(rows, ensure_ascii=False)

    assert manifest["presentationRedaction"]["projection"] == "REDACTED_REVIEW_SAFE_FIELDS"
    assert action_token not in rendered
    assert "[REDACTED_ACTION_TOKEN]" in rendered
    assert "real-user-42" not in rendered
    assert rows[0]["sourceReportSha256"] == hashlib.sha256(report.read_bytes()).hexdigest()

    _fill_sheet(open_sheet, {f"case-{index}": _labels() for index in range(1, 4)})
    rows = load_jsonl(open_sheet)
    rows[0]["comment"] = f"copied token: {action_token}"
    atomic_write_jsonl(open_sheet, rows)
    with pytest.raises(CustomerServiceAnswerReviewError, match="unredacted sensitive"):
        seal_answer_review_sheet(report, open_sheet, tmp_path / "review.sealed.jsonl")


def test_answer_review_legacy_v2_sheet_without_new_marker_remains_readable(
    tmp_path: Path,
):
    report = _fixture_http_report(tmp_path / "http-report.json")
    open_sheet = tmp_path / "review.open.jsonl"
    manifest = export_answer_review_sheet(report, open_sheet, reviewer_id="reviewer-a")
    assert manifest["messageProjection"] == ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE
    assert next(
        row for row in load_jsonl(open_sheet) if row["caseId"] == "case-1"
    )["message"] == "订单 SM202608050002 的物流到哪了"
    manifest_path = open_sheet.with_suffix(open_sheet.suffix + ".manifest.json")
    manifest = load_json(manifest_path)
    manifest.pop("presentationRedaction")
    manifest.pop("messageProjection")
    atomic_write_json(manifest_path, manifest, overwrite=True)

    assert validate_answer_review_sheet(report, open_sheet)["lifecycle"] == "OPEN"


def test_answer_review_fixture_projection_uses_runtime_question_and_fails_closed(
    tmp_path: Path,
):
    report = _fixture_http_report(tmp_path / "http-report.json")
    open_sheet = tmp_path / "fixture-aware.open.jsonl"
    manifest = export_answer_review_sheet(
        report,
        open_sheet,
        reviewer_id="reviewer-a",
        message_projection=ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
    )

    assert manifest["messageProjection"] == ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    assert next(
        row for row in load_jsonl(open_sheet) if row["caseId"] == "case-1"
    )["message"] == "订单 20220205175455334F51D3ADFEBAC358 的物流到哪了"
    assert validate_answer_review_sheet(report, open_sheet)["lifecycle"] == "OPEN"

    invalid_report = _fixture_http_report(tmp_path / "invalid-fixture-report.json")
    invalid = load_json(invalid_report)
    invalid["cases"][0]["http"].pop("fixtureEvidence")
    atomic_write_json(invalid_report, invalid, overwrite=True)
    with pytest.raises(CustomerServiceAnswerReviewError, match="requires fixture evidence"):
        export_answer_review_sheet(
            invalid_report,
            tmp_path / "invalid-fixture.open.jsonl",
            reviewer_id="reviewer-a",
            message_projection=ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
        )


def test_fixture_aware_answer_reviews_are_consistent_across_evidence_lifecycle(
    tmp_path: Path,
):
    report = _fixture_http_report(tmp_path / "http-report.json")
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-1"] = _labels(answer=False, citation="UNSUPPORTED")
    _report, sealed_a, sealed_b = _sealed_pair(
        tmp_path,
        report_path=report,
        left=left,
        right=right,
        message_projection=ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
    )
    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    assert agreement["messageProjection"] == ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE

    pending = tmp_path / "fixture-aware-pending"
    assert write_pending_answer_review_evidence(
        report,
        agreement,
        review_a_path=sealed_a,
        review_b_path=sealed_b,
        output_dir=pending,
    )["verified"] is True
    assert verify_pending_answer_review_evidence(pending)["caseCount"] == 3
    assert load_json(pending / "evidence-manifest.json")["messageProjection"] == (
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    )
    assert load_json(pending / "lifecycle.json")["messageProjection"] == (
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    )

    adjudication = tmp_path / "fixture-aware-adjudication.jsonl"
    export_answer_adjudication_template(agreement, adjudication)
    rows = load_jsonl(adjudication)
    rows[0]["adjudicator"] = "reviewer-c"
    rows[0]["reason"] = "independent review"
    rows[0]["finalLabels"] = _labels(answer=False, citation="UNSUPPORTED")
    atomic_write_jsonl(adjudication, rows)
    final_report, final_agreement = merge_answer_reviews(
        report,
        sealed_a,
        sealed_b,
        adjudication_path=adjudication,
    )
    assert final_report["reviewEvidence"]["messageProjection"] == (
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    )
    evidence = tmp_path / "fixture-aware-evidence"
    assert write_answer_review_evidence(
        final_report,
        final_agreement,
        review_a_path=sealed_a,
        review_b_path=sealed_b,
        adjudication_path=adjudication,
        output_dir=evidence,
    )["verified"] is True
    assert verify_answer_review_evidence(evidence)["caseCount"] == 3
    assert load_json(evidence / "evidence-manifest.json")["messageProjection"] == (
        ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE
    )


def test_answer_review_rejects_mixed_message_projections(tmp_path: Path):
    report = _fixture_http_report(tmp_path / "http-report.json")
    labels = {f"case-{index}": _labels() for index in range(1, 4)}
    _report, sealed_fixture, _sealed_fixture_b = _sealed_pair(
        tmp_path,
        report_path=report,
        left=labels,
        right=labels,
        message_projection=ANSWER_REVIEW_MESSAGE_PROJECTION_RUNTIME_FIXTURE,
    )
    open_source = tmp_path / "source.open.jsonl"
    export_answer_review_sheet(
        report,
        open_source,
        reviewer_id="reviewer-source",
        message_projection=ANSWER_REVIEW_MESSAGE_PROJECTION_SOURCE,
    )
    _fill_sheet(open_source, labels)
    sealed_source = tmp_path / "source.sealed.jsonl"
    seal_answer_review_sheet(report, open_source, sealed_source)

    with pytest.raises(CustomerServiceAnswerReviewError, match="different message projections"):
        compare_answer_reviews(report, sealed_fixture, sealed_source)


def test_answer_review_agreement_and_adjudicated_quality_metrics(tmp_path: Path):
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-2"] = _labels(answer=False, citation="UNSUPPORTED")
    right["case-3"] = _labels(unsafe=True)
    report, sealed_a, sealed_b = _sealed_pair(
        tmp_path, left=left, right=right
    )

    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    assert agreement["caseAgreementRate"] == pytest.approx(1 / 3, abs=1e-6)
    assert agreement["disagreementCaseCount"] == 2
    assert agreement["fieldStats"]["answerCorrect"]["cohenKappa"] is not None

    adjudication = tmp_path / "adjudication.open.jsonl"
    export_answer_adjudication_template(agreement, adjudication)
    rows = load_jsonl(adjudication)
    for row in rows:
        row["adjudicator"] = "reviewer-c"
        row["reason"] = "按冻结指南复核答案和证据"
        row["finalLabels"] = (
            _labels(answer=False, citation="UNSUPPORTED")
            if row["caseId"] == "case-2"
            else _labels()
        )
    atomic_write_jsonl(adjudication, rows)

    final_report, final_agreement = merge_answer_reviews(
        report,
        sealed_a,
        sealed_b,
        adjudication_path=adjudication,
    )
    assert final_report["schemaVersion"] == ANSWER_REVIEW_REPORT_SCHEMA
    assert final_report["metrics"]["answerCorrectness"]["value"] == pytest.approx(
        2 / 3, abs=1e-6
    )
    assert final_report["metrics"]["citationGroundingSupport"]["value"] == pytest.approx(
        2 / 3, abs=1e-6
    )
    assert final_report["metrics"]["handoffAppropriateness"]["value"] == 1.0
    assert final_report["metrics"]["unsafeAnswerRate"]["value"] == 0.0
    assert final_report["metrics"]["unsafeAnswerRate"]["lowerIsBetter"] is True
    assert final_report["metrics"]["jointQualityPassRate"]["badcaseIds"] == [
        "case-2"
    ]
    assert "frozen 3-case HTTP replay only" in final_report["limitations"][0]
    assert final_report["normalQualityDenominatorExcluded"] is True
    assert final_report["sourceEvaluation"] == {
        "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
        "releaseGateEligible": False,
        "normalQualityDenominatorExcluded": True,
    }
    assert any(
        "excluded from the normal quality denominator" in limitation
        for limitation in final_report["limitations"]
    )

    evidence = tmp_path / "answer-review-evidence"
    verification = write_answer_review_evidence(
        final_report,
        final_agreement,
        review_a_path=sealed_a,
        review_b_path=sealed_b,
        adjudication_path=adjudication,
        output_dir=evidence,
    )
    assert verification["verified"] is True
    assert verify_answer_review_evidence(evidence)["caseCount"] == 3
    evidence_manifest = load_json(evidence / "evidence-manifest.json")
    assert evidence_manifest["normalQualityDenominatorExcluded"] is True
    assert (
        evidence_manifest["sourceEvaluationStatus"]
        == "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW"
    )
    assert all(
        not path.stat().st_mode & 0o222
        for path in evidence.rglob("*")
        if path.is_file()
    )
    with pytest.raises(FileExistsError):
        write_answer_review_evidence(
            final_report,
            final_agreement,
            review_a_path=sealed_a,
            review_b_path=sealed_b,
            adjudication_path=adjudication,
            output_dir=evidence,
        )


def test_answer_review_merge_fails_closed_without_independent_adjudication(
    tmp_path: Path,
):
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-2"] = _labels(answer=False)
    report, sealed_a, sealed_b = _sealed_pair(
        tmp_path, left=left, right=right
    )
    with pytest.raises(CustomerServiceAnswerReviewError, match="require"):
        merge_answer_reviews(report, sealed_a, sealed_b)

    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    adjudication = tmp_path / "adjudication.jsonl"
    export_answer_adjudication_template(agreement, adjudication)
    rows = load_jsonl(adjudication)
    assert rows[0]["schemaVersion"] == ANSWER_REVIEW_ADJUDICATION_SCHEMA
    rows[0]["adjudicator"] = "reviewer-a"
    rows[0]["reason"] = "not independent"
    rows[0]["finalLabels"] = _labels()
    atomic_write_jsonl(adjudication, rows)
    with pytest.raises(CustomerServiceAnswerReviewError, match="independent"):
        merge_answer_reviews(
            report,
            sealed_a,
            sealed_b,
            adjudication_path=adjudication,
        )


def test_answer_review_adjudication_rejects_sensitive_reason(tmp_path: Path):
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-2"] = _labels(answer=False, citation="UNSUPPORTED")
    report, sealed_a, sealed_b = _sealed_pair(tmp_path, left=left, right=right)
    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    adjudication = tmp_path / "adjudication.open.jsonl"
    export_answer_adjudication_template(agreement, adjudication)
    rows = load_jsonl(adjudication)
    rows[0]["adjudicator"] = "reviewer-c"
    rows[0]["reason"] = "copied act_1234567890abcdef1234567890abcdef"
    rows[0]["finalLabels"] = _labels(answer=False, citation="UNSUPPORTED")
    atomic_write_jsonl(adjudication, rows)

    with pytest.raises(CustomerServiceAnswerReviewError, match="unredacted sensitive"):
        merge_answer_reviews(
            report,
            sealed_a,
            sealed_b,
            adjudication_path=adjudication,
        )


def test_pending_answer_review_evidence_freezes_dual_reviews_and_exports_input(
    tmp_path: Path,
):
    left = {f"case-{index}": _labels() for index in range(1, 4)}
    right = {f"case-{index}": _labels() for index in range(1, 4)}
    right["case-2"] = _labels(answer=False, citation="UNSUPPORTED")
    report, sealed_a, sealed_b = _sealed_pair(
        tmp_path, left=left, right=right
    )
    agreement = compare_answer_reviews(report, sealed_a, sealed_b)
    package = tmp_path / "pending-answer-review"
    editable = tmp_path / "adjudication.answer-review-v2.open.jsonl"

    verification = write_pending_answer_review_evidence(
        report,
        agreement,
        review_a_path=sealed_a,
        review_b_path=sealed_b,
        output_dir=package,
        adjudication_output=editable,
    )

    assert verification["verified"] is True
    assert verification["disagreementCaseCount"] == 1
    assert verification["editableAdjudication"]["path"].endswith(
        "adjudication.answer-review-v2.open.jsonl"
    )
    assert verify_pending_answer_review_evidence(package)["caseCount"] == 3
    assert len(load_jsonl(editable)) == 1
    adjudication_needed = (package / "adjudication-needed.md").read_text()
    assert "adjudication.answer-review-v2.open.jsonl" in adjudication_needed
    assert not adjudication_needed.endswith("\n\n")
    frozen_agreement = load_json(package / "agreement.json")
    assert frozen_agreement["reviewA"]["path"] == "reviews/reviewer-a.sealed.jsonl"
    assert frozen_agreement["reviewB"]["path"] == "reviews/reviewer-b.sealed.jsonl"
    assert all(
        not path.stat().st_mode & 0o222
        for path in package.rglob("*")
        if path.is_file()
    )
    with pytest.raises(FileExistsError):
        write_pending_answer_review_evidence(
            report,
            agreement,
            review_a_path=sealed_a,
            review_b_path=sealed_b,
            output_dir=package,
        )


def test_answer_review_agreement_needs_no_adjudication_file(tmp_path: Path):
    report, sealed_a, sealed_b = _sealed_pair(tmp_path)
    final_report, agreement = merge_answer_reviews(report, sealed_a, sealed_b)
    assert agreement["status"] == "AGREED_NO_ADJUDICATION"
    assert final_report["agreement"]["disagreementCaseCount"] == 0
    assert final_report["metrics"]["jointQualityPassRate"]["value"] == 1.0


def test_answer_review_evidence_rejects_rehashed_metric_tampering(tmp_path: Path):
    evidence = _write_adjudicated_evidence(tmp_path)
    report_path = evidence / "final-report.json"
    report = load_json(report_path)
    report["metrics"]["answerCorrectness"]["value"] = 1.0
    report_path.chmod(0o644)
    atomic_write_json(report_path, report, overwrite=True)
    _rehash_answer_review_evidence(evidence)

    with pytest.raises(
        CustomerServiceAnswerReviewError,
        match="final metrics or badcases",
    ):
        verify_answer_review_evidence(evidence)


def test_answer_review_evidence_rejects_rehashed_adjudication_tampering(
    tmp_path: Path,
):
    evidence = _write_adjudicated_evidence(tmp_path)
    adjudication_path = evidence / "reviews" / "adjudication.final.jsonl"
    rows = load_jsonl(adjudication_path)
    rows[0]["sourceRefs"] = [{"citation": "tampered"}]
    adjudication_path.chmod(0o644)
    atomic_write_jsonl(adjudication_path, rows, overwrite=True)
    _rehash_answer_review_evidence(evidence)

    with pytest.raises(
        CustomerServiceAnswerReviewError,
        match="adjudication source field sourceRefs was modified",
    ):
        verify_answer_review_evidence(evidence)
