"""Immutable run artifacts, claims and append-only diagnostic events."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class EvidenceError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EvidenceStore:
    """Own the run directory without overwriting completed artifacts."""

    def __init__(self, root: Path, *, suite: str, run_id: str) -> None:
        self.root = root / suite / run_id
        self.suite = suite
        self.run_id = run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise EvidenceError("artifact name must be a single relative filename")
        return self.root / name

    def write_json(self, name: str, payload: Any, *, overwrite: bool = False) -> Path:
        path = self.path(name)
        data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if not overwrite:
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                )
            except FileExistsError:
                raise EvidenceError(
                    f"refusing to overwrite immutable artifact: {path.name}"
                ) from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            return path
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        os.replace(temporary, path)
        return path

    def append_event(self, *, stage: str, status: str, failure_class: str = "NONE", **details: Any) -> Path:
        path = self.path("events.jsonl")
        event = {
            "schemaVersion": 1,
            "suite": self.suite,
            "runId": self.run_id,
            "recordedAt": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "status": status,
            "failureClass": failure_class,
            **details,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def claim_fresh(self, lock_path: Path, *, dataset_sha256: str) -> dict[str, Any]:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        claim = {
            "schemaVersion": 1,
            "suite": self.suite,
            "runId": self.run_id,
            "datasetSha256": dataset_sha256,
            "claimedAt": datetime.now(timezone.utc).isoformat(),
            "policy": "ONE_SHOT_FAIL_RETAINED",
        }
        temporary = lock_path.with_name(
            f".{lock_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(claim, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, lock_path)
            except FileExistsError:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
                if (
                    existing.get("suite") != self.suite
                    or existing.get("runId") != self.run_id
                    or existing.get("datasetSha256") != dataset_sha256
                ):
                    raise EvidenceError(
                        "fresh data is already claimed by another retained run"
                    ) from None
                raise EvidenceError(
                    "fresh data was already claimed by this run; formal execution is one-shot"
                ) from None
            return claim
        finally:
            temporary.unlink(missing_ok=True)

    def manifest(self, *, required: Iterable[Path], status: str) -> dict[str, Any]:
        artifacts = {}
        for path in required:
            if not path.is_file():
                raise EvidenceError(f"missing required artifact: {path}")
            artifacts[str(path.relative_to(self.root))] = _sha256(path)
        event_path = self.root / "events.jsonl"
        if event_path.is_file():
            artifacts["events.jsonl"] = _sha256(event_path)
        payload = {
            "schemaVersion": 1,
            "suite": self.suite,
            "runId": self.run_id,
            "status": status,
            "artifacts": artifacts,
        }
        self.write_json("run-manifest.json", payload)
        return payload
