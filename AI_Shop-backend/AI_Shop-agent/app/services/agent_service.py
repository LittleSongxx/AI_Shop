from __future__ import annotations

import structlog

from app.config.settings import get_settings
from app.constants import AGENT_QUEUE_LOW
from app.domain.intent.classifier import resolve_intent
from app.domain.intent.types import IntentDecision, IntentKind, NextAction, RequestMode
from app.harness.agents.contracts import VerifiedImageContext, VisualSubject
from app.harness.guardrails.input_guard import InputGuardrail
from app.harness.metrics.runtime_sensors import RESPONSE_VERIFIER_TOTAL, measure_agent_stage
from app.memory.session_memory_service import session_memory_service
from app.observability.telemetry import current_trace_id, current_traceparent
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
from app.services.shopping_mission_service import shopping_mission_service
from app.services.shopping_profile_service import shopping_profile_service
from app.services.stream_service import stream_service
from app.services.support_case_service import support_case_service
from app.services.support_service import support_service
from app.services.task_service import agent_task_service
from app.services.visual_selection_store import (
    VisualSelectionConflict,
    VisualSelectionExpired,
    visual_selection_store,
)
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
    {
        IntentKind.PRODUCT_SEARCH,
        IntentKind.PRODUCT_CONSULT,
        IntentKind.VISUAL_PRODUCT_SEARCH,
    }
)
_IMAGE_AFTER_SALES_MARKERS = (
    "退款", "退货", "售后", "破损", "损坏", "坏了", "碎了", "错发", "漏发",
    "少发", "缺件", "物流", "快递", "订单", "发票", "地址", "投诉", "支付",
    "付款", "评价", "追评", "确认收货", "取消订单",
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
        image_asset_id: str | None = None,
        rate_limit_scope: str = "sendMessage",
        request_id: str | None = None,
        run_id: str | None = None,
        episode_id: str | None = None,
        traceparent: str | None = None,
        selected_order_reference: dict | None = None,
        evaluation_trial_id: str | None = None,
        evaluation_fault: dict | None = None,
    ) -> dict:
        settings = get_settings()
        # 一次 inspect 同时完成归一化、注入判定和净化，避免同一段文本归一化两遍。
        submitted_text = str(message or "").strip()
        image_asset_id = str(image_asset_id or "").strip() or None
        if not submitted_text and not image_asset_id:
            raise ValueError("请输入咨询内容或上传一张商品图片")
        task_text = submitted_text or "查找图中同款或相似商品"
        verdict = input_guard.inspect(task_text)
        message = verdict.text
        mixed_injection_flags = [
            rule for rule in verdict.matched_rules if rule.startswith("mixed_injection_")
        ]

        # 极短消息（单字/单标点）：too_short 规则把 blocked 置 True，给出引导文案。
        # 与注入拦截的 raise 不同，这里用温和的引导——不是安全事件。
        if verdict.blocked and verdict.matched_rules == ("too_short",):
            raise ValueError("请描述您的具体问题，例如：退款咨询、商品尺寸、物流查询")

        # HTML 源码：不进 LLM，直接引导用户换一种方式描述需求。
        if verdict.html_content and not image_asset_id:
            raise ValueError(
                "看起来您发送了网页代码。请改为描述您的商品需求，"
                "或者直接粘贴商品名称，我来帮您查找。"
            )

        if not message:
            raise ValueError("请输入咨询内容或上传一张商品图片")

        if not await rate_limit_service.allow(user_id, rate_limit_scope, 1, 1):
            raise ValueError("发送消息过于频繁，请稍后再试")

        if settings.ai_chat_limit > 0:
            total = await agent_message_service.count_user_messages(user_id)
            if total >= settings.ai_chat_limit:
                raise ValueError("AI购物体验已经结束")

        # Token 预算检查（软限制）：超限时降级到 FAQ 快速路径，不中断会话。
        # 检查顺序在消息落库之前，避免超预算消息占用历史槽位。
        _token_budget_ok = await rate_limit_service.check_session_token_budget(
            user_id, settings.per_session_token_budget
        ) and await rate_limit_service.check_daily_token_quota(
            user_id, settings.daily_user_token_quota
        )

        # 限流放在注入判定之前：探测注入模式的请求也要消耗配额，否则可以无成本试探。
        if verdict.blocked:
            raise ValueError("检测到异常输入")

        selected_comparison_ids: list[str] | None = None
        if comparison_product_ids:
            selected_comparison_ids = normalize_comparison_ids(comparison_product_ids)
            allowed = set(await shopping_mission_service.allowed_candidate_ids(user_id))
            if any(product_id not in allowed for product_id in selected_comparison_ids):
                raise ValueError("只能比较当前或近期推荐列表中的商品")

        verified_image_context = await self._verify_image_context(
            user_id, image_asset_id
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
        if verified_image_context is not None:
            decision = self._route_verified_image(decision, original_user_text)

        if card:
            filtered_text = await sensitive_word_service.replace(original_user_text)
            safe_message = build_consult_card_message(card, filtered_text)
        else:
            safe_message = await sensitive_word_service.replace(message)

        active_support = await support_service.get_active(user_id)
        queue_name, priority = agent_queue_service.queue_for_decision(decision)
        run_id = str(run_id or new_run_id()).strip()
        request_id = str(request_id or f"req_{run_id}").strip()
        episode_id = str(episode_id or run_id).strip()
        traceparent = str(traceparent or current_traceparent() or "").strip() or None
        selected_order_reference = self._public_order_reference(
            selected_order_reference
        )
        trace_id = current_trace_id()

        # 重复意图快速路径（P1）：10 分钟内同一意图连续触发 ≥ intent_repeat_threshold 次时，
        # 跳过 LLM 直接提示用户转人工。节约 Token，同时给出明确的行动路径。
        # 购物类意图（PRODUCT_SEARCH/PRODUCT_CONSULT）和闲聊不参与计数——用户连续搜索商品是
        # 正常行为，不应被当做重复投诉处理。
        _INTENT_REPEAT_SKIP = frozenset(
            {"CHITCHAT", "UNKNOWN", "PRODUCT_SEARCH", "PRODUCT_CONSULT", "VISUAL_PRODUCT_SEARCH"}
        )
        if decision.intent.value not in _INTENT_REPEAT_SKIP:
            intent_count = await rate_limit_service.record_intent(
                user_id, decision.intent.value, window_seconds=600
            )
            if intent_count >= settings.intent_repeat_threshold:
                _repeat_msg = (
                    "您刚才已多次提到该问题。"
                    "如需人工处理，请点击“转人工”按钮，客服将尽快与您联系。"
                )
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
                agent_msg["requestId"] = request_id
                agent_msg["episodeId"] = episode_id
                agent_msg["traceparent"] = traceparent
                if selected_order_reference:
                    agent_msg["selectedOrderReference"] = selected_order_reference
                _repeat_run_id = agent_msg.get("runId") or run_id
                await agent_message_service.complete_message(
                    agent_msg["messageId"], _repeat_msg, "intent_repeat", None
                )
                await stream_service.push_done(
                    user_id, agent_msg["messageId"], _repeat_msg, "intent_repeat", safe_message
                )
                agent_msg["deliveryState"] = "INTENT_REPEAT"
                agent_msg["assistantMessage"] = _repeat_msg
                episode_service.finish_run("intent_repeat", run_id=_repeat_run_id)
                return agent_msg
        agent_msg = await agent_message_service.save_user_message(
            user_id,
            safe_message,
            decision=decision,
            previous_unresolved_count=previous_unresolved,
            queue_name=queue_name,
            session_id=active_support.get("session_id") if active_support else None,
            run_id=run_id,
            trace_id=trace_id,
            image_asset_id=(
                verified_image_context.asset_id if verified_image_context else None
            ),
            image_snapshot=(
                self._public_image_snapshot(verified_image_context)
                if verified_image_context
                else None
            ),
        )
        agent_msg["requestId"] = request_id
        agent_msg["episodeId"] = episode_id
        agent_msg["traceparent"] = traceparent
        if selected_order_reference:
            agent_msg["selectedOrderReference"] = selected_order_reference
        episode_keep = episode_service.start_run(
            run_id=run_id,
            message_id=int(agent_msg["messageId"]),
            user_id=user_id,
            session_id=agent_msg.get("sessionId"),
            intent=decision.intent.value,
            queue_name=queue_name,
            force_keep=(
                decision.should_handoff
                or decision.intent in _EPISODE_FULL_INTENTS
                or verified_image_context is not None
                or selected_order_reference is not None
            ),
        )
        agent_msg["episodeKeep"] = episode_keep
        episode_service.record_step(
            "INTENT_DECISION",
            run_id=run_id,
            node_name="api",
            input_data={"message": original_user_text},
            output_data=decision.model_dump(mode="json"),
        )
        if verified_image_context is not None:
            episode_service.record_step(
                "IMAGE_ASSET_VERIFIED",
                run_id=run_id,
                node_name="api",
                output_data={
                    "assetId": verified_image_context.asset_id,
                    "contentSha256": verified_image_context.content_sha256,
                    "mimeType": verified_image_context.mime_type,
                    "width": verified_image_context.width,
                    "height": verified_image_context.height,
                    "expiresAt": verified_image_context.expires_at,
                },
            )
        if decision.intent in _SHOPPING_MEMORY_INTENTS:
            try:
                durable_profile = await shopping_profile_service.update_profile(
                    user_id,
                    original_user_text,
                    source_message_id=int(agent_msg["messageId"]),
                )
                mission = await shopping_mission_service.capture_user_turn(
                    user_id,
                    int(agent_msg["messageId"]),
                    original_user_text,
                    durable_profile,
                )
                episode_service.record_step(
                    "SHOPPING_MISSION_UPDATE",
                    run_id=run_id,
                    node_name="api",
                    output_data={
                        "hasMission": bool(mission),
                        "missionId": (mission or {}).get("missionId"),
                        "category": (mission or {}).get("category"),
                        "useCases": list((mission or {}).get("useCases") or [])[:4],
                        "hardConstraints": (mission or {}).get("hardConstraints") or {},
                        "softPreferences": (mission or {}).get("softPreferences") or {},
                        "unknownSlots": (mission or {}).get("unknownSlots") or [],
                        "clarificationCount": int(
                            (mission or {}).get("clarificationCount") or 0
                        ),
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
        if mixed_injection_flags:
            # Persist only rule IDs with the queued task. The removed attack text
            # is neither stored in the normal conversation nor emitted to traces.
            agent_msg["inputSecurityFlags"] = mixed_injection_flags
        if selected_comparison_ids:
            agent_msg["comparisonProductIds"] = selected_comparison_ids
        if verified_image_context:
            agent_msg["verifiedImageContext"] = verified_image_context.model_dump(
                mode="json"
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

        # Token 预算超限时降级到 FAQ 快速路径：先尝试精确匹配，命中则返回缓存答案；
        # 未命中则返回温和的提示，不进 LLM。
        if not _token_budget_ok:
            faq = await rag_retriever.exact_faq_answer(original_user_text)
            if faq:
                answer = await sensitive_word_service.replace(
                    str(faq.get("answer") or "").strip()
                )
                await agent_message_service.complete_message(
                    agent_msg["messageId"], answer, "faq", None
                )
                await stream_service.push_done(
                    user_id, agent_msg["messageId"], answer, "faq", safe_message
                )
                agent_msg["deliveryState"] = "FAQ_FAST_PATH"
                agent_msg["assistantMessage"] = answer
                agent_msg["bizType"] = "faq"
                episode_service.finish_run("faq", run_id=run_id)
                return agent_msg
            # 无 FAQ 命中时给出温和提示，不进 LLM。
            _budget_msg = "今日 AI 咨询配额已达上限，请明日再试，或转人工客服处理。"
            await agent_message_service.complete_message(
                agent_msg["messageId"], _budget_msg, "budget_exceeded", None
            )
            await stream_service.push_done(
                user_id, agent_msg["messageId"], _budget_msg, "budget_exceeded", safe_message
            )
            agent_msg["deliveryState"] = "BUDGET_EXCEEDED"
            agent_msg["assistantMessage"] = _budget_msg
            episode_service.record_step(
                "TOKEN_BUDGET_EXCEEDED", run_id=run_id, node_name="api", status="OK"
            )
            episode_service.finish_run("budget_exceeded", run_id=run_id)
            return agent_msg

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

        task_payload = dict(agent_msg)
        if evaluation_fault is not None:
            task_payload["evaluationTrialId"] = str(
                evaluation_trial_id or ""
            ).strip()
            task_payload["evaluationFault"] = dict(evaluation_fault)
        created = await agent_task_service.create(
            agent_msg["messageId"], user_id, queue_name, priority, task_payload
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
            await agent_queue_service.publish(queue_name, task_payload)
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

    async def send_selected_visual_subject(
        self,
        user_id: str,
        selection_id: str,
        subject_id: str,
    ) -> dict:
        """Resume one server-rendered visual subject selection exactly once."""
        preview = await visual_selection_store.preview(
            selection_id=selection_id,
            subject_id=subject_id,
            user_id=user_id,
        )
        if preview.get("alreadyConsumed"):
            existing = await visual_selection_store.selected_message(selection_id, user_id)
            if existing:
                return existing
            raise VisualSelectionConflict("该图片主体选择正在恢复，请稍后重试")

        settings = get_settings()
        if settings.ai_chat_limit > 0:
            total = await agent_message_service.count_user_messages(user_id)
            if total >= settings.ai_chat_limit:
                raise ValueError("AI购物体验已经结束")
        if await support_service.get_active(user_id):
            raise ValueError("当前会话已转人工，请直接告诉客服需要查找的商品")

        try:
            selected_subject = VisualSubject.model_validate(preview.get("subject") or {})
        except ValueError as exc:
            raise VisualSelectionExpired("图片主体不存在或已失效") from exc
        image_context = await self._verify_image_context(
            user_id, str(preview.get("imageAssetId") or "")
        )
        if image_context is None:
            raise VisualSelectionExpired("图片资产已失效，请重新上传图片")
        image_context = image_context.model_copy(
            update={"selected_subject": selected_subject}
        )

        original_text = str(preview.get("originalText") or "").strip()
        task_text = original_text or "查找图中同款或相似商品"
        verdict = input_guard.inspect(task_text)
        if verdict.blocked or not verdict.text:
            raise ValueError("图片搜索请求校验失败，请重新上传图片")
        safe_message = await sensitive_word_service.replace(verdict.text)
        decision = IntentDecision(
            intent=IntentKind.VISUAL_PRODUCT_SEARCH,
            confidence=1.0,
            next_action=NextAction.TOOL,
            request_mode=RequestMode.READ_QUERY,
            source="visual_subject_selection",
        )

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
        image_snapshot = self._public_image_snapshot(image_context)
        agent_msg, created = await visual_selection_store.consume_with_message_and_task(
            selection_id=selection_id,
            user_id=user_id,
            subject_id=subject_id,
            message=safe_message,
            decision=decision,
            previous_unresolved_count=previous_unresolved,
            queue_name=queue_name,
            priority=priority,
            trace_id=trace_id,
            run_id=run_id,
            image_snapshot=image_snapshot,
            verified_image_context=image_context.model_dump(mode="json"),
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
            "VISUAL_SUBJECT_SELECTED",
            run_id=run_id,
            node_name="api",
            output_data={
                "selectionId": selection_id,
                "subjectId": selected_subject.subject_id,
                "subjectLabel": selected_subject.label,
                "sourceMessageId": preview.get("sourceMessageId"),
            },
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
                "agent_visual_selection_publish_deferred",
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
                output_data={"queue": queue_name, "status": "PENDING_RECOVERY"},
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
        *,
        verified_order_refs: dict | None = None,
        finish_episode: bool = True,
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
                image_context = agent_msg.get("verifiedImageContext")
                evidence = None
                if isinstance(image_context, dict) and image_context.get("asset_id"):
                    evidence = {
                        "imageAssetId": image_context["asset_id"],
                        "imageUnderstandingStatus": "SKIPPED_HANDOFF",
                    }
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
        try:
            history = await agent_message_service.load_recent_history(
                agent_msg["userId"], limit=6
            )
        except Exception as exc:
            logger.warning(
                "support_handoff_history_unavailable", error=type(exc).__name__
            )
            history = []
        verified_refs = dict(verified_order_refs or {})
        if support_case:
            verified_refs.update(
                {
                    "orderId": support_case.get("orderId"),
                    "orderItemId": support_case.get("orderItemId"),
                }
            )
        try:
            handoff_context = await support_service.build_handoff_context(
                agent_msg["userId"],
                safe_message,
                decision,
                history=history,
                verified_order_refs=verified_refs,
            )
        except Exception as exc:
            logger.warning(
                "support_handoff_context_build_failed", error=type(exc).__name__
            )
            handoff_context = None
        summary = support_service.build_summary(safe_message, decision, history)
        session = await support_service.create_or_get(
            agent_msg["userId"],
            agent_msg["messageId"],
            decision,
            decision.get("handoff_reason") or "AI_HANDOFF",
            summary,
            handoff_context,
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
        if finish_episode:
            episode_service.finish_run(
                "handoff", run_id=agent_msg.get("runId"), force_keep=True
            )
        return agent_msg

    async def _verify_image_context(
        self, user_id: str, image_asset_id: str | None
    ) -> VerifiedImageContext | None:
        if not image_asset_id:
            return None
        try:
            verified = await java_internal_client.verify_agent_image(
                user_id, image_asset_id
            )
            return VerifiedImageContext.model_validate(
                {
                    "asset_id": verified.get("asset_id"),
                    "moderation_status": verified.get("moderation_status"),
                    "content_sha256": verified.get("content_sha256"),
                    "mime_type": verified.get("mime_type"),
                    "width": verified.get("width"),
                    "height": verified.get("height"),
                    "scene": verified.get("scene"),
                    "expires_at": verified.get("expires_at"),
                }
            )
        except Exception as exc:
            logger.warning(
                "agent_image_verify_failed",
                user_id=user_id,
                error=type(exc).__name__,
            )
            raise ValueError("图片资产不可用、尚未通过审核或已过期，请重新上传") from exc

    @staticmethod
    def _public_order_reference(reference: dict | None) -> dict | None:
        if not isinstance(reference, dict):
            return None
        allowlist = (
            "targetType",
            "targetId",
            "orderId",
            "orderItemId",
            "productId",
            "productName",
            "propertyInfo",
            "amount",
            "orderStatus",
            "orderStatusName",
            "orderTime",
        )
        result = {
            key: reference.get(key)
            for key in allowlist
            if reference.get(key) is not None
        }
        if not result.get("targetType") or not result.get("targetId"):
            return None
        return result

    @staticmethod
    def _route_verified_image(
        decision: IntentDecision, user_text: str
    ) -> IntentDecision:
        if not any(marker in user_text for marker in _IMAGE_AFTER_SALES_MARKERS):
            return decision.model_copy(
                update={
                    "intent": IntentKind.VISUAL_PRODUCT_SEARCH,
                    "confidence": 0.99,
                    "next_action": NextAction.TOOL,
                    "request_mode": RequestMode.READ_QUERY,
                    "source": "verified_image_route",
                }
            )
        return decision

    @staticmethod
    def _public_image_snapshot(context: VerifiedImageContext) -> dict:
        return {
            "assetId": context.asset_id,
            "contentSha256": context.content_sha256,
            "mimeType": context.mime_type,
            "width": context.width,
            "height": context.height,
            "scene": context.scene,
            "moderationStatus": context.moderation_status,
            "expiresAt": context.expires_at,
        }

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
        try:
            history = await agent_message_service.load_recent_history(user_id, limit=6)
        except Exception as exc:
            logger.warning(
                "support_request_history_unavailable", error=type(exc).__name__
            )
            history = []
        handoff_context = await support_service.build_handoff_context(
            user_id,
            reason or "用户主动申请人工客服",
            payload,
            history=history,
        )
        summary = support_service.build_summary(
            reason or "用户主动申请人工客服", payload, history
        )
        session = await support_service.create_or_get(
            user_id,
            source_message_id,
            payload,
            "USER_REQUEST",
            summary,
            handoff_context,
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
