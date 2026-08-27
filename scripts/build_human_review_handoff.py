#!/usr/bin/env python3
"""Build separated, blinded human-review handoff packages for AI-Shop."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "AI_Shop-backend" / "AI_Shop-agent"
sys.path.insert(0, str(AGENT_ROOT))

from evaluation.customer_service_answer_review import (
    export_answer_review_sheet,
)

DELIVERY_ROOT = REPO_ROOT / "deliverables" / "human-review"
PACKAGE_NAME = "AI-Shop-human-review-round1-20260826"
DEFAULT_OUTPUT = DELIVERY_ROOT / PACKAGE_NAME
V43_REPORT = (
    AGENT_ROOT
    / "evaluation-evidence/benchmarks/customer-service/"
    "customer-service-http-v43-human-v2-routing-execution-fix-20260826/report.json"
)
V43_WORKSPACE = (
    AGENT_ROOT
    / "run/review-workspaces/"
    "customer-service-http-v43-human-v2-routing-execution-fix-20260826"
)
LABEL_AUDIT = (
    AGENT_ROOT
    / "evaluation-evidence/benchmarks/customer-service/"
    "customer-service-human-v2-label-consistency-audit-20260826"
)
PROVENANCE = (
    AGENT_ROOT
    / "evaluation-evidence/benchmarks/customer-service/"
    "customer-service-human-v2-provenance-pending-20260826"
)

INTENTS = (
    "ADDRESS_CHANGE",
    "AFTERSALES_UNKNOWN",
    "CANCEL_ORDER",
    "CHAT",
    "COMPLAINT",
    "CONFIRM_RECEIPT",
    "DAMAGED_OR_WRONG_ITEM",
    "HUMAN_REQUEST",
    "INVOICE",
    "PAYMENT_ISSUE",
    "PRODUCT_CONSULT",
    "PRODUCT_REVIEW",
    "PRODUCT_SEARCH",
    "QUERY_COUPON",
    "QUERY_FULFILLMENT",
    "QUERY_LOGISTICS",
    "QUERY_ORDER",
    "RECOMMENT",
    "REFUND",
    "REFUND_STATUS",
)
FORBIDDEN_BLIND_KEYS = {
    "expected",
    "predicted",
    "prediction",
    "modelOutput",
    "modelPrediction",
    "currentImmutableExpected",
    "issueCodes",
}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_text(
        path,
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError(f"JSON object required at {path}:{line_number}")
        rows.append(value)
    return rows


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if _sha256(source) != _sha256(destination):
        raise RuntimeError(f"copy hash mismatch: {source}")


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in FORBIDDEN_BLIND_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def _assert_blank_sheet(path: Path, *, expected_rows: int) -> None:
    rows = _load_jsonl(path)
    if len(rows) != expected_rows:
        raise RuntimeError(f"{path}: expected {expected_rows} rows, got {len(rows)}")
    leaked = _forbidden_keys(rows)
    if leaked:
        raise RuntimeError(f"{path}: blinded sheet leaks keys: {sorted(leaked)}")
    for index, row in enumerate(rows, 1):
        labels = row.get("labels")
        if not isinstance(labels, dict) or any(value is not None for value in labels.values()):
            raise RuntimeError(f"{path}:{index}: labels are not fully blank")


def _build_label_policy_sheets(output: Path, *, created_at: str) -> None:
    source_sheet = LABEL_AUDIT / "reaudit/label-policy-reaudit.open.jsonl"
    source_manifest = _load_json(
        LABEL_AUDIT / "reaudit/label-policy-reaudit.open.jsonl.manifest.json"
    )
    source_rows = _load_jsonl(source_sheet)
    if len(source_rows) != 25 or _forbidden_keys(source_rows):
        raise RuntimeError("label-policy source template is not a safe 25-case blind sheet")
    target_dir = output / "01-label-policy-v2.1"
    for suffix, reviewer_id, seed in (
        ("a", "label-policy-reviewer-a", 2026082603),
        ("b", "label-policy-reviewer-b", 2026082604),
    ):
        rows = json.loads(json.dumps(source_rows, ensure_ascii=False))
        for row in rows:
            row["reviewerId"] = reviewer_id
        random.Random(seed).shuffle(rows)
        sheet = target_dir / f"reviewer-{suffix}.open.jsonl"
        _write_jsonl(sheet, rows)
        manifest = {
            "schemaVersion": "aishop-customer-service-label-policy-reaudit/v1",
            "artifact": "BLINDED_LABEL_POLICY_REVIEW_SHEET",
            "lifecycle": "OPEN",
            "reviewerId": reviewer_id,
            "guidelinesVersion": "customer-service-taxonomy-v2.1",
            "sourceDatasetPath": (
                "AI_Shop-backend/AI_Shop-agent/evaluation-evidence/benchmarks/"
                "customer-service/customer-service-human-v2-label-consistency-audit-20260826/"
                "source/customer-service-human-v2.jsonl"
            ),
            "sourceDatasetSha256": source_manifest["sourceDatasetSha256"],
            "sourceTemplatePath": _relative(source_sheet),
            "sourceTemplateSha256": _sha256(source_sheet),
            "taxonomyContractPath": _relative(
                LABEL_AUDIT / "source/customer-service-taxonomy-contract-v2.1.json"
            ),
            "taxonomyContractSha256": source_manifest["taxonomyContractSha256"],
            "sheetPath": _relative(sheet),
            "sheetSha256": _sha256(sheet),
            "caseCount": len(rows),
            "orderSeed": seed,
            "labelSchema": {
                "intentValues": list(INTENTS),
                "riskLevelValues": ["HIGH", "LOW", "MEDIUM"],
                "handoffSeverityValues": ["CRITICAL", "NORMAL"],
                "requiredFields": [
                    "intent",
                    "riskLevel",
                    "shouldHandoff",
                    "handoffSeverity",
                    "slots",
                ],
            },
            "containsCurrentGoldOrModelPredictions": False,
            "selectionPolicy": source_manifest["selectionPolicy"],
            "acceptance": source_manifest["acceptance"],
            "createdAt": created_at,
        }
        _write_json(sheet.with_suffix(sheet.suffix + ".manifest.json"), manifest)
        _assert_blank_sheet(sheet, expected_rows=25)


def _build_answer_sheets(output: Path) -> None:
    target_dir = output / "02-answer-quality-v43"
    target_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("a", "b"):
        source_manifest = _load_json(
            V43_WORKSPACE / f"reviewer-{suffix}.open.jsonl.manifest.json"
        )
        sheet = target_dir / f"reviewer-{suffix}.open.jsonl"
        exported = export_answer_review_sheet(
            V43_REPORT,
            sheet,
            reviewer_id=str(source_manifest["reviewerId"]),
            seed=int(source_manifest["orderSeed"]),
            message_projection=str(source_manifest["messageProjection"]),
        )
        if exported["sourceReportSha256"] != source_manifest["sourceReportSha256"]:
            raise RuntimeError("v43 source report hash drifted while building handoff")
        if exported["sheetSha256"] != source_manifest["sheetSha256"]:
            raise RuntimeError("v43 blinded sheet bytes differ from validated workspace export")
        _assert_blank_sheet(sheet, expected_rows=120)


def _build_provenance_reaudit(output: Path) -> None:
    target_dir = output / "03-provenance-independent-reaudit"
    _copy(
        PROVENANCE / "reaudit/independent-reaudit.open.jsonl",
        target_dir / "independent-reaudit.open.jsonl",
    )
    _copy(
        PROVENANCE / "reaudit/independent-reaudit.open.jsonl.manifest.json",
        target_dir / "independent-reaudit.open.jsonl.manifest.json",
    )
    _copy(
        PROVENANCE / "reaudit/independence-attestation.template.json",
        target_dir / "historical-a-b-independence-attestation.template.json",
    )
    _copy(
        REPO_ROOT
        / "docs/evaluation/customer-service/客服v2-additions双人盲标说明-20260826.md",
        target_dir / "frozen-historical-guideline-v1.md",
    )
    _write_json(
        target_dir / "independent-reaudit-custody-attestation.template.json",
        {
            "schemaVersion": "aishop-independent-reaudit-custody-attestation/v1",
            "artifactId": "customer-service-human-v2-provenance-pending-20260826",
            "status": "TEMPLATE_NOT_EVIDENCE",
            "custodianIdentity": None,
            "reviewerIdentity": None,
            "attestedAt": None,
            "reviewerIndependentOfDatasetAndModelDevelopment": None,
            "reviewerDidNotViewSourceHumanLabels": None,
            "reviewerDidNotViewDraftExpectedOrModelOutputs": None,
            "reviewerDidNotViewPriorReviewsAgreementOrAdjudication": None,
            "materialsProvided": [],
            "exceptions": [],
            "signaturesOrExternalReferences": [],
        },
    )
    _assert_blank_sheet(target_dir / "independent-reaudit.open.jsonl", expected_rows=12)


def _copy_documents(output: Path) -> None:
    mappings = {
        REPO_ROOT / "docs/evaluation/人工标注交接总清单-20260826.md": (
            output / "00-common/人工标注交接总清单.md"
        ),
        REPO_ROOT
        / "docs/evaluation/customer-service/客服v2.1标签政策双盲重标说明-20260826.md": (
            output / "01-label-policy-v2.1/标注方法.md"
        ),
        LABEL_AUDIT / "source/customer-service-taxonomy-contract-v2.1.json": (
            output / "01-label-policy-v2.1/customer-service-taxonomy-contract-v2.1.json"
        ),
        REPO_ROOT
        / "docs/evaluation/customer-service/客服v43答案质量双盲说明-20260826.md": (
            output / "02-answer-quality-v43/标注方法.md"
        ),
        REPO_ROOT
        / "docs/evaluation/customer-service/客服v2来源独立复核说明-20260826.md": (
            output / "03-provenance-independent-reaudit/复核方法.md"
        ),
        REPO_ROOT
        / "docs/evaluation/customer-service/客服盲审后仲裁说明-20260826.md": (
            output / "04-adjudication-after-sealing/仲裁方法.md"
        ),
        REPO_ROOT
        / "docs/evaluation/external-unseen-final-independent-generation-and-custody-20260826.md": (
            output / "05-external-unseen-not-ready/独立生成与保管规范.md"
        ),
    }
    for source, destination in mappings.items():
        _copy(source, destination)


def _main_readme() -> str:
    return """# AI-Shop 人工评审协调包（第一轮，2026-08-26）

