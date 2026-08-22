from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

AGENT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = AGENT_ROOT.parents[1]
EVALUATION_ROOT = AGENT_ROOT / "evaluation"
RUNS_ROOT = EVALUATION_ROOT / ".runs"
STATE_ROOT = EVALUATION_ROOT / ".state"
EVIDENCE_ROOT = AGENT_ROOT / "evaluation-evidence" / "current"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, value: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    if not overwrite and path.exists():
        temporary.unlink(missing_ok=True)
        raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
    temporary.replace(path)


def atomic_write_text(path: Path, value: str, *, overwrite: bool = True) -> None:
    atomic_write_bytes(path, value.encode("utf-8"), overwrite=overwrite)


def atomic_write_json(path: Path, value: Any, *, overwrite: bool = True) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, rendered, overwrite=overwrite)


def atomic_write_jsonl(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    overwrite: bool = True,
) -> None:
    rendered = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    atomic_write_text(path, rendered, overwrite=overwrite)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: each JSONL row must be an object")
        rows.append(row)
    return rows


def relative_to_repo(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def hash_named_files(files: Iterable[Path], *, root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for path in sorted({item.resolve() for item in files}, key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(root.resolve()).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()
