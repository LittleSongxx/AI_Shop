#!/usr/bin/env python3
"""Build role-isolated follow-up packages from sealed round-one reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite follow-up artifact: {path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _copy(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite follow-up artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _write_sums(root: Path) -> Path:
    output = root / "_ORIGINAL-SHA256SUMS"
    content = "".join(
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n"
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != output
    )
    _write_text(output, content)
    return output


def _zip_directory(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite follow-up ZIP: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary ZIP already exists: {temporary}")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, (Path(source.name) / path.relative_to(source)).as_posix())
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"corrupt follow-up ZIP member: {bad}")
    return {
        "path": str(destination),
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
    }


def _adjudicator_attestation(
    *, task_id: str, reviewers: list[str], template_sha256: str
) -> dict[str, Any]:
    return {
        "schemaVersion": "aishop-human-adjudicator-attestation/v1",
        "taskId": task_id,
        "sourceReviewerIds": reviewers,
        "adjudicationTemplateSha256AtExport": template_sha256,
        "adjudicatorId": None,
        "adjudicatorIdentity": None,
        "adjudicatorIsHuman": None,
        "independentOfSourceReviewers": None,
        "independentOfDatasetAndModelDevelopment": None,
        "generativeAiProducedOrSuggestedFinalLabels": None,
        "completedAdjudicationSha256": None,
        "attestedAt": None,
        "signaturesOrExternalReferences": [],
        "status": "TEMPLATE_NOT_EVIDENCE",
    }


def _reviewer_attestation(
    *, task_id: str, reviewer_id: str, returned_sha256: str, hidden_materials: list[str]
) -> dict[str, Any]:
    return {
        "schemaVersion": "aishop-human-reviewer-independence-attestation/v1",
        "taskId": task_id,
        "reviewerId": reviewer_id,
        "returnedSheetSha256": returned_sha256,
        "reviewerIdentity": None,
        "reviewerIsHuman": None,
        "workedIndependentlyFromOtherReviewer": None,
        "independentOfDatasetAndModelDevelopment": None,
        "didNotViewHiddenMaterialsBeforeCompleting": None,
        "hiddenMaterials": hidden_materials,
        "generativeAiProducedOrSuggestedLabels": None,
        "custodianIdentity": None,
        "attestedAt": None,
        "signaturesOrExternalReferences": [],
        "status": "TEMPLATE_NOT_EVIDENCE",
    }


def build_followup(repo_root: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    agent = repo_root / "AI_Shop-backend/AI_Shop-agent"
    delivery = repo_root / "deliverables/human-review"
    label_pending = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-human-v2-label-policy-review-pending-adjudication-20260827"
    )
    answer_pending = (
        agent
        / "evaluation-evidence/benchmarks/customer-service/"
        "customer-service-http-v43-answer-review-pending-adjudication-20260827"
    )
    label_dir = delivery / "AI-Shop-label-policy-adjudicator-c-20260827"
    answer_dir = delivery / "AI-Shop-answer-quality-adjudicator-c-20260827"
    provenance_dir = delivery / "AI-Shop-provenance-full60-followup-20260827"
    attestation_dir = delivery / "AI-Shop-round1-reviewer-attestations-20260827"
    for root in (label_dir, answer_dir):
        actual = {path.name for path in root.iterdir()} if root.is_dir() else set()
        if actual != {"adjudication.open.jsonl"}:
            raise ValueError(f"unexpected adjudication staging inventory: {root}: {sorted(actual)}")
    if attestation_dir.exists():
        raise FileExistsError(f"refusing to overwrite follow-up directory: {attestation_dir}")
    attestation_dir.mkdir(parents=True)

    label_agreement = json.loads((label_pending / "agreement.json").read_text(encoding="utf-8"))
    answer_agreement = json.loads((answer_pending / "agreement.json").read_text(encoding="utf-8"))
    if label_agreement.get("disagreementCaseCount") != 5:
        raise ValueError("label-policy disagreement count is not the sealed 5-case set")
    if answer_agreement.get("disagreementCaseCount") != 3:
        raise ValueError("answer-quality disagreement count is not the sealed 3-case set")

    _copy(label_pending / "adjudication-needed.md", label_dir / "仲裁说明.md")
    _copy(
        label_pending / "taxonomy-contract-v2.1.json",
        label_dir / "customer-service-taxonomy-contract-v2.1.json",
    )
    _write_json(
        label_dir / "adjudicator-attestation.template.json",
        _adjudicator_attestation(
            task_id="customer-service-label-policy-v2.1-adjudication-20260827",
            reviewers=[
                label_agreement["reviewA"]["reviewerId"],
                label_agreement["reviewB"]["reviewerId"],
            ],
            template_sha256=_sha256(label_dir / "adjudication.open.jsonl"),
        ),
    )
    _write_text(
        label_dir / "_PACKAGE-README.md",
        "填写 `adjudication.open.jsonl` 的 `finalLabels`、`adjudicator`、`reason`，"
        "同时填写 `adjudicator-attestation.template.json`。两文件原名返回。\n",
    )
    _write_sums(label_dir)

    _copy(answer_pending / "adjudication-needed.md", answer_dir / "仲裁说明.md")
    _copy(
        repo_root
        / "deliverables/human-review/AI-Shop-human-review-round1-20260826/"
        "02-answer-quality-v43/标注方法.md",
        answer_dir / "标注方法.md",
    )
    _write_json(
        answer_dir / "adjudicator-attestation.template.json",
        _adjudicator_attestation(
            task_id="customer-service-http-v43-answer-quality-adjudication-20260827",
            reviewers=[
                answer_agreement["reviewA"]["reviewerId"],
                answer_agreement["reviewB"]["reviewerId"],
            ],
            template_sha256=_sha256(answer_dir / "adjudication.open.jsonl"),
        ),
    )
    _write_text(
        answer_dir / "_PACKAGE-README.md",
        "填写 `adjudication.open.jsonl` 的 `finalLabels`、`adjudicator`、`reason`，"
        "同时填写 `adjudicator-attestation.template.json`。两文件原名返回。\n",
    )
    _write_sums(answer_dir)

    hidden_by_task = {
        "label-policy-v2.1": [
            "current immutable gold",
            "model/rule predictions",
            "the other reviewer's sheet before completion",
            "adjudication context before both sheets were returned",
        ],
        "answer-quality-v43": [
            "expected labels and automated quality judgments",
            "the other reviewer's sheet before completion",
            "adjudication decisions",
        ],
    }
    returns = {
        "label-policy-reviewer-a": _sha256(
            repo_root / "holdout/01-label-policy-v2.1/reviewer-a.open.jsonl"
        ),
        "label-policy-reviewer-b": _sha256(
            repo_root / "holdout/01-label-policy-v2.1/reviewer-b.open.jsonl"
        ),
        "reviewer-a": _sha256(
            repo_root / "holdout/02-answer-quality-v43/reviewer-a.open.jsonl"
        ),
        "reviewer-b": _sha256(
            repo_root / "holdout/02-answer-quality-v43/reviewer-b.open.jsonl"
        ),
    }
    for task_id, reviewer_id in (
        ("label-policy-v2.1", "label-policy-reviewer-a"),
        ("label-policy-v2.1", "label-policy-reviewer-b"),
        ("answer-quality-v43", "reviewer-a"),
        ("answer-quality-v43", "reviewer-b"),
    ):
        _write_json(
            attestation_dir / f"{task_id}-{reviewer_id}.attestation.template.json",
            _reviewer_attestation(
                task_id=task_id,
                reviewer_id=reviewer_id,
                returned_sha256=returns[reviewer_id],
                hidden_materials=hidden_by_task[task_id],
            ),
        )
    _write_text(
        attestation_dir / "README.md",
        "将四份模板分别发给对应原 reviewer，如实填写后原名返回。A/B 必须是不同真人；"
        "若标签由生成式 AI 产生或建议，必须如实填写，且不能作为纯人工金标证据。\n",
    )
    _write_sums(attestation_dir)

    packages = {
        "labelPolicyAdjudicator": _zip_directory(
            label_dir, delivery / f"{label_dir.name}.zip"
        ),
        "answerQualityAdjudicator": _zip_directory(
            answer_dir, delivery / f"{answer_dir.name}.zip"
        ),
        "round1ReviewerAttestations": _zip_directory(
            attestation_dir, delivery / f"{attestation_dir.name}.zip"
        ),
        "provenanceFull60Followup": _zip_directory(
            provenance_dir, delivery / f"{provenance_dir.name}.zip"
        ),
    }
    created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    manifest_path = delivery / "FOLLOWUP-DELIVERY-MANIFEST-20260827.json"
    _write_json(
        manifest_path,
        {
            "schemaVersion": "aishop-human-review-followup-delivery/v1",
            "createdAt": created_at,
            "status": "OPEN_HUMAN_FOLLOWUP_REQUIRED",
            "packages": packages,
            "expectedReturns": {
                "labelPolicyAdjudicator": [
                    "adjudication.open.jsonl",
                    "adjudicator-attestation.template.json",
                ],
                "answerQualityAdjudicator": [
                    "adjudication.open.jsonl",
                    "adjudicator-attestation.template.json",
                ],
                "round1ReviewerAttestations": [
                    "four completed *.attestation.template.json files"
                ],
                "provenanceFull60Followup": [
                    "exactly one completed review JSONL and its original manifest",
                    "custody-attestation-v2.template.json",
                ],
            },
        },
    )
    return {
        "valid": True,
        "status": "OPEN_HUMAN_FOLLOWUP_REQUIRED",
        "manifest": str(manifest_path),
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_followup(args.repo_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