本目录是协调人母包，不应原样发给任何一位 reviewer，因为其中同时包含 A/B 文件、输入标签任务和模型答案任务。请分别发送同目录外生成的标签 A/B、答案 A/B 和来源独立复核 ZIP；不要把标签表与答案表合在同一个 reviewer 包中。

## 本轮可立即填写

1. `01-label-policy-v2.1/`：25 条输入标签政策重标，两位独立 reviewer 各一份。
2. `02-answer-quality-v43/`：120 条冻结答案质量评审，两位独立 reviewer 各一份。
3. `03-provenance-independent-reaudit/`：12 条历史来源独立复核，必须由第三类独立人员完成。

每位 reviewer 只能编辑分配表中的 `labels`、`comment`，以及复核表明确要求的 `reviewerId`；manifest 与其他字段不改。`ORIGINAL-SHA256SUMS` 绑定的是交付时空白状态，填写后表 hash 改变是预期行为，之后由项目方校验并 seal。

## 当前不能填写

- `04-adjudication-after-sealing/` 只有方法，没有样本。真实仲裁表必须在 A/B 均完成、seal 并比较后，仅从分歧集生成。
- 当前 125 条 external candidate 已对开发者可见，永久不具备 unseen 资格；`05-.../` 只有替代批次规范，不把这 125 条伪装成本轮正式人工任务。

