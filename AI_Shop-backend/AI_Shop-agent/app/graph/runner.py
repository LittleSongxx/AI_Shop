from __future__ import annotations

import time

import structlog

from app.config.settings import get_settings
from app.constants import MSG_STATUS_NORMAL
from app.db.pool import acquire
from app.graph.budget_guard import (
    BudgetConfig,
    BudgetExceededError,
    BudgetGuard,
    bind_budget_guard,
    reset_budget_guard,
)
from app.graph.builder import get_compiled_graph
from app.graph.checkpoint.redis_saver import get_checkpointer
from app.graph.state import initial_state, thread_id_for
from app.observability.llm_metrics import snapshot_cost_summary
from app.observability.telemetry import get_tracer
from app.services import agent_runtime as rt
from app.services.episode_service import episode_service
from app.services.redis_service import redis_service

logger = structlog.get_logger()
tracer = get_tracer()


def _runtime_budget_config() -> BudgetConfig | None:
    settings = get_settings()
    if not settings.agent_budget_enabled:
        return None
    return BudgetConfig(
        max_tokens=settings.agent_budget_max_tokens,
        max_cost_cny=settings.agent_budget_max_cost_cny,
        max_steps=settings.agent_budget_max_steps,
        deadline_seconds=settings.agent_budget_deadline_seconds,
        warn_threshold=settings.agent_budget_warn_threshold,
    )

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

