from __future__ import annotations

import structlog

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_LOW
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.types import IntentDecision, IntentKind, NextAction
from app.harness.guardrails.input_guard import InputGuardrail
from app.harness.metrics.runtime_sensors import RESPONSE_VERIFIER_TOTAL, measure_agent_stage
from app.memory.session_memory_service import session_memory_service
from app.observability.telemetry import current_trace_id
from app.rag.retriever import rag_retriever
from app.services.agent_queue_service import agent_queue_service
from app.services.badcase_service import badcase_service
from app.services.episode_service import episode_service, new_run_id
from app.services.java_internal_client import java_internal_client
from app.services.judge_service import judge_service
from app.services.message_service import agent_message_service
from app.services.order_reference_resolver import ORDER_REFERENCE_INTENTS
from app.services.order_selection_store import order_selection_store
from app.services.product_comparison_service import normalize_comparison_ids
from app.services.product_snapshot_service import product_snapshot_service
from app.services.rate_limit_service import rate_limit_service
from app.services.redis_service import redis_service
from app.services.response_verifier import response_verifier
from app.services.sensitive_word_service import sensitive_word_service
from app.services.shopping_need_service import shopping_need_service
from app.services.shopping_profile_service import shopping_profile_service
from app.services.stream_service import stream_service
from app.services.support_case_service import support_case_service
from app.services.support_service import support_service
from app.services.task_service import agent_task_service
from app.utils.product_consult import build_consult_card_message, parse_consult_card

logger = structlog.get_logger()
input_guard = InputGuardrail()
AGENT_BUSY_MESSAGE = "当前咨询较多，请稍后再试，或缩小商品需求范围"
SUPPORT_TRANSFER_MESSAGE = "已为您转接人工客服，客服接入后会在当前对话中回复。"
_EPISODE_FULL_INTENTS = frozenset(
    {
        IntentKind.REFUND,
        IntentKind.REFUND_STATUS,
        IntentKind.CONFIRM_RECEIPT,
        IntentKind.CANCEL_ORDER,
        IntentKind.PRODUCT_REVIEW,
        IntentKind.RECOMMENT,
        IntentKind.COMPLAINT,
        IntentKind.PAYMENT_ISSUE,
        IntentKind.DAMAGED_OR_WRONG_ITEM,
        IntentKind.ADDRESS_CHANGE,
        IntentKind.INVOICE,
        IntentKind.AFTERSALES_UNKNOWN,
    }
)
_SHOPPING_MEMORY_INTENTS = frozenset(
    {IntentKind.PRODUCT_SEARCH, IntentKind.PRODUCT_CONSULT}
)
_FORCED_CASE_INTENTS = frozenset(
    {
        IntentKind.COMPLAINT.value,
        IntentKind.PAYMENT_ISSUE.value,
        IntentKind.DAMAGED_OR_WRONG_ITEM.value,
        IntentKind.REFUND.value,
        IntentKind.REFUND_STATUS.value,
    }
)


