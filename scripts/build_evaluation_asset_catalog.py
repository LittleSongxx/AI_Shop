#!/usr/bin/env python3
"""Build a deterministic inventory of AI-Shop evaluation assets.

The catalog is intentionally index based: immutable evidence packages keep their
original paths and hashes, while loose intake files are moved into a dedicated
checksum-bound archive before this script is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "AI_Shop-backend" / "AI_Shop-agent"
DATASET_ROOT = AGENT_ROOT / "evaluation" / "datasets"
EVIDENCE_ROOT = AGENT_ROOT / "evaluation-evidence"
RUN_ROOT = AGENT_ROOT / "run"
DEFAULT_JSON = REPO_ROOT / "docs" / "evaluation" / "评测资产与证据归档索引-20260826.json"
DEFAULT_MARKDOWN = REPO_ROOT / "docs" / "evaluation" / "评测资产与证据归档索引-20260826.md"


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _jsonl_rows(path: Path) -> int | None:
    if path.suffix != ".jsonl":
        return None
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _is_badcase_jsonl(path: Path) -> bool:
    """Recognize both historical and current badcase filename conventions."""

    if path.suffix != ".jsonl":
        return False
    normalized = re.sub(r"[^a-z]", "", path.stem.casefold())
    return "badcase" in normalized


def _file_record(path: Path, *, include_rows: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
    if include_rows:
        rows = _jsonl_rows(path)
        if rows is not None:
            result["rowCount"] = rows
    return result


def _dataset_role(path: Path) -> str:
    relative = path.relative_to(DATASET_ROOT).as_posix()
    if relative.startswith(("development/", "regression/")):
        return "VISIBLE_DEVELOPMENT_OR_REGRESSION"
    if relative.startswith("final-inputs/"):
        return "HISTORICAL_FINAL_INPUT_LIFECYCLE"
    if relative.startswith("legacy/"):
        return "LEGACY_COMPATIBILITY"
    if relative.startswith("locks/"):
        return "LIFECYCLE_LOCK"
    if relative.startswith("customer_service/annotation-v2/"):
        return "HISTORICAL_REVIEW_WORKSPACE_SNAPSHOT"
    if relative.startswith("customer_service/adjudicated/"):
        return "ADJUDICATED_OR_BEHAVIOR_CONTRACT"
    if "candidate" in path.name:
        return "CANDIDATE_INPUT"
    if "taxonomy-contract" in path.name:
        return "ANNOTATION_POLICY_CONTRACT"
    if "gold" in path.name:
        return "GOLD_OR_HUMAN_LABEL_SOURCE"
    return "EVALUATION_DATA"


def _evidence_category(package: Path) -> str:
    relative = package.relative_to(EVIDENCE_ROOT).as_posix()
    if relative == "current":
        return "HISTORICAL_CURRENT_POINTER"
    if relative.startswith("archive/"):
        return "HISTORICAL_RUN_ARCHIVE"
    if relative.startswith("benchmarks/"):
        return "BENCHMARK_EVIDENCE"
    if relative.startswith("intake-archive/"):
        return "HISTORICAL_INTAKE_ARCHIVE"
    if relative.startswith("source-freezes/"):
        return "SOURCE_FREEZE"
    return "OTHER_EVIDENCE"


def _package_status(package: Path, manifest: dict[str, Any]) -> str:
    if package == EVIDENCE_ROOT / "current":
        return "HISTORICAL_FINAL_SOURCE_EXPOSED"
    lifecycle = _load_json(package / "lifecycle.json")
    source_freeze = _load_json(package / "source-freeze.json")
    return str(
        manifest.get("status")
        or lifecycle.get("status")
        or source_freeze.get("status")
        or (manifest.get("run") or {}).get("outcome")
        or "STATUS_NOT_DECLARED"
    )


def _collect_datasets() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(item for item in DATASET_ROOT.rglob("*") if item.is_file()):
        record = _file_record(path, include_rows=True)
        record["role"] = _dataset_role(path)
        results.append(record)
    return results


def _collect_evidence_packages() -> list[dict[str, Any]]:
    packages: list[dict[str, Any]] = []
    for manifest_path in sorted(EVIDENCE_ROOT.rglob("evidence-manifest.json")):
        package = manifest_path.parent
        manifest = _load_json(manifest_path)
        files = sorted(item for item in package.rglob("*") if item.is_file())
        badcases = [item for item in files if _is_badcase_jsonl(item)]
        package_record: dict[str, Any] = {
            "path": _relative(package),
            "category": _evidence_category(package),
            "status": _package_status(package, manifest),
            "artifactId": manifest.get("artifactId"),
            "schemaVersion": manifest.get("schemaVersion"),
            "fileCount": len(files),
            "totalBytes": sum(item.stat().st_size for item in files),
            "manifestSha256": _sha256(manifest_path),
            "checksumBound": (package / "SHA256SUMS").is_file(),
            "releaseGateEligible": manifest.get("releaseGateEligible"),
            "finalUnseenEligible": manifest.get("finalUnseenEligible"),
            "badCaseFiles": len(badcases),
            "badCaseRows": sum(_jsonl_rows(item) or 0 for item in badcases),
        }
        if (package / "SHA256SUMS").is_file():
            package_record["sha256SumsSha256"] = _sha256(package / "SHA256SUMS")
        packages.append(package_record)
    return packages


def _collect_badcases(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    package_status = {item["path"]: item["status"] for item in packages}
    results: list[dict[str, Any]] = []
    for path in sorted(EVIDENCE_ROOT.rglob("*.jsonl")):
        if not _is_badcase_jsonl(path):
            continue
        parent = path.parent
        owner = None
        while parent != EVIDENCE_ROOT.parent:
            candidate = _relative(parent)
            if candidate in package_status:
                owner = candidate
                break
            if parent == EVIDENCE_ROOT:
                break
            parent = parent.parent
        record = _file_record(path, include_rows=True)
        record["package"] = owner
        record["packageStatus"] = package_status.get(owner or "", "STATUS_NOT_DECLARED")
        results.append(record)
    return results


def _collect_docs(output_paths: set[Path]) -> list[dict[str, Any]]:
    roots = [REPO_ROOT / "docs" / "evaluation", REPO_ROOT / "docs" / "project"]
    extras = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "docs" / "README.md",
        AGENT_ROOT / "evaluation" / "README.md",
        EVIDENCE_ROOT / "README.md",
    ]
    files: set[Path] = set()
    for root in roots:
        files.update(item for item in root.rglob("*") if item.is_file())
    files.update(item for item in extras if item.is_file())
    return [
        _file_record(path)
        for path in sorted(files)
        if path.resolve() not in {item.resolve() for item in output_paths}
    ]


def _tree_record(path: Path) -> dict[str, Any]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    return {
        "path": _relative(path),
        "fileCount": len(files),
        "totalBytes": sum(item.stat().st_size for item in files),
    }


def _collect_workspaces() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for root_name in ("evaluation-observations", "review-workspaces", "agent-service-logs"):
        root = RUN_ROOT / root_name
        if not root.is_dir():
            continue
        children = sorted(item for item in root.iterdir() if item.is_dir())
        if children:
            for child in children:
                record = _tree_record(child)
                record["role"] = "NON_CANONICAL_RUNTIME_WORKSPACE"
                results.append(record)
        else:
            record = _tree_record(root)
            record["role"] = "NON_CANONICAL_RUNTIME_WORKSPACE"
            results.append(record)
    return results


def _current_authorities() -> list[dict[str, Any]]:
    return [
        {
            "role": "CENTRAL_MACHINE_INDEX",
            "path": "docs/evidence-manifest.json",
            "status": "AUTHORITATIVE_INDEX",
        },
        {
            "role": "SYSTEM_AND_EVALUATION_AUDIT",
            "path": "docs/evaluation/AI-Shop评测体系全面审计与执行结果-20260826.md",
            "status": "CURRENT_DECISION_RECORD",
        },
        {
            "role": "SYSTEM_AND_EVALUATION_AUDIT_MACHINE",
            "path": "docs/evaluation/AI-Shop评测体系全面审计与执行结果-20260826.json",
            "status": "CURRENT_DECISION_RECORD",
        },
        {
            "role": "HUMAN_WORK_QUEUE",
            "path": "docs/evaluation/人工标注交接总清单-20260826.md",
            "status": "CURRENT_HANDOFF_ENTRYPOINT",
        },
        {
            "role": "CURRENT_HUMAN_QUALITY_BASELINE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827",
            "status": "HUMAN_REVIEWED_ADJUDICATED_NON_RELEASE",
        },
        {
            "role": "CURRENT_BADCASE_REMEDIATION_HANDOFF",
            "path": "docs/evaluation/AI-Shop-v43-Badcase修复与v54复评交接-20260827.md",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED_7_BADCASES",
        },
        {
            "role": "CURRENT_POST_FIX_TARGETED_EXECUTION",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v53-badcase-fixes-handoff-evidence-targeted-20260827",
            "status": "TARGETED_BADCASE_FIXES_EXECUTED_PENDING_HUMAN_REVIEW",
        },
        {
            "role": "CURRENT_POST_FIX_FULL_EXECUTION",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-full-badcase-fixes-label-evidence-rebuilt-pending-human-review-20260827",
            "status": "EXECUTED_LABEL_EVIDENCE_BOUND_HUMAN_REVIEW_COMPLETE",
        },
        {
            "role": "CURRENT_POST_FIX_ANSWER_REVIEW_PENDING",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-badcase-fixes-answer-review-pending-adjudication-20260827",
            "status": "SEALED_PARENT_FINAL_EVIDENCE_AVAILABLE",
        },
        {
            "role": "CURRENT_POST_FIX_ANSWER_REVIEW_FINAL",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v54-badcase-fixes-answer-review-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED_NON_RELEASE",
        },
        {
            "role": "CURRENT_POST_FIX_REVIEW_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/customer-service-v54-answer-review-round1-returns-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_RETURNS_ARCHIVED_NORMALIZED_AND_SEALED",
        },
        {
            "role": "CURRENT_POST_FIX_ADJUDICATION_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/customer-service-v54-answer-review-adjudication-return-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED",
        },
        {
            "role": "CURRENT_V56_FULL_EXECUTION",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-full-v3-knowledge-regressions-pending-human-review-20260827",
            "status": "EXECUTED_PENDING_HUMAN_ANSWER_REVIEW",
        },
        {
            "role": "CURRENT_V56_ANSWER_REVIEW_PENDING",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-pending-adjudication-20260827",
            "status": "SEALED_PARENT_FINAL_EVIDENCE_AVAILABLE",
        },
        {
            "role": "CURRENT_V56_REVIEW_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/customer-service-v56-answer-review-round1-returns-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_RETURNS_ARCHIVED_NORMALIZED_AND_SEALED",
        },
        {
            "role": "CURRENT_V56_ADJUDICATION_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/customer-service-v56-answer-review-adjudication-return-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATION_ACCEPTED",
        },
        {
            "role": "CURRENT_V56_ANSWER_REVIEW_FINAL",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v56-v3-knowledge-answer-review-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED_NON_RELEASE",
        },
        {
            "role": "CURRENT_LABEL_VALIDITY_AUDIT",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-label-consistency-audit-20260826",
            "status": "RESOLVED_BY_V2_1_SUCCESSOR_FOR_DEVELOPMENT_ONLY",
        },
        {
            "role": "CURRENT_PROVENANCE_AUDIT",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-provenance-pending-20260826",
            "status": "INITIAL_REAUDIT_FAILED_FULL_60_AND_CUSTODY_REQUIRED",
        },
        {
            "role": "CURRENT_LABEL_POLICY_DUAL_REVIEW",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-label-policy-review-pending-adjudication-20260827",
            "status": "SEALED_PARENT_FINAL_SUCCESSOR_AVAILABLE",
        },
        {
            "role": "CURRENT_ANSWER_QUALITY_DUAL_REVIEW",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-pending-adjudication-20260827",
            "status": "SEALED_PARENT_FINAL_EVIDENCE_AVAILABLE",
        },
        {
            "role": "CURRENT_LABEL_POLICY_FINAL",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v2.1-label-policy-human-approved-ai-assisted-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_ADJUDICATED_NON_RELEASE",
        },
        {
            "role": "CURRENT_ANSWER_QUALITY_FINAL",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-http-v43-answer-review-human-approved-ai-assisted-20260827",
            "status": "HUMAN_REVIEWED_ADJUDICATED_NON_RELEASE",
        },
        {
            "role": "CURRENT_HUMAN_APPROVAL_PROVENANCE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/human-review-human-approval-ai-assistance-provenance-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_DISCLOSED",
        },
        {
            "role": "CURRENT_PROVENANCE_REAUDIT_RESULT",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/customer-service/customer-service-human-v2-independent-reaudit-initial-failed-20260827",
            "status": "EXPANSION_REQUIRED_CUSTODY_INCOMPLETE",
        },
        {
            "role": "ROUND1_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/human-review-round1-returns-20260827",
            "status": "EXACT_RETURNS_ARCHIVED_ANSWER_AND_LABEL_FINALIZED_PROVENANCE_OPEN",
        },
        {
            "role": "ADJUDICATION_RETURN_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/human-review-adjudication-returns-20260827",
            "status": "HUMAN_APPROVED_AI_ASSISTED_INTAKE_ACCEPTED",
        },
        {
            "role": "RECOVERED_HISTORICAL_INTAKE",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/intake-archive/customer-service-v2-additions-original-submissions-recovered-20260826",
            "status": "HISTORICAL_INTAKE_RECOVERED_PROVENANCE_STILL_BLOCKED",
        },
        {
            "role": "HISTORICAL_FINAL_POINTER",
            "path": "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/current",
            "status": "HISTORICAL_FINAL_SOURCE_EXPOSED_NOT_CURRENT_QUALITY_AUTHORITY",
        },
    ]


def build_catalog(*, output_json: Path, output_markdown: Path) -> dict[str, Any]:
    output_paths = {output_json, output_markdown}
    datasets = _collect_datasets()
    packages = _collect_evidence_packages()
    badcases = _collect_badcases(packages)
    docs = _collect_docs(output_paths)
    workspaces = _collect_workspaces()
    root_loose = [
        _file_record(path, include_rows=True)
        for path in sorted(REPO_ROOT.iterdir())
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    ]
    source_freezes = [
        _tree_record(path)
        for path in sorted((EVIDENCE_ROOT / "source-freezes").iterdir())
        if path.is_dir()
    ] if (EVIDENCE_ROOT / "source-freezes").is_dir() else []
    dataset_roles = Counter(item["role"] for item in datasets)
    evidence_categories = Counter(item["category"] for item in packages)
    catalog: dict[str, Any] = {
        "schemaVersion": "aishop-evaluation-asset-catalog/v1",
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "repositoryRoot": str(REPO_ROOT),
        "archivePolicy": {
            "mode": "REFERENCE_SAFE_INDEXED_ARCHIVE",
            "immutableEvidenceMoved": False,
            "reason": "Moving canonical evidence would invalidate paths, manifests, and historical hash chains.",
            "rootLooseStructuredFilesExpected": 0,
        },
        "summary": {
            "datasetFileCount": len(datasets),
            "evidencePackageCount": len(packages),
            "evidenceFileCount": sum(item["fileCount"] for item in packages),
            "badCaseFileCount": len(badcases),
            "badCaseRowCount": sum(item.get("rowCount", 0) for item in badcases),
            "documentationFileCount": len(docs),
            "runtimeWorkspaceCount": len(workspaces),
            "sourceFreezeCount": len(source_freezes),
            "rootLooseJsonOrJsonlCount": len(root_loose),
        },
        "currentAuthorities": _current_authorities(),
        "datasetRoleCounts": dict(sorted(dataset_roles.items())),
        "evidenceCategoryCounts": dict(sorted(evidence_categories.items())),
        "datasets": datasets,
        "evidencePackages": packages,
        "badCaseAssets": badcases,
        "documentation": docs,
        "runtimeWorkspaces": workspaces,
        "sourceFreezes": source_freezes,
        "rootLooseStructuredFiles": root_loose,
        "humanReviewQueue": [
            {
                "task": "CUSTOMER_SERVICE_V54_ANSWER_QUALITY_DUAL_REVIEW",
                "caseCount": 120,
                "reviewers": 3,
                "status": "COMPLETE_112_AGREED_8_ADJUDICATED_7_BADCASES",
            },
            {
                "task": "CUSTOMER_SERVICE_V56_ANSWER_QUALITY_ADJUDICATION",
                "caseCount": 120,
                "reviewers": 3,
                "status": "COMPLETE_118_AGREED_2_ADJUDICATED_0_BADCASES",
            },
            {
                "task": "CUSTOMER_SERVICE_V2_PROVENANCE_INDEPENDENT_REAUDIT",
                "caseCount": 60,
                "initialCompletedCaseCount": 12,
                "remainingCaseCount": 48,
                "reviewers": 1,
                "status": "EXPANSION_REQUIRED_CUSTODY_INCOMPLETE",
                "failureExpansionCaseCount": 60,
            },
            {
                "task": "EXTERNAL_UNSEEN_FINAL_REPLACEMENT",
                "minimumCaseCount": 100,
                "status": "NOT_GENERATED_BY_INDEPENDENT_CUSTODIAN",
                "current125CandidateEligible": False,
            },
        ],
        "claimBoundary": {
            "currentCustomerServiceDataset": "DEVELOPMENT_DIAGNOSTIC_ONLY",
            "v43Execution": "120/120 production-path cases and 22/22 behavior contracts completed",
            "v43HumanAnswerReview": "120/120 human-approved; 3 disagreements adjudicated; joint quality 105/120",
            "v53TargetedRemediation": "15/15 execution and 4/4 applicable behavior contracts passed; not a normal quality denominator",
            "v54PostFixExecution": "120/120 execution and 23/23 behavior contracts passed; 120/120 human-reviewed with 112 exact A/B agreements and 8 adjudications; joint quality 113/120",
            "v56V3KnowledgeRegression": "120/120 execution and 29/29 behavior contracts passed; 118 exact A/B agreements and 2 human adjudications; answer, handoff, and joint quality 120/120; citation 67/67; unsafe 0/120",
            "v21LabelPolicyReview": "25/25 human-approved; 5 disagreements adjudicated; exposed development evidence only",
            "v2ProvenanceReaudit": "initial 12 failed slot gate; full 60 and custody evidence required",
            "releaseGateEligible": False,
            "finalUnseenEligible": False,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output_markdown.write_text(_render_markdown(catalog, output_json), encoding="utf-8")
    return catalog


def _render_markdown(catalog: dict[str, Any], output_json: Path) -> str:
    summary = catalog["summary"]
    lines = [
        "# AI-Shop 评测资产与证据归档索引（更新至 2026-08-27）",
        "",
        "> 本页是面向人的归档导航；同名 JSON 是逐文件/逐包的机器清单。采用引用安全归档：不移动 immutable evidence，不改历史 manifest，只归档根目录散落回传件并建立权威索引。",
        "",
        "## 归档结果",
        "",
        f"- 数据集及契约文件：`{summary['datasetFileCount']}`",
        f"- checksum/manifest 证据包：`{summary['evidencePackageCount']}`（包内文件合计 `{summary['evidenceFileCount']}`）",
        f"- badcase 文件：`{summary['badCaseFileCount']}`，记录合计 `{summary['badCaseRowCount']}`",
        f"- 评测/项目文档：`{summary['documentationFileCount']}`",
        f"- 本地运行/盲审工作区：`{summary['runtimeWorkspaceCount']}`（非 canonical evidence）",
        f"- source freeze：`{summary['sourceFreezeCount']}`",
        f"- 项目根目录散落 JSON/JSONL：`{summary['rootLooseJsonOrJsonlCount']}`",
        "",
        "机器清单：`" + _relative(output_json) + "`。其中记录路径、bytes、SHA-256、JSONL 行数、证据包状态和 badcase 归属。",
        "",
        "## 当前权威入口",
        "",
        "| 用途 | 路径 | 状态 |",
        "|---|---|---|",
    ]
    for item in catalog["currentAuthorities"]:
        lines.append(f"| `{item['role']}` | `{item['path']}` | `{item['status']}` |")
    lines.extend(
        [
            "",
            "`evaluation-evidence/current` 只是历史 v9 final 指针；因 125 条中 120 条已有源码暴露证据，它不是当前质量结论入口。v56 是最新完整仲裁人工结果：答案/转人工/联合 120/120、引用 67/67、unsafe 0/120，但仍是已见开发集。",
            "",
            "## 数据分层",
            "",
            "| 层级 | 文件数 | 使用边界 |",
            "|---|---:|---|",
        ]
    )
    role_notes = {
        "VISIBLE_DEVELOPMENT_OR_REGRESSION": "可见开发/回归；可调优，不可称 unseen",
        "HISTORICAL_FINAL_INPUT_LIFECYCLE": "历史 final 生命周期材料；须结合暴露审计",
        "LEGACY_COMPATIBILITY": "兼容/历史数据，不作当前门禁",
        "LIFECYCLE_LOCK": "消费和锁状态",
        "HISTORICAL_REVIEW_WORKSPACE_SNAPSHOT": "历史 review 副本，不是新一轮空表",
        "ADJUDICATED_OR_BEHAVIOR_CONTRACT": "人工结果、HTTP fixture/contract；具体状态看 manifest",
        "CANDIDATE_INPUT": "候选输入，未自动等于 gold/final",
        "ANNOTATION_POLICY_CONTRACT": "标注政策；本身不是人工证据",
        "GOLD_OR_HUMAN_LABEL_SOURCE": "gold/人工标签源；仍受一致性与 provenance gate 约束",
        "EVALUATION_DATA": "其他评测数据",
    }
    for role, count in catalog["datasetRoleCounts"].items():
        lines.append(f"| `{role}` | {count} | {role_notes.get(role, '')} |")
    lines.extend(
        [
            "",
            "## 证据包分层",
            "",
            "| 类型 | 包数 | 解释 |",
            "|---|---:|---|",
        ]
    )
    category_notes = {
        "HISTORICAL_CURRENT_POINTER": "历史 current 指针，不等于当前权威结论",
        "HISTORICAL_RUN_ARCHIVE": "旧 final/run 的不可变历史归档",
        "BENCHMARK_EVIDENCE": "领域 benchmark、审计、配对和人工证据",
        "HISTORICAL_INTAKE_ARCHIVE": "从散落位置恢复并 checksum 绑定的来源件",
        "SOURCE_FREEZE": "源码/工作树冻结快照",
        "OTHER_EVIDENCE": "其他证据",
    }
    for category, count in catalog["evidenceCategoryCounts"].items():
        lines.append(f"| `{category}` | {count} | {category_notes.get(category, '')} |")
    lines.extend(
        [
            "",
            "## Badcase 归档",
            "",
            "每个 `*badcase*.jsonl` 继续留在产生它的 immutable evidence package 中；不能把不同数据版本、运行配置或人工状态的 badcase 合并成一个无来源总表。中央分析入口是 `docs/evaluation/AI质量评测与Badcase.md` / `.json`，机器清单则逐个记录文件 hash、行数和所属包。`evaluation-evidence/current/bad-cases.jsonl` 为空只代表历史 v9 当次报告，不代表当前系统没有 badcase。",
            "",
            "## 当前人工队列",
            "",
            "| 工作流 | 数量 | 人员 | 状态 |",
            "|---|---:|---:|---|",
        ]
    )
    for item in catalog["humanReviewQueue"]:
        count = item.get("caseCount", item.get("minimumCaseCount"))
        reviewers = item.get("reviewers", "外部角色隔离")
        lines.append(f"| `{item['task']}` | {count} | {reviewers} | `{item['status']}` |")
    lines.extend(
        [
            "",
            "v2.1 标签政策 25 条以及 v43、v54、v56 答案质量均已完成 A/B 审批及分歧仲裁，并按 `HUMAN_APPROVED_AI_ASSISTED` 封存；AI 仅辅助文字整理，最终决策由人工确认。v56 最终答案/转人工/联合 120/120、引用 67/67、unsafe 0/120，仍是已见开发集证据。来源独立复核初始 12 条的 slot exact agreement 为 0.50，低于预注册 0.70，因此仍须扩展到全 60 条并补齐该来源链的保管声明。",
            "",
            "## 根目录 JSON 整理",
            "",
            "原根目录四个 `reviewer-*.open.jsonl*` 实为已填写的历史 v2 additions 回传件，现已逐字节归档到 `evaluation-evidence/intake-archive/customer-service-v2-additions-original-submissions-recovered-20260826/`，并从根目录移除。恢复 reviewer-a source bytes 只补足一个历史来源缺口；export-hash 语义错误和 reviewer 独立性声明缺失仍未解决。",
            "",
            "## 归档规则",
            "",
            "1. `evaluation-evidence/**` 中已有 `SHA256SUMS`/manifest 的包保持原路径和只读语义。",
            "2. `run/**` 是可编辑工作区，不作为 canonical evidence；完成后必须 seal/package 到新目录。",
            "3. 历史文档不删除；发生政策变更时写 superseded 标记并由本索引指向当前入口。",
            "4. 任何新人工结果都使用新文件、新 hash、新 evidence package，禁止覆盖 open/sealed 历史文件。",
            "5. 当前总边界保持 `releaseGateEligible=false`、`finalUnseenEligible=false`。",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    catalog = build_catalog(
        output_json=args.output_json.resolve(),
        output_markdown=args.output_markdown.resolve(),
    )
    print(
        json.dumps(
            {
                "outputJson": _relative(args.output_json),
                "outputMarkdown": _relative(args.output_markdown),
                "summary": catalog["summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
