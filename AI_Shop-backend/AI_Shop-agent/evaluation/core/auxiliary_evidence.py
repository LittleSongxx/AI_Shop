"""Immutable evidence packages for diagnostics outside normal quality gates.

Fault-injection and repeated-Agent runs are valuable evidence, but they are not
part of the ordinary Search/RAG/Agent quality denominator.  This module gives
both diagnostics the same auditable package contract as a quality run:
redacted run files, a typed manifest, a report with an explicit denominator
boundary, and a SHA256SUMS inventory.  Packages are write-once and read-only
after verification so deleting a temporary ``.runs`` directory cannot erase
the diagnostic trail.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from evaluation.core.evidence import verify_evidence
from evaluation.core.io import (
    AGENT_ROOT,
    atomic_write_json,
    atomic_write_text,
    load_json,
    sha256_file,
    utc_now,
)

AUXILIARY_ROOT = AGENT_ROOT / "evaluation-evidence" / "benchmarks"
AUXILIARY_SCHEMA = "aishop-auxiliary-evidence/v1"
_KINDS = {"resilience", "repeated-agent"}


def _safe_id(value: str) -> str:
    text = str(value or "").strip()
    if not text or any(char in text for char in "/\\") or text in {".", ".."}:
        raise ValueError("auxiliary evidence ID must be a non-empty path-safe value")
    return text


def _root(kind: str, package_id: str) -> Path:
    if kind not in _KINDS:
        raise ValueError(f"unsupported auxiliary evidence kind: {kind!r}")
    return AUXILIARY_ROOT / kind / _safe_id(package_id)


def _inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"SHA256SUMS", "evidence-manifest.json"}
    }


def _sums(root: Path) -> str:
    values = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "SHA256SUMS"
    }
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(values.items()))


def _report(
    *,
    kind: str,
    package_id: str,
    source_run_id: str,
    source_digest: str,
    source_report: str,
    shadow_only: bool,
) -> str:
    title = "Fault-injection recovery matrix" if kind == "resilience" else "Repeated Agent pass^k"
    lines = [
        f"# AI Shop {title}",
        "",
        f"- Package: `{package_id}`",
        f"- Source run: `{source_run_id}`",
        f"- Source run SHA256SUMS: `{source_digest}`",
        f"- Scope: `{kind}` diagnostics",
        "- Normal Search/RAG/Agent quality denominator: **excluded**",
        f"- Shadow-only signal: **{'yes' if shadow_only else 'no'}**",
        "",
        "## Source run report",
        "",
        source_report.rstrip(),
        "",
        "## Evidence boundary",
        "",
        "- This package preserves diagnostic evidence and does not alter historical final scores.",
        "- Fault-injection outcomes are recovery-contract results, not normal quality passes.",
        "- pass^k is a repeated-task reliability estimate for this run and case set, not a production SLO.",
        "- Local timings, usage, and costs retain their original unknown/unpriced states.",
        "",
    ]
    return "\n".join(lines)


def write_auxiliary_evidence(
    run_root: Path,
    *,
    kind: str,
    package_id: str,
    shadow_only: bool = False,
) -> tuple[Path, str]:
    """Copy and seal one immutable diagnostic run.

    ``run_root`` must already pass the normal run verifier.  The copy is
    staged under the destination parent and atomically renamed, so a partial
    package can never be mistaken for a complete one.
    """

    run_root = run_root.resolve()
    package_id = _safe_id(package_id)
    destination = _root(kind, package_id)
    if destination.exists():
        raise FileExistsError(f"auxiliary evidence already exists: {destination}")
    source = verify_evidence(run_root)
    source_manifest = load_json(run_root / "evidence-manifest.json")
    source_run = source_manifest.get("run") or {}
    source_run_id = str(source_run.get("runId") or run_root.name)
    source_digest = str(source.get("sha256SumsSha256") or "")
    if not source_digest:
        raise ValueError("source run has no SHA256SUMS digest")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{package_id}-", dir=destination.parent))
    try:
        for path in sorted(run_root.rglob("*")):
            if not path.is_file():
                continue
            # The source run manifest is replaced by the typed auxiliary
            # manifest below; copying it first would violate write-once
            # semantics when the new manifest is created.
            if path.name in {"evidence-manifest.json", "SHA256SUMS"}:
                continue
            relative = path.relative_to(run_root)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        source_report = (run_root / "report.md").read_text(encoding="utf-8")
        atomic_write_text(
            staging / "report.md",
            _report(
                kind=kind,
                package_id=package_id,
                source_run_id=source_run_id,
                source_digest=source_digest,
                source_report=source_report,
                shadow_only=shadow_only,
            ),
            overwrite=True,
        )
        manifest = {
            "schemaVersion": AUXILIARY_SCHEMA,
            "kind": kind,
            "packageId": package_id,
            "createdAt": utc_now(),
            "sourceRunId": source_run_id,
            "sourceRunSha256SumsSha256": source_digest,
            "normalQualityDenominatorExcluded": True,
            "shadowOnly": bool(shadow_only),
            "files": _inventory(staging),
        }
        atomic_write_json(staging / "evidence-manifest.json", manifest, overwrite=False)
        atomic_write_text(staging / "SHA256SUMS", _sums(staging), overwrite=False)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        verify_auxiliary_evidence(staging)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    for path in destination.rglob("*"):
        if path.is_file():
            os.chmod(path, 0o444)
    return destination, sha256_file(destination / "SHA256SUMS")


def verify_auxiliary_evidence(root: Path) -> dict[str, Any]:
    """Verify package inventory, hashes, schema, and read-only status."""

    root = root.resolve()
    manifest_path = root / "evidence-manifest.json"
    sums_path = root / "SHA256SUMS"
    if not root.is_dir() or not manifest_path.is_file() or not sums_path.is_file():
        raise ValueError(f"invalid auxiliary evidence root: {root}")
    expected: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name or name in expected:
            raise ValueError(f"invalid auxiliary SHA256SUMS line: {line!r}")
        path = (root / name).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"auxiliary evidence path escapes package: {name}") from exc
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise ValueError("auxiliary evidence file set differs from SHA256SUMS")
    for name, digest in expected.items():
        if sha256_file(root / name) != digest:
            raise ValueError(f"auxiliary evidence hash mismatch: {name}")
    manifest = load_json(manifest_path)
    if manifest.get("schemaVersion") != AUXILIARY_SCHEMA:
        raise ValueError("auxiliary evidence schema is invalid")
    if manifest.get("kind") not in _KINDS:
        raise ValueError("auxiliary evidence kind is invalid")
    if not manifest.get("normalQualityDenominatorExcluded"):
        raise ValueError("auxiliary evidence must be excluded from normal quality denominator")
    inventory = _inventory(root)
    if manifest.get("files") != inventory:
        raise ValueError("auxiliary evidence inventory is stale")
    writable = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.stat().st_mode & 0o222
    ]
    if writable:
        raise ValueError(f"auxiliary evidence contains writable files: {writable}")
    return {
        "verified": True,
        "root": str(root),
        "kind": manifest.get("kind"),
        "packageId": manifest.get("packageId"),
        "sourceRunId": manifest.get("sourceRunId"),
        "sha256SumsSha256": sha256_file(sums_path),
    }
