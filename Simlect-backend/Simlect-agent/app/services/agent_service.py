from __future__ import annotations

import uuid

import structlog

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_LOW
from app.domain.intent.classifier import resolve_intent
from app.harness.guardrails.input_guard import InputGuardrail
from app.rag.retriever import rag_retriever
from app.services.agent_queue_service import agent_queue_service
from app.services.message_service import agent_message_service
from app.services.product_snapshot_service import product_snapshot_service
from app.services.rate_limit_service import rate_limit_service
from app.services.redis_service import redis_service
from app.services.sensitive_word_service import sensitive_word_service
from app.services.shopping_profile_service import shopping_profile_service
from app.services.stream_service import stream_service
from app.services.support_service import support_service
from app.services.task_service import agent_task_service
from app.utils.product_consult import build_consult_card_message, parse_consult_card

logger = structlog.get_logger()
input_guard = InputGuardrail()
AGENT_BUSY_MESSAGE = "当前咨询较多，请稍后再试，或缩小商品需求范围"
SUPPORT_TRANSFER_MESSAGE = "已为您转接人工客服，客服接入后会在当前对话中回复。"


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

        card, original_user_text = parse_consult_card(message)
        if card and card.get("productId"):
            await product_snapshot_service.resolve_active_snapshot(user_id, card)
            await redis_service.set_consult_active(user_id)

        await shopping_profile_service.update_profile(user_id, original_user_text)
        previous_unresolved = await agent_message_service.get_unresolved_count(user_id)
        consult_card = await redis_service.get_consult_product(user_id)
        decision = await resolve_intent(
            user_id,
            original_user_text,
            from_product=from_product,
            consult_card=consult_card,
            message_card=card,
            unresolved_count=previous_unresolved,
            allow_llm=False,
        )

        if card:
            filtered_text = await sensitive_word_service.replace(original_user_text)
            safe_message = build_consult_card_message(card, filtered_text)
        else:
            safe_message = await sensitive_word_service.replace(message)

        active_support = await support_service.get_active(user_id)
        queue_name, priority = agent_queue_service.queue_for_decision(decision)
        trace_id = uuid.uuid4().hex
        agent_msg = await agent_message_service.save_user_message(
            user_id,
            safe_message,
            decision=decision,
            previous_unresolved_count=previous_unresolved,
            queue_name=queue_name,
            session_id=active_support.get("session_id") if active_support else None,
            trace_id=trace_id,
        )
        agent_msg["fromProduct"] = from_product

        if active_support:
            await support_service.route_user_message(
                active_support, user_id, safe_message, agent_msg["messageId"]
            )
            await agent_message_service.complete_message(
                agent_msg["messageId"], "", "human_support", None
            )
            await stream_service.push_done(
                user_id,
                agent_msg["messageId"],
                "",
                "human_support",
                safe_message,
            )
            agent_msg["supportSession"] = support_service.public_session(active_support)
            agent_msg["deliveryState"] = "HUMAN_SUPPORT"
            return agent_msg

        if decision.should_handoff:
            return await self._transfer_to_support(
                agent_msg, safe_message, decision.model_dump(mode="json")
            )

        if decision.intent.value in {
            "CHAT",
            "INVOICE",
            "ADDRESS_CHANGE",
            "AFTERSALES_UNKNOWN",
        }:
            faq = await rag_retriever.exact_faq_answer(original_user_text)
            if faq:
                answer = await sensitive_word_service.replace(
                    str(faq.get("answer") or "").strip()
                )
                source_refs = [
                    {
                        "type": "faq",
                        "questionId": faq.get("questionId"),
                        "question": faq.get("question"),
                        "source": faq.get("source") or "FAQ",
                        "version": faq.get("version"),
                    }
                ]
                await agent_message_service.complete_message(
                    agent_msg["messageId"],
                    answer,
                    "faq",
                    None,
                    source_refs,
                )
                await stream_service.push_done(
                    user_id,
                    agent_msg["messageId"],
                    answer,
                    "faq",
                    safe_message,
                )
                agent_msg["deliveryState"] = "FAQ_FAST_PATH"
                agent_msg["assistantMessage"] = answer
                agent_msg["bizType"] = "faq"
                agent_msg["sourceRefs"] = source_refs
                return agent_msg

        if (
            queue_name == AGENT_QUEUE_LOW
            and await agent_task_service.count_pending() >= settings.task_queue_max
        ):
            await stream_service.push_error(
                user_id, agent_msg["messageId"], AGENT_BUSY_MESSAGE, "overload"
            )
            await agent_message_service.complete_message(
                agent_msg["messageId"], AGENT_BUSY_MESSAGE, "overload", None
            )
            agent_msg["deliveryState"] = "DEGRADED"
            agent_msg["assistantMessage"] = AGENT_BUSY_MESSAGE
            return agent_msg

        created = await agent_task_service.create(
            agent_msg["messageId"], user_id, queue_name, priority, agent_msg
        )
        if not created:
            agent_msg["deliveryState"] = "DUPLICATE"
            return agent_msg

        try:
            await agent_queue_service.publish(queue_name, agent_msg)
            await agent_task_service.mark_queued(agent_msg["messageId"])
            agent_msg["deliveryState"] = "QUEUED"
        except Exception as exc:
            logger.warning(
                "agent_task_publish_deferred",
                message_id=agent_msg["messageId"],
                queue_name=queue_name,
                error=str(exc),
            )
            agent_msg["deliveryState"] = "PENDING_RECOVERY"
        return agent_msg

    async def _transfer_to_support(
        self,
        agent_msg: dict,
        safe_message: str,
        decision: dict,
    ) -> dict:
        summary = support_service.build_summary(safe_message, decision)
        session = await support_service.create_or_get(
            agent_msg["userId"],
            agent_msg["messageId"],
            decision,
            decision.get("handoff_reason") or "AI_HANDOFF",
            summary,
        )
        await agent_message_service.bind_session(
            agent_msg["messageId"], session["session_id"]
        )
        await support_service.route_user_message(
            session,
            agent_msg["userId"],
            safe_message,
            agent_msg["messageId"],
        )
        await support_service.add_badcase(
            agent_msg["messageId"],
            "HUMAN_HANDOFF",
            decision.get("handoff_reason") or "转人工",
            {"decision": decision},
        )
        await agent_message_service.complete_message(
            agent_msg["messageId"],
            SUPPORT_TRANSFER_MESSAGE,
            "human_support",
            None,
        )
        await stream_service.push_done(
            agent_msg["userId"],
            agent_msg["messageId"],
            SUPPORT_TRANSFER_MESSAGE,
            "human_support",
            safe_message,
        )
        agent_msg["sessionId"] = session["session_id"]
        agent_msg["supportSession"] = support_service.public_session(session)
        agent_msg["deliveryState"] = "HUMAN_SUPPORT"
        agent_msg["assistantMessage"] = SUPPORT_TRANSFER_MESSAGE
        return agent_msg

    async def request_human(
        self,
        user_id: str,
        reason: str | None = None,
        source_message_id: int | None = None,
    ) -> dict:
        decision = await resolve_intent(
            user_id,
            reason or "转人工客服",
            allow_llm=False,
        )
        payload = decision.model_dump(mode="json")
        payload["handoff_reason"] = "USER_REQUEST"
        summary = support_service.build_summary(reason or "用户主动申请人工客服", payload)
        session = await support_service.create_or_get(
            user_id, source_message_id, payload, "USER_REQUEST", summary
        )
        return support_service.public_session(session)

    async def cancel_human(self, user_id: str) -> dict | None:
        session = await support_service.get_active(user_id)
        if not session:
            return None
        session = await support_service.cancel_by_user(session["session_id"], user_id)
        return support_service.public_session(session) if session else None

    async def human_status(self, user_id: str) -> dict | None:
        session = await support_service.get_active(user_id)
        return support_service.public_session(session) if session else None

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
