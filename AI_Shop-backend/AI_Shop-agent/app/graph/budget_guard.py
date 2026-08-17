"""Per-run execution budgets for the LangGraph runtime.

The guard is deliberately bound to the current async context instead of being
stored in graph state. Runtime objects must not be serialized into Redis
checkpoints, and concurrent worker tasks must not share accounting.
"""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass
from typing import Any, Callable

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class BudgetConfig:
    max_tokens: int | None = None
    max_cost_cny: float | None = None
    max_steps: int | None = None
    deadline_seconds: float | None = None
    warn_threshold: float = 0.8

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_steps"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when configured")
        for name in ("max_cost_cny", "deadline_seconds"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when configured")
        if not 0 < self.warn_threshold < 1:
            raise ValueError("warn_threshold must be between 0 and 1")

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "maxTokens": self.max_tokens,
            "maxCostCny": self.max_cost_cny,
            "maxSteps": self.max_steps,
            "deadlineSeconds": self.deadline_seconds,
            "warnThreshold": self.warn_threshold,
        }


@dataclass
class BudgetUsage:
    tokens_used: int = 0
    cost_used_cny: float = 0.0
    steps_used: int = 0
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, int | float]:
        return {
            "tokensUsed": self.tokens_used,
            "costUsedCny": round(self.cost_used_cny, 8),
            "stepsUsed": self.steps_used,
            "elapsedSeconds": round(self.elapsed_seconds, 4),
        }


class BudgetExceededError(RuntimeError):
    def __init__(
        self,
        dimension: str,
        *,
        used: int | float,
        limit: int | float,
        next_step: str,
    ) -> None:
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.next_step = next_step
        super().__init__(
            f"agent budget exceeded for {dimension}: used={used}, "
            f"limit={limit}, next_step={next_step}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": "AGENT_BUDGET_EXCEEDED",
            "dimension": self.dimension,
            "used": self.used,
            "limit": self.limit,
            "nextStep": self.next_step,
        }


class BudgetGuard:
    """Tracks real graph steps plus observed model token and cost deltas."""

    def __init__(
        self,
        config: BudgetConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.usage = BudgetUsage()
        self._clock = clock
        self._started_at = clock()
        self._warned: set[str] = set()

    def seed_llm_usage(self, *, tokens: int = 0, cost_cny: float = 0.0) -> None:
        """Include work performed before the graph, such as intent refinement."""
        self.usage.tokens_used = max(0, int(tokens))
        self.usage.cost_used_cny = max(0.0, float(cost_cny))
        self._refresh_elapsed()

    def check_before_step(self, step_name: str) -> None:
        self._refresh_elapsed()
        checks: tuple[tuple[str, int | float, int | float | None], ...] = (
            ("deadline", self.usage.elapsed_seconds, self.config.deadline_seconds),
            ("steps", self.usage.steps_used, self.config.max_steps),
            ("tokens", self.usage.tokens_used, self.config.max_tokens),
            ("cost_cny", self.usage.cost_used_cny, self.config.max_cost_cny),
        )
        for dimension, used, limit in checks:
            if limit is not None and used >= limit:
                logger.warning(
                    "agent_budget_exceeded",
                    dimension=dimension,
                    used=used,
                    limit=limit,
                    next_step=step_name,
                )
                raise BudgetExceededError(
                    dimension,
                    used=round(used, 8) if isinstance(used, float) else used,
                    limit=limit,
                    next_step=step_name,
                )
        self._emit_warnings(step_name)

    def record_step(
        self,
        step_name: str,
        *,
        tokens: int = 0,
        cost_cny: float = 0.0,
    ) -> None:
        """Record one attempted graph node and its observed LLM usage delta."""
        self.usage.steps_used += 1
        self.usage.tokens_used += max(0, int(tokens))
        self.usage.cost_used_cny += max(0.0, float(cost_cny))
        self._refresh_elapsed()
        logger.info(
            "agent_budget_step_recorded",
            step=step_name,
            step_tokens=max(0, int(tokens)),
            step_cost_cny=round(max(0.0, float(cost_cny)), 8),
            **self.usage.as_dict(),
        )
        self._emit_warnings(step_name)

    def remaining(self) -> dict[str, int | float | None]:
        self._refresh_elapsed()
        return {
            "tokens": self._remaining(self.config.max_tokens, self.usage.tokens_used),
            "costCny": self._remaining(self.config.max_cost_cny, self.usage.cost_used_cny),
            "steps": self._remaining(self.config.max_steps, self.usage.steps_used),
            "seconds": self._remaining(self.config.deadline_seconds, self.usage.elapsed_seconds),
        }

    def summary(self) -> dict[str, Any]:
        self._refresh_elapsed()
        return {
            "usage": self.usage.as_dict(),
            "limits": self.config.as_dict(),
            "remaining": self.remaining(),
            "warnedDimensions": sorted(self._warned),
        }

    def _refresh_elapsed(self) -> None:
        self.usage.elapsed_seconds = max(0.0, self._clock() - self._started_at)

    @staticmethod
    def _remaining(limit: int | float | None, used: int | float) -> int | float | None:
        if limit is None:
            return None
        return max(0, limit - used)

    def _emit_warnings(self, step_name: str) -> None:
        dimensions: tuple[tuple[str, int | float, int | float | None], ...] = (
            ("deadline", self.usage.elapsed_seconds, self.config.deadline_seconds),
            ("steps", self.usage.steps_used, self.config.max_steps),
            ("tokens", self.usage.tokens_used, self.config.max_tokens),
            ("cost_cny", self.usage.cost_used_cny, self.config.max_cost_cny),
        )
        for dimension, used, limit in dimensions:
            if limit is None or dimension in self._warned:
                continue
            ratio = used / limit
            if ratio >= self.config.warn_threshold:
                self._warned.add(dimension)
                logger.warning(
                    "agent_budget_warning",
                    dimension=dimension,
                    used=used,
                    limit=limit,
                    ratio=round(ratio, 4),
                    step=step_name,
                )


_ACTIVE_GUARD: contextvars.ContextVar[BudgetGuard | None] = contextvars.ContextVar(
    "agent_budget_guard", default=None
)


def bind_budget_guard(guard: BudgetGuard | None) -> contextvars.Token:
    return _ACTIVE_GUARD.set(guard)


def reset_budget_guard(token: contextvars.Token) -> None:
    _ACTIVE_GUARD.reset(token)


def active_budget_guard() -> BudgetGuard | None:
    return _ACTIVE_GUARD.get()
