"""Static multi-agent contracts and tool scopes for the AI_Shop harness."""

from .contracts import (
    ActionProposal,
    AgentArtifact,
    HandoffEnvelope,
    PolicyDecision,
    SpecialistState,
    SpecialistTask,
    SupervisorPlan,
)
from .registry import AGENT_SPECS, DATA_ANALYST_SPEC, AgentSpec, agent_for_intent

__all__ = [
    "AGENT_SPECS",
    "ActionProposal",
    "AgentArtifact",
    "AgentSpec",
    "DATA_ANALYST_SPEC",
    "HandoffEnvelope",
    "PolicyDecision",
    "SpecialistState",
    "SpecialistTask",
    "SupervisorPlan",
    "agent_for_intent",
]
