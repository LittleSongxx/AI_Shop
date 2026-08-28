from __future__ import annotations

import random
import secrets
import shutil
import uuid
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.text2sql.comparison import _load_evidence
from evaluation.text2sql.dataset import DEFAULT_CATALOG, load_cases, verify_human_gold
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
    write_sha256s,
)

ANSWER_REVIEW_SCHEMA = "aishop-text2sql-answer-review/v0"
ANSWER_REVIEW_FILE = "answer-review.open.jsonl"
ANSWER_REVIEW_SEALED_FILE = "answer-review.sealed.jsonl"
_ALLOWED_DECISIONS = {"ACCEPT", "REJECT"}
_ALLOWED_ISSUE_CODES = {
    "WRONG_OUTCOME",
    "WRONG_COMPLETION",
    "WRONG_RESULT",
    "MISSING_REQUIRED_FACT",
    "FORBIDDEN_CLAIM",
    "UNSAFE_DATA_EXPOSURE",
    "MISLEADING_BOUNDARY",
    "CLARIFICATION_DEFECT",
    "FLOW_CONTRACT_DEFECT",
    "INFRASTRUCTURE_FAILURE",
    "OTHER",
}
_LABEL_KEYS = {"decision", "issueCodes", "notes", "reviewerId", "reviewedAt"}


def _blank_label() -> dict[str, Any]:
    return {
        "decision": "",
        "issueCodes": [],
        "notes": "",
        "reviewerId": "",
        "reviewedAt": "",
    }


def _gold_projection(case: Any) -> dict[str, Any]:
    expected = case.expected
    return {
        "expectedOutcome": expected.outcome.value,
        "expectedCompletion": expected.completion.value,
        "reasonCode": expected.reason_code,
        "semanticPlan": [
            {
                "branchId": branch.branch_id,
                "purpose": branch.purpose,
                "semanticView": branch.semantic_view,
                "metrics": branch.metrics,
                "dimensions": branch.dimensions,
                "startDate": branch.start_date,
                "endDate": branch.end_date,
            }
            for branch in expected.branches
        ],
        "referenceResults": [
            {
                "branchId": branch.branch_id,
                "columns": oracle.columns,
                "columnTypes": oracle.column_types,
                "rows": oracle.rows,
                "orderSensitive": oracle.order_sensitive,
            }
            for branch, oracle in zip(
                expected.branches, expected.branch_result_oracles, strict=False
            )
        ],
        "expectedFailedBranchIds": expected.expected_failed_branch_ids,
        "clarificationQuestion": expected.clarification_question,
        "clarificationOptions": [
            {
                "label": option.label,
                "answerSuffix": option.answer_suffix,
            }
            for option in expected.clarification_options
        ],
        "requiredFacts": expected.required_facts,
        "forbiddenClaims": expected.forbidden_claims,
    }


def _option_projection(options: Any) -> list[dict[str, Any]]:
    if not isinstance(options, list):
        return []
    return [
        {
            "label": str(option.get("label") or ""),
            "answerSuffix": str(option.get("answerSuffix") or option.get("answer_suffix") or ""),
        }
        for option in options
        if isinstance(option, dict)
    ]


def _branch_projection(branch: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(branch.get(key))
        for key in (
            "branchId",
            "purpose",
            "status",
            "answer",
            "highlights",
            "columns",
            "columnTypes",
            "rows",
            "lineage",
            "warnings",
        )
        if branch.get(key) is not None
    }


def _candidate_projection(record: dict[str, Any]) -> dict[str, Any]:
    response = dict(record.get("normalized") or {})
    rows = response.get("allRows")
    if rows is None:
        rows = response.get("rows") or []
    candidate = {
        "httpStatus": int((record.get("initial") or {}).get("httpStatus") or 0),
        "outcome": response.get("outcome"),
        "completion": response.get("completion"),
        "status": response.get("status"),
        "reasonCode": response.get("reasonCode"),
        "answer": response.get("answer"),
        "highlights": response.get("highlights") or [],
        "clarificationQuestion": response.get("clarificationQuestion"),
        "clarificationOptions": _option_projection(response.get("clarificationOptions")),
        "columns": response.get("columns") or [],
        "columnTypes": response.get("columnTypes") or {},
        "rows": rows,
        "branches": [
            _branch_projection(branch)
            for branch in response.get("branches") or []
            if isinstance(branch, dict)
        ],
        "warnings": response.get("warnings") or [],
        "catalogVersion": response.get("catalogVersion"),
        "dataAsOf": response.get("dataAsOf"),
        "answerBoundary": response.get("answerBoundary"),
        "provisional": response.get("provisional"),
    }
    transport_error = (record.get("initial") or {}).get("error")
    if transport_error:
        candidate["transportError"] = str(transport_error)
    return candidate


