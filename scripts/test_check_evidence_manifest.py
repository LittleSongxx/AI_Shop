from __future__ import annotations

import hashlib
import json
from pathlib import Path

import check_evidence_manifest as checker


def _manifest(root: Path, artifact: Path) -> dict:
    return {
        "schemaVersion": "aishop-portfolio-evidence/v1",
        "evaluatedCommit": "a" * 40,
        "worktreeDirty": False,
        "boundaries": {
            "realUser": False,
            "productionSlo": False,
            "finalUnseen": False,
            "multiTenant": False,
        },
        "artifacts": [
            {
                "path": artifact.relative_to(root).as_posix(),
                "role": "scorecard",
                "bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
    }


def test_compact_manifest_accepts_hash_bound_artifact(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    evidence = root / "docs/evidence"
    evidence.mkdir(parents=True)
    artifact = evidence / "scorecard.json"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = evidence / "manifest.json"
    manifest.write_text(
        json.dumps(_manifest(root, artifact)), encoding="utf-8"
    )
    monkeypatch.setattr(checker, "ROOT", root)

    assert checker.validate_manifest(manifest) == []


def test_compact_manifest_detects_tampering(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    evidence = root / "docs/evidence"
    evidence.mkdir(parents=True)
    artifact = evidence / "scorecard.json"
    artifact.write_text("{}\n", encoding="utf-8")
    manifest = evidence / "manifest.json"
    manifest.write_text(
        json.dumps(_manifest(root, artifact)), encoding="utf-8"
    )
    artifact.write_text('{"tampered":true}\n', encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", root)

    assert any("hash differs" in error for error in checker.validate_manifest(manifest))


def test_compact_manifest_rejects_dirty_or_overclaimed_run(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path
    evidence = root / "docs/evidence"
    evidence.mkdir(parents=True)
    artifact = evidence / "scorecard.json"
    artifact.write_text("{}\n", encoding="utf-8")
    payload = _manifest(root, artifact)
    payload["worktreeDirty"] = True
    payload["boundaries"]["realUser"] = True
    manifest = evidence / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(checker, "ROOT", root)

    errors = checker.validate_manifest(manifest)
    assert "worktreeDirty must be false" in errors
    assert "boundary realUser must be false" in errors
