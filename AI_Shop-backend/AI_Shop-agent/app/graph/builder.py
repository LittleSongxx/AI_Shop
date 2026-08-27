from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.graph.checkpoint.redis_saver import get_checkpointer
from app.graph.multi_agent import (
    prepare_specialist_sends,
    specialist_runner_node,
    supervisor_plan_node,
    supervisor_synthesis_node,
)
from app.graph.nodes import (
    agent_loop_node,
    build_context_node,
    cleanup_node,
    deterministic_workflow_node,
    dynamic_handoff_node,
    entry_guard,
    finalize_node,
    orchestration_router_node,
    order_reference_node,
    post_turn_node,
    tools_node,
)
from app.graph.state import AgentGraphState
from app.graph.tracing import traced_node
from app.services.redis_service import redis_service


def _after_entry(state: AgentGraphState) -> str:

    if state.get("cancelled"):
        return "cleanup"
    return "build_context"

def _after_agent_loop(state: AgentGraphState) -> str:

    if state.get("cancelled"):
        return "cleanup"
    route = state.get("route", "finalize")
    if route == "tools":
        return "tools"
    if route == "end":
        return "cleanup"
    return "finalize"


def _after_order_reference(state: AgentGraphState) -> str:
    if state.get("cancelled"):
        return "cleanup"
    if state.get("route") == "finalize":
        return "finalize"
    if state.get("route") == "human_handoff":
        return "human_handoff"
    if state.get("route") == "end":
        return "cleanup"
    return "orchestration_router"


def _after_orchestration(state: AgentGraphState) -> str:
    route = state.get("route")
    if route in {"deterministic_workflow", "multi_agent_plan", "agent_loop"}:
        return route
    return "agent_loop"


def _after_deterministic_workflow(state: AgentGraphState) -> str:
    if state.get("cancelled") or state.get("route") == "end":
        return "cleanup"
    if state.get("route") == "agent_loop":
        return "agent_loop"
    return "finalize"


def _after_supervisor_plan(state: AgentGraphState):
    return prepare_specialist_sends(state)


def _after_tools(state: AgentGraphState) -> str:

    if state.get("cancelled"):
        return "cleanup"
    if state.get("route") == "finalize":
        return "finalize"
    return "agent_loop"

def _resolve_checkpointer():

    try:
        return get_checkpointer(redis_service.client)
    except RuntimeError:
        return InMemorySaver()

def build_agent_graph():

    graph = StateGraph(AgentGraphState)

    graph.add_node("entry", traced_node("entry", entry_guard))
    graph.add_node("build_context", traced_node("build_context", build_context_node))
    graph.add_node("order_reference", traced_node("order_reference", order_reference_node))
    graph.add_node("human_handoff", traced_node("human_handoff", dynamic_handoff_node))
    graph.add_node(
        "orchestration_router",
        traced_node("orchestration_router", orchestration_router_node),
    )
    graph.add_node(
        "deterministic_workflow",
        traced_node("deterministic_workflow", deterministic_workflow_node),
    )
    graph.add_node("multi_agent_plan", traced_node("multi_agent_plan", supervisor_plan_node))
    graph.add_node("specialist_runner", traced_node("specialist_runner", specialist_runner_node))
    graph.add_node("multi_agent_synthesis", traced_node("multi_agent_synthesis", supervisor_synthesis_node))
    graph.add_node("agent_loop", traced_node("agent_loop", agent_loop_node))
    graph.add_node("tools", traced_node("tools", tools_node))
    graph.add_node("finalize", traced_node("finalize", finalize_node))
    graph.add_node("post_turn", traced_node("post_turn", post_turn_node))
    graph.add_node("cleanup", traced_node("cleanup", cleanup_node))

    graph.set_entry_point("entry")

    graph.add_conditional_edges("entry", _after_entry, {"build_context": "build_context", "cleanup": "cleanup"})
    graph.add_edge("build_context", "order_reference")
    graph.add_conditional_edges(
        "order_reference",
        _after_order_reference,
        {
            "orchestration_router": "orchestration_router",
            "human_handoff": "human_handoff",
            "finalize": "finalize",
            "cleanup": "cleanup",
        },
    )
    graph.add_edge("human_handoff", "cleanup")
    graph.add_conditional_edges(
        "orchestration_router",
        _after_orchestration,
        {
            "deterministic_workflow": "deterministic_workflow",
            "agent_loop": "agent_loop",
            "multi_agent_plan": "multi_agent_plan",
        },
    )
    graph.add_conditional_edges(
        "deterministic_workflow",
        _after_deterministic_workflow,
        {
            "agent_loop": "agent_loop",
            "finalize": "finalize",
            "cleanup": "cleanup",
        },
    )
    graph.add_conditional_edges(
        "multi_agent_plan",
        _after_supervisor_plan,
        {
            "specialist_runner": "specialist_runner",
            "multi_agent_synthesis": "multi_agent_synthesis",
        },
    )
    graph.add_edge("specialist_runner", "multi_agent_synthesis")
    graph.add_edge("multi_agent_synthesis", "finalize")
    graph.add_conditional_edges(
        "agent_loop",
        _after_agent_loop,
        {"tools": "tools", "finalize": "finalize", "cleanup": "cleanup"},
    )
    graph.add_conditional_edges(
        "tools",
        _after_tools,
        {"agent_loop": "agent_loop", "finalize": "finalize", "cleanup": "cleanup"},
    )
    graph.add_edge("finalize", "post_turn")
    graph.add_edge("post_turn", "cleanup")
    graph.add_edge("cleanup", END)

    checkpointer = _resolve_checkpointer()

    return graph.compile(checkpointer=checkpointer)

@lru_cache(maxsize=1)
def get_compiled_graph():

    return build_agent_graph()

def reset_compiled_graph_cache() -> None:

    get_compiled_graph.cache_clear()
