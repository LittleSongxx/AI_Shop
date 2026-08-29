from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def empty_summary() -> dict[str, Any]:
    return {
        "version": 1,
        "narrative": "",
        "facts": {
            "goal": None,
            "budget": None,
            "preferences": [],
            "constraints": [],
            "decisions": [],
            "openQuestions": [],
        },
    }


def empty_state() -> dict[str, Any]:
    return {
        "consultProduct": None,
        "pendingAction": None,
        "shoppingMission": None,
        "lastToolResults": {
            "searchedProducts": [],
            "searchedProductNames": [],
            "queriedOrders": [],
            "viewedProductIds": [],
        },
        "turnCount": 0,
        "summaryLastMessageId": 0,
        "estimatedTokens": 0,
    }


@dataclass
class SessionMemory:
    user_id: str
    summary: dict[str, Any] = field(default_factory=empty_summary)
    state: dict[str, Any] = field(default_factory=empty_state)
    # Monotonic optimistic-concurrency token.  It is persisted inside the
    # state JSON as well as the Redis envelope so old rows/envelopes (without
    # a revision) continue to read as revision zero.
    revision: int = 0

    def __post_init__(self) -> None:
        # Callers that construct a model directly from a decoded legacy state
        # may not go through ``from_storage``.  Recover the embedded token in
        # that case without overriding an explicitly supplied revision.
        if self.revision == 0 and isinstance(self.state, dict):
            try:
                self.revision = max(0, int(self.state.get("memoryRevision") or 0))
            except (TypeError, ValueError):
                self.revision = 0

    @classmethod
    def from_storage(
        cls,
        user_id: str,
        summary: dict | None,
        state: dict | None,
        revision: int | None = None,
    ) -> SessionMemory:
        mem = cls(user_id=user_id)
        if summary:
            mem.summary = summary
        if state:
            mem.state = state
        raw_revision = revision
        if raw_revision is None:
            raw_revision = mem.state.get("memoryRevision")
        try:
            mem.revision = max(0, int(raw_revision or 0))
        except (TypeError, ValueError):
            mem.revision = 0
        return mem

    @property
    def turn_count(self) -> int:
        return int(self.state.get("turnCount") or 0)

    @turn_count.setter
    def turn_count(self, value: int) -> None:
        self.state["turnCount"] = int(value)

    @property
    def summary_last_message_id(self) -> int:
        return int(self.state.get("summaryLastMessageId") or 0)

    @summary_last_message_id.setter
    def summary_last_message_id(self, value: int) -> None:
        self.state["summaryLastMessageId"] = int(value)
