"""Explicit state machine for the single evaluation lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import RunPhase, RunState


class LifecycleError(ValueError):
    pass


_ALLOWED: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.VALIDATED: frozenset({RunPhase.PREFLIGHTED, RunPhase.BLOCKED}),
    RunPhase.PREFLIGHTED: frozenset({RunPhase.KNOWN_COLLECTED, RunPhase.BLOCKED}),
    RunPhase.KNOWN_COLLECTED: frozenset({RunPhase.FROZEN, RunPhase.BLOCKED}),
    RunPhase.FROZEN: frozenset({RunPhase.FINAL_COLLECTED, RunPhase.BLOCKED, RunPhase.FAILED_RETAINED}),
    RunPhase.FINAL_COLLECTED: frozenset({RunPhase.REVIEW_PENDING, RunPhase.REVIEWED, RunPhase.PACKAGED, RunPhase.FAILED_RETAINED}),
    RunPhase.REVIEW_PENDING: frozenset({RunPhase.REVIEWED, RunPhase.FAILED_RETAINED}),
    RunPhase.REVIEWED: frozenset({RunPhase.PACKAGED, RunPhase.FAILED_RETAINED}),
    RunPhase.PACKAGED: frozenset(),
    RunPhase.BLOCKED: frozenset(),
    RunPhase.FAILED_RETAINED: frozenset(),
}


class RunLifecycle:
    def __init__(self, path: Path, *, suite: str, run_id: str) -> None:
        self.path = path
        self.suite = suite
        self.run_id = run_id
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("suite") != suite or payload.get("runId") != run_id:
                raise LifecycleError("lifecycle identity does not match suite/run-id")
            self.phase = RunPhase(str(payload["phase"]))
            self.state = RunState(str(payload.get("state", RunState.IN_PROGRESS)))
            self.history = list(payload.get("history") or [])
        else:
            self.phase = RunPhase.VALIDATED
            self.state = RunState.IN_PROGRESS
            self.history = [{"phase": self.phase.value}]
            self._persist()

    def transition(self, target: RunPhase, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
        if target == self.phase:
            raise LifecycleError(f"run is already in phase {target.value}")
        if target not in _ALLOWED[self.phase]:
            raise LifecycleError(f"invalid lifecycle transition {self.phase.value} -> {target.value}")
        self.phase = target
        if target == RunPhase.BLOCKED:
            self.state = RunState.BLOCKED
        elif target == RunPhase.FAILED_RETAINED:
            self.state = RunState.FAILED_RETAINED
        elif target == RunPhase.PACKAGED:
            self.state = RunState.COMPLETE
        self.history.append({"phase": target.value, **(details or {})})
        self._persist()
        return self.snapshot()

    def require(self, *phases: RunPhase) -> None:
        if self.phase not in phases:
            expected = ", ".join(item.value for item in phases)
            raise LifecycleError(f"phase {self.phase.value} does not satisfy {expected}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "suite": self.suite,
            "runId": self.run_id,
            "phase": self.phase.value,
            "state": self.state.value,
            "history": self.history,
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
