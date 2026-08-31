import json
import shutil
from pathlib import Path

import pytest

from evaluation.core.io import sha256_file
from evaluation.customer_service_human_data import (
    ANSWER_LABELS_MANIFEST_PATH,
    ANSWER_LABELS_PATH,
    ANSWER_SOURCE_REPORT_PATH,
    HUMAN_GOLD_MANIFEST_PATH,
    HUMAN_GOLD_PATH,
    CustomerServiceHumanDataError,
    load_adjudicated_answer_labels,
    load_human_adjudicated_gold,
)


def _copy_with_manifest(source: Path, manifest: Path, tmp_path: Path):
    target = tmp_path / source.name
    target_manifest = tmp_path / manifest.name
    shutil.copyfile(source, target)
    shutil.copyfile(manifest, target_manifest)
    return target, target_manifest


def test_canonical_human_gold_is_hash_pinned_and_reusable():
    rows = load_human_adjudicated_gold()
    assert len(rows) == 60
    assert sha256_file(HUMAN_GOLD_PATH) == json.loads(
        HUMAN_GOLD_MANIFEST_PATH.read_text(encoding="utf-8")
    )["datasetSha256"]


def test_changed_human_gold_is_rejected(tmp_path):
    target, manifest = _copy_with_manifest(
        HUMAN_GOLD_PATH, HUMAN_GOLD_MANIFEST_PATH, tmp_path
    )
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CustomerServiceHumanDataError, match="SHA-256 differs"):
        load_human_adjudicated_gold(target, manifest_path=manifest)


@pytest.mark.archived_evidence
def test_answer_labels_require_exact_frozen_source_and_answer_hashes():
    labels = load_adjudicated_answer_labels()
    assert len(labels) == 60
    assert all(len(row["answerSha256"]) == 64 for row in labels.values())


def test_changed_answer_labels_are_rejected(tmp_path):
    labels_path, labels_manifest = _copy_with_manifest(
        ANSWER_LABELS_PATH, ANSWER_LABELS_MANIFEST_PATH, tmp_path
    )
    rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["labels"]["answerCorrect"] = not rows[0]["labels"]["answerCorrect"]
    labels_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CustomerServiceHumanDataError, match="SHA-256 differs"):
        load_adjudicated_answer_labels(
            labels_path, manifest_path=labels_manifest, source_report_path=ANSWER_SOURCE_REPORT_PATH
        )


@pytest.mark.archived_evidence
def test_changed_source_report_is_rejected(tmp_path):
    labels_path, labels_manifest = _copy_with_manifest(
        ANSWER_LABELS_PATH, ANSWER_LABELS_MANIFEST_PATH, tmp_path
    )
    source_path = tmp_path / ANSWER_SOURCE_REPORT_PATH.name
    shutil.copyfile(ANSWER_SOURCE_REPORT_PATH, source_path)
    source_path.write_text(source_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CustomerServiceHumanDataError, match="SHA-256 differs"):
        load_adjudicated_answer_labels(
            labels_path, manifest_path=labels_manifest, source_report_path=source_path
        )


@pytest.mark.archived_evidence
def test_duplicate_case_id_is_rejected_after_hash_manifest_is_updated(tmp_path):
    labels_path, labels_manifest = _copy_with_manifest(
        ANSWER_LABELS_PATH, ANSWER_LABELS_MANIFEST_PATH, tmp_path
    )
    rows = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines()]
    rows[1]["caseId"] = rows[0]["caseId"]
    labels_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )
    manifest_data = json.loads(labels_manifest.read_text(encoding="utf-8"))
    manifest_data["labelsSha256"] = sha256_file(labels_path)
    labels_manifest.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(CustomerServiceHumanDataError, match="duplicated"):
        load_adjudicated_answer_labels(
            labels_path, manifest_path=labels_manifest, source_report_path=ANSWER_SOURCE_REPORT_PATH
        )
