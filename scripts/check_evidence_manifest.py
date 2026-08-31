#!/usr/bin/env python3
"""Validate the compact recruiter-facing evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "evidence" / "manifest.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_BOUNDARIES = {
    "realUser": False,
    "productionSlo": False,
    "finalUnseen": False,
    "multiTenant": False,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    evidence_root = (ROOT / "docs" / "evidence").resolve()
    if not path.is_relative_to(evidence_root):
        raise ValueError(f"evidence path escapes docs/evidence: {relative}")
    return path


def validate_manifest(path: Path = DEFAULT_MANIFEST) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"manifest is missing: {path.relative_to(ROOT)}"]
    try:
        manifest: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest is invalid JSON: {exc}"]
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    if manifest.get("schemaVersion") != "aishop-portfolio-evidence/v1":
        errors.append("manifest schemaVersion is invalid")
    if not COMMIT_RE.fullmatch(str(manifest.get("evaluatedCommit") or "")):
        errors.append("evaluatedCommit must be a full Git SHA")
    if manifest.get("worktreeDirty") is not False:
        errors.append("worktreeDirty must be false")
    boundaries = manifest.get("boundaries")
    if not isinstance(boundaries, dict):
        errors.append("boundaries must be an object")
    else:
        for key, expected in REQUIRED_BOUNDARIES.items():
            if boundaries.get(key) is not expected:
                errors.append(f"boundary {key} must be {str(expected).lower()}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, descriptor in enumerate(artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(descriptor, dict):
            errors.append(f"{label} must be an object")
            continue
        relative = str(descriptor.get("path") or "")
        if not relative or relative in seen:
            errors.append(f"{label} path is empty or duplicated")
            continue
        seen.add(relative)
        try:
            artifact = _resolve(relative)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not artifact.is_file():
            errors.append(f"artifact is missing: {relative}")
            continue
        expected_hash = str(descriptor.get("sha256") or "")
        if not SHA256_RE.fullmatch(expected_hash) or _sha256(artifact) != expected_hash:
            errors.append(f"artifact hash differs: {relative}")
        if descriptor.get("bytes") != artifact.stat().st_size:
            errors.append(f"artifact byte count differs: {relative}")
        if not str(descriptor.get("role") or "").strip():
            errors.append(f"artifact role is missing: {relative}")
    return errors


def validate_current_binding(path: Path = DEFAULT_MANIFEST) -> list[str]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    evaluated = str(manifest.get("evaluatedCommit") or "")
    if not COMMIT_RE.fullmatch(evaluated):
        return ["cannot verify an invalid evaluatedCommit"]

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            check=False,
            text=True,
        )

    head_result = git("rev-parse", "HEAD")
    if head_result.returncode:
        return ["cannot resolve repository HEAD"]
    head = head_result.stdout.strip()
    if evaluated != head:
        ancestry = git("merge-base", "--is-ancestor", evaluated, head)
        changed = git("diff", "--name-only", evaluated, head)
        paths = [line.strip() for line in changed.stdout.splitlines() if line.strip()]
        if ancestry.returncode or changed.returncode or not paths:
            return ["evaluatedCommit is not the current source ancestor"]
        outside = [path for path in paths if not path.startswith("docs/evidence/")]
        if outside:
            return [f"HEAD contains unevaluated source changes: {outside[0]}"]
    status = git("status", "--porcelain", "--untracked-files=normal")
    if status.returncode or status.stdout.strip():
        return ["worktree is dirty while evidence claims worktreeDirty=false"]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--require-current", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    errors = validate_manifest(manifest_path)
    if not errors and args.require_current:
        errors.extend(validate_current_binding(manifest_path))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("Evidence manifest OK")


if __name__ == "__main__":
    main()
