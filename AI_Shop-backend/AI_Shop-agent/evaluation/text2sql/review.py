from __future__ import annotations

import random
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from evaluation.text2sql import REVIEW_SCHEMA_VERSION
from evaluation.text2sql.contracts import Annotation, Expected, Outcome, ResultOracle, Text2SqlCase
from evaluation.text2sql.dataset import (
    DEFAULT_CATALOG,
    DEFAULT_DATASET,
    DEFAULT_LOCK,
    dataset_sha256,
    load_cases,
    validate_v0,
)
from evaluation.text2sql.fixture import (
    FIXED_TIMESTAMP,
    READER_PASSWORD,
    READER_USER,
    _mysql_connection,
    _query_oracle,
    reset,
)
from evaluation.text2sql.io import (
    canonical_json_bytes,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now,
    verify_sha256s,
    write_json,
    write_jsonl,
    write_sha256s,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_WORKSPACE = REPO_ROOT / "run/review-workspaces/text2sql-v0-gold-20260827"
DEFAULT_EVIDENCE_ROOT = (
    REPO_ROOT / "AI_Shop-backend/evaluation-evidence/benchmarks/text2sql"
)
REVIEW_FILE = "gold-review.open.jsonl"
INPUT_FILE = "review-inputs.jsonl"
MANIFEST_FILE = "manifest.json"

_BANNED_INPUT_KEYS = {
    "expected",
    "outcome",
    "completion",
    "reasonCode",
    "referenceSql",
    "resultOracle",
    "branchResultOracles",
    "requiredFacts",
    "forbiddenClaims",
    "sliceTags",
    "risk",
    "annotationNote",
}


def _blank_label() -> dict[str, Any]:
    return {
        "reviewerId": "",
        "reviewedAt": None,
        "outcome": None,
        "completion": None,
        "reasonCode": None,
        "branches": [],
        "referenceSql": [],
        "resultOracle": None,
        "branchResultOracles": [],
        "expectedFailedBranchIds": [],
        "clarificationQuestion": None,
        "clarificationOptions": [],
        "requiredFacts": [],
        "forbiddenClaims": [],
        "notes": "",
    }


def _input_projection(case: Text2SqlCase) -> dict[str, Any]:
    return {
        "id": case.case_id,
        "question": case.question,
        "actor": case.actor.model_dump(by_alias=True, mode="json"),
        "fixtureState": case.fixture_state,
        "fixedClock": f"{FIXED_TIMESTAMP} Asia/Shanghai",
        "catalogVersion": "analytics-provisional-v0.20260827",
    }


def _contains_key(value: Any, banned: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in banned or _contains_key(item, banned) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, banned) for item in value)
    return False


def _instructions(slot: str) -> str:
    return f"""# Text2SQL V0 gold 独立评审（Reviewer {slot}）

状态：`OPEN`。这里没有模型输出，也没有 AI 候选 outcome、SQL、oracle、风险标签或自动分数。

请由一位真人独立完成 `gold-review.open.jsonl` 中每行的 `label`：

- `ANSWER`：现有十视图能回答。`COMPLETE` 表示全部分支应成功；仅故障注入题可标 `PARTIAL`，并填写 `expectedFailedBranchIds`。
- `CLARIFY`：至少两种合理解释会实质改变答案；填写一个最小问题和稳定 choice ID。只允许一轮澄清。
- `ABSTAIN`：权限具备，但目录或 V0 能力不足；填写稳定 `reasonCode` 和边界。
- `DENY`：权限、owner/scope 或安全策略拒绝；预期 HTTP 403，不执行 SQL。

ANSWER 需要填写逐分支 semantic view、metric、dimension、绝对日期和 MySQL 参考 SQL。先不要手工抄结果；完成 SQL 后运行 `review-materialize`，工具会在对应 fixture state 和同一只读一致快照中生成类型化 oracle。金额必须保持 CNY 两位字符串。Exact SQL 只用于复核，最终判断以语义计划和类型化结果为主。

必须检查 catalog 的强边界：履约混合日期不能称 cohort；推荐事件日比率不能称正式转化率；forecast confidence 只能称数据覆盖度；金额只能称暂定运营口径、不得称结算或审计结论。

不要与另一位 reviewer 沟通标签。提交前填写同一个真实 `reviewerId` 和每行 `reviewedAt`，再运行 `review-validate --complete`。评审身份必须是实际做决定的人，不得填写 AI 名称。
"""


