from __future__ import annotations

import structlog

from app.constants import MSG_STATUS_NORMAL
from app.db.pool import acquire
from app.graph.builder import get_compiled_graph
from app.graph.checkpoint.redis_saver import get_checkpointer
from app.graph.state import initial_state, thread_id_for
from app.services.agent_runtime import parse_agent_message
from app.services.redis_service import redis_service

logger = structlog.get_logger()

async def _should_resume(user_id: str, message_id: int, thread_id: str) -> bool:

    async with acquire() as cur:
        await cur.execute(
            "SELECT status, assistant_message FROM agent_message WHERE message_id=%s AND user_id=%s",
            (message_id, user_id),
        )
        row = await cur.fetchone()
    if not row:
        return False
    status = row["status"]
    assistant = row["assistant_message"]
    if status != MSG_STATUS_NORMAL:
        return False
    if assistant:
        return False
    checkpointer = get_checkpointer(redis_service.client)

    await checkpointer.hydrate_thread(thread_id)

    config = {"configurable": {"thread_id": thread_id}}

    return (await checkpointer.aget_tuple(config)) is not None

async def run_agent_graph(agent_msg: dict) -> str:
    """跑完一轮图，返回用户实际看到的结果类型。

    返回值是 outcome 字段：``ok`` / ``cancelled`` / ``llm_error`` / ``graph_error``。
    Worker 只把 ``ok`` 记成 COMPLETED——llm_error / graph_error 意味着用户收到的是
    错误文案，绝不应当进成功率（P0-1）。图内异常已经被节点消化成用户可见错误，
    这里不重抛，避免 Worker 把"用户已收到错误"再当一次可重试的普通异常。
    """

    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    thread_id = thread_id_for(user_id, message_id)
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_compiled_graph()
    checkpointer = get_checkpointer(redis_service.client)

    try:
        if await _should_resume(user_id, message_id, thread_id):
            logger.info("graph_resume", thread_id=thread_id, message_id=message_id)
            result = await graph.ainvoke(None, config)
            return str(result.get("outcome") or "ok")

        await checkpointer.adelete_thread(thread_id)
        card, user_text = parse_agent_message(agent_msg)
        state = initial_state(agent_msg, card, user_text)
        logger.info("graph_invoke", thread_id=thread_id, message_id=message_id)

        result = await graph.ainvoke(state, config)
        return str(result.get("outcome") or "ok")
    finally:
        try:
            await checkpointer.adelete_thread(thread_id)
        except Exception as e:
            logger.warning("graph_checkpoint_cleanup_failed", thread_id=thread_id, error=str(e))
