from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.graph.checkpoint.redis_saver import get_checkpointer
from app.graph.nodes import (
    agent_loop_node,
    build_context_node,
    cleanup_node,
    entry_guard,
    finalize_node,
    post_turn_node,
    tools_node,
)
from app.graph.state import AgentGraphState
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

    graph.add_node("entry", entry_guard)
    graph.add_node("build_context", build_context_node)
    graph.add_node("agent_loop", agent_loop_node)
    graph.add_node("tools", tools_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("post_turn", post_turn_node)
    graph.add_node("cleanup", cleanup_node)

    graph.set_entry_point("entry")

    graph.add_conditional_edges("entry", _after_entry, {"build_context": "build_context", "cleanup": "cleanup"})
    graph.add_edge("build_context", "agent_loop")
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