def _instructions() -> str:
    issues = "、".join(sorted(_ALLOWED_ISSUE_CODES))
    return f"""# Text2SQL canonical 输出双盲人工评审

只编辑 `answer-review.open.jsonl` 中每行的 `label`，不得修改 `input`。

每位 reviewer 必须独立审核全部 160 行，不查看另一位 reviewer 的文件。包内没有修复前/修复后标识，也没有自动分数。

填写规则：

- `decision`: 只能填 `ACCEPT` 或 `REJECT`。
- `ACCEPT`: 候选输出与人工 gold 的 outcome、completion、结果、必要事实和边界一致；`issueCodes` 必须为空。
- `REJECT`: 任一关键合同不满足；`issueCodes` 至少填一项。
- `reviewerId`: 全文件使用同一个真人标识。
- `reviewedAt`: ISO-8601 时间，例如 `2026-08-28T16:00:00+08:00`。
- `notes`: 可写判断依据；选择 `OTHER` 时必须填写。

`PARTIAL` 只有在 gold 本身要求部分完成、成功与失败分支均被准确说明时才可接受。基础设施或模型失败必须判 `REJECT`，不能跳过。

可用 `issueCodes`：{issues}。

A/B 是否需要第三人仲裁只比较 `decision`；reviewerId、时间、notes 和 issueCodes 不会制造额外分歧。
"""


def _write_open_package(
    directory: Path,
    *,
    package_id: str,
    inputs: list[dict[str, Any]],
    catalog: Path,
) -> dict[str, Any]:
    directory.mkdir(parents=True)
    write_jsonl(directory / "review-inputs.jsonl", inputs)
    write_jsonl(
        directory / ANSWER_REVIEW_FILE,
        [{"input": row, "label": _blank_label()} for row in inputs],
    )
    shutil.copy2(catalog, directory / catalog.name)
    (directory / "INSTRUCTIONS.md").write_text(_instructions(), encoding="utf-8")
    manifest = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "packageId": package_id,
        "status": "OPEN_PENDING_HUMAN_REVIEW",
        "itemCount": len(inputs),
        "reviewInputsSha256": sha256_file(directory / "review-inputs.jsonl"),
        "catalogSha256": sha256_file(directory / catalog.name),
        "versionLabelsIncluded": False,
        "automaticScoresIncluded": False,
        "otherReviewerLabelsIncluded": False,
        "humanDecisionAuthorityRequired": True,
        "aiAssistanceUsedForPackageGeneration": True,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    write_json(directory / "manifest.json", manifest)
    return manifest


