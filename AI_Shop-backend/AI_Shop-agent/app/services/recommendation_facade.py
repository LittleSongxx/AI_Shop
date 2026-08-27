from __future__ import annotations

import structlog

from app.domain.recommendation.contracts import RecommendationRequest, RecommendationResponse
from app.harness.agents.contracts import VisualSubject
from app.observability.telemetry import get_tracer
from app.services.agent_service import agent_orchestrator
from app.services.episode_service import bind_episode, episode_service, new_run_id
from app.services.product_service import product_service
from app.services.recommendation_contract_service import (
    build_response,
    parse_legacy_product_cards,
)
from app.services.visual_selection_store import visual_selection_store
from app.visual.search_service import visual_product_search_service

logger = structlog.get_logger()
tracer = get_tracer()

_BLOCKING_VISUAL_CODES = frozenset(
    {
        "VISUAL_SEARCH_DISABLED",
        "VISUAL_PROVIDER_UNAVAILABLE",
        "VISUAL_PROVIDER_TIMEOUT",
        "VISUAL_PROVIDER_AUTH",
        "VISUAL_PROVIDER_QUOTA",
        "VISUAL_SEARCH_ERROR",
    }
)


class RecommendationFacade:
    async def recommend(
        self,
        user_id: str,
        request: RecommendationRequest,
    ) -> RecommendationResponse:
        run_id = request.run_id or new_run_id()
        with tracer.start_as_current_span("recommendation.v1") as span:
            span.set_attribute("aishop.run_id", run_id)
            span.set_attribute("aishop.request_id", request.request_id)
            span.set_attribute("aishop.recommendation_mode", request.mode)
            with bind_episode(
                run_id,
                message_id=None,
                user_id=user_id,
                force_keep=True,
                request_id=request.request_id,
                episode_id=request.episode_id or run_id,
                traceparent=request.traceparent,
                trusted_user_text=request.query,
            ):
                episode_service.start_run(
                    run_id=run_id,
                    message_id=None,
                    user_id=user_id,
                    session_id=None,
                    intent="PRODUCT_SEARCH",
                    queue_name="recommendation-v1",
                    force_keep=True,
                    experiment={
                        "contract": "recommendation/v1",
                        "mode": request.mode,
                        "requestId": request.request_id,
                        "catalogVersion": request.catalog_version,
                    },
                )
                episode_service.record_step(
                    "RECOMMENDATION_REQUEST",
                    run_id=run_id,
                    node_name="recommendation_facade",
                    output_data={
                        "mode": request.mode,
                        "hasQuery": bool(request.query),
                        "hasImage": bool(request.image_asset_id),
                        "constraintCount": sum(
                            len(getattr(request.constraints, name))
                            for name in (
                                "required_brands",
                                "excluded_brands",
                                "excluded_terms",
                                "use_cases",
                                "preferred_features",
                            )
                        ),
                    },
                )
                try:
                    if request.mode == "TEXT":
                        response = await self._recommend_text(user_id, request, run_id)
                    else:
                        response = await self._recommend_visual(user_id, request, run_id)
                    episode_service.update_run(
                        run_id=run_id,
                        scenario="RECOMMENDATION",
                        quality={
                            "status": response.status,
                            "resultCount": len(response.items),
                            "fallbackUsed": response.fallback_used,
                            "contractVersion": "recommendation/v1",
                        },
                    )
                    episode_service.finish_run(
                        "completed" if response.status == "COMPLETED" else "degraded",
                        run_id=run_id,
                        force_keep=True,
                    )
                    return response
                except Exception as exc:
                    logger.warning(
                        "recommendation_v1_failed",
                        request_id=request.request_id,
                        run_id=run_id,
                        error=type(exc).__name__,
                    )
                    episode_service.record_step(
                        "RECOMMENDATION_FAILED",
                        run_id=run_id,
                        node_name="recommendation_facade",
                        status="ERROR",
                        error_code=type(exc).__name__,
                    )
                    episode_service.finish_run(
                        "failed",
                        run_id=run_id,
                        force_keep=True,
                    )
                    raise

    async def _recommend_text(
        self,
        user_id: str,
        request: RecommendationRequest,
        run_id: str,
    ) -> RecommendationResponse:
        if request.candidate_product_ids:
            candidates = await product_service.load_verified_products(
                request.candidate_product_ids
            )
            products, source = await product_service.decide_verified_candidates(
                user_id=user_id,
                candidates=candidates,
                user_text=request.query or request.constraints.category or "候选商品推荐",
                request_id=request.request_id,
                runtime_constraints=request.constraints.model_dump(by_alias=True),
            )
            return build_response(
                request,
                run_id=run_id,
                products=products,
                status="COMPLETED" if products else "NO_RESULT",
                degradation=None if products else "候选商品不存在或已不可购买",
                trace={"source": source, "candidateMode": "EXPLICIT_IDS"},
                message=None if products else "没有可核验的候选商品，请重新描述需求。",
            )
        assistant, _biz_data, _biz_type, products, source = await product_service.search_products(
            user_id=user_id,
            keyword=request.query,
            user_text=request.query or "",
            request_id=request.request_id,
            runtime_constraints=request.constraints.model_dump(by_alias=True),
        )
        if source == "clarify":
            return build_response(
                request,
                run_id=run_id,
                products=[],
                status="CLARIFICATION_REQUIRED",
                message=assistant,
            )
        return build_response(
            request,
            run_id=run_id,
            products=products,
            status="COMPLETED" if products else "NO_RESULT",
            degradation=("检索没有返回满足当前约束且可购买的商品" if not products else None),
            fallback_used=source in {"rrf_fallback", "category", "browse", "hot_sale_explicit"},
            trace={"source": source},
            message=assistant if not products else None,
        )

    async def _recommend_visual(
        self,
        user_id: str,
        request: RecommendationRequest,
        run_id: str,
    ) -> RecommendationResponse:
        image_context = await agent_orchestrator._verify_image_context(
            user_id, request.image_asset_id
        )
        if image_context is None:
            return build_response(
                request,
                run_id=run_id,
                products=[],
                status="BLOCKED",
                degradation="图片资产未通过 Java Gateway 校验",
            )
        if request.selection_id or request.selected_subject_id:
            if not request.selection_id or not request.selected_subject_id:
                return build_response(
                    request,
                    run_id=run_id,
                    products=[],
                    status="CLARIFICATION_REQUIRED",
                    message="主体选择必须同时携带 selectionId 和 subjectId。",
                )
            preview = await visual_selection_store.preview(
                selection_id=request.selection_id,
                subject_id=request.selected_subject_id,
                user_id=user_id,
            )
            subject = VisualSubject.model_validate(preview.get("subject"))
            image_context = image_context.model_copy(update={"selected_subject": subject})
        result = await visual_product_search_service.search(
            user_id=user_id,
            image_context=image_context,
            query_text=request.query or "查找图中同款或相似商品",
            source_message_id=None,
            request_id=request.request_id,
            runtime_constraints=request.constraints.model_dump(by_alias=True),
        )
        if result.biz_type == "visual_subject_selection":
            return build_response(
                request,
                run_id=run_id,
                products=[],
                status="CLARIFICATION_REQUIRED",
                trace=result.retrieval_trace,
                message=result.content,
            )
        if not result.success:
            code = str(result.error_code or "VISUAL_SEARCH_ERROR")
            return build_response(
                request,
                run_id=run_id,
                products=[],
                status="BLOCKED" if code in _BLOCKING_VISUAL_CODES else "NO_RESULT",
                degradation=code,
                trace=result.retrieval_trace,
                message=result.content,
            )
        products = await product_service.load_verified_products(result.product_ids)
        if not products:
            products = parse_legacy_product_cards(result.assistant_cards)
        trace = result.retrieval_trace or {}
        return build_response(
            request,
            run_id=run_id,
            products=products,
            status="COMPLETED" if products else "NO_RESULT",
            fallback_used=bool(trace.get("degraded") or trace.get("fallback")),
            trace=trace,
            message=result.content if not products else None,
        )


recommendation_facade = RecommendationFacade()