def create_open_packages(
    output_root: Path = DEFAULT_WORKSPACE,
    *,
    dataset_path: Path = DEFAULT_DATASET,
    overwrite: bool = False,
) -> dict[str, Any]:
    if dataset_path.resolve() != DEFAULT_DATASET.resolve():
        raise ValueError("V0 review packages must be generated from the locked candidate dataset")
    cases = load_cases(dataset_path)
    summary = validate_v0(cases)
    if any(case.lifecycle != "AI_DRAFT_PENDING_HUMAN_REVIEW" for case in cases):
        raise ValueError("open review packages require the AI draft candidate dataset")
    packages: dict[str, str] = {}
    for slot, seed in (("A", 2026082701), ("B", 2026082702)):
        directory = output_root / f"reviewer-{slot.lower()}"
        if directory.exists():
            if not overwrite:
                raise FileExistsError(directory)
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        projections = [_input_projection(case) for case in cases]
        random.Random(seed).shuffle(projections)
        rows = [
            {
                "schemaVersion": REVIEW_SCHEMA_VERSION,
                "reviewerSlot": slot,
                "displayIndex": index,
                "input": projection,
                "label": _blank_label(),
            }
            for index, projection in enumerate(projections, 1)
        ]
        write_jsonl(directory / INPUT_FILE, projections)
        write_jsonl(directory / REVIEW_FILE, rows)
        shutil.copy2(DEFAULT_CATALOG, directory / DEFAULT_CATALOG.name)
        manifest = {
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "packageId": f"text2sql-v0-gold-review-{slot.lower()}-open-20260827",
            "status": "OPEN",
            "reviewKind": "INDEPENDENT_GOLD_LABELING",
            "reviewerSlot": slot,
            "shuffleSeed": seed,
            "caseCount": len(rows),
            "sourceDatasetSha256": sha256_file(dataset_path),
            "sourceDatasetCanonicalSha256": dataset_sha256(cases),
            "sourceLockSha256": sha256_file(DEFAULT_LOCK),
            "catalogSha256": sha256_file(DEFAULT_CATALOG),
            "inputProjectionSha256": sha256_file(directory / INPUT_FILE),
            "containsModelOutput": False,
            "containsCandidateLabels": False,
            "containsAutomaticScores": False,
            "humanDecisionAuthorityRequired": True,
            "development": True,
            "provisional": True,
            "unseen": False,
            "releaseGateEligible": False,
            "summaryWithoutLabels": {
                "caseCount": summary["caseCount"],
                "fixedClock": f"{FIXED_TIMESTAMP} Asia/Shanghai",
            },
        }
        write_json(directory / MANIFEST_FILE, manifest)
        (directory / "INSTRUCTIONS.md").write_text(_instructions(slot), encoding="utf-8")
        validate_review(directory, require_complete=False)
        packages[slot] = str(directory)
    return {"status": "OPEN", "packages": packages, "caseCount": len(cases)}


def _label_to_expected(label: dict[str, Any]) -> Expected:
    outcome = Outcome(str(label.get("outcome")))
    branches = list(label.get("branches") or [])
    reference_sql = list(label.get("referenceSql") or [])
    branch_oracles = list(label.get("branchResultOracles") or [])
    result_oracle = label.get("resultOracle")
    if outcome is not Outcome.ANSWER:
        result_oracle = ResultOracle(mode="NO_QUERY", materialized=True).model_dump(
            by_alias=True, mode="json"
        )
        branches = []
        reference_sql = []
        branch_oracles = []
    if not isinstance(result_oracle, dict):
        raise ValueError("ANSWER label requires a materialized resultOracle")
    return Expected.model_validate(
        {
            "outcome": outcome.value,
            "completion": label.get("completion"),
            "reasonCode": label.get("reasonCode"),
            "branches": branches,
            "referenceSql": reference_sql,
            "resultOracle": result_oracle,
            "branchResultOracles": branch_oracles,
            "expectedFailedBranchIds": label.get("expectedFailedBranchIds") or [],
            "clarificationQuestion": label.get("clarificationQuestion"),
            "clarificationOptions": label.get("clarificationOptions") or [],
            "requiredFacts": label.get("requiredFacts") or [],
            "forbiddenClaims": label.get("forbiddenClaims") or [],
            "maxModelCalls": min(2 * len(branches), 6) if branches else 1,
            "maxQueryCount": len(branches),
        }
    )