class AgentOrchestrator:

    async def send_message(
        self,
        user_id: str,
        message: str,
        from_product: bool = False,
        consult_product_id: str | None = None,
        comparison_product_ids: list[str] | None = None,
        image_path: str | None = None,
        image_moderation_id: int | None = None,
        rate_limit_scope: str = "sendMessage",
    ) -> dict:
        settings = get_settings()
        # 一次 inspect 同时完成归一化、注入判定和净化，避免同一段文本归一化两遍。
        verdict = input_guard.inspect(message)
        message = verdict.text
        if not message:
            raise ValueError("请输入咨询内容")

        if not await rate_limit_service.allow(user_id, rate_limit_scope, 1, 1):
            raise ValueError("发送消息过于频繁，请稍后再试")

        if settings.ai_chat_limit > 0:
            total = await agent_message_service.count_user_messages(user_id)
            if total >= settings.ai_chat_limit:
                raise ValueError("AI购物体验已经结束")

        # 限流放在注入判定之前：探测注入模式的请求也要消耗配额，否则可以无成本试探。
        if verdict.blocked:
            raise ValueError("检测到异常输入")

        selected_comparison_ids: list[str] | None = None
        if comparison_product_ids:
            selected_comparison_ids = normalize_comparison_ids(comparison_product_ids)
            allowed = set(await shopping_need_service.allowed_candidate_ids(user_id))
            if any(product_id not in allowed for product_id in selected_comparison_ids):
                raise ValueError("只能比较当前或近期推荐列表中的商品")

        image_evidence = await support_case_service.verify_image(
            user_id, image_path, image_moderation_id
        )

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

        previous_unresolved = await agent_message_service.get_unresolved_count(user_id)
        # A2/A3：会话级意图延续 + 死循环检测的输入。一次查询同时给两处用。
        recent_intents = await agent_message_service.get_recent_intents(user_id)
        session_intent = recent_intents[0] if recent_intents else None
        consult_card = await self._resolve_consult_card_for_routing(user_id)
        with measure_agent_stage("intent"):
            decision = await resolve_intent(
                user_id,
                original_user_text,
                from_product=from_product,
                consult_card=consult_card,
                message_card=card,
                unresolved_count=previous_unresolved,
                allow_llm=False,
                session_intent=session_intent,
                recent_intents=recent_intents,
                after_sales_workflow=True,
            )

        if card:
            filtered_text = await sensitive_word_service.replace(original_user_text)
            safe_message = build_consult_card_message(card, filtered_text)
        else:
            safe_message = await sensitive_word_service.replace(message)

        active_support = await support_service.get_active(user_id)
        queue_name, priority = agent_queue_service.queue_for_decision(decision)
        run_id = new_run_id()
        trace_id = current_trace_id()
        agent_msg = await agent_message_service.save_user_message(
            user_id,
            safe_message,
            decision=decision,
            previous_unresolved_count=previous_unresolved,
            queue_name=queue_name,
            session_id=active_support.get("session_id") if active_support else None,
            run_id=run_id,
            trace_id=trace_id,
        )
        episode_keep = episode_service.start_run(
            run_id=run_id,
            message_id=int(agent_msg["messageId"]),
            user_id=user_id,
            session_id=agent_msg.get("sessionId"),
            intent=decision.intent.value,
            queue_name=queue_name,
            force_keep=decision.should_handoff or decision.intent in _EPISODE_FULL_INTENTS,
        )
        agent_msg["episodeKeep"] = episode_keep
        episode_service.record_step(
            "INTENT_DECISION",
            run_id=run_id,
            node_name="api",
            input_data={"message": original_user_text},
            output_data=decision.model_dump(mode="json"),
        )
        if decision.intent in _SHOPPING_MEMORY_INTENTS:
            try:
                durable_profile = await shopping_profile_service.update_profile(
                    user_id,
                    original_user_text,
                    source_message_id=int(agent_msg["messageId"]),
                )
                need = await shopping_need_service.capture_user_turn(
                    user_id,
                    int(agent_msg["messageId"]),
                    original_user_text,
                    durable_profile,
                )
                episode_service.record_step(
                    "SHOPPING_NEED_UPDATE",
                    run_id=run_id,
                    node_name="api",
                    output_data={
                        "hasNeed": bool(need),
                        "missingSlots": (need or {}).get("missingSlots") or [],
                    },
                )
            except Exception as exc:
                logger.warning(
                    "shopping_memory_update_failed",
                    message_id=agent_msg["messageId"],
                    error=type(exc).__name__,
                )
        try:
            await badcase_service.detect_user_correction(
                user_id=user_id,
                current_message_id=int(agent_msg["messageId"]),
                user_text=original_user_text,
            )
        except Exception as exc:
            logger.warning(
                "user_correction_badcase_capture_failed",
                message_id=agent_msg["messageId"],
                error=type(exc).__name__,
            )
        agent_msg["fromProduct"] = from_product
        if selected_comparison_ids:
            agent_msg["comparisonProductIds"] = selected_comparison_ids
        if image_evidence:
            agent_msg["imageEvidence"] = image_evidence
            agent_msg["imageUrl"] = java_internal_client.support_image_url(
                image_evidence["path"]
            )

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
            episode_service.record_step(
                "HUMAN_SESSION_ROUTE",
                run_id=run_id,
                node_name="api",
                output_data={"sessionId": active_support.get("session_id")},
            )
            episode_service.finish_run(
                "human_support", run_id=run_id, force_keep=True
            )
            return agent_msg

        if decision.should_handoff:
            return await self._transfer_to_support(
                agent_msg, safe_message, decision.model_dump(mode="json")
            )

        if decision.intent.value == "CHAT":
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
                verification = response_verifier.verify(
                    assistant=answer,
                    biz_type="faq",
                    tools_called=[],
                    source_refs=source_refs,
                    has_pending_action=False,
                )
                RESPONSE_VERIFIER_TOTAL.labels(
                    result=(
                        "pass" if verification.passed else verification.action.lower()
                    ),
                    rule=(
                        verification.issues[0].code if verification.issues else "NONE"
                    ),
                ).inc()
                answer = verification.assistant
                episode_service.update_run(
                    run_id=run_id,
                    quality=verification.quality(),
                    reward_signals={
                        "verifier": {
                            "passed": verification.passed,
                            "action": verification.action,
                            "issueCodes": [
                                issue.code for issue in verification.issues
                            ],
                        }
                    },
                )
                episode_service.record_step(
                    "RESPONSE_VERIFIER",
                    run_id=run_id,
                    node_name="api",
                    status="OK" if verification.passed else "BLOCKED",
                    output_data=verification.quality(),
                )
                if not verification.passed:
                    try:
                        await badcase_service.add_candidate(
                            int(agent_msg["messageId"]),
                            "VERIFIER_FAILURE",
                            verification.issues[0].detail,
                            run_id=run_id,
                            source="VERIFIER",
                            severity=verification.issues[0].severity,
                            snapshot={
                                "action": verification.action,
                                "issues": [
                                    issue.public() for issue in verification.issues
                                ],
                            },
                        )
                    except Exception as exc:
                        logger.warning(
                            "faq_verifier_badcase_capture_failed",
                            message_id=agent_msg["messageId"],
                            error=type(exc).__name__,
                        )
                await agent_message_service.complete_message(
                    agent_msg["messageId"],
                    answer,
                    "faq",
                    None,
                    source_refs,
                )
                await agent_message_service.reset_unresolved_count(
                    agent_msg["messageId"]
                )
                await stream_service.push_done(
                    user_id,
                    agent_msg["messageId"],
                    answer,
                    "faq",
                    safe_message,
                    source_refs,
                )
                agent_msg["deliveryState"] = "FAQ_FAST_PATH"
                agent_msg["assistantMessage"] = answer
                agent_msg["bizType"] = "faq"
                agent_msg["sourceRefs"] = source_refs
                episode_service.record_step(
                    "FAQ_FAST_PATH",
                    run_id=run_id,
                    node_name="api",
                    output_data={"sourceRefs": source_refs},
                )
                episode_service.finish_run(
                    "faq", run_id=run_id, force_keep=episode_keep
                )
                judge_service.enqueue(
                    run_id=run_id,
                    message_id=int(agent_msg["messageId"]),
                    user_text=original_user_text,
                    assistant=answer,
                    intent=decision.intent.value,
                    tools_called=[],
                    source_refs=source_refs,
                    verifier_passed=verification.passed,
                )
                return agent_msg

        if (
            queue_name == AGENT_QUEUE_LOW
            and await agent_task_service.count_pending() >= settings.task_queue_max
        ):
            await agent_message_service.complete_message(
                agent_msg["messageId"], AGENT_BUSY_MESSAGE, "overload", None
            )
            await stream_service.push_error(
                user_id, agent_msg["messageId"], AGENT_BUSY_MESSAGE, "overload"
            )
            await agent_message_service.reset_unresolved_count(
                agent_msg["messageId"]
            )
            agent_msg["deliveryState"] = "DEGRADED"
            agent_msg["assistantMessage"] = AGENT_BUSY_MESSAGE
            episode_service.record_step(
                "OVERLOAD",
                run_id=run_id,
                node_name="api",
                status="ERROR",
                error_code="QUEUE_OVERLOAD",
            )
            episode_service.finish_run(
                "degraded", run_id=run_id, force_keep=True
            )
            return agent_msg

        created = await agent_task_service.create(
            agent_msg["messageId"], user_id, queue_name, priority, agent_msg
        )
        if not created:
            agent_msg["deliveryState"] = "DUPLICATE"
            return agent_msg

        try:
            # 先原子预占 DISPATCHING，再 publish。Consumer 允许直接认领
            # DISPATCHING，因此 publish confirm 与 QUEUED 落库之间不存在丢消息窗口；
            # 若进程在 publish 前崩溃，恢复扫描只会在预占超时后重发一次。
            if not await agent_task_service.mark_dispatching(agent_msg["messageId"]):
                agent_msg["deliveryState"] = "DUPLICATE"
                return agent_msg
            await agent_queue_service.publish(queue_name, agent_msg)
            await agent_task_service.mark_queued(agent_msg["messageId"])
            agent_msg["deliveryState"] = "QUEUED"
            episode_service.record_step(
                "MQ_PUBLISH",
                run_id=run_id,
                node_name="api",
                output_data={"queue": queue_name, "status": "QUEUED"},
            )
        except Exception as exc:
            logger.warning(
                "agent_task_publish_deferred",
                message_id=agent_msg["messageId"],
                queue_name=queue_name,
                error=str(exc),
            )
            agent_msg["deliveryState"] = "PENDING_RECOVERY"
            episode_service.record_step(
                "MQ_PUBLISH",
                run_id=run_id,
                node_name="api",
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc),
                output_data={"queue": queue_name, "status": "PENDING_RECOVERY"},
            )
        return agent_msg

    async def send_selected_order_candidate(
        self,
        user_id: str,
        selection_id: str,
        target_type: str,
        target_id: str,
    ) -> dict:
        """Persist one candidate choice and its task in a single transaction."""

        preview = await order_selection_store.preview(
            selection_id=selection_id,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )
        if preview.get("alreadyConsumed"):
            existing = await order_selection_store.selected_message(selection_id, user_id)
            if existing:
                return existing
            raise ValueError("该候选消息正在恢复，请稍后重试")

        intent_value = str(preview.get("intent") or "")
        if intent_value not in ORDER_REFERENCE_INTENTS:
            raise ValueError("订单候选意图无效，请重新发起")
        intent = IntentKind(intent_value)
        candidate = dict(preview.get("candidate") or {})
        if not candidate:
            raise ValueError("订单候选已失效，请重新发起")

        settings = get_settings()
        if settings.ai_chat_limit > 0:
            total = await agent_message_service.count_user_messages(user_id)
            if total >= settings.ai_chat_limit:
                raise ValueError("AI购物体验已经结束")

        active_support = await support_service.get_active(user_id)
        if active_support:
            raise ValueError("当前会话已转人工，请直接告诉客服要处理的订单")

        context = preview.get("context") or {}
        raw_decision = context.get("intentDecision") if isinstance(context, dict) else None
        try:
            decision = IntentDecision.model_validate(raw_decision or {})
        except (TypeError, ValueError):
            decision = IntentDecision(
                intent=intent,
                confidence=1.0,
                next_action=NextAction.TOOL,
                source="order_selection",
            )
        entities = dict(decision.entities)
        if candidate.get("orderId"):
            entities["orderId"] = str(candidate["orderId"])
        if candidate.get("orderItemId"):
            entities["orderItemId"] = str(candidate["orderItemId"])
        decision = decision.model_copy(
            update={
                "intent": intent,
                "confidence": max(decision.confidence, 0.99),
                "entities": entities,
                "next_action": NextAction.TOOL,
                "handoff_reason": None,
                "source": "order_selection",
            }
        )

        label = {
            IntentKind.REFUND: "退款",
            IntentKind.REFUND_STATUS: "查询退款进度",
            IntentKind.QUERY_LOGISTICS: "查询物流",
            IntentKind.QUERY_FULFILLMENT: "查询发货状态",
            IntentKind.CANCEL_ORDER: "取消订单",
            IntentKind.CONFIRM_RECEIPT: "确认收货",
            IntentKind.PRODUCT_REVIEW: "评价",
            IntentKind.RECOMMENT: "追评",
            IntentKind.QUERY_COMMENT: "查看评价",
            IntentKind.ADDRESS_CHANGE: "处理地址问题",
            IntentKind.INVOICE: "处理发票问题",
            IntentKind.DAMAGED_OR_WRONG_ITEM: "处理商品问题",
        }.get(intent, "继续处理")
        target_label = str(candidate.get("productName") or target_id)
        original = str(preview.get("originalText") or "").strip()
        readable = f"选择“{target_label}”订单继续{label}。"
        if original:
            readable += f"原诉求：{original}"
        verdict = input_guard.inspect(readable)
        if verdict.blocked:
            raise ValueError("订单候选内容校验失败，请重新发起")
        safe_message = await sensitive_word_service.replace(verdict.text)

        await redis_service.pause_consult(user_id)
        previous_unresolved = await agent_message_service.get_unresolved_count(user_id)
        queue_name, priority = agent_queue_service.queue_for_decision(decision)
        if (
            queue_name == AGENT_QUEUE_LOW
            and await agent_task_service.count_pending() >= settings.task_queue_max
        ):
            raise ValueError(AGENT_BUSY_MESSAGE)

        run_id = new_run_id()
        trace_id = current_trace_id()
        selected_reference = {
            **candidate,
            "intent": intent.value,
            "selectionId": selection_id,
            "expiresAt": preview.get("expiresAt"),
        }
        agent_msg, created = await order_selection_store.consume_with_message_and_task(
            selection_id=selection_id,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            message=safe_message,
            decision=decision,
            previous_unresolved_count=previous_unresolved,
            queue_name=queue_name,
            priority=priority,
            run_id=run_id,
            trace_id=trace_id,
            selected_reference=selected_reference,
        )
        if not created:
            return agent_msg

        episode_keep = episode_service.start_run(
            run_id=run_id,
            message_id=int(agent_msg["messageId"]),
            user_id=user_id,
            session_id=None,
            intent=decision.intent.value,
            queue_name=queue_name,
            force_keep=True,
        )
        agent_msg["episodeKeep"] = episode_keep
        episode_service.record_step(
            "ORDER_TARGET_SELECTED",
            run_id=run_id,
            node_name="api",
            output_data=selected_reference,
        )

        try:
            if not await agent_task_service.mark_dispatching(agent_msg["messageId"]):
                agent_msg["deliveryState"] = "DUPLICATE"
                return agent_msg
            await agent_queue_service.publish(queue_name, agent_msg)
            await agent_task_service.mark_queued(agent_msg["messageId"])
            agent_msg["deliveryState"] = "QUEUED"
            episode_service.record_step(
                "MQ_PUBLISH",
                run_id=run_id,
                node_name="api",
                output_data={"queue": queue_name, "status": "QUEUED"},
            )
        except Exception as exc:
            logger.warning(
                "agent_order_selection_publish_deferred",
                selection_id=selection_id,
                message_id=agent_msg["messageId"],
                queue_name=queue_name,
                error=str(exc),
            )
            agent_msg["deliveryState"] = "PENDING_RECOVERY"
            episode_service.record_step(
                "MQ_PUBLISH",
                run_id=run_id,
                node_name="api",
                status="ERROR",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
        return agent_msg

    async def _resolve_consult_card_for_routing(self, user_id: str) -> dict | None:
        """Resolve the active consult card for pre-classification.

        The graph's ``build_context_node`` already falls back to the durable
        session-memory state when the Redis consult key has expired.  This
        routing pass must use the same fallback, otherwise the persisted intent
        and the queue it selects can disagree with what the graph later sees.
        """
        cached = await redis_service.get_consult_product(user_id)
        if cached:
            return cached
        try:
            memory = await session_memory_service.load(user_id, redis_service.client)
        except Exception as exc:
            logger.warning("consult_card_memory_fallback_failed", user_id=user_id, error=str(exc))
            return None
        return memory.state.get("consultProduct")

    async def _transfer_to_support(
        self,
        agent_msg: dict,
        safe_message: str,
        decision: dict,
    ) -> dict:
        support_case = None
        intent = str(decision.get("intent") or "")
        if intent in _FORCED_CASE_INTENTS:
            try:
                entities = (
                    decision.get("entities")
                    if isinstance(decision.get("entities"), dict)
                    else {}
                )
                evidence = dict(agent_msg.get("imageEvidence") or {}) or None
                if evidence is not None:
                    evidence.setdefault("vlmStatus", "SKIPPED_HANDOFF")
                support_case = await support_case_service.create(
                    agent_msg["userId"],
                    support_case_service.category_for_intent(intent, safe_message),
                    safe_message,
                    order_id=entities.get("orderId"),
                    order_item_id=entities.get("orderItemId"),
                    evidence=evidence,
                    source_message_id=agent_msg.get("messageId"),
                    run_id=agent_msg.get("runId"),
                    idempotency_key=(
                        f"forced:{agent_msg['userId']}:{agent_msg['messageId']}:{intent}"
                    ),
                    priority="CRITICAL",
                    forced_handoff=True,
                )
            except Exception as exc:
                logger.warning(
                    "forced_support_case_create_failed",
                    message_id=agent_msg.get("messageId"),
                    intent=intent,
                    error=type(exc).__name__,
                )
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
        if support_case:
            try:
                support_case = await support_case_service.link_session(
                    support_case["caseId"], session["session_id"]
                )
            except Exception as exc:
                logger.warning(
                    "support_case_session_link_failed",
                    case_id=support_case.get("caseId"),
                    session_id=session.get("session_id"),
                    error=type(exc).__name__,
                )
        await support_service.route_user_message(
            session,
            agent_msg["userId"],
            safe_message,
            agent_msg["messageId"],
        )
        handoff_reason = decision.get("handoff_reason") or "AI_HANDOFF"
        if handoff_reason in {
            "AI_HANDOFF",
            "LOW_CONFIDENCE",
            "REPEATED_INTENT",
            "REPEATED_UNRESOLVED",
        }:
            await support_service.add_badcase(
                agent_msg["messageId"],
                "ABNORMAL_HANDOFF",
                handoff_reason,
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
        if support_case:
            agent_msg["supportCase"] = support_case
        agent_msg["deliveryState"] = "HUMAN_SUPPORT"
        agent_msg["assistantMessage"] = SUPPORT_TRANSFER_MESSAGE
        episode_service.record_step(
            "HANDOFF",
            run_id=agent_msg.get("runId"),
            node_name="support",
            output_data={
                "reason": decision.get("handoff_reason") or "AI_HANDOFF",
                "sessionId": session.get("session_id"),
                "caseId": (support_case or {}).get("caseId"),
            },
        )
        episode_service.finish_run(
            "handoff", run_id=agent_msg.get("runId"), force_keep=True
        )
        return agent_msg

    async def request_human(
        self,
        user_id: str,
        reason: str | None = None,
        source_message_id: int | None = None,
    ) -> dict:
        with measure_agent_stage("intent"):
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
            changed = await agent_message_service.interrupt_message(
                user_id, message_id, partial_assistant_message
            )
        else:
            changed = await agent_message_service.cancel_message(user_id, message_id)
        if changed:
            await agent_task_service.cancel(message_id, user_id)

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
