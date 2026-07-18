import asyncio

import structlog

from app.config.settings import get_settings
from app.harness.guardrails.input_guard import InputGuardrail
from app.services.agent_engine import agent_engine
from app.services.message_service import agent_message_service
from app.services.product_snapshot_service import product_snapshot_service
from app.services.rate_limit_service import rate_limit_service
from app.services.redis_service import redis_service
from app.services.sensitive_word_service import sensitive_word_service
from app.services.stream_service import stream_service
from app.utils.product_consult import build_consult_card_message, parse_consult_card

logger = structlog.get_logger()
input_guard = InputGuardrail()
AGENT_BUSY_MESSAGE = "客服繁忙，请稍后再试"

_active_tasks = 0

_active_lock = asyncio.Lock()

class AgentOrchestrator:

    async def send_message(
        self,
        user_id: str,
        message: str,
        from_product: bool = False,
        consult_product_id: str | None = None,
    ) -> dict:

        settings = get_settings()

        if not await rate_limit_service.allow(user_id, "sendMessage", 1, 1):
            raise ValueError("发送消息过于频繁，请稍后再试")

        if settings.ai_chat_limit > 0:
            total = await agent_message_service.count_user_messages(user_id)
            if total >= settings.ai_chat_limit:
                raise ValueError("AI购物体验已经结束")

        if input_guard.detect_injection(message):
            raise ValueError("检测到异常输入")

        if from_product and consult_product_id:
            await product_snapshot_service.ensure_consult_snapshot(
                user_id, consult_product_id.strip()
            )
        elif from_product:

            await redis_service.set_consult_active(user_id)
        else:

            await redis_service.pause_consult(user_id)

        card, user_text = parse_consult_card(message)
        if card and card.get("productId"):
            await product_snapshot_service.resolve_active_snapshot(user_id, card)
            await redis_service.set_consult_active(user_id)

        if card:
            filtered_text = await sensitive_word_service.replace(user_text)
            message = build_consult_card_message(card, filtered_text)
        else:
            message = await sensitive_word_service.replace(message)

        agent_msg = await agent_message_service.save_user_message(user_id, message)

        agent_msg["fromProduct"] = from_product

        global _active_tasks
        async with _active_lock:
            if _active_tasks >= settings.task_queue_max:
                await self._notify_busy(agent_msg)
                return agent_msg
            _active_tasks += 1

        asyncio.create_task(self._run_agent(agent_msg))
        return agent_msg

    async def _run_agent(self, agent_msg: dict) -> None:

        global _active_tasks
        try:
            await agent_engine.assistant_answer(agent_msg)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(
                "agent_task_failed",
                error=str(e),
                error_type=type(e).__name__,
                message_id=agent_msg.get("messageId"),
                user_id=agent_msg.get("userId"),
            )
            user_id = agent_msg["userId"]
            message_id = agent_msg["messageId"]

            await stream_service.push_error(
                user_id, message_id, "服务暂时不可用，请稍后重试", None
            )
            await agent_message_service.complete_message(
                message_id, "服务异常", None, None
            )
        finally:
            async with _active_lock:
                _active_tasks = max(0, _active_tasks - 1)

    async def _notify_busy(self, agent_msg: dict) -> None:

        user_id = agent_msg["userId"]
        message_id = agent_msg["messageId"]
        await stream_service.push_error(user_id, message_id, AGENT_BUSY_MESSAGE, None)
        await agent_message_service.complete_message(
            message_id, AGENT_BUSY_MESSAGE, None, None
        )

    async def cancel_message(
        self,
        user_id: str,
        message_id: int,
        partial_assistant_message: str | None = None,
    ) -> None:

        if not await rate_limit_service.allow(user_id, "cancelMessage", 1, 1):
            raise ValueError("取消消息过于频繁，请稍后再试")

        await redis_service.set_cancel_flag(user_id, message_id)
        if partial_assistant_message:

            await agent_message_service.interrupt_message(
                user_id, message_id, partial_assistant_message
            )
        else:
            await agent_message_service.cancel_message(user_id, message_id)

    async def get_consult_context(self, user_id: str) -> dict | None:

        snapshot = await redis_service.get_consult_product(user_id)
        if not snapshot:
            return None
        return {

            "productId": snapshot.get("product_id") or snapshot.get("productId"),
            "productName": snapshot.get("product_name") or snapshot.get("productName"),
            "active": await redis_service.is_consult_active(user_id),
        }

agent_orchestrator = AgentOrchestrator()
