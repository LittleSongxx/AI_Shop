from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from evaluation.core.contracts import LifecycleError, Split
from evaluation.core.datasets import (
    canonical_dataset_sha256,
    parse_case,
    validate_final_against_known,
    validate_repository_datasets,
)
from evaluation.core.fingerprints import source_fingerprint, stable_fingerprint
from evaluation.core.io import (
    EVALUATION_ROOT,
    STATE_ROOT,
    atomic_write_bytes,
    atomic_write_json,
    load_json,
    load_jsonl,
    utc_now,
)

CONSUMED_FINAL_PATH = EVALUATION_ROOT / "datasets" / "locks" / "consumed-final.json"
FINAL_INPUTS_ROOT = EVALUATION_ROOT / "datasets" / "final-inputs"
_RELEASE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{5,95}$")


def _release_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not _RELEASE_RE.fullmatch(cleaned):
        raise LifecycleError(f"invalid release id: {cleaned!r}")
    return cleaned


def _release_root(release_id: str) -> Path:
    return STATE_ROOT / "releases" / _release_id(release_id)


def _state_path(release_id: str) -> Path:
    return _release_root(release_id) / "state.json"


def _load_state(release_id: str) -> dict[str, Any]:
    path = _state_path(release_id)
    if not path.is_file():
        raise LifecycleError(f"release {release_id!r} has not been frozen")
    value = load_json(path)
    if not isinstance(value, dict):
        raise LifecycleError(f"invalid lifecycle state for {release_id!r}")
    return value


def _registry() -> dict[str, Any]:
    if not CONSUMED_FINAL_PATH.is_file():
        return {
            "schemaVersion": "aishop-evaluation-final-registry/v2",
            "claims": [],
        }
    value = load_json(CONSUMED_FINAL_PATH)
    if value.get("schemaVersion") not in {
        "aishop-evaluation-final-registry/v2",
        "aishop-evaluation-final-registry/v3",
    }:
        raise LifecycleError("invalid final-consumption registry schema")
    if not isinstance(value.get("claims"), list):
        raise LifecycleError("invalid final-consumption registry claims")
    return value


@contextmanager
def _lifecycle_lock() -> Iterator[None]:
    lock_path = STATE_ROOT / "lifecycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def freeze_final(release_id: str) -> dict[str, Any]:
    release_id = _release_id(release_id)
    validate_repository_datasets()
    path = _state_path(release_id)
    if path.exists():
        raise LifecycleError(f"release {release_id!r} already has lifecycle state")
    frozen = {
        "schemaVersion": "aishop-evaluation-final-lifecycle/v3",
        "releaseId": release_id,
        "status": "FROZEN",
        "frozenAt": utc_now(),
        "sourceFingerprint": source_fingerprint(),
        "dataset": None,
        "run": None,
    }
    atomic_write_json(path, frozen, overwrite=False)
    return frozen


def assert_frozen_source(state: dict[str, Any]) -> dict[str, Any]:
    expected = stable_fingerprint(dict(state["sourceFingerprint"]))
    current = source_fingerprint()
    if stable_fingerprint(current) != expected:
        raise LifecycleError(
            "source, configuration, provider, or knowledge fingerprint changed after freeze"
        )
    return current


def claim_final(release_id: str, dataset_path: Path) -> dict[str, Any]:
    release_id = _release_id(release_id)
    state = _load_state(release_id)
    if state.get("status") != "FROZEN":
        raise LifecycleError(
            f"release {release_id!r} must be FROZEN before claim, got {state.get('status')}"
        )
    assert_frozen_source(state)
    rows = load_jsonl(dataset_path)
    cases = [parse_case(row, expected_split=Split.FINAL) for row in rows]
    validate_final_against_known(cases)
    # Historical final content is immutable even when its .runs directory is
    # later removed.  Compare the actual {domain,input} fingerprints, not only
    # IDs or a registry hash, so a newly claimed holdout cannot silently reuse
    # an old question under a new identifier.
    historical_paths = [
        path
        for path in FINAL_INPUTS_ROOT.glob("*.jsonl")
        if path.resolve() != dataset_path.resolve()
    ]
    for release_path in (STATE_ROOT / "releases").glob("*/final.jsonl"):
        if release_path.resolve() != dataset_path.resolve():
            historical_paths.append(release_path)
    historical_cases = []
    for path in historical_paths:
        try:
            historical_cases.extend(
                parse_case(row, expected_split=Split.FINAL) for row in load_jsonl(path)
            )
        except (OSError, ValueError, LifecycleError) as exc:
            raise LifecycleError(f"cannot validate historical final dataset {path}: {exc}") from exc
    if historical_cases:
        from evaluation.core.datasets import case_content_sha256

        known_hashes = {case_content_sha256(case) for case in historical_cases}
        overlap = sorted(
            case.case_id for case in cases if case_content_sha256(case) in known_hashes
        )
        if overlap:
            raise LifecycleError(
                "final dataset overlaps an immutable historical final input: "
                + ", ".join(overlap)
            )
    dataset_sha256 = canonical_dataset_sha256(cases)
    claimed_path = _release_root(release_id) / "final.jsonl"

    with _lifecycle_lock():
        registry = _registry()
        if any(
            claim.get("releaseId") == release_id or claim.get("datasetSha256") == dataset_sha256
            for claim in registry["claims"]
        ):
            raise LifecycleError("release ID or final dataset hash has already been claimed")
        atomic_write_bytes(claimed_path, dataset_path.read_bytes(), overwrite=False)
        claim = {
            "releaseId": release_id,
            "datasetSha256": dataset_sha256,
            "claimedAt": utc_now(),
            "status": "CLAIMED",
            "caseCount": len(cases),
        }
        if registry.get("schemaVersion") == "aishop-evaluation-final-registry/v2":
            registry["schemaVersion"] = "aishop-evaluation-final-registry/v3"
        registry["claims"].append(claim)
        atomic_write_json(CONSUMED_FINAL_PATH, registry)
        state["status"] = "CLAIMED"
        state["claimedAt"] = claim["claimedAt"]
        state["dataset"] = {
            "canonicalSha256": dataset_sha256,
            "caseCount": len(cases),
            "localPath": str(claimed_path),
        }
        atomic_write_json(_state_path(release_id), state)
    return state