async def run_agent_graph(agent_msg: dict, budget_config: BudgetConfig | None = None) -> str:
    """跑完一轮图，返回用户实际看到的结果类型。

    返回值是 outcome 字段：``ok`` / ``cancelled`` / ``llm_error`` / ``graph_error`` / ``budget_exceeded``。
    Worker 只把 ``ok`` 记成 COMPLETED——llm_error / graph_error 意味着用户收到的是
    错误文案，绝不应当进成功率（P0-1）。图内异常已经被节点消化成用户可见错误，
    这里不重抛，避免 Worker 把"用户已收到错误"再当一次可重试的普通异常。

    Args:
        agent_msg: Agent消息对象
        budget_config: 显式预算配置；None 使用 Settings 中的运行时预算

    Returns:
        执行结果类型：ok / cancelled / llm_error / graph_error / budget_exceeded
    """

    user_id = agent_msg["userId"]
    message_id = agent_msg["messageId"]
    thread_id = thread_id_for(user_id, message_id)
    config = {"configurable": {"thread_id": thread_id}}
    graph = get_compiled_graph()
    checkpointer = get_checkpointer(redis_service.client)

    effective_budget = budget_config or _runtime_budget_config()
    budget_guard = BudgetGuard(effective_budget) if effective_budget else None
    if budget_guard:
        initial_cost = snapshot_cost_summary()
        budget_guard.seed_llm_usage(
            tokens=int(initial_cost.get("inputTokens") or 0)
            + int(initial_cost.get("outputTokens") or 0),
            cost_cny=float(initial_cost.get("costCny") or 0.0),
        )
        logger.info(
            "budget_guard_enabled",
            message_id=message_id,
            limits=effective_budget.as_dict(),
        )
    budget_context_token = bind_budget_guard(budget_guard)

    started = time.perf_counter()
    with tracer.start_as_current_span("agent.graph") as span:
        span.set_attribute("agent.message_id", int(message_id))
        span.set_attribute("agent.run_id", str(agent_msg.get("runId") or ""))
        episode_service.record_step(
            "GRAPH_START",
            node_name="graph",
            input_data={
                "messageId": message_id,
                "intent": agent_msg.get("intent"),
                "queue": agent_msg.get("queueName"),
            },
        )
        try:
            if await _should_resume(user_id, message_id, thread_id):
                logger.info("graph_resume", thread_id=thread_id, message_id=message_id)
                episode_service.record_step(
                    "STATE_TRANSITION",
                    node_name="graph",
                    output_data={"transition": "RESUME"},
                )
                result = await graph.ainvoke(None, config)
            else:
                await checkpointer.adelete_thread(thread_id)
                card, user_text = rt.parse_agent_message(agent_msg)
                state = initial_state(agent_msg, card, user_text)
                logger.info("graph_invoke", thread_id=thread_id, message_id=message_id)
                result = await graph.ainvoke(state, config)

            outcome = str(result.get("outcome") or "ok")
            elapsed_ms = round((time.perf_counter() - started) * 1_000)

            # Token 消耗累计：将本次图运行消耗的真实 token 累加到用户的会话和每日配额。
            # snapshot_cost_summary() 从 contextvar 读取本次请求内所有 LLM 调用的汇总。
            cost_summary = snapshot_cost_summary(tools_called=result.get("tools_called"))
            total_tokens = int(cost_summary.get("inputTokens") or 0) + int(
                cost_summary.get("outputTokens") or 0
            )
            if total_tokens > 0:
                try:
                    from app.services.rate_limit_service import rate_limit_service
                    await rate_limit_service.record_token_usage(user_id, total_tokens)
                except Exception as _token_err:
                    logger.warning(
                        "token_usage_record_failed",
                        user_id=user_id,
                        tokens=total_tokens,
                        error=type(_token_err).__name__,
                    )

            episode_service.update_run(
                intent=result.get("intent"),
                experiment={
                    "rag": result.get("rag_trace"),
                }
                if result.get("rag_trace")
                else None,
            )
            episode_service.record_step(
                "GRAPH_END",
                node_name="graph",
                status="OK" if outcome == "ok" else "ERROR",
                output_data={
                    "outcome": outcome,
                    "intent": result.get("intent"),
                    "tools": result.get("tools_called") or [],
                    "orchestrationMode": result.get("orchestration_mode"),
                    "orchestrationReason": result.get("orchestration_reason"),
                    "llmSkipped": bool(result.get("llm_skipped")),
                    "llmSkipReason": result.get("llm_skip_reason"),
                    "structuredResultFinalized": bool(
                        result.get("structured_result_finalized")
                    ),
                    "llmCallCount": int(cost_summary.get("llmCalls") or 0),
                    "successfulLlmCallCount": int(
                        cost_summary.get("successfulLlmCalls") or 0
                    ),
                    "failedLlmCallCount": int(
                        cost_summary.get("failedLlmCalls") or 0
                    ),
                    "usageStatus": cost_summary.get("costStatus"),
                    "costSummary": cost_summary,
                },
                latency_ms=elapsed_ms,
            )
            # 记录预算使用情况（如果启用了预算守卫）
            if budget_guard:
                budget_summary = budget_guard.summary()
                logger.info(
                    "budget_guard_summary",
                    message_id=message_id,
                    summary=budget_summary,
                )
                episode_service.record_step(
                    "BUDGET_SUMMARY",
                    node_name="graph",
                    output_data=budget_summary,
                )

            episode_service.finish_run(
                outcome,
                latency_ms=elapsed_ms,
                force_keep=True if outcome != "ok" else None,
            )
            return outcome
        except BudgetExceededError as budget_exc:
            # 预算超限错误
            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            logger.warning(
                "budget_exceeded",
                message_id=message_id,
                error=str(budget_exc),
                usage=budget_guard.usage.as_dict() if budget_guard else {},
            )
            span.record_exception(budget_exc)
            await rt.push_budget_error(agent_msg)
            episode_service.record_step(
                "BUDGET_EXCEEDED",
                node_name="graph",
                status="ERROR",
                error_code="BudgetExceededError",
                error_message=str(budget_exc),
                latency_ms=elapsed_ms,
                output_data={
                    "costSummary": snapshot_cost_summary(),
                    "budgetSummary": budget_guard.summary() if budget_guard else {},
                    "reason": budget_exc.as_dict(),
                },
            )
            episode_service.finish_run(
                "budget_exceeded", latency_ms=elapsed_ms, force_keep=True
            )
            return "budget_exceeded"
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1_000)
            span.record_exception(exc)
            episode_service.record_step(
                "GRAPH_ERROR",
                node_name="graph",
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc),
                latency_ms=elapsed_ms,
                # 异常路径同样补成本快照：已累计的 LLM 成本随异常结束不应丢失
                # per-request 摘要（E 工作线"每条消息"口径的异常兜底）。
                output_data={"costSummary": snapshot_cost_summary()},
            )
            episode_service.finish_run(
                "graph_exception", latency_ms=elapsed_ms, force_keep=True
            )
            raise
        finally:
            try:
                await checkpointer.adelete_thread(thread_id)
            except Exception as e:
                logger.warning(
                    "graph_checkpoint_cleanup_failed",
                    thread_id=thread_id,
                    error=str(e),
                )
            reset_budget_guard(budget_context_token)