def _validated_label_expected(label: dict[str, Any], *, case_id: str) -> Expected:
    expected = _label_to_expected(label)
    if expected.outcome is not Outcome.ANSWER:
        return expected
    if len(expected.branch_result_oracles) != len(expected.branches):
        raise ValueError(f"{case_id}: every ANSWER branch requires an oracle")
    if not expected.result_oracle.materialized or not all(
        oracle.materialized for oracle in expected.branch_result_oracles
    ):
        raise ValueError(f"{case_id}: ANSWER oracles must be materialized")
    if canonical_json_bytes(expected.result_oracle.model_dump(by_alias=True, mode="json")) != (
        canonical_json_bytes(
            expected.branch_result_oracles[0].model_dump(by_alias=True, mode="json")
        )
    ):
        raise ValueError(f"{case_id}: resultOracle must equal the first branch oracle")
    for oracle in expected.branch_result_oracles:
        if len(oracle.rows) > expected.resource_budget.max_rows:
            raise ValueError(f"{case_id}: oracle exceeds maxRows")
        if len(oracle.columns) != len(set(oracle.columns)):
            raise ValueError(f"{case_id}: oracle columns must be unique")
        if set(oracle.columns) != set(oracle.column_types):
            raise ValueError(f"{case_id}: every oracle column requires a type")
        if any(not set(oracle.columns).issubset(row) for row in oracle.rows):
            raise ValueError(f"{case_id}: oracle row is missing a declared column")
        if oracle.mode == "EXACT_ROWS" and not oracle.rows:
            raise ValueError(f"{case_id}: EXACT_ROWS oracle must not be empty")
        if oracle.mode == "EMPTY_ROWS" and oracle.rows:
            raise ValueError(f"{case_id}: EMPTY_ROWS oracle must be empty")
    return expected


def validate_review(directory: Path, *, require_complete: bool) -> dict[str, Any]:
    manifest = read_json(directory / MANIFEST_FILE)
    rows = read_jsonl(directory / REVIEW_FILE)
    inputs = read_jsonl(directory / INPUT_FILE)
    if manifest.get("status") != "OPEN":
        raise ValueError("editable workspace manifest must remain OPEN")
    if manifest.get("containsModelOutput") or manifest.get("containsCandidateLabels"):
        raise ValueError("review package leaks model output or candidate labels")
    source_checks = {
        "sourceDatasetSha256": manifest.get("sourceDatasetSha256")
        == sha256_file(DEFAULT_DATASET),
        "sourceDatasetCanonicalSha256": manifest.get("sourceDatasetCanonicalSha256")
        == dataset_sha256(load_cases()),
        "sourceLockSha256": manifest.get("sourceLockSha256") == sha256_file(DEFAULT_LOCK),
        "catalogSha256": manifest.get("catalogSha256")
        == sha256_file(directory / DEFAULT_CATALOG.name)
        == sha256_file(DEFAULT_CATALOG),
        "inputProjectionSha256": manifest.get("inputProjectionSha256")
        == sha256_file(directory / INPUT_FILE),
    }
    if not all(source_checks.values()):
        raise ValueError(f"review package source binding failed: {source_checks}")
    if len(rows) != 80 or len(inputs) != 80:
        raise ValueError("each independent review must contain exactly 80 cases")
    if _contains_key(inputs, _BANNED_INPUT_KEYS):
        raise ValueError("review input projection contains candidate-label leakage")
    input_ids = [str(item.get("id") or "") for item in inputs]
    row_ids = [str((row.get("input") or {}).get("id") or "") for row in rows]
    if len(set(input_ids)) != 80 or input_ids != row_ids:
        raise ValueError("review rows must align one-to-one with shuffled input projections")
    labels = [row.get("label") for row in rows]
    if any(not isinstance(label, dict) for label in labels):
        raise ValueError("every review row requires a label object")
    if not require_complete:
        if any(label.get("outcome") is not None for label in labels):
            raise ValueError("fresh OPEN package must not contain candidate outcomes")
        return {"valid": True, "status": "OPEN", "caseCount": len(rows)}
    reviewers = {str(label.get("reviewerId") or "").strip() for label in labels}
    reviewers.discard("")
    if len(reviewers) != 1:
        raise ValueError("completed review must use one non-empty reviewerId")
    for row, label in zip(rows, labels, strict=True):
        case_id = str((row.get("input") or {}).get("id") or "")
        if not label.get("reviewedAt"):
            raise ValueError(f"{case_id}: reviewedAt is required")
        _validated_label_expected(label, case_id=case_id)
    return {
        "valid": True,
        "status": "COMPLETE",
        "caseCount": len(rows),
        "reviewerId": next(iter(reviewers)),
    }


