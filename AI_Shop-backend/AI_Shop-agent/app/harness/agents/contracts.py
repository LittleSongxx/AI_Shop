from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class HandoffEnvelope(BaseModel):
    """The minimum, typed context a specialist may receive from Supervisor."""

    handoff_id: str
    source_agent: str = "supervisor"
    target_agent: str
    goal: str
    user_id: str
    session_summary: str = Field(default="", max_length=1000)
    verified_context: dict = Field(default_factory=dict)
    tool_scope: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_tokens: int = Field(default=2400, ge=256, le=16_000)


class SpecialistTask(BaseModel):
    """The small, explicit payload sent to one parallel specialist."""

    handoff_id: str
    child_run_id: str
    parent_run_id: str | None = None
    agent_id: str
    agent_version: str = "v1"
    goal: str
    user_id: str
    user_text: str
    session_summary: str = Field(default="", max_length=1000)
    verified_context: dict = Field(default_factory=dict)
    tool_scope: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_tokens: int = Field(default=2400, ge=256, le=16_000)
    timeout_seconds: int = Field(default=8, ge=1, le=30)


class SupervisorPlan(BaseModel):
    """Validated routing output. It is never accepted directly from an LLM."""

    intent: str | None = None
    specialists: list[str] = Field(default_factory=list, max_length=2)
    goals: dict[str, str] = Field(default_factory=dict)
    requires_action: bool = False
    action_type: str | None = None
    fallback: Literal["SUPERVISOR_ONLY", "PARTIAL_ARTIFACTS", "HUMAN_HANDOFF"] = "PARTIAL_ARTIFACTS"
    planner_source: Literal["LLM_STRUCTURED", "DETERMINISTIC_FALLBACK"] = "DETERMINISTIC_FALLBACK"

    @field_validator("specialists")
    @classmethod
    def specialists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Prevent duplicate fan-out branches from one structured plan."""
        if len(value) != len(set(value)):
            raise ValueError("SPECIALIST_DUPLICATE")
        return value


class SpecialistState(BaseModel):
    """Bounded state exposed to a specialist subgraph."""

    agent_id: str
    goal: str
    verified_context: dict = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    remaining_rounds: int = Field(default=2, ge=0, le=5)


class AgentArtifact(BaseModel):
    """A specialist result. It is not a second user-facing conversation."""

    status: Literal["SUCCESS", "DEGRADED", "NEEDS_CLARIFICATION", "BLOCKED", "FAILED"]
    agent_id: str
    facts: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    draft_answer: str = ""
    assistant_cards: str | None = None
    proposed_action: dict | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    next_step: Literal["FINALIZE", "ASK_CLARIFICATION", "HUMAN_HANDOFF", "FALLBACK"] = "FINALIZE"
    warnings: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    handoff_id: str | None = None
    latency_ms: int | None = None


class ActionProposal(BaseModel):
    """The root-only, confirmation-gated write proposal contract."""

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool = False
    requires_confirmation: bool = True
    evidence_refs: list[dict] = Field(default_factory=list, max_length=20)
    reason: str | None = Field(default=None, max_length=128)

    @field_validator("tool")
    @classmethod
    def write_tool_only(cls, value: str) -> str:
        if not value.startswith("PROPOSE_"):
            raise ValueError("ActionProposal 只能引用 PROPOSE_* 工具")
        return value


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str | None = None
    requires_confirmation: bool = False
