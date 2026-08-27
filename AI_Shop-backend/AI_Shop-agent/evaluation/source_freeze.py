"""Capture and verify a dirty-worktree source baseline without leaking secrets.

The package binds tracked changes to a Git base commit and separately records
hashes for untracked regular files.  It intentionally does not copy untracked
files because they may include credentials or large runtime artifacts.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from evaluation.core.io import (
    REPO_ROOT,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    load_json,
    sha256_bytes,
    sha256_file,
)


class SourceFreezeError(ValueError):
    """Raised when a source-freeze package cannot reproduce its declaration."""


SOURCE_FREEZE_SCHEMA = "aishop-source-freeze/v1"
SOURCE_FREEZE_EVIDENCE_SCHEMA = "aishop-source-freeze-evidence/v1"
UNTRACKED_INVENTORY_SCHEMA = "aishop-source-freeze-untracked-inventory/v1"


def _git_output(arguments: Iterable[str], *, repo_root: Path) -> bytes:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", errors="replace").strip()
        raise SourceFreezeError(f"git command failed: {message}") from exc
    return result.stdout


def tracked_patch(*, repo_root: Path = REPO_ROOT, base: str = "HEAD") -> bytes:
    """Return the complete binary-safe tracked diff for ``base``."""

    return _git_output(
        ("diff", "--full-index", "--binary", base, "--"),
        repo_root=repo_root,
    )


def _git_text(arguments: Iterable[str], *, repo_root: Path) -> str:
    return _git_output(arguments, repo_root=repo_root).decode(
        "utf-8", errors="surrogateescape"
    )


def _untracked_inventory(*, repo_root: Path, freeze_id: str) -> dict[str, Any]:
    raw = _git_output(
        ("ls-files", "--others", "--exclude-standard", "-z"),
        repo_root=repo_root,
    )
    files: list[dict[str, Any]] = []
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        relative = encoded.decode("utf-8", errors="surrogateescape")
        path = repo_root / relative
        # Do not follow an untracked symlink outside the repository boundary.
        if not path.is_file() or path.is_symlink():
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schemaVersion": UNTRACKED_INVENTORY_SCHEMA,
        "freezeId": freeze_id,
        "fileCount": len(files),
        "files": files,
    }


def create_source_freeze(
    output_dir: Path,
    *,
    freeze_id: str,
    purpose: str,
    repo_root: Path = REPO_ROOT,
    evaluation_source_fingerprint: Mapping[str, Any] | None = None,
    runtime_source_fingerprint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an immutable dirty-worktree source baseline.

    Tracked bytes are embedded as a binary-safe patch. Untracked regular files
    are path/size/hash bound but are never copied into the package, which keeps
    runtime outputs and accidental credentials outside evidence.
    """

    normalized_id = str(freeze_id or "").strip()
    normalized_purpose = str(purpose or "").strip()
    if not normalized_id or not normalized_purpose:
        raise SourceFreezeError("freeze_id and purpose are required")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite source freeze: {output_dir}")

    repo_root = repo_root.resolve()
    base_commit = _git_text(("rev-parse", "HEAD"), repo_root=repo_root).strip()
    branch = _git_text(
        ("rev-parse", "--abbrev-ref", "HEAD"), repo_root=repo_root
    ).strip()
    status = _git_text(
        ("status", "--porcelain", "--untracked-files=all"), repo_root=repo_root
    )
    patch = tracked_patch(repo_root=repo_root, base=base_commit)
    changed_raw = _git_output(
        ("diff", "--name-only", "-z", base_commit, "--"), repo_root=repo_root
    )
    tracked_changed_count = sum(bool(value) for value in changed_raw.split(b"\0"))
    inventory = _untracked_inventory(repo_root=repo_root, freeze_id=normalized_id)

    if evaluation_source_fingerprint is None:
        from evaluation.core.fingerprints import source_fingerprint

        evaluation_source_fingerprint = source_fingerprint()
    if runtime_source_fingerprint is None:
        from app.services.runtime_identity import source_fingerprint as runtime_fingerprint

        runtime_source_fingerprint = runtime_fingerprint()

    patch_sha = sha256_bytes(patch)
    descriptor = {
        "schemaVersion": SOURCE_FREEZE_SCHEMA,
        "freezeId": normalized_id,
        "purpose": normalized_purpose,
        "lifecycle": "LOCAL_SOURCE_BASELINE_IMMUTABLE",
        "releaseGateEligible": False,
        "notModelQualityEvidence": True,
        "git": {
            "commit": base_commit,
            "branch": branch,
            "worktreeDirty": bool(status.strip()),
            "statusEntryCount": len(status.splitlines()),
            "trackedChangedFileCount": tracked_changed_count,
            "untrackedFileCount": inventory["fileCount"],
        },
        "trackedPatch": {
            "path": "tracked.patch",
            "bytes": len(patch),
            "sha256": patch_sha,
        },
        "untrackedInventory": {
            "path": "untracked-files.json",
            "fileCount": inventory["fileCount"],
            "note": "Hashes bind untracked regular files; their bytes are not embedded.",
        },
        "evaluationSourceFingerprint": dict(evaluation_source_fingerprint),
        "runtimeSourceFingerprint": dict(runtime_source_fingerprint),
        "limitations": [
            "This binds a dirty worktree and is not a Git commit or release.",
            "The tracked patch plus base commit reconstructs tracked changes; untracked files are hash-bound but not embedded.",
            "No metric or human-review result may inherit release eligibility from this package.",
        ],
    }
    readme = (
        f"# {normalized_id}\n\n"
        f"Purpose: {normalized_purpose}\n\n"
        "- `tracked.patch` is the complete binary-safe diff from the declared base commit.\n"
        "- `untracked-files.json` binds untracked regular files by path, size, and SHA-256 without copying their bytes.\n"
        "- This is a local source baseline, not model-quality or release evidence.\n"
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent)
    )
    try:
        atomic_write_text(staging / "README.md", readme, overwrite=False)
        atomic_write_json(staging / "source-freeze.json", descriptor, overwrite=False)
        atomic_write_bytes(staging / "tracked.patch", patch, overwrite=False)
        atomic_write_json(
            staging / "untracked-files.json", inventory, overwrite=False
        )
        evidence_manifest = {
            "schemaVersion": SOURCE_FREEZE_EVIDENCE_SCHEMA,
            "freezeId": normalized_id,
            "baseCommit": base_commit,
            "lifecycle": "LOCAL_SOURCE_BASELINE_IMMUTABLE",
            "releaseGateEligible": False,
            "trackedPatchSha256": patch_sha,
            "evaluationSourceSha256": (
                (evaluation_source_fingerprint.get("source") or {}).get("sha256")
            ),
            "runtimeSourceSha256": runtime_source_fingerprint.get("sha256"),
            "files": _manifest_files(staging),
        }
        _write_integrity_files(staging, evidence_manifest)
        verification = verify_source_freeze(staging, repo_root=repo_root)
        if verification.get("valid") is not True:
            raise SourceFreezeError("new source freeze failed verification")
        staging.rename(output_dir)
        for path in output_dir.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        return {
            **verify_source_freeze(output_dir, repo_root=repo_root),
            "package": str(output_dir),
            "freezeId": normalized_id,
            "baseCommit": base_commit,
            "trackedPatchBytes": len(patch),
            "trackedPatchSha256": patch_sha,
            "sha256SumsSha256": sha256_file(output_dir / "SHA256SUMS"),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _manifest_files(package_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name in {"SHA256SUMS", "evidence-manifest.json"}:
            continue
        relative = path.relative_to(package_dir).as_posix()
        result[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return result


def _write_integrity_files(package_dir: Path, manifest: Mapping[str, Any]) -> None:
    manifest_path = package_dir / "evidence-manifest.json"
    atomic_write_json(manifest_path, dict(manifest))
    paths = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [
        f"{sha256_file(path)}  {path.relative_to(package_dir).as_posix()}"
        for path in paths
    ]
    atomic_write_text(package_dir / "SHA256SUMS", "\n".join(lines) + "\n")


def repair_declared_tracked_patch(
    package_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Repair only a truncated patch when the live diff matches its declaration.

    This is intentionally fail-closed: the function refuses to update the
    package unless the current complete diff has exactly the byte count and
    SHA-256 already recorded by ``source-freeze.json``.  It therefore cannot
    silently move the baseline to newer source.
    """

    descriptor_path = package_dir / "source-freeze.json"
    descriptor = load_json(descriptor_path)
    if not isinstance(descriptor, Mapping):
        raise SourceFreezeError("source-freeze.json must be an object")
    declaration = descriptor.get("trackedPatch")
    if not isinstance(declaration, Mapping):
        raise SourceFreezeError("source-freeze.json has no trackedPatch declaration")
    base_commit = str((descriptor.get("git") or {}).get("commit") or "").strip()
    if not base_commit:
        raise SourceFreezeError("source-freeze.json has no base Git commit")

    patch = tracked_patch(repo_root=repo_root, base=base_commit)
    actual_sha = sha256_bytes(patch)
    actual_bytes = len(patch)
    if actual_sha != declaration.get("sha256") or actual_bytes != declaration.get("bytes"):
        raise SourceFreezeError(
            "live tracked diff does not match the frozen declaration; refusing repair"
        )

    patch_path = package_dir / str(declaration.get("path") or "tracked.patch")
    atomic_write_bytes(patch_path, patch)
    if sha256_file(patch_path) != actual_sha or patch_path.stat().st_size != actual_bytes:
        raise SourceFreezeError("repaired tracked patch failed its post-write integrity check")

    old_manifest = load_json(package_dir / "evidence-manifest.json")
    if not isinstance(old_manifest, Mapping):
        raise SourceFreezeError("evidence-manifest.json must be an object")
    evidence_manifest = {
        **dict(old_manifest),
        "trackedPatchSha256": actual_sha,
        "files": _manifest_files(package_dir),
    }
    _write_integrity_files(package_dir, evidence_manifest)
    return {
        "status": "REPAIRED_AND_VERIFIED",
        "package": str(package_dir),
        "baseCommit": base_commit,
        "trackedPatchBytes": actual_bytes,
        "trackedPatchSha256": actual_sha,
        "sha256SumsSha256": sha256_file(package_dir / "SHA256SUMS"),
    }


def verify_source_freeze(package_dir: Path, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Verify package checksums, declarations, and available untracked hashes."""

    descriptor = load_json(package_dir / "source-freeze.json")
    evidence = load_json(package_dir / "evidence-manifest.json")
    if not isinstance(descriptor, Mapping) or not isinstance(evidence, Mapping):
        raise SourceFreezeError("source-freeze package manifests must be objects")

    checksum_errors: list[str] = []
    expected_sums: dict[str, str] = {}
    for line in (package_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if (
            not separator
            or len(digest) != 64
            or not relative
            or relative in expected_sums
        ):
            checksum_errors.append(relative or line)
            continue
        expected_sums[relative] = digest
    actual_files = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected_sums) != actual_files:
        checksum_errors.append("<FILE_SET_MISMATCH>")
    for relative, digest in expected_sums.items():
        path = package_dir / relative
        if not path.is_file() or sha256_file(path) != digest:
            checksum_errors.append(relative)

    schema_valid = (
        descriptor.get("schemaVersion") == SOURCE_FREEZE_SCHEMA
        and evidence.get("schemaVersion") == SOURCE_FREEZE_EVIDENCE_SCHEMA
        and evidence.get("freezeId") == descriptor.get("freezeId")
    )

    declaration = descriptor.get("trackedPatch") or {}
    patch_path = package_dir / str(declaration.get("path") or "tracked.patch")
    declared_patch_ok = (
        patch_path.is_file()
        and patch_path.stat().st_size == declaration.get("bytes")
        and sha256_file(patch_path) == declaration.get("sha256")
        and evidence.get("trackedPatchSha256") == declaration.get("sha256")
    )

    untracked_errors: list[str] = []
    inventory_path = package_dir / str(
        (descriptor.get("untrackedInventory") or {}).get("path") or "untracked-files.json"
    )
    inventory = load_json(inventory_path)
    items = inventory.get("files") if isinstance(inventory, Mapping) else None
    # The first v1 package predates the redundant top-level ``fileCount`` in
    # untracked-files.json. Its descriptor already binds the count and the
    # package checksum binds the inventory bytes, so accept the missing field
    # for backward compatibility while still rejecting a conflicting value.
    inventory_file_count = (
        inventory.get("fileCount") if isinstance(inventory, Mapping) else None
    )
    inventory_valid = (
        isinstance(inventory, Mapping)
        and inventory.get("schemaVersion") == UNTRACKED_INVENTORY_SCHEMA
        and inventory.get("freezeId") == descriptor.get("freezeId")
        and isinstance(items, list)
        and inventory_file_count in {None, len(items)}
        and (descriptor.get("untrackedInventory") or {}).get("fileCount")
        == len(items)
    )
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                untracked_errors.append("<invalid-entry>")
                continue
            relative = str(item.get("path") or "")
            path = repo_root / relative
            # New work may legitimately move an untracked file.  Report it;
            # do not reinterpret the original inventory.
            if not path.is_file() or sha256_file(path) != item.get("sha256"):
                untracked_errors.append(relative)

    manifest_files_valid = evidence.get("files") == _manifest_files(package_dir)
    valid = (
        not checksum_errors
        and schema_valid
        and inventory_valid
        and manifest_files_valid
        and declared_patch_ok
    )
    return {
        "valid": valid,
        "status": "VERIFIED" if valid else "INVALID",
        "checksumErrors": checksum_errors,
        "schemaValid": schema_valid,
        "manifestFilesValid": manifest_files_valid,
        "untrackedInventoryValid": inventory_valid,
        "untrackedInventoryCountDeclared": inventory_file_count is not None,
        "trackedPatchDeclarationValid": declared_patch_ok,
        "untrackedInventoryChangedCount": len(untracked_errors),
        "untrackedInventoryChanged": untracked_errors,
        "note": (
            "Untracked inventory drift is reported but does not invalidate the embedded "
            "tracked patch; the inventory remains a historical hash declaration."
        ),
    }