def materialize_review(directory: Path) -> dict[str, Any]:
    rows = read_jsonl(directory / REVIEW_FILE)
    for state in ("base", "boundary", "empty"):
        reset(state)
        with _mysql_connection(
            user=READER_USER, password=READER_PASSWORD, database="aishop_admin"
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SET SESSION time_zone = '+08:00'")
                cursor.execute(f"SET SESSION timestamp = UNIX_TIMESTAMP('{FIXED_TIMESTAMP}')")
                for row in rows:
                    case_input = row.get("input") or {}
                    label = row.get("label") or {}
                    if case_input.get("fixtureState") != state or not label.get("outcome"):
                        continue
                    if label["outcome"] != Outcome.ANSWER.value:
                        no_query = ResultOracle(mode="NO_QUERY", materialized=True).model_dump(
                            by_alias=True, mode="json"
                        )
                        label["resultOracle"] = no_query
                        label["branchResultOracles"] = []
                        continue
                    branches = list(label.get("branches") or [])
                    sqls = list(label.get("referenceSql") or [])
                    if not branches or len(branches) != len(sqls):
                        raise ValueError(
                            f"{case_input.get('id')}: ANSWER needs aligned branches/referenceSql"
                        )
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    try:
                        oracles = [
                            _query_oracle(cursor, sql, str(branch.get("semanticView") or ""))
                            for branch, sql in zip(branches, sqls, strict=True)
                        ]
                    finally:
                        cursor.execute("ROLLBACK")
                    label["branchResultOracles"] = [
                        oracle.model_dump(by_alias=True, mode="json") for oracle in oracles
                    ]
                    label["resultOracle"] = label["branchResultOracles"][0]
    write_jsonl(directory / REVIEW_FILE, rows, overwrite=True)
    return {"materialized": True, "directory": str(directory), "caseCount": len(rows)}


def materialize_adjudication(directory: Path) -> dict[str, Any]:
    path = directory / "adjudication.open.jsonl"
    rows = read_jsonl(path)
    for state in ("base", "boundary", "empty"):
        reset(state)
        with _mysql_connection(
            user=READER_USER, password=READER_PASSWORD, database="aishop_admin"
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor.execute("SET SESSION time_zone = '+08:00'")
                cursor.execute(f"SET SESSION timestamp = UNIX_TIMESTAMP('{FIXED_TIMESTAMP}')")
                for row in rows:
                    case_input = row.get("input") or {}
                    label = row.get("label") or {}
                    if case_input.get("fixtureState") != state or not label.get("outcome"):
                        continue
                    if label["outcome"] != Outcome.ANSWER.value:
                        label["resultOracle"] = ResultOracle(
                            mode="NO_QUERY", materialized=True
                        ).model_dump(by_alias=True, mode="json")
                        label["branchResultOracles"] = []
                        continue
                    branches = list(label.get("branches") or [])
                    sqls = list(label.get("referenceSql") or [])
                    if not branches or len(branches) != len(sqls):
                        raise ValueError(
                            f"{case_input.get('id')}: adjudicated ANSWER needs aligned SQL"
                        )
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                    try:
                        oracles = [
                            _query_oracle(cursor, sql, str(branch.get("semanticView") or ""))
                            for branch, sql in zip(branches, sqls, strict=True)
                        ]
                    finally:
                        cursor.execute("ROLLBACK")
                    label["branchResultOracles"] = [
                        oracle.model_dump(by_alias=True, mode="json") for oracle in oracles
                    ]
                    label["resultOracle"] = label["branchResultOracles"][0]
    write_jsonl(path, rows, overwrite=True)
    return {"materialized": True, "directory": str(directory), "caseCount": len(rows)}


def seal_review(directory: Path, output: Path) -> dict[str, Any]:
    validation = validate_review(directory, require_complete=True)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    for name in (REVIEW_FILE, INPUT_FILE, MANIFEST_FILE, "INSTRUCTIONS.md", DEFAULT_CATALOG.name):
        shutil.copy2(directory / name, output / name)
    seal = {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "status": "SEALED",
        "sealedAt": utc_now(),
        "reviewerId": validation["reviewerId"],
        "reviewerSlot": read_json(directory / MANIFEST_FILE)["reviewerSlot"],
        "sourceDatasetSha256": read_json(directory / MANIFEST_FILE)["sourceDatasetSha256"],
        "reviewSha256": sha256_file(directory / REVIEW_FILE),
        "humanDecisionAuthority": True,
        "aiAssistanceUsed": True,
        "caseCount": 80,
    }
    write_json(output / "seal.json", seal)
    write_sha256s(output)
    return {"sealed": True, "output": str(output), **seal}


def _decision_payload(label: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(label)
    for key in ("reviewerId", "reviewedAt", "notes"):
        payload.pop(key, None)
    return payload


def compare_reviews(sealed_a: Path, sealed_b: Path, output: Path) -> dict[str, Any]:
    verify_sha256s(sealed_a)
    verify_sha256s(sealed_b)
    seal_a = read_json(sealed_a / "seal.json")
    seal_b = read_json(sealed_b / "seal.json")
    if seal_a["reviewerId"] == seal_b["reviewerId"]:
        raise ValueError("reviewer A and B must be distinct real people")
    if seal_a["sourceDatasetSha256"] != seal_b["sourceDatasetSha256"]:
        raise ValueError("review A/B source datasets differ")
    by_a = {(row["input"])["id"]: row for row in read_jsonl(sealed_a / REVIEW_FILE)}
    by_b = {(row["input"])["id"]: row for row in read_jsonl(sealed_b / REVIEW_FILE)}
    disagreements = []
    for case_id in sorted(by_a):
        a_label = _decision_payload(by_a[case_id]["label"])
        b_label = _decision_payload(by_b[case_id]["label"])
        if canonical_json_bytes(a_label) != canonical_json_bytes(b_label):
            disagreements.append(
                {
                    "schemaVersion": REVIEW_SCHEMA_VERSION,
                    "caseId": case_id,
                    "input": by_a[case_id]["input"],
                    "reviewA": a_label,
                    "reviewB": b_label,
                    "label": _blank_label(),
                }
            )
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    write_jsonl(output / "adjudication.open.jsonl", disagreements)
    manifest = {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "status": "PENDING_ADJUDICATION" if disagreements else "AGREED",
        "reviewA": seal_a,
        "reviewB": seal_b,
        "reviewAPackageSha256": sha256_file(sealed_a / "SHA256SUMS"),
        "reviewBPackageSha256": sha256_file(sealed_b / "SHA256SUMS"),
        "agreementCount": 80 - len(disagreements),
        "disagreementCount": len(disagreements),
        "humanDecisionAuthorityRequired": True,
    }
    write_json(output / MANIFEST_FILE, manifest)
    (output / "INSTRUCTIONS.md").write_text(
        "# 第三人仲裁\n\n仅对 A/B 分歧行填写 `label`。仲裁人必须是真人，且不得是 A/B。"
        "填写完整最终标签后再运行 adjudicate；不能用自动分数替代。\n",
        encoding="utf-8",
    )
    return {"output": str(output), **manifest}


def _adjudication_labels(directory: Path, disagreement_ids: set[str]) -> tuple[dict[str, Any], str]:
    if not disagreement_ids:
        return {}, ""
    rows = read_jsonl(directory / "adjudication.open.jsonl")
    row_ids = [str(row.get("caseId") or "") for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("adjudication contains duplicate case IDs")
    labels: dict[str, Any] = {}
    adjudicators: set[str] = set()
    for row in rows:
        case_id = str(row.get("caseId") or "")
        label = row.get("label") or {}
        if case_id not in disagreement_ids:
            raise ValueError(f"unexpected adjudication case: {case_id}")
        reviewer = str(label.get("reviewerId") or "").strip()
        if not reviewer or not label.get("reviewedAt"):
            raise ValueError(f"{case_id}: adjudicator identity and reviewedAt are required")
        _validated_label_expected(label, case_id=case_id)
        adjudicators.add(reviewer)
        labels[case_id] = label
    if set(labels) != disagreement_ids or len(adjudicators) != 1:
        raise ValueError("all disagreements require one consistent adjudicator")
    return labels, next(iter(adjudicators))


def adjudicate_gold(
    sealed_a: Path,
    sealed_b: Path,
    comparison: Path,
    output: Path,
    *,
    source_dataset: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    verify_sha256s(sealed_a)
    verify_sha256s(sealed_b)
    comparison_manifest = read_json(comparison / MANIFEST_FILE)
    seal_a = read_json(sealed_a / "seal.json")
    seal_b = read_json(sealed_b / "seal.json")
    if seal_a["reviewerId"] == seal_b["reviewerId"]:
        raise ValueError("reviewer A and B must be distinct real people")
    package_checks = {
        "reviewA": comparison_manifest.get("reviewAPackageSha256")
        == sha256_file(sealed_a / "SHA256SUMS"),
        "reviewB": comparison_manifest.get("reviewBPackageSha256")
        == sha256_file(sealed_b / "SHA256SUMS"),
    }
    if not all(package_checks.values()):
        raise ValueError(f"comparison is not bound to the supplied review packages: {package_checks}")
    by_a = {(row["input"])["id"]: row for row in read_jsonl(sealed_a / REVIEW_FILE)}
    by_b = {(row["input"])["id"]: row for row in read_jsonl(sealed_b / REVIEW_FILE)}
    source_cases = {case.case_id: case for case in load_cases(source_dataset)}
    if set(by_a) != set(by_b) or set(by_a) != set(source_cases):
        raise ValueError("review A/B and source dataset case IDs differ")
    source_hash = sha256_file(source_dataset)
    if source_hash != seal_a["sourceDatasetSha256"] or (
        seal_a["sourceDatasetSha256"] != seal_b["sourceDatasetSha256"]
    ):
        raise ValueError("review packages are not bound to the supplied source dataset")
    disagreement_ids = {
        case_id
        for case_id in by_a
        if canonical_json_bytes(_decision_payload(by_a[case_id]["label"]))
        != canonical_json_bytes(_decision_payload(by_b[case_id]["label"]))
    }
    if comparison_manifest.get("agreementCount") != 80 - len(disagreement_ids) or (
        comparison_manifest.get("disagreementCount") != len(disagreement_ids)
    ):
        raise ValueError("comparison counts do not match the supplied review decisions")
    c_labels, adjudicator = _adjudication_labels(comparison, disagreement_ids)
    review_a_hash = sha256_file(sealed_a / REVIEW_FILE)
    review_b_hash = sha256_file(sealed_b / REVIEW_FILE)
    reviewers = [seal_a["reviewerId"], seal_b["reviewerId"]]
    if adjudicator and adjudicator in reviewers:
        raise ValueError("adjudicator must be independent from reviewers A/B")
    final_cases: list[Text2SqlCase] = []
    for case_id in sorted(source_cases):
        label = c_labels.get(case_id, by_a[case_id]["label"])
        expected = _label_to_expected(label)
        public = source_cases[case_id].public()
        status = "HUMAN_REVIEWED_ADJUDICATED" if disagreement_ids else "HUMAN_VERIFIED"
        public["lifecycle"] = status
        public["expected"] = expected.model_dump(by_alias=True, mode="json")
        public["annotation"] = Annotation(
            status=status,
            humanDecisionAuthority=True,
            aiAssistanceUsed=True,
            reviewers=reviewers,
            adjudicator=adjudicator or None,
            reviewEvidence={
                "sourceDatasetSha256": source_hash,
                "reviewASha256": review_a_hash,
                "reviewBSha256": review_b_hash,
                **(
                    {"adjudicationSha256": sha256_file(comparison / "adjudication.open.jsonl")}
                    if disagreement_ids
                    else {}
                ),
            },
        ).model_dump(by_alias=True, mode="json")
        public["annotationNote"] = "最终标签由两位真人独立评审；分歧仅由第三位真人仲裁。"
        final_cases.append(Text2SqlCase.model_validate(public))
    summary = validate_v0(final_cases)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    write_jsonl(output / "gold-v0.jsonl", [case.public() for case in final_cases])
    shutil.copy2(DEFAULT_CATALOG, output / DEFAULT_CATALOG.name)
    evidence = {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "status": "HUMAN_REVIEWED_ADJUDICATED" if disagreement_ids else "HUMAN_VERIFIED",
        "createdAt": utc_now(),
        "reviewers": reviewers,
        "adjudicator": adjudicator or None,
        "agreementCount": comparison_manifest["agreementCount"],
        "disagreementCount": len(disagreement_ids),
        "sourceDatasetSha256": source_hash,
        "goldDatasetSha256": sha256_file(output / "gold-v0.jsonl"),
        "catalogSha256": sha256_file(output / DEFAULT_CATALOG.name),
        "summary": summary,
        "humanDecisionAuthority": True,
        "aiAssistanceUsed": True,
        "pureHumanUnaided": False,
        "development": True,
        "provisional": True,
        "unseen": False,
        "releaseGateEligible": False,
    }
    write_json(output / "evidence.json", evidence)
    write_sha256s(output)
    return {"output": str(output), **evidence}
