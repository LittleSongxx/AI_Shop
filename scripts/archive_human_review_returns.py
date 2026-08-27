#!/usr/bin/env python3
"""Archive the exact round-one human-review returns before further processing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_FILES = frozenset(
    {
        "01-label-policy-v2.1/reviewer-a.open.jsonl",
        "01-label-policy-v2.1/reviewer-a.open.jsonl.manifest.json",
        "01-label-policy-v2.1/reviewer-b.open.jsonl",
        "01-label-policy-v2.1/reviewer-b.open.jsonl.manifest.json",
        "02-answer-quality-v43/reviewer-a.open.jsonl",
        "02-answer-quality-v43/reviewer-a.open.jsonl.manifest.json",
        "02-answer-quality-v43/reviewer-b.open.jsonl",
        "02-answer-quality-v43/reviewer-b.open.jsonl.manifest.json",
        "independent-reaudit.open.jsonl",
        "independent-reaudit.open.jsonl.manifest.json",
        "independent-reaudit-custody-attestation.template.json",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _inventory(root: Path, *, exclude: set[str] | None = None) -> dict[str, dict[str, Any]]:
    excluded = exclude or set()
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def archive_returns(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite return archive: {output}")
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    }
    if actual != EXPECTED_FILES:
        raise ValueError(
            "human-review return inventory differs; "
            f"missing={sorted(EXPECTED_FILES - actual)}, extra={sorted(actual - EXPECTED_FILES)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        returns = staging / "returns"
        for relative in sorted(EXPECTED_FILES):
            destination = returns / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
            if _sha256(source / relative) != _sha256(destination):
                raise ValueError(f"return copy hash mismatch: {relative}")
        created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        audit = {
            "schemaVersion": "aishop-human-review-return-intake-audit/v1",
            "artifactId": output.name,
            "createdAt": created_at,
            "status": "EXACT_RETURNS_ARCHIVED_PROCESSING_REQUIRED",
            "sourceDirectory": str(source),
            "returnFileCount": len(EXPECTED_FILES),
            "sourceBytesPreserved": True,
            "sourceDirectoryModified": False,
            "notes": [
                "This archive preserves the exact returned bytes before sealing or scoring.",
                "Archival does not establish that reviewer identities are human or independent.",
                "Label agreement, custody attestation, and adjudication remain separate controls.",
            ],
            "returns": _inventory(returns),
        }
        _write_json(staging / "intake-audit.json", audit)
        _write_text(
            staging / "README.md",
            "# Human-review round-one returns — exact intake archive\n\n"
            "`returns/` is an exact byte copy of the user-provided `holdout/` directory. "
            "It is not edited during validation, sealing, comparison, or adjudication.\n\n"
            "This archive proves file custody only. Reviewer humanity/independence and label "
            "quality are evaluated separately and fail closed when evidence is incomplete.\n",
        )
        manifest = {
            "schemaVersion": "aishop-human-review-return-archive/v1",
            "artifactId": output.name,
            "createdAt": created_at,
            "status": audit["status"],
            "readOnly": True,
            "returnFileCount": len(EXPECTED_FILES),
            "files": _inventory(
                staging, exclude={"evidence-manifest.json", "SHA256SUMS"}
            ),
        }
        _write_json(staging / "evidence-manifest.json", manifest)
        sums = "".join(
            f"{_sha256(path)}  {path.relative_to(staging).as_posix()}\n"
            for path in sorted(staging.rglob("*"))
            if path.is_file() and path.name != "SHA256SUMS"
        )
        _write_text(staging / "SHA256SUMS", sums)
        for path in staging.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o444)
        staging.replace(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "valid": True,
        "output": str(output),
        "returnFileCount": len(EXPECTED_FILES),
        "sha256SumsSha256": _sha256(output / "SHA256SUMS"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive_returns(args.source, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