def create_answer_review_packages(
    pre: Path,
    post: Path,
    dataset: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    verify_human_gold(dataset)
    cases = {case.case_id: case for case in load_cases(dataset)}
    _, pre_records = _load_evidence(pre, phase="pre-foundation")
    _, post_records = _load_evidence(post, phase="post-foundation")
    canonical = {
        "pre-foundation": [row for row in pre_records if row.get("canonical")],
        "post-foundation": [row for row in post_records if row.get("canonical")],
    }
    if any(len(rows) != 80 for rows in canonical.values()):
        raise ValueError("answer review requires 80 canonical outputs from each evidence set")
    if any({row["caseId"] for row in rows} != set(cases) for rows in canonical.values()):
        raise ValueError("canonical outputs do not match the gold case set")

    bindings: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []
    for source_name, rows in canonical.items():
        for record in sorted(rows, key=lambda row: row["caseId"]):
            case = cases[record["caseId"]]
            item_id = f"t2sar-{uuid.uuid4().hex}"
            item = {
                "schemaVersion": ANSWER_REVIEW_SCHEMA,
                "reviewItemId": item_id,
                "question": case.question,
                "gold": _gold_projection(case),
                "candidate": _candidate_projection(record),
            }
            inputs.append(item)
            bindings.append(
                {
                    "reviewItemId": item_id,
                    "caseId": case.case_id,
                    "source": source_name,
                    "trial": 1,
                    "inputSha256": sha256_bytes(canonical_json_bytes(item)),
                    "rawRecordSha256": sha256_bytes(canonical_json_bytes(record)),
                }
            )

    output.mkdir(parents=True)
    catalog = dataset.parent / DEFAULT_CATALOG.name
    if not catalog.is_file():
        raise ValueError(f"gold catalog is missing: {catalog}")
    seed_a = secrets.randbits(256)
    seed_b = secrets.randbits(256)
    inputs_a = deepcopy(inputs)
    inputs_b = deepcopy(inputs)
    random.Random(seed_a).shuffle(inputs_a)
    random.Random(seed_b).shuffle(inputs_b)
    if [row["reviewItemId"] for row in inputs_a] == [row["reviewItemId"] for row in inputs_b]:
        inputs_b.reverse()
    package_a = output / "reviewer-a-open"
    package_b = output / "reviewer-b-open"
    manifest_a = _write_open_package(package_a, package_id="A", inputs=inputs_a, catalog=catalog)
    manifest_b = _write_open_package(package_b, package_id="B", inputs=inputs_b, catalog=catalog)
    control = output / "control"
    control.mkdir()
    write_jsonl(control / "source-bindings.jsonl", bindings)
    control_manifest = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "createdAt": utc_now(),
        "status": "OPEN_PENDING_HUMAN_REVIEW",
        "itemCount": 160,
        "goldSha256": sha256_file(dataset),
        "catalogSha256": sha256_file(catalog),
        "preEvidenceSha256": sha256_file(pre / "SHA256SUMS"),
        "postEvidenceSha256": sha256_file(post / "SHA256SUMS"),
        "sourceBindingsSha256": sha256_file(control / "source-bindings.jsonl"),
        "packageAInputsSha256": manifest_a["reviewInputsSha256"],
        "packageBInputsSha256": manifest_b["reviewInputsSha256"],
        "packageAOrderSeed": f"{seed_a:064x}",
        "packageBOrderSeed": f"{seed_b:064x}",
        "disagreementPolicy": "decision-only",
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    write_json(control / "manifest.json", control_manifest)
    write_sha256s(control)
    return {
        "output": str(output),
        "reviewerA": str(package_a),
        "reviewerB": str(package_b),
        **control_manifest,
    }


def _validate_label(label: Any, *, item_id: str, complete: bool) -> str | None:
    if not isinstance(label, dict) or set(label) != _LABEL_KEYS:
        raise ValueError(f"{item_id}: label fields do not match the review schema")
    decision = str(label.get("decision") or "").strip().upper()
    issue_codes = label.get("issueCodes")
    if not isinstance(issue_codes, list) or len(issue_codes) != len(set(issue_codes)):
        raise ValueError(f"{item_id}: issueCodes must be a unique array")
    unknown = set(issue_codes) - _ALLOWED_ISSUE_CODES
    if unknown:
        raise ValueError(f"{item_id}: unknown issueCodes: {sorted(unknown)}")
    if not complete and not decision:
        return None
    if decision not in _ALLOWED_DECISIONS:
        raise ValueError(f"{item_id}: decision must be ACCEPT or REJECT")
    if decision == "ACCEPT" and issue_codes:
        raise ValueError(f"{item_id}: ACCEPT must not contain issueCodes")
    if decision == "REJECT" and not issue_codes:
        raise ValueError(f"{item_id}: REJECT requires at least one issueCode")
    notes = str(label.get("notes") or "").strip()
    if "OTHER" in issue_codes and not notes:
        raise ValueError(f"{item_id}: OTHER requires notes")
    reviewer = str(label.get("reviewerId") or "").strip()
    reviewed_at = str(label.get("reviewedAt") or "").strip()
    if not reviewer or not reviewed_at:
        raise ValueError(f"{item_id}: reviewerId and reviewedAt are required")
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{item_id}: reviewedAt must be ISO-8601") from exc
    return reviewer


def validate_answer_review(
    directory: Path,
    *,
    review_file: Path | None = None,
    require_complete: bool = False,
) -> dict[str, Any]:
    manifest = read_json(directory / "manifest.json")
    if manifest.get("schemaVersion") != ANSWER_REVIEW_SCHEMA:
        raise ValueError("unsupported answer review package")
    inputs_path = directory / "review-inputs.jsonl"
    if sha256_file(inputs_path) != manifest.get("reviewInputsSha256"):
        raise ValueError("review inputs do not match the package manifest")
    inputs = read_jsonl(inputs_path)
    if len(inputs) != 160:
        raise ValueError("answer review package must contain 160 inputs")
    by_id = {str(row.get("reviewItemId") or ""): row for row in inputs}
    if len(by_id) != 160 or "" in by_id:
        raise ValueError("review input IDs must be unique and non-empty")
    encoded = canonical_json_bytes(inputs).decode("utf-8", errors="replace")
    for forbidden in ('"source"', '"sourcePhase"', '"caseId"', '"trial"', '"score"'):
        if forbidden in encoded:
            raise ValueError(f"review package leaks hidden metadata: {forbidden}")
    rows = read_jsonl(review_file or directory / ANSWER_REVIEW_FILE)
    row_ids = [str((row.get("input") or {}).get("reviewItemId") or "") for row in rows]
    if len(rows) != 160 or len(set(row_ids)) != 160 or set(row_ids) != set(by_id):
        raise ValueError("review rows must cover each of the 160 inputs exactly once")
    reviewers: set[str] = set()
    completed = 0
    for row in rows:
        item_id = str((row.get("input") or {}).get("reviewItemId") or "")
        if canonical_json_bytes(row.get("input")) != canonical_json_bytes(by_id[item_id]):
            raise ValueError(f"{item_id}: locked review input was modified")
        reviewer = _validate_label(row.get("label"), item_id=item_id, complete=require_complete)
        if reviewer:
            reviewers.add(reviewer)
            completed += 1
    if require_complete and (completed != 160 or len(reviewers) != 1):
        raise ValueError("complete review requires one consistent reviewer for all 160 items")
    return {
        "valid": True,
        "complete": completed == 160,
        "completedCount": completed,
        "reviewerId": next(iter(reviewers)) if len(reviewers) == 1 else None,
        "itemCount": 160,
    }


def seal_answer_review(
    directory: Path,
    output: Path,
    *,
    review_file: Path | None = None,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    validation = validate_answer_review(directory, review_file=review_file, require_complete=True)
    output.mkdir(parents=True)
    shutil.copy2(directory / "review-inputs.jsonl", output / "review-inputs.jsonl")
    shutil.copy2(
        review_file or directory / ANSWER_REVIEW_FILE,
        output / ANSWER_REVIEW_SEALED_FILE,
    )
    shutil.copy2(directory / DEFAULT_CATALOG.name, output / DEFAULT_CATALOG.name)
    seal = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "sealedAt": utc_now(),
        "reviewerId": validation["reviewerId"],
        "itemCount": 160,
        "reviewInputsSha256": sha256_file(output / "review-inputs.jsonl"),
        "reviewDecisionsSha256": sha256_file(output / ANSWER_REVIEW_SEALED_FILE),
        "humanDecisionAuthority": True,
        "aiAssistanceUsed": True,
        "pureHumanUnaided": False,
    }
    write_json(output / "seal.json", seal)
    write_sha256s(output)
    return {"output": str(output), **seal}


def _sealed_rows(directory: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    verify_sha256s(directory)
    seal = read_json(directory / "seal.json")
    rows = read_jsonl(directory / ANSWER_REVIEW_SEALED_FILE)
    by_id = {str((row.get("input") or {}).get("reviewItemId") or ""): row for row in rows}
    if len(by_id) != 160:
        raise ValueError("sealed answer review must contain 160 unique items")
    return seal, by_id


def _verify_control_bindings(
    rows: dict[str, dict[str, Any]], bindings: dict[str, dict[str, Any]]
) -> None:
    for item_id, row in rows.items():
        actual = sha256_bytes(canonical_json_bytes(row.get("input")))
        if actual != bindings[item_id].get("inputSha256"):
            raise ValueError(f"{item_id}: sealed review input is not bound to control")


def compare_answer_reviews(
    sealed_a: Path,
    sealed_b: Path,
    control: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    verify_sha256s(control)
    control_manifest = read_json(control / "manifest.json")
    bindings = {row["reviewItemId"]: row for row in read_jsonl(control / "source-bindings.jsonl")}
    seal_a, by_a = _sealed_rows(sealed_a)
    seal_b, by_b = _sealed_rows(sealed_b)
    if seal_a["reviewerId"] == seal_b["reviewerId"]:
        raise ValueError("reviewer A and B must be distinct real people")
    if set(by_a) != set(by_b) or set(by_a) != set(bindings):
        raise ValueError("review A/B and control item sets differ")
    _verify_control_bindings(by_a, bindings)
    _verify_control_bindings(by_b, bindings)
    disagreements = []
    agreements = []
    for item_id in sorted(by_a):
        a_label = by_a[item_id]["label"]
        b_label = by_b[item_id]["label"]
        if a_label["decision"] != b_label["decision"]:
            disagreements.append(
                {
                    "schemaVersion": ANSWER_REVIEW_SCHEMA,
                    "reviewItemId": item_id,
                    "input": by_a[item_id]["input"],
                    "reviewA": deepcopy(a_label),
                    "reviewB": deepcopy(b_label),
                    "label": _blank_label(),
                }
            )
        else:
            agreements.append(
                {
                    "reviewItemId": item_id,
                    "decision": a_label["decision"],
                    "issueCodes": sorted(set(a_label["issueCodes"]) | set(b_label["issueCodes"])),
                }
            )
    output.mkdir(parents=True)
    write_jsonl(output / "agreements.jsonl", agreements)
    write_jsonl(output / "adjudication.open.jsonl", disagreements)
    evidence = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "status": "PENDING_ADJUDICATION" if disagreements else "AGREED",
        "reviewerA": seal_a["reviewerId"],
        "reviewerB": seal_b["reviewerId"],
        "reviewAPackageSha256": sha256_file(sealed_a / "SHA256SUMS"),
        "reviewBPackageSha256": sha256_file(sealed_b / "SHA256SUMS"),
        "controlPackageSha256": sha256_file(control / "SHA256SUMS"),
        "agreementCount": 160 - len(disagreements),
        "disagreementCount": len(disagreements),
        "disagreementPolicy": control_manifest.get("disagreementPolicy"),
        "humanDecisionAuthorityRequired": True,
    }
    write_json(output / "manifest.json", evidence)
    (output / "INSTRUCTIONS.md").write_text(
        "# Text2SQL canonical 输出第三人仲裁\n\n"
        "只编辑 `adjudication.open.jsonl` 中每行的 `label`，不得修改 `input`、"
        "`reviewA` 或 `reviewB`。仲裁人必须是真人且不得是 A/B；只仲裁包内的 "
        "decision 分歧，不得查看版本映射或自动分数。\n\n"
        "填写规则：\n\n"
        "- `decision`: 只能填 `ACCEPT` 或 `REJECT`。\n"
        "- `ACCEPT`: 候选输出满足 gold outcome、completion、结果、必要事实和边界；"
        "`issueCodes` 必须为空。\n"
        "- `REJECT`: 任一关键合同不满足；`issueCodes` 至少填一项。\n"
        "- `reviewerId`: 全文件使用同一个第三位真人标识，且不得与 A/B 相同。\n"
        "- `reviewedAt`: ISO-8601 时间，例如 `2026-08-28T16:00:00+08:00`。\n"
        "- `notes`: 可写仲裁依据；选择 `OTHER` 时必须填写。\n\n"
        "`PARTIAL` 只有在 gold 本身要求部分完成、成功与失败分支均被准确说明时才可接受。"
        "基础设施或模型失败必须判 `REJECT`，不能跳过。\n\n"
        "可用 `issueCodes`：CLARIFICATION_DEFECT、FLOW_CONTRACT_DEFECT、"
        "FORBIDDEN_CLAIM、INFRASTRUCTURE_FAILURE、MISLEADING_BOUNDARY、"
        "MISSING_REQUIRED_FACT、OTHER、UNSAFE_DATA_EXPOSURE、WRONG_COMPLETION、"
        "WRONG_OUTCOME、WRONG_RESULT。\n",
        encoding="utf-8",
    )
    return {"output": str(output), **evidence}


def adjudicate_answer_reviews(
    sealed_a: Path,
    sealed_b: Path,
    comparison: Path,
    control: Path,
    output: Path,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(output)
    verify_sha256s(control)
    comparison_manifest = read_json(comparison / "manifest.json")
    seal_a, by_a = _sealed_rows(sealed_a)
    seal_b, by_b = _sealed_rows(sealed_b)
    bindings = {row["reviewItemId"]: row for row in read_jsonl(control / "source-bindings.jsonl")}
    if set(by_a) != set(by_b) or set(by_a) != set(bindings):
        raise ValueError("review packages and control item sets differ")
    _verify_control_bindings(by_a, bindings)
    _verify_control_bindings(by_b, bindings)
    if comparison_manifest.get("reviewAPackageSha256") != sha256_file(
        sealed_a / "SHA256SUMS"
    ) or comparison_manifest.get("reviewBPackageSha256") != sha256_file(sealed_b / "SHA256SUMS"):
        raise ValueError("comparison is not bound to the supplied sealed reviews")
    disagreement_ids = {
        item_id
        for item_id in by_a
        if by_a[item_id]["label"]["decision"] != by_b[item_id]["label"]["decision"]
    }
    if comparison_manifest.get("disagreementCount") != len(disagreement_ids):
        raise ValueError("comparison disagreement count does not match sealed reviews")
    adjudication_rows = read_jsonl(comparison / "adjudication.open.jsonl")
    adjudication_by_id: dict[str, dict[str, Any]] = {}
    adjudicators: set[str] = set()
    if disagreement_ids:
        for row in adjudication_rows:
            item_id = str(row.get("reviewItemId") or "")
            if item_id not in disagreement_ids or item_id in adjudication_by_id:
                raise ValueError(f"unexpected or duplicate adjudication item: {item_id}")
            reviewer = _validate_label(row.get("label"), item_id=item_id, complete=True)
            if reviewer:
                adjudicators.add(reviewer)
            adjudication_by_id[item_id] = row["label"]
        if set(adjudication_by_id) != disagreement_ids or len(adjudicators) != 1:
            raise ValueError("all disagreements require one consistent adjudicator")
    elif adjudication_rows:
        raise ValueError("adjudication rows exist even though A/B decisions agree")
    adjudicator = next(iter(adjudicators)) if adjudicators else None
    if adjudicator in {seal_a["reviewerId"], seal_b["reviewerId"]}:
        raise ValueError("adjudicator must be independent from reviewers A/B")

    final_rows = []
    for item_id in sorted(bindings):
        label_a = by_a[item_id]["label"]
        label_b = by_b[item_id]["label"]
        final_label = adjudication_by_id.get(item_id)
        if final_label is None:
            final_label = {
                "decision": label_a["decision"],
                "issueCodes": sorted(set(label_a["issueCodes"]) | set(label_b["issueCodes"])),
                "notes": "A/B decision consensus",
                "reviewerId": "A/B_CONSENSUS",
                "reviewedAt": max(label_a["reviewedAt"], label_b["reviewedAt"]),
            }
        binding = bindings[item_id]
        final_rows.append(
            {
                "reviewItemId": item_id,
                "caseId": binding["caseId"],
                "source": binding["source"],
                "inputSha256": binding["inputSha256"],
                "finalLabel": final_label,
                "reviewA": label_a,
                "reviewB": label_b,
            }
        )
    by_source = {
        source: {
            decision: sum(
                row["source"] == source and row["finalLabel"]["decision"] == decision
                for row in final_rows
            )
            for decision in sorted(_ALLOWED_DECISIONS)
        }
        for source in ("pre-foundation", "post-foundation")
    }
    by_case: dict[str, dict[str, str]] = {}
    for row in final_rows:
        by_case.setdefault(row["caseId"], {})[row["source"]] = row["finalLabel"]["decision"]
    paired = Counter()
    for decisions in by_case.values():
        before = decisions["pre-foundation"]
        after = decisions["post-foundation"]
        if before == after:
            paired["UNCHANGED_ACCEPT" if after == "ACCEPT" else "UNCHANGED_REJECT"] += 1
        elif after == "ACCEPT":
            paired["IMPROVED"] += 1
        else:
            paired["REGRESSED"] += 1
    evidence = {
        "schemaVersion": ANSWER_REVIEW_SCHEMA,
        "status": "HUMAN_REVIEWED_ADJUDICATED" if disagreement_ids else "HUMAN_VERIFIED",
        "createdAt": utc_now(),
        "reviewers": [seal_a["reviewerId"], seal_b["reviewerId"]],
        "adjudicator": adjudicator,
        "agreementCount": 160 - len(disagreement_ids),
        "disagreementCount": len(disagreement_ids),
        "finalDecisionCountsByBlindedSource": by_source,
        "pairedHumanTransitions": dict(sorted(paired.items())),
        "humanDecisionAuthority": True,
        "aiAssistanceUsed": True,
        "pureHumanUnaided": False,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    output.mkdir(parents=True)
    write_jsonl(output / "answer-review.adjudicated.jsonl", final_rows)
    write_json(output / "evidence.json", evidence)
    write_sha256s(output)
    return {"output": str(output), **evidence}
