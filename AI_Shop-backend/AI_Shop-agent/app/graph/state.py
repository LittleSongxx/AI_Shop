import operator
from typing import Annotated, Literal

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

RouteKind = Literal["agent_loop", "tools", "finalize", "post_turn", "end"]

class AgentGraphState(TypedDict, total=False):

    agent_msg: dict
    user_id: str
    message_id: int
    user_message: str
    user_text: str
    image_url: str | None
    from_product: bool
    card: dict | None
    message_card: dict | None

    cancelled: bool
    finished: bool
    route: RouteKind

    # 用户最终看到什么。Worker 以此决定任务终态：只有 ok 才进 COMPLETED，
    # llm_error / graph_error 说明用户收到的是错误文案，绝不应当成成功任务。
    outcome: str | None

    llm_messages: list[BaseMessage]
    working_turns: list[dict]
    working_oldest_id: int | None

    chunks: Annotated[list[str], operator.add]
    react_round: int
    tools_called: Annotated[list[str], operator.add]
    pending_tool_calls: list[dict]

    tool_biz: dict | None
    biz_type: str | None
    biz_data: str | None
    assistant_cards: str | None
    search_tool_hint: str | None
    search_fallback_done: bool
    category_switch_search: bool
    intent: str | None
    intent_data: str | None
    intent_decision: dict | None
    rag_source_refs: list[dict] | None
    rag_trace: dict | None

def initial_state(agent_msg: dict, card: dict | None, user_text: str) -> AgentGraphState:

    return {
        "agent_msg": agent_msg,
        "user_id": agent_msg["userId"],
        "message_id": agent_msg["messageId"],
        "user_message": agent_msg.get("userMessage") or "",
        "user_text": user_text,
        "image_url": None,
        "from_product": bool(agent_msg.get("fromProduct")),
        "card": card,
        "message_card": card,
        "cancelled": False,
        "finished": False,
        "route": "agent_loop",
        "outcome": None,
        "llm_messages": [],
        "working_turns": [],
        "working_oldest_id": None,
        "chunks": [],
        "react_round": 0,
        "tools_called": [],
        "pending_tool_calls": [],
        "tool_biz": None,
        "biz_type": None,
        "biz_data": None,
        "assistant_cards": None,
        "search_tool_hint": None,
        "search_fallback_done": False,
        "category_switch_search": False,
        "intent": None,
        "intent_data": None,
        "intent_decision": None,
        "rag_source_refs": [],
        "rag_trace": None,
    }

def thread_id_for(user_id: str, message_id: int) -> str:

    return f"{user_id}:{message_id}"