## 角色要求

- 标签 A/B 不得看到当前 gold、任何模型输出或仲裁上下文；答案 A/B 不得看到旧答案标签、模型自评或另一人的结果。
- 同一个人如确需承担标签与答案两类任务，必须先完成并 seal 标签表，再取得答案包；更推荐使用不同人员。
- 来源独立复核者应与历史数据/模型开发、旧 A/B、当前 v2.1 A/B 都不同。
- 仲裁者在 A/B seal 前不得看中间结果，且不能由 A/B 自己兼任。

回传时保留原文件名和同名 manifest。项目方会在 `shop` conda 环境完成来源校验、封存、比较、仲裁模板导出和 successor evidence 构建。
"""


def _external_status_note() -> str:
    return """# 本轮未附 external unseen 正式样本

当前已有的 125 条 external candidate 已在开发环境生成并对开发者可见，状态永久为 `DISQUALIFIED_DEVELOPER_VISIBLE`。对它们补做人工标注不能恢复 unseen，因此没有把它们放进本轮正式评审包。

若只是验证表单/协议，可以另建明确标为 `PROTOCOL_TRIAL_ONLY` 的副本；正式质量评测必须由独立保管人按随附规范重新生成至少 100 条替代数据，并在运行前对开发者隐藏正文和 expected/qrel。
"""


def _file_role(relative: str) -> tuple[str, str | None, bool]:
    if relative.endswith("reviewer-a.open.jsonl"):
        return "EDITABLE_BLIND_REVIEW_SHEET", "reviewer-a", True
    if relative.endswith("reviewer-b.open.jsonl"):
        return "EDITABLE_BLIND_REVIEW_SHEET", "reviewer-b", True
    if relative.endswith("independent-reaudit.open.jsonl"):
        return "EDITABLE_INDEPENDENT_REAUDIT_SHEET", "provenance-independent-reviewer", True
    if relative.endswith("attestation.template.json"):
        return "EDITABLE_ATTESTATION_TEMPLATE", "custodian-and-specified-reviewer", True
    if relative.endswith(".manifest.json"):
        return "READ_ONLY_SOURCE_BINDING", None, False
    if relative.endswith((".md", ".json")):
        return "READ_ONLY_GUIDANCE_OR_CONTRACT", None, False
    return "READ_ONLY_PACKAGE_FILE", None, False


def _build_package_manifest(output: Path, *, created_at: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        if path.name in {"PACKAGE-MANIFEST.json", "ORIGINAL-SHA256SUMS"}:
            continue
        relative = path.relative_to(output).as_posix()
        role, audience, editable = _file_role(relative)
        files[relative] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "role": role,
            "audience": audience,
            "editable": editable,
        }
    manifest = {
        "schemaVersion": "aishop-human-review-handoff/v1",
        "packageId": PACKAGE_NAME,
        "createdAt": created_at,
        "lifecycle": "OPEN_HUMAN_WORK_PENDING",
        "humanWorkComplete": False,
        "releaseGateEligible": False,
        "finalUnseenEligible": False,
        "tasks": {
            "labelPolicyV2_1": {"caseCount": 25, "reviewers": 2, "completed": 0},
            "answerQualityV43": {"caseCount": 120, "reviewers": 2, "completed": 0},
            "provenanceIndependentReaudit": {"caseCount": 12, "reviewers": 1, "completed": 0},
            "adjudication": {"caseCount": None, "status": "WAITING_FOR_SEALED_A_B_COMPARISON"},
            "externalUnseenReplacement": {"caseCount": 0, "status": "NOT_YET_CUSTODIAN_GENERATED"},
        },
        "files": files,
    }
    _write_json(output / "PACKAGE-MANIFEST.json", manifest)
    checksum_files = sorted(item for item in output.rglob("*") if item.is_file())
    _write_text(
        output / "ORIGINAL-SHA256SUMS",
        "".join(
            f"{_sha256(path)}  {path.relative_to(output).as_posix()}\n"
            for path in checksum_files
        ),
    )
    return manifest


def _verify_package(output: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = _load_json(output / "PACKAGE-MANIFEST.json")
    for relative, metadata in (manifest.get("files") or {}).items():
        path = output / relative
        if not path.is_file():
            errors.append(f"missing:{relative}")
            continue
        if path.stat().st_size != metadata.get("bytes"):
            errors.append(f"bytes:{relative}")
        if _sha256(path) != metadata.get("sha256"):
            errors.append(f"sha256:{relative}")
    for line in (output / "ORIGINAL-SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        path = output / relative
        if not separator or not path.is_file() or _sha256(path) != digest:
            errors.append(f"checksum:{relative or line}")
    for relative, count in (
        ("01-label-policy-v2.1/reviewer-a.open.jsonl", 25),
        ("01-label-policy-v2.1/reviewer-b.open.jsonl", 25),
        ("02-answer-quality-v43/reviewer-a.open.jsonl", 120),
        ("02-answer-quality-v43/reviewer-b.open.jsonl", 120),
        ("03-provenance-independent-reaudit/independent-reaudit.open.jsonl", 12),
    ):
        try:
            _assert_blank_sheet(output / relative, expected_rows=count)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"blind-sheet:{relative}:{exc}")
    return {"valid": not errors, "errors": errors, "fileCount": len(manifest.get("files") or {})}


def _zip_datetime() -> tuple[int, int, int, int, int, int]:
    return (2026, 8, 26, 12, 0, 0)


def _write_zip(
    zip_path: Path,
    *,
    output: Path,
    selected: list[str],
    package_label: str,
    audience_note: str,
) -> dict[str, Any]:
    checksums = []
    for relative in selected:
        path = output / relative
        checksums.append(f"{_sha256(path)}  {relative}")
    generated = {
        "_PACKAGE-README.md": (
            f"# {package_label}\n\n{audience_note}\n\n"
            "只修改分配给你的 JSONL 中允许编辑的字段；不要修改 manifest。"
            "完成后保留文件名并回传 JSONL 与同名 manifest。\n"
        ).encode(),
        "_ORIGINAL-SHA256SUMS": ("\n".join(checksums) + "\n").encode("utf-8"),
    }
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(generated.items()):
            info = zipfile.ZipInfo(f"{package_label}/{name}", _zip_datetime())
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
        for relative in sorted(selected):
            path = output / relative
            info = zipfile.ZipInfo(f"{package_label}/{relative}", _zip_datetime())
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return {"path": _relative(zip_path), "bytes": zip_path.stat().st_size, "sha256": _sha256(zip_path)}


def _build_zips(output: Path) -> dict[str, Any]:
    all_files = [
        path.relative_to(output).as_posix()
        for path in sorted(item for item in output.rglob("*") if item.is_file())
    ]
    label_common = [
        "01-label-policy-v2.1/customer-service-taxonomy-contract-v2.1.json",
        "01-label-policy-v2.1/标注方法.md",
    ]
    label_reviewer_a = label_common + [
        "01-label-policy-v2.1/reviewer-a.open.jsonl",
        "01-label-policy-v2.1/reviewer-a.open.jsonl.manifest.json",
    ]
    label_reviewer_b = label_common + [
        "01-label-policy-v2.1/reviewer-b.open.jsonl",
        "01-label-policy-v2.1/reviewer-b.open.jsonl.manifest.json",
    ]
    answer_reviewer_a = [
        "02-answer-quality-v43/标注方法.md",
        "02-answer-quality-v43/reviewer-a.open.jsonl",
        "02-answer-quality-v43/reviewer-a.open.jsonl.manifest.json",
    ]
    answer_reviewer_b = [
        "02-answer-quality-v43/标注方法.md",
        "02-answer-quality-v43/reviewer-b.open.jsonl",
        "02-answer-quality-v43/reviewer-b.open.jsonl.manifest.json",
    ]
    provenance = [
        "03-provenance-independent-reaudit/independent-reaudit.open.jsonl",
        "03-provenance-independent-reaudit/independent-reaudit.open.jsonl.manifest.json",
        "03-provenance-independent-reaudit/independent-reaudit-custody-attestation.template.json",
        "03-provenance-independent-reaudit/frozen-historical-guideline-v1.md",
        "03-provenance-independent-reaudit/复核方法.md",
    ]
    packages = {
        "coordinator": _write_zip(
            DELIVERY_ROOT / "AI-Shop-human-review-coordinator-20260826.zip",
            output=output,
            selected=all_files,
            package_label="AI-Shop-human-review-coordinator-20260826",
            audience_note="协调人母包：同时含 A/B，禁止原样发给单个 reviewer。",
        ),
        "labelPolicyReviewerA": _write_zip(
            DELIVERY_ROOT / "AI-Shop-label-policy-reviewer-a-20260826.zip",
            output=output,
            selected=label_reviewer_a,
            package_label="AI-Shop-label-policy-reviewer-a-20260826",
            audience_note=(
                "仅供标签政策 reviewer-a；在 seal 前不得查看 reviewer-b、旧 gold、"
                "任何模型输出或仲裁上下文。"
            ),
        ),
        "labelPolicyReviewerB": _write_zip(
            DELIVERY_ROOT / "AI-Shop-label-policy-reviewer-b-20260826.zip",
            output=output,
            selected=label_reviewer_b,
            package_label="AI-Shop-label-policy-reviewer-b-20260826",
            audience_note=(
                "仅供标签政策 reviewer-b；在 seal 前不得查看 reviewer-a、旧 gold、"
                "任何模型输出或仲裁上下文。"
            ),
        ),
        "answerQualityReviewerA": _write_zip(
            DELIVERY_ROOT / "AI-Shop-answer-quality-reviewer-a-20260826.zip",
            output=output,
            selected=answer_reviewer_a,
            package_label="AI-Shop-answer-quality-reviewer-a-20260826",
            audience_note=(
                "仅供答案质量 reviewer-a；不得查看 reviewer-b、旧答案标签、"
                "模型自评或仲裁上下文。"
            ),
        ),
        "answerQualityReviewerB": _write_zip(
            DELIVERY_ROOT / "AI-Shop-answer-quality-reviewer-b-20260826.zip",
            output=output,
            selected=answer_reviewer_b,
            package_label="AI-Shop-answer-quality-reviewer-b-20260826",
            audience_note=(
                "仅供答案质量 reviewer-b；不得查看 reviewer-a、旧答案标签、"
                "模型自评或仲裁上下文。"
            ),
        ),
        "provenanceIndependentReviewer": _write_zip(
            DELIVERY_ROOT / "AI-Shop-provenance-independent-reviewer-20260826.zip",
            output=output,
            selected=provenance,
            package_label="AI-Shop-provenance-independent-reviewer-20260826",
            audience_note="仅供来源独立复核者；不得接触历史标签、旧评审、v2.1 新政策或模型输出。",
        ),
    }
    _write_json(
        DELIVERY_ROOT / "DELIVERY-MANIFEST-20260826.json",
        {
            "schemaVersion": "aishop-human-review-delivery-manifest/v1",
            "packageId": PACKAGE_NAME,
            "status": "OPEN_HUMAN_WORK_PENDING",
            "packages": packages,
        },
    )
    return packages


def build(output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite handoff package: {output}")
    for path in (
        DELIVERY_ROOT / "AI-Shop-human-review-coordinator-20260826.zip",
        DELIVERY_ROOT / "AI-Shop-label-policy-reviewer-a-20260826.zip",
        DELIVERY_ROOT / "AI-Shop-label-policy-reviewer-b-20260826.zip",
        DELIVERY_ROOT / "AI-Shop-answer-quality-reviewer-a-20260826.zip",
        DELIVERY_ROOT / "AI-Shop-answer-quality-reviewer-b-20260826.zip",
        DELIVERY_ROOT / "AI-Shop-provenance-independent-reviewer-20260826.zip",
        DELIVERY_ROOT / "DELIVERY-MANIFEST-20260826.json",
    ):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite delivery artifact: {path}")
    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    output.mkdir(parents=True)
    _write_text(output / "README.md", _main_readme())
    _copy_documents(output)
    _write_text(
        output / "05-external-unseen-not-ready/README.md",
        _external_status_note(),
    )
    _build_label_policy_sheets(output, created_at=created_at)
    _build_answer_sheets(output)
    _build_provenance_reaudit(output)
    manifest = _build_package_manifest(output, created_at=created_at)
    verification = _verify_package(output)
    if not verification["valid"]:
        raise RuntimeError(f"handoff package verification failed: {verification['errors']}")
    packages = _build_zips(output)
    return {
        "status": manifest["lifecycle"],
        "output": _relative(output),
        "verification": verification,
        "packages": packages,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.verify:
        result = _verify_package(args.verify.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["valid"] else 1
    result = build(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