def begin_final_execution(release_id: str, run_id: str) -> dict[str, Any]:
    release_id = _release_id(release_id)
    with _lifecycle_lock():
        state = _load_state(release_id)
        if state.get("status") != "CLAIMED":
            raise LifecycleError(
                f"final can execute exactly once from CLAIMED, got {state.get('status')}"
            )
        assert_frozen_source(state)
        registry = _registry()
        claim = next(
            (item for item in registry["claims"] if item.get("releaseId") == release_id),
            None,
        )
        if claim is None or claim.get("status") != "CLAIMED":
            raise LifecycleError("final registry does not contain a claimable entry")
        started_at = utc_now()
        claim.update({"status": "EXECUTING", "runId": run_id, "executionStartedAt": started_at})
        atomic_write_json(CONSUMED_FINAL_PATH, registry)
        state.update(
            {
                "status": "EXECUTING",
                "run": {"runId": run_id, "startedAt": started_at},
            }
        )
        atomic_write_json(_state_path(release_id), state)
        return state


def complete_final_execution(
    release_id: str,
    *,
    outcome: str,
    evidence_sha256: str | None,
) -> dict[str, Any]:
    release_id = _release_id(release_id)
    if outcome not in {"PASSED", "FAILED", "ERROR"}:
        raise LifecycleError(f"invalid final outcome: {outcome}")
    with _lifecycle_lock():
        state = _load_state(release_id)
        if state.get("status") != "EXECUTING":
            raise LifecycleError(f"release {release_id!r} is not executing: {state.get('status')}")
        registry = _registry()
        claim = next(item for item in registry["claims"] if item.get("releaseId") == release_id)
        completed_at = utc_now()
        claim.update(
            {
                "status": "EXECUTED",
                "outcome": outcome,
                "completedAt": completed_at,
                "evidenceSha256": evidence_sha256,
            }
        )
        atomic_write_json(CONSUMED_FINAL_PATH, registry)
        state["status"] = "EXECUTED"
        state["run"].update(
            {
                "outcome": outcome,
                "completedAt": completed_at,
                "evidenceSha256": evidence_sha256,
            }
        )
        atomic_write_json(_state_path(release_id), state)
        return state


def mark_final_error(release_id: str) -> dict[str, Any]:
    """Atomically record an unrecoverable final execution/publication error.

    This transition is intentionally terminal. It covers both failures while
    the case loop is running and failures after the gate outcome was computed
    but before the immutable evidence package could be published.
    """

    release_id = _release_id(release_id)
    with _lifecycle_lock():
        state = _load_state(release_id)
        if state.get("status") not in {"EXECUTING", "EXECUTED"}:
            raise LifecycleError(
                f"release {release_id!r} cannot be marked ERROR from {state.get('status')}"
            )
        registry = _registry()
        claim = next(item for item in registry["claims"] if item.get("releaseId") == release_id)
        completed_at = (state.get("run") or {}).get("completedAt") or utc_now()
        claim.update(
            {
                "status": "EXECUTED",
                "outcome": "ERROR",
                "completedAt": completed_at,
                "evidenceSha256": None,
            }
        )
        atomic_write_json(CONSUMED_FINAL_PATH, registry)
        state["status"] = "EXECUTED"
        state.setdefault("run", {})
        state["run"].update(
            {
                "outcome": "ERROR",
                "completedAt": completed_at,
                "evidenceSha256": None,
            }
        )
        atomic_write_json(_state_path(release_id), state)
        return state


def attach_final_evidence(release_id: str, evidence_sha256: str) -> dict[str, Any]:
    release_id = _release_id(release_id)
    with _lifecycle_lock():
        state = _load_state(release_id)
        if state.get("status") != "EXECUTED":
            raise LifecycleError("evidence can only attach to an executed final release")
        registry = _registry()
        claim = next(item for item in registry["claims"] if item.get("releaseId") == release_id)
        if claim.get("evidenceSha256"):
            raise LifecycleError("final evidence hash is already attached")
        claim["evidenceSha256"] = evidence_sha256
        state["run"]["evidenceSha256"] = evidence_sha256
        atomic_write_json(CONSUMED_FINAL_PATH, registry)
        atomic_write_json(_state_path(release_id), state)
        return state


def final_dataset_path(release_id: str) -> Path:
    state = _load_state(release_id)
    if state.get("status") not in {"CLAIMED", "EXECUTING", "EXECUTED"}:
        raise LifecycleError("final dataset has not been claimed")
    return Path(str(state["dataset"]["localPath"]))


def lifecycle_status(release_id: str | None = None) -> dict[str, Any]:
    if release_id:
        return _load_state(release_id)
    releases_root = STATE_ROOT / "releases"
    releases = []
    if releases_root.is_dir():
        for path in sorted(releases_root.glob("*/state.json")):
            state = load_json(path)
            releases.append(
                {
                    "releaseId": state.get("releaseId"),
                    "status": state.get("status"),
                    "datasetSha256": (state.get("dataset") or {}).get("canonicalSha256"),
                    "runId": (state.get("run") or {}).get("runId"),
                }
            )
    return {"registry": _registry(), "localReleases": releases}
