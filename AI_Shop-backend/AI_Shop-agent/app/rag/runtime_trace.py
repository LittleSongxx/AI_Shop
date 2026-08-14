"""Task-local stage accounting for the production RAG path."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class RagRuntimeTrace:
    stage_samples_ms: dict[str, list[float]] = field(default_factory=dict)
    provider_calls: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    fallbacks: list[str] = field(default_factory=list)
    route: str = "GENERAL"
    policy_fingerprint: str | None = None
    observations: dict[str, Any] = field(default_factory=dict)

    def observe(self, stage: str, elapsed_ms: float) -> None:
        self.stage_samples_ms.setdefault(stage, []).append(round(max(0.0, elapsed_ms), 4))

    def called(self, provider: str, count: int = 1) -> None:
        self.provider_calls[provider] = self.provider_calls.get(provider, 0) + count

    def fallback(self, reason: str) -> None:
        if reason not in self.fallbacks:
            self.fallbacks.append(reason)

    def public(self) -> dict[str, Any]:
        return {
            "stageLatencyMs": {
                stage: round(sum(values), 4)
                for stage, values in sorted(self.stage_samples_ms.items())
            },
            "stageCallCounts": {
                stage: len(values)
                for stage, values in sorted(self.stage_samples_ms.items())
            },
            "providerCalls": dict(sorted(self.provider_calls.items())),
            "cacheHits": self.cache_hits,
            "fallbacks": list(self.fallbacks),
            "route": self.route,
            "policyFingerprint": self.policy_fingerprint,
            "observations": dict(self.observations),
        }


_ACTIVE_TRACE: contextvars.ContextVar[RagRuntimeTrace | None] = contextvars.ContextVar(
    "rag_runtime_trace", default=None
)


@contextmanager
def rag_runtime_trace_scope(trace: RagRuntimeTrace | None = None) -> Iterator[RagRuntimeTrace]:
    value = trace or RagRuntimeTrace()
    token = _ACTIVE_TRACE.set(value)
    try:
        yield value
    finally:
        _ACTIVE_TRACE.reset(token)


def active_rag_runtime_trace() -> RagRuntimeTrace | None:
    return _ACTIVE_TRACE.get()
