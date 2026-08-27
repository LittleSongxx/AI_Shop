from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evaluation.core.io import sha256_file
from evaluation.source_freeze import create_source_freeze, verify_source_freeze


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_source_freeze_embeds_tracked_patch_and_hash_binds_untracked_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "source-freeze@example.invalid")
    _git(repo, "config", "user.name", "Source Freeze Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "baseline")

    tracked.write_text("after\n", encoding="utf-8")
    untracked = repo / "untracked.txt"
    untracked.write_text("hash me\n", encoding="utf-8")
    output = tmp_path / "source-freeze"
    result = create_source_freeze(
        output,
        freeze_id="source-freeze-test",
        purpose="unit-test dirty source binding",
        repo_root=repo,
        evaluation_source_fingerprint={"source": {"sha256": "a" * 64}},
        runtime_source_fingerprint={
            "scope": "test-runtime-source/v1",
            "sha256": "b" * 64,
            "fileCount": 1,
        },
    )

    assert result["valid"] is True
    assert result["trackedPatchBytes"] > 0
    assert b"after" in (output / "tracked.patch").read_bytes()
    inventory = (output / "untracked-files.json").read_text(encoding="utf-8")
    assert "untracked.txt" in inventory
    assert verify_source_freeze(output, repo_root=repo)["valid"] is True

    # The first production v1 freeze omitted the redundant inventory-level
    # fileCount while its descriptor and checksums still bound the same list.
    inventory_path = output / "untracked-files.json"
    manifest_path = output / "evidence-manifest.json"
    sums_path = output / "SHA256SUMS"
    for path in (inventory_path, manifest_path, sums_path):
        path.chmod(0o644)
    legacy_inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    legacy_inventory.pop("fileCount")
    inventory_path.write_text(
        json.dumps(legacy_inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    evidence_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_manifest["files"]["untracked-files.json"] = {
        "bytes": inventory_path.stat().st_size,
        "sha256": sha256_file(inventory_path),
    }
    manifest_path.write_text(
        json.dumps(evidence_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    package_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    sums_path.write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(output).as_posix()}\n"
            for path in package_files
        ),
        encoding="utf-8",
    )
    legacy = verify_source_freeze(output, repo_root=repo)
    assert legacy["valid"] is True
    assert legacy["untrackedInventoryCountDeclared"] is False

    untracked.write_text("changed later\n", encoding="utf-8")
    drifted = verify_source_freeze(output, repo_root=repo)
    assert drifted["valid"] is True
    assert drifted["untrackedInventoryChanged"] == ["untracked.txt"]
